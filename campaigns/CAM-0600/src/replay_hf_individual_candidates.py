from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, load_panels


OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0026"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0026.yaml"
START, END = pd.Timestamp("2025-05-01"), pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")
REPAIR = {"CAM-0608", "CAM-0609", "CAM-0617", "CAM-0621"}
EXTRA = (0.0, 1.0, 2.0, 5.0)
SOURCE_RUN_BY_CAMPAIGN = {}
EXTRA_QUOTE_DIRS = []


def utc(date, hhmm):
    hh, mm = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(date).date(), time(hh, mm), tzinfo=NY)).tz_convert("UTC")


def specs():
    run = yaml.safe_load(RUN.read_text(encoding="utf-8"))
    return run, run["configuration"]["candidates"]


def variants():
    run, wanted = specs()
    panels = load_panels()
    fundamental, _ = _load_or_build_fundamentals(panels)
    result = {}
    for cid, vid in wanted.items():
        builder = build_repair_variants if cid in REPAIR else build_deep_variants
        matches = [v for v in builder(cid, panels, fundamental) if v.variant_id == vid]
        if len(matches) != 1:
            raise RuntimeError(f"variant reconciliation failed {cid} {vid}: {len(matches)}")
        result[cid] = matches[0]
    return result


def build_ledgers():
    OUT.mkdir(parents=True, exist_ok=True)
    vs = variants()
    for clock in ("09:30", "09:40"):
        rows = []
        for cid, v in vs.items():
            executed = np.zeros_like(v.weights)
            executed[1:] = v.weights[:-1] if v.execution_lag == 1 else v.weights[1:]
            if v.execution_lag == 0:
                executed[:] = v.weights
            if (executed < -1e-12).any():
                raise RuntimeError(f"direct short blocked: {cid}")
            previous = np.zeros(v.panel.n_symbols)
            for i, date in enumerate(v.panel.dates):
                date = pd.Timestamp(date).normalize()
                current = executed[i]
                if date < START:
                    previous = current.copy()
                    continue
                if date > END:
                    break
                delta = current - previous
                for col in np.flatnonzero(np.abs(delta) > 1e-8):
                    side = "buy" if delta[col] > 0 else "sell"
                    rows.append({"campaign_id": cid, "variant_id": v.variant_id,
                                 "session_date": date, "symbol": str(v.panel.symbols[col]),
                                 "side": side, "delta_weight": float(abs(delta[col])),
                                 "target_ts": utc(date, clock),
                                 "role": "entry_ask_after" if side == "buy" else "exit_bid_after"})
                previous = current.copy()
        ledger = pd.DataFrame(rows)
        if ledger.empty or (pd.to_datetime(ledger.target_ts, utc=True) >= pd.Timestamp("2026-05-01", tz="UTC")).any():
            raise RuntimeError("empty or holdout-crossing ledger")
        label = clock.replace(":", "")
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        ledger[["symbol", "target_ts", "role"]].drop_duplicates().to_parquet(OUT / f"roles_{label}.parquet", index=False)
        print(label, len(ledger), ledger[["symbol", "target_ts", "role"]].drop_duplicates().shape[0])


def max_dd(x):
    eq = 1 + x.cumsum()
    return float(((eq.cummax() - eq) / eq.cummax()).max()) if len(x) else 0.0


