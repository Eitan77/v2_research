from __future__ import annotations
import json, math, sys
from itertools import product
from pathlib import Path
import duckdb, numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/'campaigns'/'CAM-0638'; OUT=CAM/'artifacts'/'RUN-0001'; CAT=Path(r'D:\AlgoResearch\data\catalog.duckdb')
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0637'/'src')); import run0001_qqq_oco as prior
FEATURES=['green_bp','rvol','prior3_bp','prior5_bp','range_bp','close_loc','time_min','entry_spread_bp']

def feature_signals():
 s=prior.signals().copy(); c=duckdb.connect(str(CAT),read_only=True)
 b=c.execute("""select date,try_cast(timestamp as timestamptz) ts,arg_max(open,try_cast(ingested_at as timestamp)) as "open",arg_max(high,try_cast(ingested_at as timestamp)) as high,arg_max(low,try_cast(ingested_at as timestamp)) as low,arg_max(close,try_cast(ingested_at as timestamp)) as "close" from bars_1m where date between date '2025-04-25' and date '2025-05-31' and feed='sip' and adjustment='raw' and symbol='QQQ' and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:45' group by 1,2 order by 1,2""").fetchdf();c.close()
 b['date']=pd.to_datetime(b.date);b['ts']=pd.to_datetime(b.ts,utc=True);b['prior3_bp']=b.groupby('date').close.pct_change(3)*1e4;b['prior5_bp']=b.groupby('date').close.pct_change(5)*1e4;b['range_bp']=(b.high/b.low-1)*1e4;b['close_loc']=(b.close-b.low)/(b.high-b.low).replace(0,np.nan)
 z=s.merge(b[['ts','prior3_bp','prior5_bp','range_bp','close_loc']],on='ts',how='left');local=z.ts.dt.tz_convert('America/New_York');z['time_min']=local.dt.hour*60+local.dt.minute-570;return z.sort_values('entry_target').reset_index(drop=True)

def quote_days(s):
 frames=[pd.read_parquet(p) for p in sorted((ROOT/'campaigns'/'CAM-0637'/'artifacts'/'RUN-0001'/'quotes').glob('*.parquet'))];q=pd.concat(frames,ignore_index=True).drop_duplicates().sort_values('quote_ts');q['quote_ts']=pd.to_datetime(q.quote_ts,utc=True);return {d:x.reset_index(drop=True) for d,x in q.groupby(q.quote_ts.dt.date)},len(q)

def outcomes(s,qdays,target,stop,hold):
 rows=[]
 for e in s.itertuples(index=False):
  q=qdays.get(e.date.date());ts=q.quote_ts.to_numpy(dtype='datetime64[ns]');bid=q.bid.to_numpy();ask=q.ask.to_numpy();t=np.datetime64(e.entry_target.to_datetime64());i=int(np.searchsorted(ts,t))
  if i>=len(q) or ts[i]>t+np.timedelta64(2,'s'):continue
  entry=ask[i];limit=math.ceil(entry*(1+target/1e4)*100-1e-10)/100;stop_px=math.floor(entry*(1-stop/1e4)*100+1e-10)/100;end=min(int(np.searchsorted(ts,t+np.timedelta64(hold,'m'))),len(q)-1);out='timeout';j=end
  for k in range(i+1,end+1):
   if bid[k]>=limit:out='target';j=k;break
   if bid[k]<=stop_px:out='stop';j=k;break
  ex=limit if out=='target' else bid[j]*(1-1/1e4)
  rows.append({**{f:getattr(e,f) for f in FEATURES[:-1]},'date':e.date,'entry_ts':pd.Timestamp(ts[i],tz='UTC'),'exit_ts':pd.Timestamp(ts[j],tz='UTC'),'entry_spread_bp':(ask[i]/bid[i]-1)*1e4,'outcome':out,'ret':ex/entry-1})
 return pd.DataFrame(rows)

def rule_masks(x):
 r={'all':np.ones(len(x),bool)}
 for v in [7.5,10,15]:r[f'green_ge_{v}']=x.green_bp>=v
 for v in [3,4,5]:r[f'rvol_ge_{v}']=x.rvol>=v
 for f in ['prior3_bp','prior5_bp']:
  for v in [0,5,10]:r[f'{f}_ge_{v}']=x[f]>=v
  for v in [0,-5]:r[f'{f}_le_{v}']=x[f]<=v
 for v in [10,15,20]:r[f'range_ge_{v}']=x.range_bp>=v
 for v in [.75,.9]:r[f'close_loc_ge_{v}']=x.close_loc>=v
 r['early']=x.time_min<90;r['midday']=(x.time_min>=90)&(x.time_min<270);r['late']=x.time_min>=270
 r['green10_rvol3']=(x.green_bp>=10)&(x.rvol>=3);r['rvol3_prior3_pos']=(x.rvol>=3)&(x.prior3_bp>=0);r['rvol3_prior3_neg']=(x.rvol>=3)&(x.prior3_bp<0);r['range15_close90']=(x.range_bp>=15)&(x.close_loc>=.9)
 return r

