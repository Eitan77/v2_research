"""Safety gates for configuration, signal ledgers, and reproducibility.

These checks deliberately fail closed.  A backtest that cannot establish when
an input was available is a research note, not a promotable result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .contracts import BarTiming, ContractError, as_utc, fingerprint, normalized_side, require_columns, timeframe_delta


class SafetyGateError(ContractError):
    """A non-negotiable research safety gate failed."""


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    config_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_run_config(config: dict[str, Any], *, strict: bool = True) -> ValidationResult:
    """Validate the version-2 research contract without making unsafe defaults."""

    errors: list[str] = []
    warnings: list[str] = []
    if int(config.get("schema_version", 0) or 0) < 2:
        errors.append("schema_version=2 is required; legacy run configurations are blocked from promotion workflows")

    data = config.get("data") if isinstance(config.get("data"), dict) else {}
    scan = config.get("scan") if isinstance(config.get("scan"), dict) else {}
    research = config.get("research") if isinstance(config.get("research"), dict) else {}
    quote = config.get("quote_fill") if isinstance(config.get("quote_fill"), dict) else {}

    for key in ("catalog_path", "table", "feed", "adjustment", "bar_timestamp_label"):
        if not data.get(key):
            errors.append(f"data.{key} must be explicit")
    if data.get("feed") and str(data["feed"]).lower() not in {"sip", "iex", "otc"}:
        errors.append("data.feed must identify the provider feed (for example sip or iex)")
    if data.get("adjustment") and str(data["adjustment"]).lower() not in {"raw", "split", "dividend", "all"}:
        errors.append("data.adjustment must be raw, split, dividend, or all")

    try:
        BarTiming(
            timeframe=str(scan.get("timeframe", "")),
            timestamp_label=str(data.get("bar_timestamp_label", "")),
            decision_latency_ms=int(scan.get("decision_latency_ms", 0)),
        )
    except (ContractError, TypeError, ValueError) as exc:
        errors.append(str(exc))

    for key in ("train_start", "train_end", "holding_bars", "entry_model"):
        if scan.get(key) in {None, ""}:
            errors.append(f"scan.{key} must be explicit")
    if scan.get("entry_model") and scan.get("entry_model") != "next_actionable_bar_open":
        errors.append("scan.entry_model must be 'next_actionable_bar_open' for the canonical bar screen")
    try:
        if int(scan.get("holding_bars", 0)) <= 0:
            errors.append("scan.holding_bars must be > 0")
    except (TypeError, ValueError):
        errors.append("scan.holding_bars must be an integer")

    holdout = research.get("sealed_holdout") if isinstance(research.get("sealed_holdout"), dict) else {}
    for key in ("start", "end"):
        if not holdout.get(key):
            errors.append(f"research.sealed_holdout.{key} must be explicit")
    if not bool(holdout.get("locked", False)):
        errors.append("research.sealed_holdout.locked must remain true until an explicit OOS approval")
    try:
        train_end = as_utc(f"{scan['train_end']}T23:59:59Z")
        holdout_start = as_utc(f"{holdout['start']}T00:00:00Z")
        if train_end >= holdout_start:
            errors.append("training range overlaps the sealed holdout")
    except (KeyError, ContractError):
        pass

    universe = data.get("universe") if isinstance(data.get("universe"), dict) else {}
    mode = str(universe.get("mode", "explicit_symbols"))
    if mode == "pit_index" and not universe.get("snapshot_id"):
        errors.append("data.universe.snapshot_id is required for a PIT index universe")
    if mode == "pit_index" and not bool(universe.get("known_at_verified", False)):
        errors.append("PIT universe is not verified as known at decision time; do not use approximate membership for promotion")
    if mode not in {"explicit_symbols", "pit_index", "all"}:
        errors.append("data.universe.mode must be explicit_symbols, pit_index, or all")

    if quote.get("mode") not in {"alpaca_sip_quote_path", "source_proxy_test_only", None}:
        errors.append("quote_fill.mode must be alpaca_sip_quote_path or source_proxy_test_only")
    if quote.get("mode") == "source_proxy_test_only" and strict:
        warnings.append("source_proxy_test_only may be used in tests but never supports promotion")
    if quote.get("mode") == "alpaca_sip_quote_path" and str(quote.get("feed", data.get("feed", ""))).lower() != "sip":
        errors.append("quote-path promotion requires explicitly configured SIP quotes")

    return ValidationResult(not errors, tuple(errors), tuple(warnings), fingerprint(config))


def assert_safe_run_config(config: dict[str, Any]) -> ValidationResult:
    result = validate_run_config(config)
    if not result.ok:
        raise SafetyGateError("Unsafe run configuration:\n- " + "\n- ".join(result.errors))
    return result


def validate_signal_ledger(signals: pd.DataFrame, timing: BarTiming) -> pd.DataFrame:
    """Normalize and validate a signal ledger before it reaches an execution engine."""

    require_columns(signals, {"candidate_id", "symbol", "signal_ts", "side"}, "signal ledger")
    out = signals.copy()
    out["signal_ts"] = pd.to_datetime(out["signal_ts"], utc=True, errors="coerce", format="mixed")
    if out["signal_ts"].isna().any():
        raise SafetyGateError("signal ledger contains invalid signal_ts values")
    if "signal_available_ts" not in out:
        out["signal_available_ts"] = [timing.available_at(ts) for ts in out["signal_ts"]]
    else:
        out["signal_available_ts"] = pd.to_datetime(out["signal_available_ts"], utc=True, errors="coerce", format="mixed")
    if out["signal_available_ts"].isna().any():
        raise SafetyGateError("signal ledger contains invalid signal_available_ts values")
    legal = pd.Series([timing.available_at(ts) for ts in out["signal_ts"]], index=out.index)
    if (out["signal_available_ts"] < legal).any():
        raise SafetyGateError("signal ledger makes a decision before the source bar is complete")
    out["side"] = [normalized_side(value) for value in out["side"]]
    if out.duplicated(["candidate_id", "symbol", "signal_ts"], keep=False).any():
        raise SafetyGateError("signal ledger contains duplicate candidate/symbol/signal_ts rows")
    return out


def validate_trade_ledger(trades: pd.DataFrame) -> pd.DataFrame:
    """Ensure every emitted trade has a causally valid, auditable timeline."""

    require_columns(
        trades,
        {"candidate_id", "symbol", "side", "signal_ts", "signal_available_ts", "entry_submit_ts", "entry_ts", "exit_ts"},
        "trade ledger",
    )
    out = trades.copy()
    for col in ("signal_ts", "signal_available_ts", "entry_submit_ts", "entry_ts", "exit_ts"):
        out[col] = pd.to_datetime(out[col], utc=True, errors="coerce", format="mixed")
    if out[["signal_ts", "signal_available_ts", "entry_submit_ts", "entry_ts", "exit_ts"]].isna().any().any():
        raise SafetyGateError("trade ledger contains invalid timestamps")
    if (out["entry_submit_ts"] < out["signal_available_ts"]).any():
        raise SafetyGateError("trade submitted before signal availability")
    if (out["entry_ts"] < out["entry_submit_ts"]).any():
        raise SafetyGateError("trade fill occurs before submission")
    if (out["exit_ts"] <= out["entry_ts"]).any():
        raise SafetyGateError("trade exit must follow entry")
    out["side"] = [normalized_side(value) for value in out["side"]]
    return out


def file_fingerprint(path: str | Path) -> str:
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)
    digest = __import__("hashlib").sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def required_outcome_embargo(timeframe: str, holding_bars: int) -> pd.Timedelta:
    if int(holding_bars) <= 0:
        raise SafetyGateError("holding_bars must be positive")
    return pd.Timedelta(timeframe_delta(timeframe)) * int(holding_bars)
