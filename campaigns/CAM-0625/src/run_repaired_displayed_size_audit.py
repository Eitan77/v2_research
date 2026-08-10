from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
SHARED = CAM / "CAM-0600" / "artifacts" / "shared"
OUT = CAM / "CAM-0625" / "artifacts" / "RUN-0031"
IDS = ["CAM-0600", "CAM-0621", "CAM-0624", "CAM-0618"]
UNIT_CHANGE = pd.Timestamp("2025-11-03")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    roles = pd.read_parquet(SHARED / "split_repaired_quote_replay_0940.parquet")
    roles["session_date"] = pd.to_datetime(roles.session_date)
    roles = roles[
        roles.campaign_id.isin(IDS)
        & roles.effective_complete
        & roles.session_date.between("2025-05-01", "2026-04-30")
    ].copy()
    buy = roles.side.eq("buy")
    roles["display_price"] = np.where(buy, roles.ask_price, roles.bid_price)
    roles["reported_size"] = np.where(buy, roles.ask_size, roles.bid_size)
    roles["size_unit"] = np.where(roles.session_date < UNIT_CHANGE, "round_lots", "shares")
    roles["display_shares"] = np.where(
        roles.session_date < UNIT_CHANGE, roles.reported_size * 100.0, roles.reported_size
    )
    roles["display_notional"] = roles.display_price * roles.display_shares
    roles["portfolio_delta_weight"] = roles.delta_weight.abs() / len(IDS)
    valid = roles[
        roles.display_price.gt(0)
        & roles.display_shares.gt(0)
        & roles.portfolio_delta_weight.gt(0)
    ].copy()
    if len(valid) != len(roles):
        raise RuntimeError(f"invalid displayed-size roles: {len(roles) - len(valid)}")

    rows = []
    scopes = [("all", valid)] + [(campaign_id, valid[valid.campaign_id.eq(campaign_id)]) for campaign_id in IDS]
    for fraction in (0.01, 0.05, 0.10):
        for scope, frame in scopes:
            supported = fraction * frame.display_notional / frame.portfolio_delta_weight
            rows.append({
                "participation_of_single_displayed_size": fraction,
                "scope": scope,
                "roles": int(len(supported)),
                "minimum_capital_dollars": float(supported.min()),
                "p01_capital_dollars": float(supported.quantile(0.01)),
                "p05_capital_dollars": float(supported.quantile(0.05)),
                "p10_capital_dollars": float(supported.quantile(0.10)),
                "median_capital_dollars": float(supported.median()),
            })
    metrics = pd.DataFrame(rows)
    central = metrics[(metrics.scope == "all") & (metrics.participation_of_single_displayed_size == 0.10)].iloc[0]
    report = {
        "status": "completed",
        "run_id": "RUN-0031",
        "roles": int(len(valid)),
        "round_lot_unit_roles": int((valid.size_unit == "round_lots").sum()),
        "share_unit_roles": int((valid.size_unit == "shares").sum()),
        "ten_percent_displayed_size_all": {
            "minimum_capital_dollars": float(central.minimum_capital_dollars),
            "p01_capital_dollars": float(central.p01_capital_dollars),
            "p05_capital_dollars": float(central.p05_capital_dollars),
            "p10_capital_dollars": float(central.p10_capital_dollars),
            "median_capital_dollars": float(central.median_capital_dollars),
        },
        "metrics": metrics.to_dict("records"),
        "quote_size_unit_change": "Alpaca round lots before 2025-11-03; shares on and after 2025-11-03",
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "interpretation": "Single displayed NBBO snapshot participation proxy only; not a fill, capacity, depth, replenishment, or impact claim.",
    }
    metrics.to_csv(OUT / "displayed_nbbo_participation.csv", index=False)
    valid[[
        "campaign_id", "session_date", "symbol", "side", "delta_weight", "display_price",
        "reported_size", "size_unit", "display_shares", "display_notional", "portfolio_delta_weight"
    ]].to_parquet(OUT / "displayed_size_roles.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0625" / "runs" / "RUN-0031.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = report
    run["decision"] = "Retain as a conservative top-of-book warning only; require prospective order-level fill and impact tracking before sizing capital."
    path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0625" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0031", "event": "completed", "result": report}) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
