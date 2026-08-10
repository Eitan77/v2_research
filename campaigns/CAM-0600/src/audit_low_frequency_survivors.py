from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[3]
CAMPAIGNS = ROOT / "campaigns"
OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0041"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0041.yaml"
RECENT_START = pd.Timestamp("2025-05-01")
RECENT_END = pd.Timestamp("2026-04-30")

CANDIDATES = [
    ("CAM-0612", "Triple MA", "sp500__ma10_50_200__monthly__top3__momentum", "monthly"),
    ("CAM-0611", "Dual MA", "sp500__ma50_200__weekly__top3__momentum", "weekly"),
    ("CAM-0610", "Single MA", "qqq__ma150__weekly__top3__momentum", "weekly"),
    ("CAM-0623", "Distress quality", "qqq__chs_safe__top5__liquid__raw", "monthly"),
    ("CAM-0600", "Price momentum", "qqq__mom252_skip21__top3__liquid_trend__panic1", "monthly"),
    ("CAM-0602", "Value quality", "qqq__value_quality__top10__trend1", "monthly"),
    ("CAM-0617", "ETF alpha combo", "etf__alpha_M20_E5__top5__monthly__trend0", "monthly"),
    ("CAM-0608", "Cluster residual", "qqq__slow_residual_r10__top10__monthly", "monthly"),
    ("CAM-0619", "Sector momentum MA", "sector11__mom63_skip0__monthly__top1", "monthly"),
    ("CAM-0605", "Residual momentum", "sp500__resmom__top10__liquid_trend", "monthly"),
]

DETAIL_VARIANT = {}
DETAIL_RUN = {
    ("CAM-0608", "qqq__slow_residual_r10__top10__monthly"): "RUN-0021",
    ("CAM-0617", "etf__alpha_M20_E5__top5__monthly__trend0"): "RUN-0021",
}


def drawdown(pnl: pd.Series) -> float:
    equity = 1.0 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max()) if len(equity) else 0.0


