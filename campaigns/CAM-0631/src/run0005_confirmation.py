from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from run0002_toxicity_signal import ENV, HORIZONS, SYMBOLS, WINDOWS, auc_score, build_events, fetch_endpoint


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0005"
CONFIRMATION = ["2024-12-04", "2025-01-08", "2025-03-05", "2025-04-02", "2025-06-04", "2025-07-02", "2025-09-03", "2025-10-01", "2025-12-03", "2026-01-07", "2026-02-04", "2026-03-04"]


def load_package() -> dict:
    path = OUT / "frozen_model.json"
    package = json.loads(path.read_text(encoding="utf-8"))
    digest = package.pop("package_sha256")
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"))
    if sha256(canonical.encode()).hexdigest() != digest:
        raise RuntimeError("frozen model hash mismatch")
    package["package_sha256"] = digest
    if package.get("confirmation_data_accessed") is not False:
        raise RuntimeError("model was not frozen before confirmation")
    return package


def confirmation_clusters() -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    jobs = []
    for symbol in SYMBOLS:
        for session in CONFIRMATION:
            for label, start, end in WINDOWS:
                events = build_events(symbol, session, label, start, end)
                jobs.append({"symbol": symbol, "session": session, "window": label, "candidate_events": len(events)})
                if len(events):
                    frames.append(events)
    data = pd.concat(frames, ignore_index=True)
    data["side_size_imbalance"] = data.side * data.size_imbalance
    data["side_trade_imbalance_1s"] = data.side * data.trade_imbalance_1s
    data["cluster_100ms"] = data.ts.dt.floor("100ms")
    numerical = ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_trade_imbalance_1s"] + [f"markout_{h:g}s_bps" for h in HORIZONS]
    clustered = data.groupby(["symbol", "session", "window", "cluster_100ms", "side"], as_index=False)[numerical].mean()
    return clustered, pd.DataFrame(jobs)


def main() -> None:
    package = load_package()
    load_dotenv(ENV)
    key = os.getenv("ALPACA_API_KEY_ID", "")
    secret = os.getenv("ALPACA_API_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("credentials missing")
    jobs = [(symbol, date, label, start, end, kind) for symbol in SYMBOLS for date in CONFIRMATION for label, start, end in WINDOWS for kind in ("quotes", "trades")]
    with ThreadPoolExecutor(max_workers=12, thread_name_prefix="epdc-confirm") as executor:
        futures = {executor.submit(fetch_endpoint, *job, key, secret): job for job in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            future.result()
            if number % 96 == 0:
                print(f"downloads {number}/{len(jobs)}", flush=True)
    data, job_stats = confirmation_clusters()
    features = package["features"]
    mean = np.asarray(package["mean"])
    std = np.asarray(package["std"])
    beta = np.asarray(package["beta_intercept_then_features"])
    x = np.c_[np.ones(len(data)), (data[features].to_numpy() - mean) / std]
    probability = 1.0 / (1.0 + np.exp(-np.clip(x @ beta, -30, 30)))
    data["predicted_toxicity"] = probability
    data["bucket"] = np.where(probability <= package["training_low_cut"], "low", np.where(probability >= package["training_high_cut"], "high", "middle"))
    data["target_5s"] = (data.markout_5s_bps < 0).astype(int)
    session_rows = []
    for session, group in data.groupby("session"):
        symbol_bucket = group.groupby(["symbol", "bucket"])[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean().reset_index()
        means = symbol_bucket.groupby("bucket")[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean()
        if "low" not in means.index or "high" not in means.index:
            raise RuntimeError(f"confirmation session lacks frozen low/high buckets: {session}")
        low = means.loc["low"]
        high = means.loc["high"]
        per_symbol = symbol_bucket.pivot(index="symbol", columns="bucket", values="markout_5s_bps").dropna()
        session_rows.append({
            "session": session,
            "clusters": len(group),
            "low_clusters": int((group.bucket == "low").sum()),
            "high_clusters": int((group.bucket == "high").sum()),
            "auc": auc_score(group.target_5s.to_numpy(), group.predicted_toxicity.to_numpy()),
            "symbols_with_both_buckets": len(per_symbol),
            "symbol_fraction_low_gt_high_5s": float((per_symbol.low > per_symbol.high).mean()) if len(per_symbol) else np.nan,
            **{f"low_markout_{h:g}s_bps": float(low[f"markout_{h:g}s_bps"]) for h in HORIZONS},
            **{f"high_markout_{h:g}s_bps": float(high[f"markout_{h:g}s_bps"]) for h in HORIZONS},
        })
    session_summary = pd.DataFrame(session_rows).sort_values("session")
    pooled_symbol = data.groupby(["symbol", "bucket"])[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean().reset_index()
    pooled_pivot = pooled_symbol.pivot(index="symbol", columns="bucket", values="markout_5s_bps").dropna()
    pooled_means = pooled_symbol.groupby("bucket")[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean()
    date_positive = int((session_summary.low_markout_5s_bps > 0).sum())
    date_ordered = int((session_summary.low_markout_5s_bps > session_summary.high_markout_5s_bps).sum())
    pooled_breadth = float((pooled_pivot.low > pooled_pivot.high).mean())
    pooled_difference = float(pooled_means.loc["low", "markout_5s_bps"] - pooled_means.loc["high", "markout_5s_bps"])
    gates = {
        "sessions_low_positive_5s": date_positive,
        "sessions_low_gt_high_5s": date_ordered,
        "pooled_symbol_fraction_low_gt_high_5s": pooled_breadth,
        "pooled_low_minus_high_5s_bps": pooled_difference,
        "pass": bool(date_positive >= 9 and date_ordered >= 9 and pooled_breadth >= 0.60 and pooled_difference > 0),
    }
    job_stats.to_csv(OUT / "confirmation_job_attrition.csv", index=False)
    session_summary.to_csv(OUT / "confirmation_session_markouts.csv", index=False)
    pooled_symbol.to_csv(OUT / "confirmation_symbol_markouts.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_model_sha256": package["package_sha256"],
        "planned_jobs": len(jobs),
        "executed_jobs": len(jobs),
        "confirmation_sessions": CONFIRMATION,
        "candidate_events": int(job_stats.candidate_events.sum()),
        "confirmation_clusters_100ms": len(data),
        "zero_candidate_jobs": int((job_stats.candidate_events == 0).sum()),
        "maximum_loaded_session": max(CONFIRMATION),
        "holdout_rows_loaded": 0,
        "refit_performed": False,
        "confirmation_rethreshold_performed": False,
        "gates": gates,
        "fill_simulation_performed": False,
        "decision_gate": "queue_simulation_earned" if gates["pass"] else "queue_simulation_blocked_diagnose_confirmation_failure",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(session_summary.to_string(index=False))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
