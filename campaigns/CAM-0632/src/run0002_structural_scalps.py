from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns" / "CAM-0632"
OUT = CAM / "artifacts" / "RUN-0002"
CACHE = ROOT / "tmp" / "cam0632_run0002_bars.parquet"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
CUTOFF = pd.Timestamp("2026-04-30")
PAIRS = [("SMH", "SOXL", "SOXS", 3.0), ("QQQ", "TQQQ", "SQQQ", 3.0)]
FORMATIONS = [1, 2, 5, 10]
HOLDS = [1, 2, 3, 5, 10, 15]
THRESHOLDS_BP = [5, 10, 20, 40]
MECHANISMS = ["impulse_continuation", "impulse_reversal", "leveraged_lag_convergence"]
COSTS_BP = [0, 1, 2, 5, 10]


def load_bars() -> pd.DataFrame:
    if CACHE.exists():
        bars = pd.read_parquet(CACHE)
    else:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        symbols = sorted({symbol for row in PAIRS for symbol in row[:3]})
        marks = ",".join("?" for _ in symbols)
        con = duckdb.connect(str(CATALOG), read_only=True)
        temp = (ROOT / "tmp" / "duckdb_cam0632_run0002").resolve()
        temp.mkdir(parents=True, exist_ok=True)
        con.execute(f"set temp_directory='{temp.as_posix()}'")
        con.execute("set threads=16")
        query = f"""
            select date, symbol, try_cast(timestamp as timestamptz) ts,
                   arg_max(open, try_cast(ingested_at as timestamp)) as "open",
                   arg_max(close, try_cast(ingested_at as timestamp)) as "close"
            from bars_1m
            where date <= date '2026-04-30'
              and feed='sip' and adjustment='raw'
              and symbol in ({marks})
              and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:59'
            group by 1,2,3
            order by 1,2,3
        """
        bars = con.execute(query, symbols).fetchdf()
        con.close()
        bars.to_parquet(CACHE, index=False)
    bars["date"] = pd.to_datetime(bars.date)
    bars["ts"] = pd.to_datetime(bars.ts, utc=True)
    if bars.date.max() > CUTOFF:
        raise RuntimeError("holdout row loaded")
    if bars.duplicated(["date", "symbol", "ts"]).any():
        raise RuntimeError("duplicate bar key")
    return bars


def max_drawdown_and_recovery(daily: pd.Series) -> tuple[float, int | None]:
    equity = 1.0 + daily.cumsum()
    peak = equity.cummax()
    dd = (peak - equity) / peak.replace(0, np.nan)
    max_dd = float(dd.max()) if len(dd) else 0.0
    trough_date = dd.idxmax() if len(dd) else None
    if trough_date is None or max_dd <= 0:
        return max_dd, 0
    prior_peak = peak.loc[trough_date]
    after = equity.loc[trough_date:]
    recovered = after[after >= prior_peak]
    if recovered.empty:
        return max_dd, None
    return max_dd, int((daily.index <= recovered.index[0]).sum() - (daily.index < trough_date).sum() - 1)


def select_nonoverlap(indices: np.ndarray, hold: int) -> np.ndarray:
    selected: list[int] = []
    last_exit = -1
    for index in indices:
        if int(index) > last_exit:
            selected.append(int(index))
            last_exit = int(index) + hold
    return np.asarray(selected, dtype=np.int32)


def build_contexts(bars: pd.DataFrame) -> tuple[dict[str, list[dict]], pd.DatetimeIndex, list[dict]]:
    contexts: dict[str, list[dict]] = {}
    attrition: list[dict] = []
    all_dates: set[pd.Timestamp] = set()
    for underlying, bull, inverse, _ in PAIRS:
        pair_name = f"{underlying}_{bull}_{inverse}"
        subset = bars[bars.symbol.isin([underlying, bull, inverse])]
        pivot_o = subset.pivot_table(index=["date", "ts"], columns="symbol", values="open", aggfunc="last")
        pivot_c = subset.pivot_table(index=["date", "ts"], columns="symbol", values="close", aggfunc="last")
        joined = pd.concat({"open": pivot_o, "close": pivot_c}, axis=1).dropna()
        parent_dates = int(subset.date.nunique())
        kept = 0
        for date, group in joined.groupby(level="date", sort=True):
            frame = group.droplevel("date").sort_index()
            if len(frame) < 300:
                continue
            local = frame.index.tz_convert("America/New_York")
            if local.strftime("%H:%M").min() > "09:30" or local.strftime("%H:%M").max() < "15:59":
                continue
            contexts.setdefault(pair_name, []).append({
                "date": pd.Timestamp(date),
                "times": frame.index,
                "local_hhmm": local.strftime("%H:%M").to_numpy(),
                "open": {symbol: frame[("open", symbol)].to_numpy(float) for symbol in (underlying, bull, inverse)},
                "close": {symbol: frame[("close", symbol)].to_numpy(float) for symbol in (underlying, bull, inverse)},
            })
            all_dates.add(pd.Timestamp(date))
            kept += 1
        attrition.append({"pair": pair_name, "parent_symbol_union_dates": parent_dates, "complete_aligned_dates": kept, "dates_removed": parent_dates - kept})
    return contexts, pd.DatetimeIndex(sorted(all_dates)), attrition


