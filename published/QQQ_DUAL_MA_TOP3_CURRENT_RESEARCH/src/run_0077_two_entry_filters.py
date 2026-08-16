from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'campaigns'/'CAM-0600'/'src')]
from run_0033_exit_overlays import base_context
from run_0067_last_year_breadth import extension,signals
from run_0068_compounded_breadth import panel,simulate,COST,RESERVE,START,END
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0077'

def metrics(daily,label,extra=None):
 w=daily.loc[(daily.index>=START)&(daily.index<=END),'equity'];prior=daily.loc[daily.index<START,'equity'].iloc[-1];path=pd.concat([pd.Series([prior],index=[START-pd.Timedelta(nanoseconds=1)]),w]);dd=path/path.cummax()-1;dr=path.pct_change().dropna();mo=path.groupby(path.index.to_period('M')).last().pct_change().dropna();wk=path.groupby(path.index.to_period('W-FRI')).last().pct_change().dropna();r={'variant':label,'return':w.iloc[-1]/prior-1,'maximum_drawdown':-dd.min(),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':mo.min(),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'worst_week':wk.min()};r.update(extra or {});return r

def delayed(dates,rets,score,mask,op,cl):
 targets={}
 for s in signals(dates):
  e=int(s)+1
  if e>=len(dates):continue
  c=np.flatnonzero(mask[s]&np.isfinite(score[s]));chosen=c[np.argsort(score[s,c],kind='stable')[-min(3,len(c)):]] if len(c) else np.array([],int);targets[e]=tuple(sorted(map(int,chosen)))
 current=np.zeros(rets.shape[1]);cash=1.;active=tuple();pending={};rows=[];entries=waited=skipped=0
 for i,d in enumerate(dates):
  gap=np.divide(op[i],cl[i-1],out=np.full(op.shape[1],np.nan),where=np.isfinite(op[i])&np.isfinite(cl[i-1])&(cl[i-1]>0)) - 1 if i else np.full(op.shape[1],np.nan)
  if i in targets and (targets[i]!=active or pending):
   target=targets[i];keep=np.zeros(len(current),bool);keep[list(target)]=True;cash+=float((current[~keep]*(1-COST)).sum());current[~keep]=0;nav=cash+current.sum();qual=[c for c in target if np.isfinite(gap[c]) and gap[c]>=0];miss=[c for c in target if c not in qual];reserve=RESERVE*nav
   lo,hi=0.,nav/max(1,len(target))
   for _ in range(80):
    x=(lo+hi)/2;wanted=np.zeros_like(current);wanted[qual]=x;delta=wanted-current;end=cash-delta.sum()-COST*np.abs(delta).sum()
    if end>=reserve+len(miss)*x:lo=x
    else:hi=x
   wanted=np.zeros_like(current);wanted[qual]=lo;delta=wanted-current;cash-=delta.sum()+COST*np.abs(delta).sum();current=wanted;pending={c:lo for c in miss};entries+=len(qual);waited+=len(miss);active=target
  elif pending:
   for c,x in list(pending.items()):
    if np.isfinite(gap[c]) and gap[c]>=0:
     buy=min(x,max(0.,(cash-RESERVE*(cash+current.sum()))/(1+COST)));cash-=buy*(1+COST);current[c]=buy;entries+=1;pending.pop(c)
  if i+1 in targets:skipped+=len(pending);pending={}
  current*=1+np.nan_to_num(rets[i],nan=0.);rows.append({'date':pd.Timestamp(d),'equity':cash+current.sum(),'cash':cash})
 return pd.DataFrame(rows).set_index('date'),{'entries':entries,'initially_waited_slots':waited,'slots_never_entered_that_week':skipped}

def main():
 OUT.mkdir(parents=True,exist_ok=True);dates,rets,score,mask,_=panel();p,*_=base_context();ext_dates,ext_open,ext_close,_,_,_=extension(p);op=np.vstack([p.adj_open,ext_open]);cl=np.vstack([p.adj_close,ext_close]);sma200=pd.DataFrame(cl,index=dates).rolling(200,min_periods=200).mean().to_numpy();rows=[]
 base,_=simulate(dates,rets,score,mask,3);rows.append(metrics(base,'baseline'))
 for lim in [1.,.9,.8]:
  filtered=mask&np.isfinite(sma200)&(cl<=lim*sma200);d,m=simulate(dates,rets,score,filtered,3);sig=signals(dates);counts=[int((filtered[i]&np.isfinite(score[i])).sum()) for i in sig if dates[i]>=START-pd.Timedelta(days=7)];rows.append(metrics(d,f'price_le_{lim:g}x_sma200',{'median_eligible_names':float(np.median(counts)),'zero_candidate_signal_weeks':int(np.sum(np.array(counts)==0))}))
 d,x=delayed(dates,rets,score,mask,op,cl);rows.append(metrics(d,'wait_until_nonnegative_open',x));out=pd.DataFrame(rows);out.to_csv(OUT/'comparison.csv',index=False);report={'status':'completed','window_start':str(START.date()),'window_end':str(END.date()),'maximum_loaded_date':str(dates.max().date()),'evidence_label':'observed_oos_descriptive_not_fresh_validation','metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda v:v.item() if hasattr(v,'item') else v)+'\n');print(out.to_string(index=False))
if __name__=='__main__':main()
