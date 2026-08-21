from __future__ import annotations
import json
from itertools import combinations,product
import pandas as pd
import run0001_conditional_search as r

OUT=r.CAM/'artifacts'/'RUN-0002'

def main():
 OUT.mkdir(parents=True,exist_ok=True);s=r.feature_signals();qdays,nq=r.quote_days(s);split=pd.Timestamp('2025-05-16');rows=[];cache={};rules={}
 for cfg in product([1,2,3,5],[1,2,3,5],[1,3]):
  x=r.outcomes(s,qdays,*cfg);cache[cfg]=x;dev=x[x.date<split];masks=r.rule_masks(dev);names=[n for n in masks if n not in {'all','early','midday','late'}]
  for a,b in combinations(names,2):
   m=r.evaluate(dev,masks[a]&masks[b])
   if m['trades']>=20:rows.append({'rule':f'{a}&{b}','a':a,'b':b,'target':cfg[0],'stop':cfg[1],'hold':cfg[2],**m})
 grid=pd.DataFrame(rows).sort_values('net_return',ascending=False);locked=[]
 for e in grid.head(5).itertuples(index=False):
  x=cache[(e.target,e.stop,e.hold)];v=x[x.date>=split];masks=r.rule_masks(v);m=r.evaluate(v,masks[e.a]&masks[e.b]);locked.append({'rule':e.rule,'target':e.target,'stop':e.stop,'hold':e.hold,'dev_return':e.net_return,**{f'validation_{k}':z for k,z in m.items()}})
 grid.to_csv(OUT/'development_depth2.csv',index=False);pd.DataFrame(locked).to_csv(OUT/'locked_validation.csv',index=False);report={'executed_depth2_rows':len(grid),'locked':len(locked),'validation_positive':sum(x['validation_net_return']>0 for x in locked),'validation_gate_pass':sum(x['validation_net_return']>0 and x['validation_trades']>=20 for x in locked),'results':locked};(OUT/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
