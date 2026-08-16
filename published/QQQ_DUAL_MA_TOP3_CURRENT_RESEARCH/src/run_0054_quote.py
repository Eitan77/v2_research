from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0033_exit_overlays import base_context, summary
from run_0054_profit_trims import OUT, build_trim_weights, specs
from suite_core import evaluate_weights

NY = ZoneInfo("America/New_York")
IDS = (
    "control",
    "weekly_t15_f25",
    "weekly_t15_f50",
    "weekly_t15_f75",
    "weekly_t5_f50",
)


def weights():
    p, _, _, sig, base, _, _ = base_context()
    all_specs = specs()
    return p, {name: build_trim_weights(p, sig, base, all_specs[name])[0] for name in IDS}


def ledgers():
    p, variants = weights()
    records = {"0930": [], "0940": []}
    for name, w in variants.items():
        executed = np.zeros_like(w)
        executed[1:] = w[:-1]
        executed = np.where(np.isfinite(p.adj_open), executed, 0.0)
        previous = np.zeros(p.n_symbols)
        for i, day in enumerate(p.dates):
            delta = executed[i] - previous
            for c in np.flatnonzero(np.abs(delta) > 1e-12):
                side = "buy" if delta[c] > 0 else "sell"
                for label, clock in (("0930", (9, 30)), ("0940", (9, 40))):
                    records[label].append({
                        "variant": name,
                        "session_date": pd.Timestamp(day).normalize(),
                        "symbol": str(p.symbols[c]),
                        "side": side,
                        "delta_weight": float(abs(delta[c])),
                        "target_ts": pd.Timestamp(
                            datetime.combine(pd.Timestamp(day).date(), time(*clock), tzinfo=NY)
                        ).tz_convert("UTC"),
                        "role": "entry_ask_after" if side == "buy" else "exit_bid_after",
                    })
            previous = executed[i].copy()
    for label, rows in records.items():
        frame = pd.DataFrame(rows).sort_values(["target_ts", "variant", "symbol"])
        frame.to_parquet(OUT / f"quote_ledger_{label}.parquet", index=False)
        frame[["symbol", "target_ts", "role"]].drop_duplicates().to_parquet(
            OUT / f"quote_roles_{label}.parquet", index=False
        )
    print({label: len(rows) for label, rows in records.items()})


def cache(label: str) -> pd.DataFrame:
    frames = []
    for run in ("RUN-0030", "RUN-0031", "RUN-0032", "RUN-0033", "RUN-0034"):
        base = ROOT / "campaigns" / "CAM-0611" / "artifacts" / run
        for seconds in (5, 30, 1200):
            for path in (
                base / f"cached_quotes_{label}.parquet",
                base / f"quotes_{label}_{seconds}s.parquet",
                base / f"quote_quotes_{label}_{seconds}s.parquet",
            ):
                if path.exists():
                    frame = pd.read_parquet(path)
                    needed = {"symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"}
                    if needed.issubset(frame.columns):
                        frames.append(frame)
    for seconds in (5, 30, 1200):
        path = OUT / f"quotes_{label}_{seconds}s.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"])
    q = pd.concat(frames, ignore_index=True)
    q["target_ts"] = pd.to_datetime(q.target_ts, utc=True)
    q["quote_ts"] = pd.to_datetime(q.quote_ts, utc=True)
    return q.sort_values("quote_ts").drop_duplicates(["symbol", "target_ts", "role"])


def missing():
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"quote_roles_{label}.parquet")
        roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
        q = cache(label)
        merged = roles.merge(
            q[["symbol", "target_ts", "role"]],
            on=["symbol", "target_ts", "role"], how="left", indicator=True,
        )
        absent = merged.loc[merged._merge.eq("left_only"), ["symbol", "target_ts", "role"]]
        absent.to_parquet(OUT / f"quote_missing_{label}.parquet", index=False)
        print(label, "roles", len(roles), "missing", len(absent))


