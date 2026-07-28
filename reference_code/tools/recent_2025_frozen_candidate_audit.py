"""Audit 2025 performance of candidates selected without using 2025."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

import big_move_entry_exit_refinement as entry_ref
import big_move_volatility_direction_cuda as big
import cross_pair_big_move_cuda as cross
import earnings_opening_momentum_scan as earn
import opening_information_persistence_cuda as opening


def recent_metrics(dates: np.ndarray, daily: np.ndarray, trades: int, label: str, rank: int) -> dict:
    m = big.metrics(dates, daily)
    return {
        "family": label, "training_rank": rank, "period_start": str(dates[0]),
        "period_end": str(dates[-1]), "trades": int(trades),
        "total_return": float(np.prod(1 + daily) - 1),
        **m,
    }


def audit_opening(root: Path) -> list[dict]:
    stage = root / "stage_02_cuda_scan"
    df = pd.read_parquet(stage / "event_matrix_features.parquet")
    df["session_date"] = pd.to_datetime(df.session_date)
    dates = np.array(sorted(df.loc[df.session_date.dt.year.eq(2025), "session_date"].unique()), dtype="datetime64[D]")
    rows = pd.read_csv(stage / "train_selected_top30.csv").head(30)
    out = []
    for rank, r in rows.iterrows():
        picks = opening.select_trades(
            df, int(r.checkpoint_min), str(r.formula), int(r.top_n),
            float(r.min_open_bps), float(r.min_open_rvol), str(r.regime),
        )
        picks = picks[picks.session_date.dt.year.eq(2025)].dropna(subset=[f"ret_{int(r.exit_end_min)}"])
        gross = picks.groupby("session_date")[f"ret_{int(r.exit_end_min)}"].mean()
        daily = pd.Series(0.0, index=pd.to_datetime(dates))
        daily.loc[pd.to_datetime(gross.index)] = gross.values - 2 * float(r.cost_bps_side) / 10000
        rec = recent_metrics(dates, daily.to_numpy(), len(picks), "opening_information", rank + 1)
        rec["spec"] = json.dumps({
            "checkpoint": int(r.checkpoint_min), "formula": r.formula, "top_n": int(r.top_n),
            "min_open_bps": r.min_open_bps, "rvol": r.min_open_rvol,
            "regime": r.regime, "exit": int(r.exit_end_min),
        }, sort_keys=True)
        out.append(rec)
    return out


def audit_earnings(root: Path, catalog: str) -> list[dict]:
    base_df = pd.read_parquet(root / "stage_02_cuda_scan" / "event_matrix_features.parquet")
    base_df["session_date"] = pd.to_datetime(base_df.session_date)
    df = earn.attach_earnings(base_df, catalog)
    dates = np.array(sorted(base_df.loc[
        base_df.session_date.between("2025-01-01", earn.EVENT_END), "session_date"
    ].unique()), dtype="datetime64[D]")
    rows = pd.read_csv(root / "stage_02b_earnings_conditioned" / "earnings_train_selected_top30.csv").head(30)
    out = []
    for rank, r in rows.iterrows():
        picks = earn.select(
            df, int(r.checkpoint_min), str(r.formula), int(r.top_n),
            float(r.min_open_bps), float(r.min_gap_bps), str(r.surprise_filter), str(r.regime),
        )
        picks = picks[picks.session_date.dt.year.eq(2025)].dropna(subset=[f"ret_{int(r.exit_end_min)}"])
        gross = picks.groupby("session_date")[f"ret_{int(r.exit_end_min)}"].mean()
        daily = pd.Series(0.0, index=pd.to_datetime(dates))
        daily.loc[pd.to_datetime(gross.index)] = gross.values - 2 * float(r.cost_bps_side) / 10000
        rec = recent_metrics(dates, daily.to_numpy(), len(picks), "earnings_partial_2025", rank + 1)
        rec["spec"] = json.dumps({
            "checkpoint": int(r.checkpoint_min), "formula": r.formula,
            "surprise": r.surprise_filter, "exit": int(r.exit_end_min),
        }, sort_keys=True)
        out.append(rec)
    return out


def big_cache(a: dict, cp: int, model: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    i = cp // 5 - 1
    idx = a["symbol_to_i"]
    score = big.direction_score(a, model, i)
    pair_range = np.maximum(
        a["cum_high"][idx["SOXL"], :, i] / a["cum_low"][idx["SOXL"], :, i] - 1,
        a["cum_high"][idx["SOXS"], :, i] / a["cum_low"][idx["SOXS"], :, i] - 1,
    )
    pair_rvol = np.nanmean(np.stack([
        a["cum_volume"][idx["SOXL"], :, i] / a["hist_cum_volume"][idx["SOXL"], :, i],
        a["cum_volume"][idx["SOXS"], :, i] / a["hist_cum_volume"][idx["SOXS"], :, i],
    ]), axis=0)
    entry, hi, lo, cl = big.selected_paths(a, score, cp)
    returns = big.path_returns_gpu(entry, hi, lo, cl, big.exit_specs())
    return score, pair_range, pair_rvol, returns


def find_exit_id(specs: list[dict], r: pd.Series) -> int:
    for i, x in enumerate(specs):
        if str(x["horizon"]) == str(r.horizon) and x["tp_bps"] == r.tp_bps and x["sl_bps"] == r.sl_bps:
            return i
    raise KeyError("exit spec")


def audit_big(root: Path) -> list[dict]:
    stage = root / "stage_03_big_move_volatility_direction"
    bars = pd.read_parquet(stage / "bars_cache.parquet")
    big.SYMBOLS = ("SOXL", "SOXS", "SMH", "QQQ", "NVDA", "AMD", "AVGO")
    a = big.build_arrays(bars)
    recent = a["dates"] >= np.datetime64("2025-01-01")
    dates = a["dates"][recent]
    rows = pd.read_csv(stage / "training_frozen_top50.csv").head(50)
    cache = {}
    out = []
    for rank, r in rows.iterrows():
        key = (int(r.checkpoint_min), str(r.direction_model))
        cache.setdefault(key, big_cache(a, *key))
        score, rng, rvol, returns = cache[key]
        active = (
            np.isfinite(score) & (score != 0)
            & (np.abs(score) * 10000 >= r.score_bps)
            & (rng * 10000 >= r.range_bps)
            & (rvol >= r.rvol)
        )
        gross = returns[:, find_exit_id(big.exit_specs(), r)]
        active &= np.isfinite(gross)
        daily = np.zeros(recent.sum())
        daily[active[recent]] = gross[recent][active[recent]] - 2 * r.cost_bps_side / 10000
        rec = recent_metrics(dates, daily, int(active[recent].sum()), "big_move_soxl_soxs", rank + 1)
        rec["spec"] = json.dumps({
            "checkpoint": key[0], "model": key[1], "gate": r.gate,
            "score_bps": r.score_bps, "range_bps": r.range_bps,
            "rvol": r.rvol, "horizon": r.horizon, "tp": r.tp_bps, "sl": r.sl_bps,
        }, sort_keys=True)
        out.append(rec)
    return out


def audit_entry(root: Path) -> list[dict]:
    stage = root / "stage_04_big_move_entry_exit_refinement"
    bars = pd.read_parquet(root / "stage_03_big_move_volatility_direction" / "bars_cache.parquet")
    big.SYMBOLS = ("SOXL", "SOXS", "SMH", "QQQ", "NVDA", "AMD", "AVGO")
    a = big.build_arrays(bars)
    recent = a["dates"] >= np.datetime64("2025-01-01")
    dates = a["dates"][recent]
    rows = pd.read_csv(stage / "training_frozen_top50.csv").head(50)
    core_map = {x["core"]: x for x in entry_ref.CORES}
    cache = {}
    out = []
    for rank, r in rows.iterrows():
        core = core_map[str(r.core)]
        entry_spec = {"entry_model": r.entry_model, "offset_bps": int(r.offset_bps), "wait_min": int(r.wait_min)}
        key = (r.core, r.entry_model, int(r.offset_bps), int(r.wait_min))
        if key not in cache:
            score, mask = entry_ref.core_state(a, core)
            entry, _, hi, lo, cl = entry_ref.entry_paths(a, score, core["checkpoint"], entry_spec)
            cache[key] = (mask, big.path_returns_gpu(entry, hi, lo, cl, entry_ref.EXIT_SPECS))
        mask, returns = cache[key]
        gross = returns[:, find_exit_id(entry_ref.EXIT_SPECS, r)]
        active = mask & np.isfinite(gross)
        daily = np.zeros(recent.sum())
        daily[active[recent]] = gross[recent][active[recent]] - 2 * r.cost_bps_side / 10000
        rec = recent_metrics(dates, daily, int(active[recent].sum()), "entry_exit_refinement", rank + 1)
        rec["spec"] = json.dumps({
            "core": r.core, **entry_spec, "horizon": r.horizon, "tp": r.tp_bps, "sl": r.sl_bps,
        }, sort_keys=True)
        out.append(rec)
    return out


def audit_cross(root: Path) -> list[dict]:
    stage = root / "stage_05_cross_pair_big_move"
    big.SYMBOLS = cross.SYMBOLS
    a = big.build_arrays(pd.read_parquet(stage / "bars_cache.parquet"))
    recent = a["dates"] >= np.datetime64("2025-01-01")
    dates = a["dates"][recent]
    rows = pd.read_csv(stage / "training_frozen_top50.csv").head(50)
    cache = {}
    out = []
    for rank, r in rows.iterrows():
        key = (int(r.checkpoint_min), str(r.selector))
        if key not in cache:
            st = cross.state(a, *key)
            entry, hi, lo, cl = cross.selected_paths(a, st["selected"], key[0])
            cache[key] = (st, big.path_returns_gpu(entry, hi, lo, cl, big.exit_specs()))
        st, returns = cache[key]
        active = (
            np.isfinite(st["score"]) & (st["score"] != 0)
            & (np.abs(st["score"]) * 10000 >= r.score_bps)
            & (st["range"] * 10000 >= r.range_bps)
            & (st["rvol"] >= r.rvol)
        )
        gross = returns[:, find_exit_id(big.exit_specs(), r)]
        active &= np.isfinite(gross)
        daily = np.zeros(recent.sum())
        daily[active[recent]] = gross[recent][active[recent]] - 2 * r.cost_bps_side / 10000
        rec = recent_metrics(dates, daily, int(active[recent].sum()), "cross_pair_big_move", rank + 1)
        rec["spec"] = json.dumps({
            "checkpoint": key[0], "selector": key[1], "gate": r.gate,
            "score_bps": r.score_bps, "range_bps": r.range_bps, "rvol": r.rvol,
            "horizon": r.horizon, "tp": r.tp_bps, "sl": r.sl_bps,
        }, sort_keys=True)
        out.append(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    args = ap.parse_args()
    root = Path(args.root)
    rows = []
    rows += audit_opening(root)
    rows += audit_earnings(root, args.catalog)
    rows += audit_big(root)
    rows += audit_entry(root)
    rows += audit_cross(root)
    result = pd.DataFrame(rows)
    result["recent_screen"] = (
        result.total_return.ge(.15)
        & result.max_drawdown.ge(-.15)
        & result.worst_month.ge(-.08)
        & result.trades.ge(20)
        & result.positive_month_fraction.ge(.60)
    )
    result.to_csv(root / "RECENT_2025_FROZEN_AUDIT.csv", index=False)
    summary = (
        result.sort_values(["recent_screen", "total_return"], ascending=[False, False])
        .groupby("family", as_index=False).head(3)
    )
    summary.to_csv(root / "RECENT_2025_FAMILY_LEADERS.csv", index=False)
    print(summary[[
        "family", "training_rank", "trades", "total_return", "max_drawdown",
        "worst_month", "positive_month_fraction", "recent_screen", "spec",
    ]].to_string(index=False))
    print(json.dumps({"rows": len(result), "recent_screen_passes": int(result.recent_screen.sum())}, indent=2))


if __name__ == "__main__":
    main()
