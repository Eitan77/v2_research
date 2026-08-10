from __future__ import annotations
from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]; A=ROOT/"campaigns"/"CAM-0625"/"artifacts"; OUT=A/"checkpoint"; OUT.mkdir(parents=True,exist_ok=True)

def load(path):
 d=pd.read_parquet(path); d["date"]=pd.to_datetime(d.date); return d.set_index("date").net_pnl

full_equal=load(A/"RUN-0002"/"daily_full_history_equal.parquet")
full_risk=load(A/"RUN-0002"/"daily_full_history_causal_inverse_vol.parquet")
quote_equal=load(A/"RUN-0002"/"daily_quote_0940_2bps_extra_equal.parquet")
quote_risk=load(A/"RUN-0002"/"daily_quote_0940_2bps_extra_causal_inverse_vol.parquet")

plt.style.use("seaborn-v0_8-whitegrid")
fig,ax=plt.subplots(2,1,figsize=(11,8),constrained_layout=True)
ax[0].plot(1+full_equal.cumsum(),label="Equal four",lw=2); ax[0].plot(1+full_risk.cumsum(),label="Causal inverse vol",lw=2); ax[0].set_title("CAM-0625 development equity (fixed-base additive)"); ax[0].set_ylabel("Equity = 1 + cumulative P&L"); ax[0].legend()
ax[1].plot(1+quote_equal.cumsum(),label="Equal four",lw=2); ax[1].plot(1+quote_risk.cumsum(),label="Causal inverse vol",lw=2); ax[1].set_title("Corrected 09:40 target-change SIP, +2 bp/side"); ax[1].set_ylabel("Equity = 1 + cumulative P&L"); ax[1].legend()
fig.savefig(OUT/"ensemble_equity.png",dpi=180); plt.close(fig)

m=pd.concat({"Equal four":quote_equal.groupby(quote_equal.index.to_period("M")).sum(),"Causal inverse vol":quote_risk.groupby(quote_risk.index.to_period("M")).sum()},axis=1); m.index=m.index.astype(str)
fig,ax=plt.subplots(figsize=(12,5),constrained_layout=True); (100*m).plot(kind="bar",ax=ax,color=["#1f77b4","#ff7f0e"]); ax.axhline(0,color="black",lw=.8); ax.set_title("CAM-0625 monthly net simple P&L — corrected quote window"); ax.set_ylabel("Percent of fixed capital"); ax.set_xlabel(""); ax.tick_params(axis="x",rotation=45); fig.savefig(OUT/"ensemble_monthly.png",dpi=180); plt.close(fig)
m.to_csv(OUT/"ensemble_monthly.csv",index_label="month")
print(OUT)
