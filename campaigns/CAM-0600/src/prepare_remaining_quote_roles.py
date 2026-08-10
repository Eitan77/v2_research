import argparse
from pathlib import Path

import pandas as pd


SHARED = Path(__file__).resolve().parents[1] / "artifacts" / "shared"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--roles", type=Path, default=SHARED / "quote_roles.parquet")
    parser.add_argument("--match", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=SHARED / "quote_roles_remaining.parquet")
    args = parser.parse_args()
    roles = pd.read_parquet(args.roles)
    roles["target_ts"] = pd.to_datetime(roles["target_ts"], utc=True)
    match_paths = args.match or [
        SHARED / "remote_quote_role_matches.parquet", SHARED / "remote_quote_role_matches_expanded.parquet",
        SHARED / "remote_quote_role_matches_final.parquet",
    ]
    frames = []
    for path in match_paths:
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        if frame.empty:
            continue
        frame["target_ts"] = pd.to_datetime(frame["target_ts"], utc=True)
        if "match_valid" in frame.columns:
            frame = frame[frame["match_valid"].fillna(False).astype(bool)]
        frames.append(frame)
    matched = pd.concat(frames, ignore_index=True)
    matched = matched.drop_duplicates(["symbol", "target_ts", "role"])
    key = ["symbol", "target_ts", "role"]
    remaining = roles.merge(matched[key], on=key, how="left", indicator=True)
    remaining = remaining[remaining["_merge"] != "both"][key]
    remaining.to_parquet(args.output, index=False)
    print({"remaining_roles": len(remaining), "symbols": remaining.symbol.nunique()})


if __name__ == "__main__":
    main()
