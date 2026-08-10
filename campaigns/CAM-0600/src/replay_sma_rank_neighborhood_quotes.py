from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

import replay_six_sma_full_history as base
import run_sma_rank_neighborhood as sweep
from suite_core import CAMPAIGNS

base.OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0044"
base.RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0044.yaml"
NY = ZoneInfo("America/New_York")


def candidates():
    configs = pd.read_csv(base.OUT / "quote_candidate_configs.csv")
    definitions = {family: (panel, condition, signals) for family, panel, condition, signals in sweep.definitions()}
    liquid_cache = {}
    out = {}
    for row in configs.itertuples(index=False):
        panel, condition, signals = definitions[row.family]
        key = panel.name
        if key not in liquid_cache:
            from deep_strategies import liquid_mask
            liquid_cache[key] = liquid_mask(panel, 0.50)
        if row.role == "walkforward":
            from run_sma_rank_walkforward import build_walkforward_weights
            decisions = pd.read_csv(base.OUT / "walkforward_selections.csv")
            decisions = decisions.loc[decisions.family.eq(row.family) & decisions.top_k.eq(int(row.top_k))]
            weights = build_walkforward_weights(panel, condition, signals, int(row.top_k), decisions, liquid_cache[key])
            name = f"{row.family}_top{int(row.top_k)}_walkforward"
        else:
            weights, _ = sweep.build_weights(panel, condition, signals, int(row.formation), int(row.skip), int(row.top_k), liquid_cache[key])
            name = f"{row.family}_top{int(row.top_k)}_f{int(row.formation)}_s{int(row.skip)}"
        out[name] = (panel, weights)
    if len(out) != len(configs):
        raise RuntimeError("quote candidate names are not unique")
    return out


base.candidates = candidates


def make_terminal_roles() -> None:
    missing = pd.concat([
        pd.read_parquet(base.OUT / "missing_0930.parquet"),
        pd.read_parquet(base.OUT / "missing_0940.parquet"),
    ], ignore_index=True).drop_duplicates(["symbol", "target_ts", "role"])
    ledgers = pd.concat([
        pd.read_parquet(base.OUT / "ledger_0930.parquet"),
        pd.read_parquet(base.OUT / "ledger_0940.parquet"),
    ], ignore_index=True)
    unresolved = ledgers.merge(missing, on=["symbol", "target_ts", "role"])
    if unresolved.empty:
        pd.DataFrame(columns=["symbol", "target_ts", "role"]).to_parquet(base.OUT / "terminal_exception_roles.parquet", index=False)
        print("no terminal exceptions")
        return
    if not unresolved.side.eq("sell").all():
        bad = unresolved.loc[~unresolved.side.eq("sell"), ["symbol", "target_ts", "side"]]
        raise RuntimeError(f"unresolved non-sell roles remain:\n{bad.to_string(index=False)}")
    selected = candidates()
    panels = {panel.name: panel for panel, _ in selected.values()}
    frozen_final_sessions = {
        "ALXN": pd.Timestamp("2021-07-20"),
        "XLNX": pd.Timestamp("2022-02-11"),
        "TWTR": pd.Timestamp("2022-10-27"),
        "ATVI": pd.Timestamp("2023-10-12"),
    }
    rows = []
    for symbol in sorted(unresolved.symbol.astype(str).unique()):
        candidate_names = unresolved.loc[unresolved.symbol.astype(str).eq(symbol), "candidate"].unique()
        relevant_panels = {selected[name][0].name for name in candidate_names}
        if len(relevant_panels) != 1:
            raise RuntimeError(f"ambiguous panel for terminal symbol {symbol}")
        panel = panels[next(iter(relevant_panels))]
        col = panel.symbol_to_col[symbol]
        valid = np.isfinite(panel.raw_open[:, col]) & np.isfinite(panel.raw_close[:, col])
        dates = panel.dates[valid]
        if not len(dates):
            raise RuntimeError(f"no valid trading sessions for {symbol}")
        observed_panel_final = pd.Timestamp(dates.max())
        final_session = frozen_final_sessions.get(symbol, observed_panel_final)
        scheduled = pd.to_datetime(unresolved.loc[unresolved.symbol.astype(str).eq(symbol), "target_ts"], utc=True)
        if not (scheduled.dt.tz_convert(NY).dt.tz_localize(None).dt.normalize() > final_session.normalize()).all():
            raise RuntimeError(f"missing ordinary-session quote is not a terminal exit for {symbol}")
        target = pd.Timestamp(datetime.combine(final_session.date(), time(16, 0), tzinfo=NY)).tz_convert("UTC")
        rows.append({"symbol": symbol, "target_ts": target, "role": "exit_bid_before"})
    roles = pd.DataFrame(rows)
    roles.to_parquet(base.OUT / "terminal_exception_roles.parquet", index=False)
    print(roles.to_string(index=False))


def make_reference_roles() -> None:
    quotes = pd.read_parquet(base.OUT / "terminal_exception_quotes.parquet")
    rows = []
    for row in quotes.itertuples(index=False):
        local_day = pd.Timestamp(row.quote_ts).tz_convert(NY).date()
        target = pd.Timestamp(datetime.combine(local_day, time(9, 30), tzinfo=NY)).tz_convert("UTC")
        rows.append({"symbol": str(row.symbol), "target_ts": target, "role": "entry_ask_after"})
    refs = pd.DataFrame(rows, columns=["symbol", "target_ts", "role"]).drop_duplicates(["symbol", "target_ts", "role"])
    refs.to_parquet(base.OUT / "terminal_reference_roles.parquet", index=False)
    print(refs.to_string(index=False) if len(refs) else "no terminal references")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "terminal_roles", "reference_roles", "replay"))
    args = parser.parse_args()
    {
        "ledgers": base.make_ledgers,
        "missing": base.write_missing,
        "terminal_roles": make_terminal_roles,
        "reference_roles": make_reference_roles,
        "replay": base.replay,
    }[args.phase]()