def replay():
    p, variants = weights()
    keys = ["symbol", "target_ts", "role"]
    merged = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"quote_ledger_{label}.parquet")
        ledger["target_ts"] = pd.to_datetime(ledger.target_ts, utc=True)
        q = cache(label)
        merged[label] = ledger.merge(
            q[keys + ["quote_ts", "bid_price", "ask_price"]],
            on=keys, how="left", validate="many_to_one",
        )
    reference = merged["0930"].copy()
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2.0
    reference = reference[["variant", "session_date", "symbol", "side", "reference_mid"]]
    fills = merged["0940"].merge(
        reference, on=["variant", "session_date", "symbol", "side"], how="left", validate="one_to_one"
    )
    for symbol, original_date, terminal_date in (
        ("XLNX", "2022-02-14", "2022-02-11"),
        ("ALXN", "2021-07-21", "2021-07-20"),
    ):
        mask = fills.symbol.eq(symbol) & fills.session_date.eq(pd.Timestamp(original_date)) & fills.side.eq("sell")
        if not mask.any():
            continue
        if symbol == "XLNX":
            base = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042"
            a = pd.read_parquet(base / "xlnx_reference_quote.parquet").iloc[0]
            b = pd.read_parquet(base / "xlnx_terminal_quote.parquet").iloc[0]
        else:
            base = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0044"
            a = pd.read_parquet(base / "terminal_reference_quotes.parquet").query("symbol == @symbol").iloc[0]
            b = pd.read_parquet(base / "terminal_exception_quotes.parquet").query("symbol == @symbol").iloc[0]
        fills.loc[mask, "reference_mid"] = (float(a.bid_price) + float(a.ask_price)) / 2.0
        fills.loc[mask, "bid_price"] = float(b.bid_price)
        fills.loc[mask, "ask_price"] = float(b.ask_price)
        fills.loc[mask, "session_date"] = pd.Timestamp(terminal_date)

    valid = (
        fills.bid_price.notna() & fills.ask_price.notna() & fills.reference_mid.notna()
        & (fills.bid_price > 0) & (fills.ask_price >= fills.bid_price) & (fills.reference_mid > 0)
    )
    if not valid.all():
        raise RuntimeError(f"missing or invalid quote roles: {int((~valid).sum())}")
    fills.to_parquet(OUT / "quote_fill_ledger.parquet", index=False)

    rows = []
    for name, w in variants.items():
        group = fills[fills.variant.eq(name)]
        _, daily, *_ = evaluate_weights(p, w, 0.0, holding="open_to_next_open", execution_lag=1)
        for extra in (0.0, 1.0, 2.0, 5.0, 10.0):
            quote_drag = np.where(
                group.side.eq("buy"),
                group.delta_weight * (group.ask_price / group.reference_mid - 1.0),
                group.delta_weight * (1.0 - group.bid_price / group.reference_mid),
            )
            cost = quote_drag + group.delta_weight.to_numpy() * extra / 10000.0
            cost_daily = pd.Series(cost, index=pd.to_datetime(group.session_date)).groupby(level=0).sum()
            net = daily.gross_pnl.subtract(cost_daily, fill_value=0.0)
            rows.append({
                "variant": name,
                "extra_bps": extra,
                **summary(net),
                "turnover": float(group.delta_weight.sum()),
                "trade_roles": int(len(group)),
                "trade_sessions": int(pd.to_datetime(group.session_date).nunique()),
                "role_coverage": 1.0,
            })
            pd.DataFrame({"date": net.index, "net_pnl": net.values}).to_parquet(
                OUT / f"quote_daily_{name}_{extra:g}bps.parquet", index=False
            )
    report = {
        "status": "completed",
        "metrics": rows,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    (OUT / "quote_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(pd.DataFrame(rows).query("extra_bps == 2").sort_values("net_simple_return", ascending=False).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "replay"))
    args = parser.parse_args()
    globals()[args.phase]()
