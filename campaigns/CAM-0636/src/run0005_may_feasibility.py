from __future__ import annotations

import json

import pandas as pd

import run0003_quote_microtarget as replay
import run0004_pull_and_replay as pull


OUT=replay.base.CAM/"artifacts"/"RUN-0005"


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    all_sig=replay.signals(); sig=all_sig[(all_sig.date>=pd.Timestamp("2025-05-01"))&(all_sig.date<=pd.Timestamp("2025-05-31"))].copy()
    all_windows=pull.windows(all_sig); indices=[i for i,(a,b) in enumerate(all_windows) if a<pd.Timestamp("2025-06-01",tz="UTC")]
    paths=[pull.RAW/f"window_{i:04d}.parquet" for i in indices]
    missing=[str(p) for p in paths if not p.exists()]
    if missing: raise RuntimeError(f"missing May quote windows: {len(missing)}")
    frames=[pd.read_parquet(p) for p in paths]; quotes=pd.concat(frames,ignore_index=True)
    quotes["quote_ts"]=pd.to_datetime(quotes.quote_ts,utc=True); quotes=quotes.drop_duplicates().sort_values("quote_ts")
    results={}
    for target in [1,2]:
      for hold in [1,3,5]:
        key=f"t{target}_h{hold}"; x=replay.replay(sig,quotes,target,hold,0); results[key]=replay.summarize(x)
        x.assign(config=key).to_csv(OUT/f"ledger_{key}.csv",index=False)
    report={"month":"2025-05","signal_events":len(sig),"merged_quote_windows":len(indices),"quote_rows":len(quotes),"all_required_windows_present":True,"results":results}
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__=="__main__":main()
