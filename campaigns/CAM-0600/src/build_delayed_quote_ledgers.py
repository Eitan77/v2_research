from pathlib import Path

import pandas as pd


SHARED = Path(__file__).resolve().parents[1] / "artifacts" / "shared"


def main():
    rejected = pd.read_csv(SHARED / "quote_summary.csv")
    rejected = set(rejected.loc[rejected["decision"] == "quote_rejected", "campaign_id"])
    ledger = pd.read_parquet(SHARED / "quote_candidate_positions.parquet")
    ledger = ledger[ledger["campaign_id"].isin(rejected)].copy()
    ledger["entry_target_ts"] = pd.to_datetime(ledger["entry_target_ts"], utc=True) + pd.Timedelta(minutes=10)
    next_open = ledger["holding"].eq("open_to_next_open")
    ledger.loc[next_open, "exit_target_ts"] = pd.to_datetime(ledger.loc[next_open, "exit_target_ts"], utc=True) + pd.Timedelta(minutes=10)
    ledger.to_parquet(SHARED / "quote_candidate_positions_0940.parquet", index=False)
    roles = pd.concat([
        ledger[["symbol", "entry_target_ts"]].rename(columns={"entry_target_ts": "target_ts"}).assign(role="entry_ask_after"),
        ledger[["symbol", "exit_target_ts"]].rename(columns={"exit_target_ts": "target_ts"}).assign(
            role=ledger["holding"].map(lambda x: "exit_bid_after" if x == "open_to_next_open" else "exit_bid_before")
        ),
    ], ignore_index=True).drop_duplicates(["symbol", "target_ts", "role"])
    roles.to_parquet(SHARED / "quote_roles_0940.parquet", index=False)
    print({"campaigns": len(rejected), "position_rows": len(ledger), "roles": len(roles), "symbols": roles.symbol.nunique()})


if __name__ == "__main__":
    main()
