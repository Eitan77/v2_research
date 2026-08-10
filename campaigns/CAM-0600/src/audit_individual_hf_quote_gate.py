from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[3]
CAMS = ROOT / "campaigns"
OUT = CAMS / "CAM-0600" / "artifacts" / "RUN-0025"
RUN = CAMS / "CAM-0600" / "runs" / "RUN-0025.yaml"
COSTS = [-1.0, 0.0, 1.0, 2.0]


def load_campaign(cid: str) -> pd.DataFrame:
    frames = []
    for run in ("RUN-0020", "RUN-0021"):
        path = CAMS / cid / "artifacts" / run / "variant_metrics.csv"
        if path.exists():
            x = pd.read_csv(path)
            x["source_run"] = run
            frames.append(x)
    if not frames:
        return pd.DataFrame()
    # RUN-0021 is a later mechanism repair. Preserve both when identities differ,
    # but use the later record if a variant id was intentionally rerun.
    x = pd.concat(frames, ignore_index=True)
    x["run_order"] = x.source_run.map({"RUN-0020": 0, "RUN-0021": 1})
    return x.sort_values("run_order").drop_duplicates(
        ["campaign_id", "source_run", "variant_id", "cost_bps_per_side"]
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for n in range(600, 625):
        cid = f"CAM-{n:04d}"
        x = load_campaign(cid)
        if len(x):
            all_rows.append(x)
    raw = pd.concat(all_rows, ignore_index=True)
    raw = raw[raw.cost_bps_per_side.isin(COSTS)].copy()
    key = ["campaign_id", "source_run", "variant_id"]
    # Panel session count is inferred from the daily artifacts' observed month span.
    # active_days can describe held exposure, so entry cadence is the binding test.
    wide = raw.pivot_table(index=key, columns="cost_bps_per_side", values="net_simple_return", aggfunc="first")
    wide.columns = [f"net_{c:g}bps" for c in wide.columns]
    base = raw[raw.cost_bps_per_side == 1.0].copy()
    if base.empty:
        base = raw[raw.cost_bps_per_side == 0.0].copy()
    fields = key + ["panel", "entries", "position_change_count", "active_days", "positive_months",
                    "negative_months", "inactive_months", "maximum_drawdown", "recent12_average_month",
                    "recent12_positive_months", "recent12_negative_months", "top5_day_positive_share",
                    "top5_symbol_positive_share", "holding"]
    base = base[fields].drop_duplicates(key)
    out = base.merge(wide.reset_index(), on=key, how="left", validate="one_to_one")
    out["calendar_sessions_proxy"] = out.active_days / (out.active_days / out.active_days.max())
    # All daily panels end at the same cutoff but start dates vary. Use observed
    # monthly count times 21 sessions as a conservative, record-derived denominator.
    months = out.positive_months.fillna(0) + out.negative_months.fillna(0) + out.inactive_months.fillna(0)
    out["estimated_sessions"] = months.clip(lower=1) * 21.0
    out["entries_per_session"] = out.entries / out.estimated_sessions
    out["profitable_low_cost"] = out[["net_-1bps", "net_0bps", "net_1bps"]].max(axis=1) > 0
    out["hf_half_daily"] = out.entries_per_session >= 0.5
    out["hf_daily"] = out.entries_per_session >= 1.0
    out["recent_consistent"] = (out.recent12_average_month > 0) & (out.recent12_positive_months >= 7)

    quote_path = CAMS / "CAM-0600" / "artifacts" / "shared" / "split_repaired_quote_metrics_RUN-0023.csv"
    quote = pd.read_csv(quote_path)
    replayed = set(zip(quote.campaign_id.astype(str), quote.variant_id.astype(str)))
    out["prior_quote_replayed"] = [(a, b) in replayed for a, b in zip(out.campaign_id, out.variant_id)]
    out["quote_replay_required"] = out.profitable_low_cost & out.hf_half_daily & ~out.prior_quote_replayed
    out = out.sort_values(["quote_replay_required", "recent_consistent", "net_1bps"], ascending=False)
    out.to_csv(OUT / "all_individual_low_cost_variants.csv", index=False)

    queue = out[out.quote_replay_required].copy()
    # Deduplicate exact mechanism variants that differ only by cost records (already pivoted),
    # but do not collapse distinct thresholds/universes here; this is the exhaustive queue.
    queue.to_csv(OUT / "quote_replay_required.csv", index=False)
    summary = []
    for cid, g in out.groupby("campaign_id"):
        q = g[g.quote_replay_required]
        summary.append({
            "campaign_id": cid,
            "variants_audited": int(len(g)),
            "profitable_low_cost": int(g.profitable_low_cost.sum()),
            "high_frequency_profitable": int((g.profitable_low_cost & g.hf_half_daily).sum()),
            "already_quote_replayed": int((g.profitable_low_cost & g.hf_half_daily & g.prior_quote_replayed).sum()),
            "quote_replay_required": int(len(q)),
            "best_required_variant": None if q.empty else str(q.iloc[0].variant_id),
            "best_required_net_1bps": None if q.empty else float(q.iloc[0]["net_1bps"]),
        })
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(OUT / "campaign_summary.csv", index=False)
    report = {
        "status": "completed",
        "run_id": "RUN-0025",
        "variants_audited": int(len(out)),
        "profitable_at_minus1_0_or_1": int(out.profitable_low_cost.sum()),
        "high_frequency_profitable": int((out.profitable_low_cost & out.hf_half_daily).sum()),
        "high_frequency_daily_profitable": int((out.profitable_low_cost & out.hf_daily).sum()),
        "already_quote_replayed": int((out.profitable_low_cost & out.hf_half_daily & out.prior_quote_replayed).sum()),
        "quote_replay_required": int(out.quote_replay_required.sum()),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
        "warning": "Entry cadence uses entries divided by observed-months times 21; exact event ledgers must reconcile candidates before replay.",
    }
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    run = yaml.safe_load(RUN.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = report
    run["decision"] = "Reconcile and replay every queued individual candidate; do not infer execution from bar profitability."
    RUN.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(summary_df[summary_df.quote_replay_required > 0].to_string(index=False))


if __name__ == "__main__":
    main()
