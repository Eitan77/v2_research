from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"checkpoint_split_repaired"; OUT.mkdir(parents=True,exist_ok=True)
paths={"Parent four":CAM/"CAM-0625"/"artifacts"/"RUN-0020"/"parent4_full_daily.parquet","Lean three":CAM/"CAM-0625"/"artifacts"/"RUN-0020"/"leave_out_CAM-0604_full_daily.parquet","Final substitution":CAM/"CAM-0625"/"artifacts"/"RUN-0022"/"lean3_plus_sector_full_daily.parquet"}
plt.style.use("seaborn-v0_8-whitegrid"); fig,ax=plt.subplots(figsize=(11,6))
for label,path in paths.items():
 d=pd.read_parquet(path); d.date=pd.to_datetime(d.date); ax.plot(d.date,1+d.net_pnl.cumsum(),label=label,linewidth=1.8)
ax.set_title("CAM-0625 split-repaired fixed-capital equity"); ax.set_ylabel("Equity (1 + cumulative additive P&L)"); ax.legend(); fig.tight_layout(); fig.savefig(OUT/"equity_comparison.png",dpi=180); plt.close(fig)
q=pd.read_parquet(CAM/"CAM-0625"/"artifacts"/"RUN-0023"/"daily_0940_2bps_extra.parquet"); q.date=pd.to_datetime(q.date); monthly=q.groupby(q.date.dt.to_period("M")).net_pnl.sum(); colors=["#2a9d8f" if x>=0 else "#e76f51" for x in monthly]
fig,ax=plt.subplots(figsize=(11,5)); ax.bar(monthly.index.astype(str),monthly.values*100,color=colors); ax.axhline(0,color="black",linewidth=.8); ax.set_title("Final substitution: 09:40 SIP +2 bp monthly P&L"); ax.set_ylabel("Additive return (%)"); ax.tick_params(axis="x",rotation=45); fig.tight_layout(); fig.savefig(OUT/"quote_monthly.png",dpi=180); plt.close(fig)
print(OUT)