def detail_dir(cam: str, variant: str) -> Path:
    saved = DETAIL_VARIANT.get(variant, variant)
    run = DETAIL_RUN.get((cam, variant), "RUN-0020")
    return CAMPAIGNS / cam / "artifacts" / run / "variants" / f"{saved}__cost_2bps"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, annual_rows, contributor_rows, quote_daily = [], [], [], {}
    for cam, family, variant, cadence in CANDIDATES:
        detail = detail_dir(cam, variant)
        metrics = json.loads((detail / "metrics.json").read_text(encoding="utf-8"))
        source_run = DETAIL_RUN.get((cam, variant), "RUN-0020")
        grid = pd.read_csv(CAMPAIGNS / cam / "artifacts" / source_run / "variant_metrics.csv")
        at10 = grid.loc[grid.cost_bps_per_side.eq(10.0)]
        exact10 = at10.loc[at10.variant_id.eq(variant)]
        if exact10.empty:
            raise RuntimeError(f"missing exact 10 bp row for {cam}/{variant}")
        daily = pd.read_parquet(detail / "daily.parquet")
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.set_index("date").sort_index()
        symbols = pd.read_csv(detail / "symbols.csv").sort_values("net_pnl", ascending=False)
        years = pd.read_csv(detail / "yearly.csv")
        quote = pd.read_csv(CAMPAIGNS / cam / "artifacts" / "RUN-0023" / "quote_metrics_0940.csv")
        q2 = quote.loc[quote.extra_slippage_bps_per_side.eq(2.0)].iloc[0]
        q10 = quote.loc[quote.extra_slippage_bps_per_side.eq(10.0)].iloc[0]
        qdaily = pd.read_parquet(CAMPAIGNS / cam / "artifacts" / "RUN-0023" / "daily_0940_2bps_extra.parquet")
        date_col = "date" if "date" in qdaily else qdaily.columns[0]
        pnl_col = "net_pnl" if "net_pnl" in qdaily else qdaily.columns[-1]
        qdaily[date_col] = pd.to_datetime(qdaily[date_col])
        qseries = qdaily.set_index(date_col)[pnl_col].sort_index()
        quote_daily[family] = qseries
        first6 = qseries[qseries.index < pd.Timestamp("2025-11-01")].sum()
        last6 = qseries[qseries.index >= pd.Timestamp("2025-11-01")].sum()
        pre = daily.loc[daily.index < RECENT_START, "net_pnl"]
        recent_bar = daily.loc[(daily.index >= RECENT_START) & (daily.index <= RECENT_END), "net_pnl"]
        rolling12 = daily.net_pnl.rolling(252, min_periods=252).sum().dropna()
        recent_roll = float(rolling12.iloc[-1]) if len(rolling12) else np.nan
        percentile = float((rolling12 <= recent_roll).mean()) if len(rolling12) else np.nan
        positive_symbols = symbols.net_pnl.clip(lower=0)
        positive_total = float(positive_symbols.sum())
        top5_share = float(positive_symbols.head(5).sum() / positive_total) if positive_total else np.nan
        leave_top5 = float(metrics["net_simple_return"] - symbols.head(5).net_pnl.sum())
        for rank, s in enumerate(symbols.head(10).itertuples(index=False), 1):
            contributor_rows.append({"campaign": cam, "family": family, "rank": rank, "symbol": s.symbol, "full_history_net_pnl": s.net_pnl})
        for y in years.itertuples(index=False):
            annual_rows.append({"campaign": cam, "family": family, "year": int(y.date), "net_pnl": float(y.net_pnl)})
        full_years = years.loc[years.net_pnl.abs() > 1e-12]
        rows.append({
            "campaign": cam,
            "family": family,
            "variant": variant,
            "scheduled_rebalance": cadence,
            "quote_trade_roles": int(q2.trade_roles),
            "quote_2bps_last12": float(q2.net_simple_return),
            "quote_10bps_last12": float(q10.net_simple_return),
            "quote_dd_last12": float(q2.maximum_drawdown),
            "quote_positive_months": int(q2.positive_months),
            "quote_negative_months": int(q2.negative_months),
            "quote_worst_month": float(q2.worst_month),
            "quote_first6": float(first6),
            "quote_last6": float(last6),
            "full_history_start": str(daily.index.min().date()),
            "full_history_net_2bps": float(metrics["net_simple_return"]),
            "full_history_net_10bps": float(exact10.iloc[0].net_simple_return),
            "full_history_dd": float(metrics["maximum_drawdown"]),
            "pre_recent_net_2bps": float(pre.sum()),
            "pre_recent_dd": drawdown(pre),
            "recent_bar_net_2bps": float(recent_bar.sum()),
            "recent_share_of_full_net": float(recent_bar.sum() / metrics["net_simple_return"]) if metrics["net_simple_return"] else np.nan,
            "profitable_full_years": int((full_years.net_pnl > 0).sum()),
            "losing_full_years": int((full_years.net_pnl < 0).sum()),
            "worst_full_year": float(full_years.net_pnl.min()) if len(full_years) else np.nan,
            "positive_rolling12_fraction": float((rolling12 > 0).mean()) if len(rolling12) else np.nan,
            "recent12_historical_percentile": percentile,
            "full_top5_positive_share": top5_share,
            "full_leave_top5_return": leave_top5,
            "campaign_grid_variants": int(len(at10)),
            "campaign_grid_positive_at_10bps_fraction": float((at10.net_simple_return > 0).mean()),
            "campaign_grid_median_net_10bps": float(at10.net_simple_return.median()),
            "best_symbol": str(symbols.iloc[0].symbol),
            "best_symbol_pnl": float(symbols.iloc[0].net_pnl),
            "role_coverage": float(q2.role_coverage),
        })
    frame = pd.DataFrame(rows).sort_values("quote_2bps_last12", ascending=False)
    frame.to_csv(OUT / "audit.csv", index=False)
    pd.DataFrame(annual_rows).to_csv(OUT / "annual_returns.csv", index=False)
    pd.DataFrame(contributor_rows).to_csv(OUT / "top_contributors.csv", index=False)
    quote_frame = pd.DataFrame(quote_daily).sort_index()
    quote_frame.to_parquet(OUT / "quote_daily_2bps.parquet")
    quote_frame.corr().to_csv(OUT / "quote_daily_correlations.csv")
    report = {
        "status": "completed",
        "run_id": "RUN-0041",
        "candidate_count": len(frame),
        "selection_frozen_before_audit": True,
        "metrics": frame.to_dict("records"),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    record = yaml.safe_load(RUN.read_text(encoding="utf-8"))
    record["status"] = "completed"
    record["result"] = {"artifact": "artifacts/RUN-0041/execution_report.json", "candidate_count": len(frame), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0}
    record["decision"] = "Judge each survivor from persistence, concentration, time-path, exact quote, and cost evidence; do not treat the selected top-ten table as out-of-sample evidence."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
