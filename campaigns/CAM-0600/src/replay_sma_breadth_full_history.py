from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

import replay_six_sma_full_history as base
from baseline_strategies import moving_average
from deep_strategies import active_trend_rank
from suite_core import CAMPAIGNS, load_panels, month_end_indices, weekly_indices

base.OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0043"
base.RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0043.yaml"


def candidates():
    panels = load_panels()
    qqq, sp = panels["qqq"], panels["sp500"]
    definitions = [
        ("qqq_single_ma150_weekly", qqq, qqq.adj_close > moving_average(qqq, 150), weekly_indices(qqq.dates)),
        ("qqq_dual_ma50_200_weekly", qqq, moving_average(qqq, 50) > moving_average(qqq, 200), weekly_indices(qqq.dates)),
        ("qqq_triple_ma10_50_200_monthly", qqq, (moving_average(qqq, 10) > moving_average(qqq, 50)) & (moving_average(qqq, 50) > moving_average(qqq, 200)), month_end_indices(qqq.dates)),
        ("sp500_dual_ma50_200_weekly", sp, moving_average(sp, 50) > moving_average(sp, 200), weekly_indices(sp.dates)),
        ("sp500_triple_ma10_50_200_monthly", sp, (moving_average(sp, 10) > moving_average(sp, 50)) & (moving_average(sp, 50) > moving_average(sp, 200)), month_end_indices(sp.dates)),
    ]
    out = {}
    for family, panel, condition, signals in definitions:
        for top_k in (1, 2, 3, 10):
            out[f"{family}_top{top_k}"] = (panel, active_trend_rank(panel, condition, signals, top_k, "momentum"))
    return out


base.candidates = candidates

NY = ZoneInfo("America/New_York")


def make_terminal_roles():
    missing = pd.read_parquet(base.OUT / "missing_0930.parquet")
    ledger = pd.read_parquet(base.OUT / "ledger_0930.parquet")
    unresolved = ledger.merge(missing, on=["symbol", "target_ts", "role"])
    if not unresolved.side.eq("sell").all():
        raise RuntimeError("terminal exception includes a non-sell role")
    final_sessions = {
        "XLNX": pd.Timestamp("2022-02-11"),
        "TWTR": pd.Timestamp("2022-10-27"),
        "ATVI": pd.Timestamp("2023-10-12"),
    }
    rows = []
    for symbol in sorted(unresolved.symbol.astype(str).unique()):
        if symbol not in final_sessions:
            raise RuntimeError(f"no frozen final-session mapping for {symbol}")
        target = pd.Timestamp(datetime.combine(final_sessions[symbol].date(), time(16, 0), tzinfo=NY)).tz_convert("UTC")
        rows.append({"symbol": symbol, "target_ts": target, "role": "exit_bid_before"})
    roles = pd.DataFrame(rows)
    roles.to_parquet(base.OUT / "terminal_exception_roles.parquet", index=False)
    print(roles.to_string(index=False))


def make_reference_roles():
    quotes = pd.read_parquet(base.OUT / "terminal_exception_quotes.parquet")
    rows = []
    for row in quotes.itertuples(index=False):
        local_day = pd.Timestamp(row.quote_ts).tz_convert(NY).date()
        target = pd.Timestamp(datetime.combine(local_day, time(9, 30), tzinfo=NY)).tz_convert("UTC")
        rows.append({"symbol": str(row.symbol), "target_ts": target, "role": "entry_ask_after"})
    refs = pd.DataFrame(rows).drop_duplicates(["symbol", "target_ts", "role"])
    refs.to_parquet(base.OUT / "terminal_reference_roles.parquet", index=False)
    print(refs.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "exception_roles", "terminal_roles", "reference_roles", "replay"))
    args = parser.parse_args()
    {"ledgers": base.make_ledgers, "missing": base.write_missing, "exception_roles": base.make_exception_roles, "terminal_roles": make_terminal_roles, "reference_roles": make_reference_roles, "replay": base.replay}[args.phase]()
