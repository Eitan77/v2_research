from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0628'/'artifacts'/'RUN-0003';SRC=ROOT/'campaigns'/'CAM-0628'/'artifacts'/'RUN-0001'/'daily_2bps.parquet';C=[('XLK',10,.3,'none'),('TQQQ',20,.3,'none'),('SOXL',20,.3,'none')]
def main():
 OUT.mkdir(parents=True,exist_ok=True);d=pd.read_parquet(SRC);parts=[]
 for s,w,t,g in C:
  x=d[(d.symbol.eq(s))&(d.vol_window.eq(w))&(d.target_vol.eq(t))&(d.trend.eq(g))&(d.weight>0)][['date','symbol','weight']].drop_duplicates(['date','symbol']);parts.append(x)
 x=pd.concat(parts,ignore_index=True);rows=[]
 for r in x.itertuples():
  for role,clock in [('entry0930','09:30'),('entry0940','09:40'),('exit1550','15:50')]:rows.append({'date':r.date,'symbol':r.symbol,'weight':r.weight,'target_ts':pd.Timestamp(f'{r.date.date()} {clock}',tz='America/New_York').tz_convert('UTC'),'role':role})
 roles=pd.DataFrame(rows);roles.to_parquet(OUT/'roles.parquet',index=False);x.to_parquet(OUT/'weights.parquet',index=False);print({'days':len(x),'roles':len(roles),'max_date':str(x.date.max().date())})
if __name__=='__main__':main()
