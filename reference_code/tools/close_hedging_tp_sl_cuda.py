from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd, torch

H=(5,10,15,20,25); TP=(5,10,20,30,50); SL=(5,10,20,30); COSTS=(0,2,5,10)
def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 d=pd.read_parquet(a.input); rows=[]
 for leader in sorted(d.leader.unique()):
  for m in sorted(d.m.unique()):
   q0=d[(d.leader==leader)&(d.m==m)].copy()
   for thresh in (0,10,25,50,100):
    q=q0[abs(q0.leader_ret)>=thresh/10000].copy().sort_values(['session_date','leader'])
    if len(q)<30: continue
    hi=np.stack([q[f'hi_{h}'].to_numpy(np.float32) for h in H],axis=1); lo=np.stack([q[f'lo_{h}'].to_numpy(np.float32) for h in H],axis=1); cl=np.stack([q[f'fwd_{h}'].to_numpy(np.float32) for h in H],axis=1); yrs=pd.to_datetime(q.session_date).dt.year.to_numpy()
    dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu'); thi=torch.as_tensor(np.nan_to_num(hi),device=dev); tlo=torch.as_tensor(np.nan_to_num(lo),device=dev); tcl=torch.as_tensor(np.nan_to_num(cl),device=dev); valid=torch.as_tensor(np.isfinite(cl),device=dev)
    tpv=torch.as_tensor(np.array(TP,np.float32)/10000,device=dev).reshape(1,1,len(TP),1,1); slv=torch.as_tensor(np.array(SL,np.float32)/10000,device=dev).reshape(1,1,1,len(SL),1); cv=torch.as_tensor(2*np.array(COSTS,np.float32)/10000,device=dev).reshape(1,1,1,1,len(COSTS))
    xhi=thi.unsqueeze(2).unsqueeze(3).unsqueeze(4); xlo=tlo.unsqueeze(2).unsqueeze(3).unsqueeze(4); xcl=tcl.unsqueeze(2).unsqueeze(3).unsqueeze(4); vv=valid.unsqueeze(2).unsqueeze(3).unsqueeze(4)
    hit_tp=xhi.ge(tpv); hit_sl=xlo.le(-slv); gross=torch.where(hit_sl,-slv,torch.where(hit_tp,tpv,xcl))-cv; gross=torch.where(vv,gross,torch.full_like(gross,float('nan')))
    for hi_i,h in enumerate(H):
     for ti,tp in enumerate(TP):
      for si,sl in enumerate(SL):
       for ci,c in enumerate(COSTS):
        z=gross[:,hi_i,ti,si,ci].detach().cpu().numpy(); ok=np.isfinite(z); z=z[ok]; yv=yrs[ok];
        if len(z)<30: continue
        ys=[float(z[yv==y].sum()) for y in sorted(set(yv))]
        rows.append(dict(leader=leader,signal_min=m,threshold_bps=thresh,horizon_min=h,tp_bps=tp,sl_bps=sl,cost_bps_side=c,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in ys),years_tested=len(ys),worst_year_simple=min(ys)))
 pd.DataFrame(rows).to_csv(out/'close_hedging_tp_sl_grid.csv',index=False); (out/'run_metadata.json').write_text(json.dumps({'device':str(dev),'rows':len(rows),'tp_bps':TP,'sl_bps':SL,'costs_bps_side':COSTS,'path':'full high/low/close through each horizon; same-bar stop wins'},indent=2),encoding='utf-8'); print(json.dumps({'rows':len(rows),'device':str(dev)},indent=2))
if __name__=='__main__': main()
