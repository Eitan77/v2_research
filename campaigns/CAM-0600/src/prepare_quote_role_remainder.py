from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--roles",type=Path,required=True); parser.add_argument("--matches",type=Path,nargs="+",required=True); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    roles=pd.read_parquet(args.roles); roles["target_ts"]=pd.to_datetime(roles.target_ts,utc=True)
    found=[]
    for path in args.matches:
        if path.exists():
            x=pd.read_parquet(path); x["target_ts"]=pd.to_datetime(x.target_ts,utc=True)
            if {"bid_price","ask_price"}.issubset(x.columns):
                x=x[x.bid_price.notna()&x.ask_price.notna()&(x.bid_price>0)&(x.ask_price>=x.bid_price)]
            found.append(x[["symbol","target_ts","role"]])
    keys=pd.concat(found,ignore_index=True).drop_duplicates() if found else pd.DataFrame(columns=["symbol","target_ts","role"])
    missing=roles.merge(keys,on=["symbol","target_ts","role"],how="left",indicator=True); missing=missing[missing._merge=="left_only"].drop(columns="_merge")
    missing.to_parquet(args.output,index=False); print({"roles":len(roles),"matched":len(roles)-len(missing),"missing":len(missing),"output":str(args.output)})


if __name__=="__main__": main()
