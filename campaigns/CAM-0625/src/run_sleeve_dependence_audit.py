from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0625" / "artifacts" / "RUN-0029"
IDS = ["CAM-0600", "CAM-0621", "CAM-0624", "CAM-0618"]
REPAIR = {"CAM-0621"}


def drawdown(series: pd.Series) -> float:
    equity = 1.0 + series.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def metrics(series: pd.Series) -> dict:
    monthly = series.groupby(series.index.to_period("M")).sum()
    return {
        "net_simple_return": float(series.sum()),
        "maximum_drawdown": drawdown(series),
        "positive_months": int((monthly > 0).sum()),
        "negative_months": int((monthly < 0).sum()),
        "worst_month": float(monthly.min()),
        "best_month": float(monthly.max()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shared = CAM / "CAM-0600" / "artifacts" / "shared"
    base = pd.read_csv(shared / "split_repaired_diagnostic_summary.csv").set_index("campaign_id")
    repair = pd.read_csv(shared / "split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id")
    replay = pd.read_parquet(shared / "split_repaired_quote_replay_0940.parquet")
    full = {}
    quote = {}
    variants = {}
    for campaign_id in IDS:
        row = (repair if campaign_id in REPAIR else base).loc[campaign_id]
        variant = str(row.selected_variant)
        variants[campaign_id] = variant
        parent = "RUN-0021" if campaign_id in REPAIR else "RUN-0020"
        safe = f"{variant}__cost_2bps".replace("/", "_").replace(":", "_")
        frame = pd.read_parquet(CAM / campaign_id / "artifacts" / parent / "variants" / safe / "daily.parquet")
        frame["date"] = pd.to_datetime(frame.date)
        full[campaign_id] = frame.set_index("date").net_pnl.sort_index()
        q = pd.read_parquet(CAM / campaign_id / "artifacts" / "RUN-0023" / "daily_0940_2bps_extra.parquet")
        q["date"] = pd.to_datetime(q.date)
        quote[campaign_id] = q.set_index("date").net_pnl.sort_index()

    full_frame = pd.concat(full, axis=1, sort=True).fillna(0.0)
    quote_frame = pd.concat(quote, axis=1, sort=True).fillna(0.0)
    full_equal = full_frame.mean(axis=1)
    quote_equal = quote_frame.mean(axis=1)
    expected_quote = 0.39962722208704066
    if abs(float(quote_equal.sum()) - expected_quote) > 1e-8:
        raise RuntimeError(f"quote reconciliation failed: {quote_equal.sum()} != {expected_quote}")

    leave_one_out = {}
    for campaign_id in IDS:
        leave_one_out[campaign_id] = {
            "full": metrics(full_frame.drop(columns=campaign_id).mean(axis=1)),
            "quote": metrics(quote_frame.drop(columns=campaign_id).mean(axis=1)),
        }

    quote_monthly = quote_frame.groupby(quote_frame.index.to_period("M")).sum() / len(IDS)
    positive_contribution = quote_monthly.clip(lower=0)
    month_positive_total = positive_contribution.sum(axis=1).replace(0, np.nan)
    month_top_share = positive_contribution.max(axis=1) / month_positive_total

    q = replay[
        replay.campaign_id.isin(IDS)
        & replay.effective_complete
        & replay.session_date.between("2025-05-01", "2026-04-30")
    ].copy()
    q["absolute_weight_change"] = q.delta_weight.abs() / len(IDS)
    turnover = q.groupby("campaign_id").agg(
        quote_role_rows=("delta_weight", "size"),
        absolute_weight_turnover=("absolute_weight_change", "sum"),
        median_absolute_role_change=("absolute_weight_change", "median"),
    )

    report = {
        "status": "completed",
        "run_id": "RUN-0029",
        "variants": variants,
        "full_ensemble": metrics(full_equal),
        "quote_ensemble": metrics(quote_equal),
        "full_sleeves": {c: metrics(full_frame[c]) for c in IDS},
        "quote_sleeves": {c: metrics(quote_frame[c]) for c in IDS},
        "full_daily_correlations": full_frame.corr().to_dict(),
        "quote_daily_correlations": quote_frame.corr().to_dict(),
        "leave_one_sleeve_out": leave_one_out,
        "quote_month_top_positive_sleeve_share": {
            "median": float(month_top_share.median()),
            "maximum": float(month_top_share.max()),
            "months_over_75pct": int((month_top_share > 0.75).sum()),
            "months_observed": int(month_top_share.notna().sum()),
        },
        "quote_turnover": turnover.reset_index().to_dict("records"),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "interpretation": "Dependence diagnostic for the frozen ensemble; no strategy parameter was changed.",
    }
    quote_monthly.rename_axis("month").reset_index().assign(month=lambda x: x.month.astype(str)).to_csv(
        OUT / "quote_monthly_sleeve_contributions.csv", index=False
    )
    turnover.to_csv(OUT / "quote_turnover_by_sleeve.csv")
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0625" / "runs" / "RUN-0029.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = report
    run["decision"] = "Treat the strongest-sleeve dependence as a qualification; preserve the frozen ensemble without tuning."
    path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0625" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0029", "event": "completed", "result": report}) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