def trades_for_variant(contexts: dict[str, list[dict]], pair: tuple, formation: int, hold: int, threshold_bp: int, mechanism: str) -> pd.DataFrame:
    underlying, bull, inverse, leverage = pair
    pair_name = f"{underlying}_{bull}_{inverse}"
    threshold = threshold_bp / 10_000.0
    rows: list[dict] = []
    for context in contexts.get(pair_name, []):
        date = context["date"]
        times = context["times"]
        local_hhmm = context["local_hhmm"]
        opens = context["open"]
        closes = context["close"]
        n = len(times)
        t = np.arange(n)
        valid = (t >= formation - 1) & (t + hold < n) & (local_hhmm >= "09:35") & (local_hhmm <= "15:40")
        if not valid.any():
            continue
        idx = t[valid]
        uret = closes[underlying][idx] / opens[underlying][idx - formation + 1] - 1.0
        bret = closes[bull][idx] / opens[bull][idx - formation + 1] - 1.0
        iret = closes[inverse][idx] / opens[inverse][idx - formation + 1] - 1.0
        side = np.full(len(idx), "", dtype=object)
        strength = np.zeros(len(idx), dtype=float)
        if mechanism == "impulse_continuation":
            side[uret >= threshold] = bull
            side[uret <= -threshold] = inverse
            strength = np.abs(uret)
        elif mechanism == "impulse_reversal":
            side[uret >= threshold] = inverse
            side[uret <= -threshold] = bull
            strength = np.abs(uret)
        elif mechanism == "leveraged_lag_convergence":
            bull_lag = bret - leverage * uret
            inverse_lag = iret + leverage * uret
            buy_bull = (uret >= threshold) & (bull_lag <= -threshold)
            buy_inverse = (uret <= -threshold) & (inverse_lag <= -threshold)
            side[buy_bull] = bull
            side[buy_inverse] = inverse
            strength = np.where(buy_bull, -bull_lag, np.where(buy_inverse, -inverse_lag, 0.0))
        else:
            raise ValueError(mechanism)
        candidate_positions = np.flatnonzero(side != "")
        if not len(candidate_positions):
            continue
        chosen_positions = select_nonoverlap(candidate_positions, hold)
        for pos in chosen_positions:
            signal_i = int(idx[pos])
            symbol = str(side[pos])
            entry_i = signal_i + 1
            exit_i = signal_i + hold
            entry = float(opens[symbol][entry_i])
            exit_price = float(closes[symbol][exit_i])
            rows.append({
                "date": date,
                "pair": pair_name,
                "symbol": symbol,
                "signal_ts": times[signal_i],
                "entry_ts": times[entry_i],
                "exit_ts": times[exit_i] + pd.Timedelta(minutes=1),
                "gross_return": exit_price / entry - 1.0,
                "signal_strength": float(strength[pos]),
            })
    return pd.DataFrame(rows)


