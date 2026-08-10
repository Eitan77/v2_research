from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0625" / "artifacts" / "RUN-0016"
SPECS = {"CAM-0600": "RUN-0008", "CAM-0604": "RUN-0008", "CAM-0621": "RUN-0010", "CAM-0624": "RUN-0008"}


def drawdown(s: pd.Series) -> float:
    equity = 1.0 + s.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max()) if len(s) else 0.0


def load_variants() -> dict[str, dict[str, pd.Series]]:
    out: dict[str, dict[str, pd.Series]] = {}
    for campaign_id, run_id in SPECS.items():
        out[campaign_id] = {}
        for path in (CAM / campaign_id / "artifacts" / run_id / "variants").glob("*/daily.parquet"):
            frame = pd.read_parquet(path)
            frame["date"] = pd.to_datetime(frame["date"])
            out[campaign_id][path.parent.name] = frame.set_index("date").net_pnl.sort_index()
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    variants = load_variants()
    start, end = pd.Timestamp("2023-01-03"), pd.Timestamp("2026-04-30")
    all_dates = sorted(set().union(*(set(s.index) for family in variants.values() for s in family.values())))
    dates = pd.DatetimeIndex([d for d in all_dates if start <= d <= end])
    portfolio = pd.Series(0.0, index=dates, name="net_pnl")
    selections: list[dict] = []

    for year in sorted(set(dates.year)):
        period_dates = dates[dates.year == year]
        decision = period_dates.min()
        train_start = decision - pd.DateOffset(months=24)
        chosen: list[tuple[str, str, pd.Series, dict]] = []
        for campaign_id, family in variants.items():
            eligible = []
            for variant_id, series in family.items():
                train = series.loc[(series.index < decision) & (series.index >= train_start)]
                monthly = train.groupby(train.index.to_period("M")).sum()
                active = int((train.abs() > 1e-12).sum())
                net = float(train.sum())
                dd = drawdown(train)
                green = float((monthly > 0).mean()) if len(monthly) else 0.0
                ok = len(train) >= 252 and active >= 126 and net > 0.0 and dd <= 0.20 and green >= 0.50
                score = net / max(dd, 0.05)
                record = {"year": year, "decision_date": str(decision.date()), "training_start": str(train_start.date()), "campaign_id": campaign_id, "variant_id": variant_id, "training_net": net, "training_drawdown": dd, "training_positive_month_fraction": green, "training_rows": len(train), "training_active_days": active, "eligible": ok, "score": score}
                selections.append(record)
                if ok:
                    eligible.append((score, variant_id, series, record))
            if eligible:
                _, variant_id, series, record = max(eligible, key=lambda x: x[0])
                chosen.append((campaign_id, variant_id, series, record))
        if chosen:
            weight = 1.0 / len(chosen)
            for _, _, series, _ in chosen:
                portfolio.loc[period_dates] += weight * series.reindex(period_dates).fillna(0.0)

    selected = pd.DataFrame(selections)
    selected["chosen"] = False
    for year in sorted(set(dates.year)):
        for campaign_id in SPECS:
            mask = (selected.year == year) & (selected.campaign_id == campaign_id) & selected.eligible
            if mask.any():
                idx = selected.loc[mask, "score"].idxmax()
                selected.loc[idx, "chosen"] = True
    monthly = portfolio.groupby(portfolio.index.to_period("M")).sum()
    annual = portfolio.groupby(portfolio.index.year).sum()
    report = {
        "status": "completed",
        "run_id": "RUN-0016",
        "evaluation_start": str(portfolio.index.min().date()),
        "evaluation_end": str(portfolio.index.max().date()),
        "net_return": float(portfolio.sum()),
        "maximum_drawdown": drawdown(portfolio),
        "positive_months": int((monthly > 0).sum()),
        "negative_months": int((monthly < 0).sum()),
        "annual_returns": {str(k): float(v) for k, v in annual.items()},
        "chosen_families_by_year": {str(year): selected.loc[(selected.year == year) & selected.chosen, "campaign_id"].tolist() for year in sorted(set(dates.year))},
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "interpretation": "Retrospective adapted walk-forward evidence, not genuine OOS.",
    }
    pd.DataFrame({"date": portfolio.index, "net_pnl": portfolio.values}).to_parquet(OUT / "daily.parquet", index=False)
    selected.to_csv(OUT / "selection_audit.csv", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    run_path = CAM / "CAM-0625" / "runs" / "RUN-0016.yaml"
    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = report
    run["decision"] = "Compare with fixed ensemble and plain component controls; no promotion from this adapted development test."
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0625" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": pd.Timestamp.now(tz="America/Los_Angeles").isoformat(), "run_id": "RUN-0016", "event": "completed", "net_return": report["net_return"], "maximum_drawdown": report["maximum_drawdown"], "holdout_rows_loaded": 0}) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
