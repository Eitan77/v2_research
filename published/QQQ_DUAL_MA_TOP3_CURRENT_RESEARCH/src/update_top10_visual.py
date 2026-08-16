from __future__ import annotations
import json,re
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]
HTML=Path(r'C:\Users\decla\.codex\visualizations\2026\08\07\019fd9a0-9bf8-7450-92a5-0fcc860e61bf\strategy-vs-qqq-spy.html')
A=ROOT/'campaigns'/'CAM-0611'/'artifacts'

def summarized(path):
 d=pd.read_parquet(path);d.date=pd.to_datetime(d.date);d=d.sort_values('date');d['cum']=100*d.net_pnl.cumsum()
 weekly=d.groupby(d.date.dt.to_period('W-FRI')).tail(1)
 if weekly.date.iloc[-1]!=d.date.iloc[-1]:weekly=pd.concat([weekly,d.tail(1)]).drop_duplicates('date')
 series=[[str(x.date()),round(float(v),2)] for x,v in zip(weekly.date,weekly['cum'])]
 monthly={str(k):round(float(100*g.net_pnl.sum()),2) for k,g in d.groupby(d.date.dt.to_period('M'))}
 return series,monthly

def main():
 text=HTML.read_text(encoding='utf-8');m=re.search(r'<script type="application/json" id="strategy-benchmark-data">(.*?)</script>',text,re.S);raw=json.loads(m.group(1));q,qm=summarized(A/'RUN-0049'/'qqq_top10_daily.parquet');s,sm=summarized(A/'RUN-0049'/'sp500_top10_daily.parquet');raw['series']['QQQ top-10']=q;raw['series']['S&P top-10']=s
 for row in raw['monthly']:row['QQQ top-10']=qm[row['period']];row['S&P top-10']=sm[row['period']]
 text=text[:m.start(1)]+json.dumps(raw,separators=(',',':'))+text[m.end(1):]
 text=text.replace('QQQ and S&amp;P dual-MA top-3 vs QQQ and SPY','Dual-MA top-3 and top-10 vs QQQ and SPY')
 text=text.replace('strategy uses fixed-base additive P&amp;L and exact SIP fills +2 bp','fixed-base additive P&amp;L · top-3 exact SIP +2 bp · top-10 frozen average execution +2 bp')
 text=text.replace('<th class="text-end">QQQ strat</th><th class="text-end">S&amp;P strat</th><th class="text-end">QQQ</th><th class="text-end">SPY</th>','<th class="text-end">QQQ 3</th><th class="text-end">QQQ 10</th><th class="text-end">S&amp;P 3</th><th class="text-end">S&amp;P 10</th><th class="text-end">QQQ</th><th class="text-end">SPY</th>')
 old="names=['Strategy','S&P strategy','QQQ','SPY'],labels={'Strategy':'QQQ strategy','S&P strategy':'S&P strategy','QQQ':'QQQ','SPY':'SPY'},colors=['var(--viz-series-1)','var(--viz-series-4)','var(--viz-series-2)','var(--viz-series-3)']"
 new="names=['Strategy','QQQ top-10','S&P strategy','S&P top-10','QQQ','SPY'],labels={'Strategy':'QQQ top-3','QQQ top-10':'QQQ top-10','S&P strategy':'S&P top-3','S&P top-10':'S&P top-10','QQQ':'QQQ','SPY':'SPY'},colors=['var(--viz-series-1)','var(--viz-series-2)','var(--viz-series-4)','var(--viz-series-5)','var(--viz-series-3)','var(--viz-series-6)']"
 if old not in text:raise RuntimeError('visual script signature changed')
 text=text.replace(old,new).replace(".attr('stroke-width',n==='Strategy'?3:2)",".attr('stroke-width',(n==='Strategy'||n==='S&P strategy')?3:2)")
 HTML.write_text(text,encoding='utf-8')
 print(json.dumps({'updated':str(HTML),'qqq_top10_end':q[-1][1],'sp500_top10_end':s[-1][1],'rows_after_authorized_end':0},indent=2))
if __name__=='__main__':main()