def metrics(trades: pd.DataFrame, calendar: pd.DatetimeIndex, cost_bp: int) -> tuple[dict, pd.Series]:
    net = trades.gross_return - 2 * cost_bp / 10_000.0
    daily = pd.Series(0.0, index=calendar)
    if len(trades):
        realized = pd.Series(net.to_numpy(), index=pd.to_datetime(trades.date)).groupby(level=0).sum()
        daily.loc[realized.index] = realized
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    weekly = daily.groupby(daily.index.to_period("W-FRI")).sum()
    recent12_start = CUTOFF - pd.DateOffset(months=12) + pd.Timedelta(days=1)
    recent18_start = CUTOFF - pd.DateOffset(months=18) + pd.Timedelta(days=1)
    max_dd, recovery = max_drawdown_and_recovery(daily)
    blocks = np.array_split(daily, 3)
    active = daily[daily != 0]
    return {
        "cost_bp_side": cost_bp,
        "trades": len(trades),
        "trades_per_session": len(trades) / len(calendar),
        "gross_return": float(trades.gross_return.sum()),
        "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= recent12_start].sum()),
        "recent18_return": float(daily[daily.index >= recent18_start].sum()),
        "mean_trade_bp": float(net.mean() * 10_000) if len(net) else np.nan,
        "green_all_days": float((daily > 0).mean()),
        "green_active_days": float((active > 0).mean()) if len(active) else np.nan,
        "positive_month_fraction": float((monthly > 0).mean()),
        "positive_week_fraction": float((weekly > 0).mean()),
        "max_drawdown": max_dd,
        "recovery_sessions": recovery,
        "block_returns": [float(block.sum()) for block in blocks],
        "worst_day": float(daily.min()),
    }, daily


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bars = load_bars()
    contexts, calendar, attrition = build_contexts(bars)
    if bars.date.max() > CUTOFF:
        raise RuntimeError("holdout access")
    variants = [(pair, f, h, threshold, mechanism) for pair in PAIRS for f in FORMATIONS for h in HOLDS for threshold in THRESHOLDS_BP for mechanism in MECHANISMS]
    rows: list[dict] = []
    for number, (pair, formation, hold, threshold, mechanism) in enumerate(variants, 1):
        pair_name = "_".join(pair[:3])
        variant = f"{pair_name}_{mechanism}_f{formation}_h{hold}_t{threshold}"
        trades = trades_for_variant(contexts, pair, formation, hold, threshold, mechanism)
        for cost in COSTS_BP:
            result, daily = metrics(trades, calendar, cost)
            rows.append({"variant": variant, "pair": pair_name, "mechanism": mechanism, "formation_min": formation, "hold_min": hold, "threshold_bp": threshold, **result})
        if number % 48 == 0:
            print(f"variants {number}/{len(variants)}", flush=True)
    leaderboard = pd.DataFrame(rows)
    leaderboard.to_csv(OUT / "leaderboard.csv", index=False)
    eligible = leaderboard[(leaderboard.cost_bp_side == 5) & (leaderboard.trades >= 250)].sort_values(["recent12_return", "net_return"], ascending=False)
    top = eligible.head(10).copy()
    top.to_csv(OUT / "top_5bp_candidates.csv", index=False)
    paths = []
    selected_trades = []
    for selected in top.drop_duplicates("variant").itertuples():
        variant = selected.variant
        pair = next(pair for pair in PAIRS if "_".join(pair[:3]) == selected.pair)
        trades = trades_for_variant(contexts, pair, int(selected.formation_min), int(selected.hold_min), int(selected.threshold_bp), selected.mechanism)
        selected_trades.append(trades.assign(variant=variant))
        for cost in [0, 2, 5, 10]:
            _, daily = metrics(trades, calendar, cost)
            for period_name, grouped in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum())]:
                paths.extend({"variant": variant, "cost_bp_side": cost, "period_type": period_name, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
    pd.DataFrame(paths).to_csv(OUT / "top_period_paths.csv", index=False)
    if selected_trades:
        pd.concat(selected_trades, ignore_index=True).to_parquet(OUT / "top_trade_ledgers.parquet", index=False)
    best_by_cost = leaderboard[leaderboard.trades >= 250].sort_values(["cost_bp_side", "recent12_return"], ascending=[True, False]).groupby("cost_bp_side", as_index=False).head(1)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_signal_variants": len(variants),
        "executed_signal_variants": int(leaderboard.variant.nunique()),
        "executed_costed_rows": len(leaderboard),
        "minimum_loaded_date": bars.date.min().date().isoformat(),
        "maximum_loaded_date": bars.date.max().date().isoformat(),
        "holdout_rows_loaded": 0,
        "cuda_used": False,
        "compute_reason": "576 path variants are sequential stateful CPU calculations; GPU transfer would not be material",
        "attrition": attrition,
        "best_by_cost": json.loads(best_by_cost.replace({np.nan: None}).to_json(orient="records")),
        "five_bp_variants_positive_all_blocks": int(((leaderboard.cost_bp_side == 5) & leaderboard.block_returns.apply(lambda x: all(value > 0 for value in x))).sum()),
        "decision_gate": "inspect_cost_chronology_activity_and_concentration_before_quote_replay",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