def replay():
    vs = variants()
    replays = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet")
        quote_frames = []
        for directory in [OUT, *EXTRA_QUOTE_DIRS]:
            for seconds in (5, 30, 120, 300, 1200):
                p = directory / f"quotes_{label}_{seconds}s.parquet"
                if p.exists():
                    q = pd.read_parquet(p); q["priority"] = seconds; quote_frames.append(q)
        q = pd.concat(quote_frames, ignore_index=True).sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])
        ledger["target_ts"] = pd.to_datetime(ledger.target_ts, utc=True)
        q["target_ts"] = pd.to_datetime(q.target_ts, utc=True)
        z = ledger.merge(q[["symbol", "target_ts", "role", "bid_price", "ask_price", "quote_ts"]],
                         on=["symbol", "target_ts", "role"], how="left", validate="many_to_one")
        z["complete"] = z.bid_price.notna() & z.ask_price.notna() & (z.bid_price > 0) & (z.ask_price >= z.bid_price)
        replays[label] = z
    ref = replays["0930"].copy()
    ref["reference_mid"] = (ref.bid_price + ref.ask_price) / 2
    ref = ref[["campaign_id", "session_date", "symbol", "side", "reference_mid"]].drop_duplicates()
    replays["0940"] = replays["0940"].merge(ref, on=["campaign_id", "session_date", "symbol", "side"], how="left", validate="many_to_one")
    replays["0930"]["reference_mid"] = (replays["0930"].bid_price + replays["0930"].ask_price) / 2
    rows = []
    for label, replay_df in replays.items():
        replay_df["effective"] = replay_df.complete & replay_df.reference_mid.notna() & (replay_df.reference_mid > 0)
        replay_df.to_parquet(OUT / f"replay_{label}.parquet", index=False)
        for cid, g in replay_df.groupby("campaign_id"):
            v = vs[cid]
            source_run = SOURCE_RUN_BY_CAMPAIGN.get(cid, "RUN-0021" if cid in REPAIR else "RUN-0020")
            safe = f"{v.variant_id}__cost_2bps".replace("/", "_").replace(":", "_")
            daily_path = CAMPAIGNS / cid / "artifacts" / source_run / "variants" / safe / "daily.parquet"
            bar = pd.read_parquet(daily_path)
            bar.date = pd.to_datetime(bar.date)
            bar = bar[(bar.date >= START) & (bar.date <= END)].set_index("date")
            complete = g[g.effective].copy()
            for extra in EXTRA:
                buy = complete.side.eq("buy")
                adj = np.where(buy, complete.delta_weight * (complete.ask_price / complete.reference_mid - 1),
                               complete.delta_weight * (1 - complete.bid_price / complete.reference_mid))
                complete["adjustment"] = adj + complete.delta_weight * extra / 10000
                daily = bar.gross_pnl.subtract(complete.groupby(pd.to_datetime(complete.session_date)).adjustment.sum(), fill_value=0).sort_index()
                monthly = daily.groupby(daily.index.to_period("M")).sum()
                entry_roles = int(complete.side.eq("buy").sum())
                trade_sessions = int(pd.to_datetime(complete.session_date).nunique())
                rows.append({"campaign_id": cid, "variant_id": v.variant_id, "clock": label,
                             "extra_adverse_bps_per_side": extra, "net_simple_return": float(daily.sum()),
                             "maximum_drawdown": max_dd(daily), "role_coverage": float(g.effective.mean()),
                             "trade_roles": int(len(g)), "active_sessions": int((daily.abs() > 1e-12).sum()),
                             "entry_roles": entry_roles, "trade_sessions": trade_sessions,
                             "entries_per_calendar_session": entry_roles / max(len(bar), 1),
                             "trade_session_fraction": trade_sessions / max(len(bar), 1),
                             "green_sessions": int((daily > 1e-12).sum()), "red_sessions": int((daily < -1e-12).sum()),
                             "positive_months": int((monthly > 1e-12).sum()), "negative_months": int((monthly < -1e-12).sum()),
                             "monthly_average": float(monthly.mean()), "monthly_median": float(monthly.median()),
                             "worst_month": float(monthly.min()), "best_month": float(monthly.max())})
                daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(
                    OUT / f"daily_{cid}_{label}_{extra:g}bps.parquet", index=False
                )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "quote_metrics.csv", index=False)
    run_id = yaml.safe_load(RUN.read_text(encoding="utf-8"))["run_id"]
    report = {"status": "completed", "run_id": run_id, "metrics": json.loads(metrics.to_json(orient="records")),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False,
              "direct_short": False, "combination_allowed": False}
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    run = yaml.safe_load(RUN.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report
    run["decision"] = "Judge each individual candidate independently; passive limits require a separate sequential fill audit."
    RUN.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    print(metrics[(metrics.clock == "0940") & (metrics.extra_adverse_bps_per_side.isin([0, 1, 2]))].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("phase", choices=["ledgers", "replay"]); args = ap.parse_args()
    build_ledgers() if args.phase == "ledgers" else replay()
