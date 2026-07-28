from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def write_reports(results: pd.DataFrame, quantiles: dict[tuple[str, str], pd.DataFrame], root: Path, frame: pd.DataFrame | None = None) -> None:
    """Write diagnostics only; never strategy-level performance metrics."""
    candidates = results.head(50) if not results.empty else results
    candidates.to_html(root / "ranked_candidates.html", index=False, float_format=lambda x: f"{x:.6g}")
    charts = root / "charts"; charts.mkdir(exist_ok=True)
    selected={(r.feature,r.target) for r in candidates.itertuples()} if not candidates.empty else set()
    for (feature, target), table in quantiles.items():
        if (feature,target) not in selected:
            continue
        if table.empty:
            continue
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.errorbar(table.bin, table["mean"], yerr=1.96 * table.se, fmt="o-")
        ax.axhline(0, color="black", linewidth=.8)
        ax.set(title=f"{feature} -> {target}", xlabel="Feature quantile", ylabel="Mean forward return")
        fig.tight_layout(); fig.savefig(charts / f"{feature}__{target}.png", dpi=140); plt.close(fig)
    text = ["# Phase 1 anomaly scan", "", "Statistical relationships only; no result is a trading strategy.", "", f"Completed pairs: {len(results)}"]
    if not results.empty:
        columns=[c for c in ["feature","target","pearson","top_bottom_spread","raw_p","bh_fdr_p","year_consistency","symbol_breadth","status"] if c in candidates]
        markdown=["| "+" | ".join(columns)+" |","| "+" | ".join(["---"]*len(columns))+" |"]
        markdown += ["| "+" | ".join(str(getattr(row,c)) for c in columns)+" |" for row in candidates[columns].itertuples(index=False)]
        text += ["", "## Top relationships", "", "\n".join(markdown)]
    (root / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    if frame is not None and not candidates.empty:
        stable=root/"stability"; stable.mkdir(exist_ok=True)
        for row in candidates.head(25).itertuples():
            cols=["session_date","symbol","decision_ts",row.feature,row.target]
            z=frame[cols].dropna().copy(); z["year"]=pd.to_datetime(z.session_date).dt.year
            local=pd.to_datetime(z.decision_ts,utc=True).dt.tz_convert("America/New_York"); z["time_bucket"]=(local.dt.hour*60+local.dt.minute).floordiv(60)
            for key in ["year","symbol","time_bucket"]:
                tab=z.groupby(key).agg(observations=(row.target,"size"),mean_target=(row.target,"mean"),spearman=(row.feature,lambda s:s.corr(z.loc[s.index,row.target],method="spearman"))).reset_index()
                tab.to_csv(stable/f"{row.feature}__{row.target}__by_{key}.csv",index=False)
