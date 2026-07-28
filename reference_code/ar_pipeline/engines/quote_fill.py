"""Conservative SIP top-of-book quote-path simulation.

This is not broker-fill ground truth: SIP top of book cannot reconstruct venue
routing, hidden liquidity, queue position, or passive limit-order fills.  It
does provide a materially stricter validation layer than bar prices by replaying
the side of the NBBO available after a configured order-arrival time.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from ar_pipeline.contracts import normalized_side, require_columns
from ar_pipeline.marketdata import AlpacaHistoricalClient, CachedResponseStore, QuoteRequest, fetch_quote_requests
from ar_pipeline.validation import SafetyGateError, validate_trade_ledger


# Alpaca SIP quote sizes (`bs`/`as`) are reported in round lots.  The trade
# ledger quantity is expressed in shares, matching the bar-fill engine and
# Alpaca order quantities, so convert before applying displayed-size
# participation.  Treating the raw round-lot count as shares understates
# displayed liquidity by 100x and creates false missing-fill blockers.
ROUND_LOT_SHARES = 100.0


@dataclass(frozen=True)
class QuotePathPolicy:
    feed: str = "sip"
    order_latency_ms: int = 250
    max_quote_wait_ms: int = 2_000
    impact_bps_per_side: float = 0.0
    fee_bps_per_side: float = 0.0
    displayed_size_participation: float = 0.05
    allow_partial_fills: bool = False
    require_full_path_for_brackets: bool = True
    workers: int | str = "auto"

    def __post_init__(self) -> None:
        if self.feed.lower() != "sip":
            raise SafetyGateError("promotion quote validation requires SIP; use a non-promotable diagnostic for another feed")
        if self.order_latency_ms < 0 or self.max_quote_wait_ms <= 0:
            raise SafetyGateError("quote latency must be >= 0 and max_quote_wait_ms must be > 0")
        if self.impact_bps_per_side < 0 or self.fee_bps_per_side < 0:
            raise SafetyGateError("quote impact and fees must be non-negative")
        if not 0 < self.displayed_size_participation <= 1:
            raise SafetyGateError("displayed_size_participation must be in (0, 1]")

    @property
    def wait(self) -> pd.Timedelta:
        return pd.Timedelta(milliseconds=self.max_quote_wait_ms)

    @property
    def latency(self) -> pd.Timedelta:
        return pd.Timedelta(milliseconds=self.order_latency_ms)

    @property
    def side_cost_rate(self) -> float:
        return (self.impact_bps_per_side + self.fee_bps_per_side) / 10_000.0


def run_quote_fill(config: dict[str, Any], input_trades: Path, output_dir: Path) -> pd.DataFrame:
    """Run the configured quote layer and persist a complete result ledger."""

    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_trades.exists():
        raise FileNotFoundError(input_trades)
    trades = pd.read_parquet(input_trades)
    fill_cfg = config.get("quote_fill", {})
    mode = str(fill_cfg.get("mode", "source_proxy_test_only"))
    if mode in {"source_proxy", "source_proxy_test_only"}:
        out = _run_source_proxy_quote_fill(trades, fill_cfg)
    elif mode in {"alpaca_sip", "alpaca_sip_quote_path"}:
        out = _run_alpaca_sip_quote_path(trades, fill_cfg, output_dir)
    else:
        raise ValueError(f"Unknown quote_fill.mode {mode!r}")
    out.to_parquet(output_dir / "quote_filled_trades.parquet", index=False)
    _write_summary(out, output_dir, mode)
    return out


def _run_source_proxy_quote_fill(trades: pd.DataFrame, fill_cfg: dict[str, Any]) -> pd.DataFrame:
    """Offline fixture only; structurally marked as non-promotable."""

    if "source_return" not in trades.columns:
        raise ValueError("trade ledger must contain source_return")
    extra_bps = float(fill_cfg.get("extra_bps_per_side", 10.0))
    out = trades.copy()
    out["quote_fill_mode"] = "source_proxy_test_only"
    out["quote_fill_status"] = "filled_proxy_non_promotable"
    out["quote_return"] = pd.to_numeric(out["source_return"], errors="coerce") - extra_bps / 10_000.0 * 2.0
    out["source_quote_gap"] = out["quote_return"] - pd.to_numeric(out["source_return"], errors="coerce")
    out["quote_fill_promotable"] = False
    out["quote_fill_reason"] = "proxy_not_market_data"
    return out


def _run_alpaca_sip_quote_path(trades: pd.DataFrame, fill_cfg: dict[str, Any], output_dir: Path) -> pd.DataFrame:
    required = {"candidate_id", "symbol", "side", "signal_ts", "signal_available_ts", "entry_submit_ts", "entry_ts", "exit_ts"}
    require_columns(trades, required, "quote-path trade ledger")
    policy = QuotePathPolicy(
        feed=str(fill_cfg.get("feed", "sip")),
        order_latency_ms=int(fill_cfg.get("order_latency_ms", 250)),
        max_quote_wait_ms=int(fill_cfg.get("max_quote_wait_ms", fill_cfg.get("window_seconds", 2) * 1_000)),
        impact_bps_per_side=float(fill_cfg.get("impact_bps_per_side", 0.0)),
        fee_bps_per_side=float(fill_cfg.get("fee_bps_per_side", 0.0)),
        displayed_size_participation=float(fill_cfg.get("displayed_size_participation", 0.05)),
        allow_partial_fills=bool(fill_cfg.get("allow_partial_fills", False)),
        require_full_path_for_brackets=bool(fill_cfg.get("require_full_path_for_brackets", True)),
        workers=fill_cfg.get("workers", "auto"),
    )
    work = validate_trade_ledger(trades).sort_values(["entry_submit_ts", "symbol", "candidate_id"], kind="stable").reset_index(drop=True)
    client = AlpacaHistoricalClient.from_env(fill_cfg.get("env_path", "D:/AlgoResearch/.env"))
    cache = CachedResponseStore(fill_cfg.get("cache_dir", output_dir / "quote_cache"))
    requests, intervals = _plan_quote_requests(work, policy)
    frames = fetch_quote_requests(requests, client=client, cache=cache, workers=policy.workers)
    rows: list[dict[str, Any]] = []
    for record in work.to_dict("records"):
        interval = intervals[_trade_key(record)]
        quotes = _quotes_for_interval(frames, interval)
        rows.append(_simulate_one_quote_path(record, quotes, policy, interval))
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class _QuoteInterval:
    request: QuoteRequest
    exit_request: QuoteRequest | None
    entry_arrival: pd.Timestamp
    exit_arrival: pd.Timestamp
    full_path: bool


def _plan_quote_requests(trades: pd.DataFrame, policy: QuotePathPolicy) -> tuple[list[QuoteRequest], dict[str, _QuoteInterval]]:
    requests: list[QuoteRequest] = []
    intervals: dict[str, _QuoteInterval] = {}
    for record in trades.to_dict("records"):
        entry_arrival = pd.Timestamp(record["entry_submit_ts"]) + policy.latency
        exit_submit = pd.Timestamp(record.get("exit_submit_ts", record["exit_ts"]))
        exit_arrival = exit_submit + policy.latency
        if exit_arrival <= entry_arrival:
            raise SafetyGateError("quote-path exit arrival must follow entry arrival")
        full_path = policy.require_full_path_for_brackets and (
            _present(record.get("stop_price")) or _present(record.get("take_profit_price"))
        )
        if full_path:
            start, end = entry_arrival, exit_arrival + policy.wait
            exit_request = None
        else:
            # Endpoint fills are separate requests; grouping by trade avoids
            # accidentally treating unobserved intrabar quotes as a full path.
            start, end = entry_arrival, entry_arrival + policy.wait
            exit_request = QuoteRequest(str(record["symbol"]), exit_arrival, exit_arrival + policy.wait, policy.feed)
            requests.append(exit_request)
        request = QuoteRequest(str(record["symbol"]), start, end, policy.feed)
        requests.append(request)
        intervals[_trade_key(record)] = _QuoteInterval(request, exit_request, entry_arrival, exit_arrival, full_path)
    return requests, intervals


def _quotes_for_interval(frames: dict[QuoteRequest, pd.DataFrame], interval: _QuoteInterval) -> pd.DataFrame:
    entry = frames.get(interval.request, pd.DataFrame())
    if interval.full_path:
        return entry
    exit_ = frames.get(interval.exit_request, pd.DataFrame()) if interval.exit_request else pd.DataFrame()
    return pd.concat([entry, exit_], ignore_index=True).sort_values("timestamp", kind="stable").reset_index(drop=True)


def _simulate_one_quote_path(
    record: dict[str, Any],
    entry_quotes: pd.DataFrame,
    policy: QuotePathPolicy,
    interval: _QuoteInterval,
) -> dict[str, Any]:
    # Endpoint exit frames are attached by a direct cache lookup in the public
    # runner below; this helper deliberately accepts the normalized path frame
    # to keep all order-state decisions in one place.
    side = normalized_side(record["side"])
    entry = _first_quote_after(entry_quotes, interval.entry_arrival, policy.wait)
    base = _quote_record_base(record, policy, interval)
    if entry is None:
        return {**base, "quote_fill_status": "unfilled", "quote_fill_reason": "missing_or_stale_entry_quote"}
    entry_price, entry_quantity, entry_reason = _market_fill(entry, side, is_entry=True, quantity=float(record.get("quantity", 1.0) or 1.0), policy=policy)
    if entry_price is None:
        return {**base, "quote_fill_status": "unfilled", "quote_fill_reason": entry_reason, "quote_entry_ts": entry["timestamp"]}
    exit_quote, exit_reason = _choose_exit_quote(entry_quotes, interval, side, record, policy)
    if exit_quote is None:
        return {
            **base,
            "quote_fill_status": "unfilled",
            "quote_fill_reason": exit_reason,
            "quote_entry_ts": entry["timestamp"],
            "quote_entry_price": entry_price,
            "quote_entry_quantity": entry_quantity,
        }
    exit_price, exit_quantity, fill_reason = _market_fill(exit_quote, side, is_entry=False, quantity=entry_quantity, policy=policy)
    if exit_price is None:
        return {
            **base,
            "quote_fill_status": "unfilled",
            "quote_fill_reason": fill_reason,
            "quote_entry_ts": entry["timestamp"],
            "quote_entry_price": entry_price,
            "quote_entry_quantity": entry_quantity,
        }
    if not policy.allow_partial_fills and exit_quantity + 1e-12 < entry_quantity:
        return {**base, "quote_fill_status": "unfilled", "quote_fill_reason": "exit_displayed_size_insufficient"}
    quantity = min(entry_quantity, exit_quantity)
    if quantity <= 0:
        return {**base, "quote_fill_status": "unfilled", "quote_fill_reason": "zero_fill_quantity"}
    quote_return = exit_price / entry_price - 1.0 if side == "long" else entry_price / exit_price - 1.0
    source_return = pd.to_numeric(pd.Series([record.get("source_return")]), errors="coerce").iloc[0]
    return {
        **base,
        "quote_fill_status": "filled" if quantity >= float(record.get("quantity", 1.0) or 1.0) else "partial_fill",
        "quote_fill_reason": exit_reason,
        "quote_entry_ts": entry["timestamp"],
        "quote_exit_ts": exit_quote["timestamp"],
        "quote_entry_price": entry_price,
        "quote_exit_price": exit_price,
        "quote_entry_quantity": entry_quantity,
        "quote_exit_quantity": exit_quantity,
        "quote_filled_quantity": quantity,
        "quote_entry_spread_bps": _spread_bps(entry),
        "quote_exit_spread_bps": _spread_bps(exit_quote),
        "quote_return": quote_return,
        "source_quote_gap": quote_return - float(source_return) if pd.notna(source_return) else np.nan,
        "quote_fill_promotable": True,
        "entry_quote_conditions": _safe_json(entry.get("conditions")),
        "exit_quote_conditions": _safe_json(exit_quote.get("conditions")),
        "entry_quote_tape": entry.get("tape"),
        "exit_quote_tape": exit_quote.get("tape"),
    }


def _choose_exit_quote(
    quotes: pd.DataFrame,
    interval: _QuoteInterval,
    side: str,
    record: dict[str, Any],
    policy: QuotePathPolicy,
) -> tuple[pd.Series | None, str]:
    if quotes.empty:
        return None, "missing_quote_path"
    stop = _number_or_none(record.get("stop_price"))
    target = _number_or_none(record.get("take_profit_price"))
    if interval.full_path:
        path = quotes[(quotes["timestamp"] >= interval.entry_arrival) & (quotes["timestamp"] <= interval.exit_arrival)].copy()
        for quote in path.itertuples(index=False):
            bid = float(quote.bid_price)
            ask = float(quote.ask_price)
            if side == "long":
                if stop is not None and bid <= stop:
                    return pd.Series(quote._asdict()), "stop_quote_path"
                if target is not None and bid >= target:
                    return pd.Series(quote._asdict()), "take_profit_quote_path"
            else:
                if stop is not None and ask >= stop:
                    return pd.Series(quote._asdict()), "stop_quote_path"
                if target is not None and ask <= target:
                    return pd.Series(quote._asdict()), "take_profit_quote_path"
    quote = _first_quote_after(quotes, interval.exit_arrival, policy.wait)
    return quote, "time_exit_quote_path" if interval.full_path else "time_exit_endpoint_quote"


def _first_quote_after(quotes: pd.DataFrame, arrival: pd.Timestamp, wait: pd.Timedelta) -> pd.Series | None:
    if quotes.empty:
        return None
    usable = quotes[(quotes["timestamp"] >= arrival) & (quotes["timestamp"] <= arrival + wait)]
    if usable.empty:
        return None
    return usable.iloc[0]


def _market_fill(quote: pd.Series, side: str, *, is_entry: bool, quantity: float, policy: QuotePathPolicy) -> tuple[float | None, float, str]:
    buy = (side == "long" and is_entry) or (side == "short" and not is_entry)
    price_col = "ask_price" if buy else "bid_price"
    size_col = "ask_size" if buy else "bid_size"
    raw_price = float(quote[price_col])
    raw_size = float(quote[size_col]) if pd.notna(quote[size_col]) else 0.0
    if raw_price <= 0:
        return None, 0.0, "invalid_quote_side_price"
    available = max(0.0, raw_size * ROUND_LOT_SHARES * policy.displayed_size_participation)
    if available <= 0:
        return None, 0.0, "displayed_size_missing_or_zero"
    if quantity > available and not policy.allow_partial_fills:
        return None, 0.0, "displayed_size_insufficient"
    filled = min(quantity, available)
    adjusted = raw_price * (1.0 + policy.side_cost_rate if buy else 1.0 - policy.side_cost_rate)
    return adjusted, filled, "filled"


def _quote_record_base(record: dict[str, Any], policy: QuotePathPolicy, interval: _QuoteInterval) -> dict[str, Any]:
    return {
        **record,
        "quote_fill_mode": "alpaca_sip_quote_path",
        "quote_feed": policy.feed,
        "quote_path_mode": "full_path" if interval.full_path else "endpoint_path",
        "quote_entry_arrival_ts": interval.entry_arrival,
        "quote_exit_arrival_ts": interval.exit_arrival,
        "quote_fill_promotable": False,
        "quote_entry_ts": pd.NaT,
        "quote_exit_ts": pd.NaT,
        "quote_entry_price": np.nan,
        "quote_exit_price": np.nan,
        "quote_entry_quantity": np.nan,
        "quote_exit_quantity": np.nan,
        "quote_filled_quantity": np.nan,
        "quote_entry_spread_bps": np.nan,
        "quote_exit_spread_bps": np.nan,
        "quote_return": np.nan,
        "source_quote_gap": np.nan,
    }


def _write_summary(out: pd.DataFrame, output_dir: Path, mode: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if "candidate_id" not in out.columns:
        summary = pd.DataFrame(rows)
    else:
        for candidate_id, group in out.groupby("candidate_id", sort=True):
            quote_values = pd.to_numeric(group.get("quote_return"), errors="coerce")
            if "quote_fill_status" in group.columns:
                valid_status = group["quote_fill_status"].astype(str).isin({"filled", "partial_fill"})
            else:
                # Compatibility for pre-v2 diagnostic dataframes.  New quote
                # ledgers always include an explicit status/reason.
                valid_status = quote_values.notna()
            filled = group[valid_status & quote_values.notna()].copy()
            source_all = pd.to_numeric(group.get("source_return"), errors="coerce").dropna()
            source_filled = pd.to_numeric(filled.get("source_return"), errors="coerce").dropna()
            quote_filled = pd.to_numeric(filled.get("quote_return"), errors="coerce").dropna()
            proxy = group.get("quote_fill_mode", pd.Series("", index=group.index)).astype(str).eq("source_proxy_test_only").all()
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "sampled_trades": int(len(group)),
                    "filled_trades": int(len(filled)),
                    "missing_trades": int(len(group) - len(filled)),
                    "fill_rate": float(len(filled) / len(group)) if len(group) else np.nan,
                    "source_total_all_sampled": float((1.0 + source_all).prod() - 1.0) if len(source_all) else np.nan,
                    "source_total_filled_only": float((1.0 + source_filled).prod() - 1.0) if len(source_filled) else np.nan,
                    "quote_total_filled_only": float((1.0 + quote_filled).prod() - 1.0) if len(quote_filled) else np.nan,
                    "source_mean_filled_only": float(source_filled.mean()) if len(source_filled) else np.nan,
                    "quote_mean_filled_only": float(quote_filled.mean()) if len(quote_filled) else np.nan,
                    "avg_gap_filled_only": float(pd.to_numeric(filled.get("source_quote_gap"), errors="coerce").mean()) if len(filled) else np.nan,
                    "avg_entry_spread_bps": float(pd.to_numeric(filled.get("quote_entry_spread_bps"), errors="coerce").mean()) if len(filled) else np.nan,
                    "avg_exit_spread_bps": float(pd.to_numeric(filled.get("quote_exit_spread_bps"), errors="coerce").mean()) if len(filled) else np.nan,
                    "quote_evidence_promotable": bool(not proxy and len(group) == len(filled) and len(filled) > 0),
                    "quote_mode": mode,
                }
            )
        summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "source_vs_quote.csv", index=False)
    lines = [
        "# Quote-Path Fill Report",
        "",
        f"Mode: `{mode}`",
        "",
        "A SIP top-of-book replay is a conservative execution simulation, not broker-fill ground truth. Missing or partial fills are retained and block promotion by default.",
        "",
        "```text",
        summary.to_string(index=False),
        "```",
        "",
    ]
    (output_dir / "fill_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def _trade_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            str(record.get("candidate_id", "")),
            str(record.get("symbol", "")),
            str(pd.Timestamp(record.get("signal_ts", record.get("entry_ts"))).isoformat()),
            str(pd.Timestamp(record.get("entry_ts")).isoformat()),
            str(pd.Timestamp(record.get("exit_ts")).isoformat()),
        ]
    )


def _spread_bps(quote: pd.Series) -> float:
    bid = float(quote["bid_price"])
    ask = float(quote["ask_price"])
    midpoint = (bid + ask) / 2.0
    return (ask - bid) / midpoint * 10_000.0 if midpoint > 0 else np.nan


def _present(value: Any) -> bool:
    return _number_or_none(value) is not None


def _number_or_none(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except TypeError:
        return str(value)


# Compatibility helpers used by existing notebooks/tests.  They follow the
# stricter normalized names internally and do not expose a proxy as real SIP.
def first_valid_quote(quotes: pd.DataFrame, side: str) -> pd.Series | None:
    if quotes.empty:
        return None
    normalized = quotes.copy()
    rename = {"t": "timestamp", "bp": "bid_price", "ap": "ask_price", "bs": "bid_size", "as": "ask_size"}
    normalized = normalized.rename(columns={key: value for key, value in rename.items() if key in normalized.columns})
    if not {"timestamp", "bid_price", "ask_price"}.issubset(normalized.columns):
        return None
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True, errors="coerce", format="mixed")
    for column in ("bid_price", "ask_price"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    valid = normalized[(normalized["bid_price"] > 0) & (normalized["ask_price"] >= normalized["bid_price"])]
    if valid.empty:
        return None
    return valid.iloc[0]


def spread_bps(quote: pd.Series) -> float:
    if "bid_price" not in quote and "bp" in quote:
        quote = quote.rename({"bp": "bid_price", "ap": "ask_price"})
    return _spread_bps(quote)