def evaluate(x,mask):
 y=x.loc[np.asarray(mask)].sort_values('entry_ts');keep=[];last=pd.Timestamp('1970-01-01',tz='UTC')
 for i,e in y.iterrows():
  if e.entry_ts>last:keep.append(i);last=e.exit_ts
 z=y.loc[keep]
 if z.empty:return {'trades':0,'net_return':0.0}
 a=z.ret.abs().sum();return {'trades':len(z),'net_return':float(z.ret.sum()),'mean_bp':float(z.ret.mean()*1e4),'target_rate':float((z.outcome=='target').mean()),'green_rate':float((z.ret>0).mean()),'top_abs_share':float(z.ret.abs().max()/a) if a else 0.0}

def ridge_masks(dev,val):
 mu=dev[FEATURES].mean();sd=dev[FEATURES].std().replace(0,1);xd=(dev[FEATURES]-mu)/sd;xv=(val[FEATURES]-mu)/sd;xd=np.c_[np.ones(len(xd)),xd.fillna(0)];xv=np.c_[np.ones(len(xv)),xv.fillna(0)]
 beta=np.linalg.solve(xd.T@xd+np.diag([0]+[3]*len(FEATURES)),xd.T@dev.ret.to_numpy());pdv=xd@beta;pvv=xv@beta;cut=np.quantile(pdv,.70);return pdv>=cut,pvv>=cut,beta.tolist(),float(cut)

def main():
 OUT.mkdir(parents=True,exist_ok=True);s=feature_signals();qdays,nq=quote_days(s);cache={};disc_rows=[];model_rows=[];split=pd.Timestamp('2025-05-16')
 configs=list(product([1,2,3,5],[1,2,3,5],[1,3]))
 for target,stop,hold in configs:
  x=outcomes(s,qdays,target,stop,hold);cache[(target,stop,hold)]=x;dev=x[x.date<split];val=x[x.date>=split]
  for name,mask in rule_masks(dev).items():
   m=evaluate(dev,mask);disc_rows.append({'family':'simple','rule':name,'target':target,'stop':stop,'hold':hold,**m})
  md,mv,beta,cut=ridge_masks(dev,val);m=evaluate(dev,md);model_rows.append({'family':'ridge','rule':'ridge_top30','target':target,'stop':stop,'hold':hold,'beta':json.dumps(beta),'cut':cut,**m})
 disc=pd.DataFrame(disc_rows);models=pd.DataFrame(model_rows);eligible=disc[disc.trades>=20].sort_values('net_return',ascending=False).head(10);mel=models[models.trades>=20].sort_values('net_return',ascending=False).head(3)
 locked=[]
 for e in eligible.itertuples(index=False):
  x=cache[(e.target,e.stop,e.hold)];v=x[x.date>=split];m=evaluate(v,rule_masks(v)[e.rule]);locked.append({'family':'simple','rule':e.rule,'target':e.target,'stop':e.stop,'hold':e.hold,'dev_return':e.net_return,**{f'validation_{k}':z for k,z in m.items()}})
 for e in mel.itertuples(index=False):
  x=cache[(e.target,e.stop,e.hold)];dev=x[x.date<split];val=x[x.date>=split];_,mv,_,_=ridge_masks(dev,val);m=evaluate(val,mv);locked.append({'family':'ridge','rule':e.rule,'target':e.target,'stop':e.stop,'hold':e.hold,'dev_return':e.net_return,**{f'validation_{k}':z for k,z in m.items()}})
 pd.DataFrame(disc_rows+model_rows).to_csv(OUT/'development_candidates.csv',index=False);pd.DataFrame(locked).to_csv(OUT/'locked_validation.csv',index=False)
 report={'signals':len(s),'quote_rows':nq,'configs':len(configs),'simple_candidate_rows':len(disc),'ridge_candidate_rows':len(models),'locked_candidates':len(locked),'validation_positive':sum(r['validation_net_return']>0 for r in locked),'validation_gate_pass':sum(r['validation_net_return']>0 and r['validation_trades']>=20 and r['validation_top_abs_share']<.25 for r in locked),'locked':locked};(OUT/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
