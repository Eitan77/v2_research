from __future__ import annotations

import numpy as np

from baseline_strategies import Variant, alpha_combo_weights, moving_average, rank_percentile, sector_panel_mask
from deep_strategies import active_trend_rank, benchmark_gate, concentrate_positive, liquid_mask, rank_long, trend_mask
from suite_core import Panel, month_end_indices, rank_weights, trailing_return, trailing_vol, weekly_indices


CONTROL_IDS=("CAM-0600","CAM-0602","CAM-0604","CAM-0610","CAM-0611","CAM-0612","CAM-0615","CAM-0616","CAM-0617","CAM-0619","CAM-0620","CAM-0623")


def _simple_momentum(p: Panel, signals: np.ndarray, top_k: int, horizon: int=126, skip: int=21) -> np.ndarray:
    score=trailing_return(p,horizon,skip)
    mask=liquid_mask(p,.50)&np.isfinite(score)&(score>0)
    return rank_long(p,score,signals,mask,top_k,63)


def build_control_variants(campaign_id, panels, f):
    out=[]
    if campaign_id=="CAM-0600":
        p=panels["sp500"]; signals=month_end_indices(p.dates)
        for formation in (40,63,84,126):
            for skip in (0,5,10,21):
                if skip>=formation: continue
                score=trailing_return(p,formation,skip)
                for top_k in (3,5,10,20):
                    base=rank_long(p,score,signals,liquid_mask(p,.50)&(score>0),top_k,63)
                    for panic in (False,True):
                        w=base*benchmark_gate(p,panic)[:,None]
                        out.append(Variant(campaign_id,f"sp500__mom{formation}_skip{skip}__top{top_k}__panic{int(panic)}",p,w,"open_to_next_open",1,{"formation":formation,"skip":skip,"top_k":top_k,"panic":panic,"control_family":"momentum_neighborhood"}))

    elif campaign_id in {"CAM-0602","CAM-0604"}:
        for name in ("sp500","qqq"):
            p=panels[name]; signals=month_end_indices(p.dates); mask=liquid_mask(p,.50)
            mom=rank_percentile(trailing_return(p,252,21),mask,signals)
            val=rank_percentile(f[name].book_to_price,mask,signals)
            quality=rank_percentile(f[name].profitability-f[name].leverage,mask,signals)
            lowvol=1-rank_percentile(trailing_vol(p,126),mask,signals)
            scores={"value":val,"quality":quality,"momentum":mom,"lowvol":lowvol,
                    "value_quality":.65*val+.35*quality,
                    "value_quality_mom":.45*val+.35*quality+.20*mom,
                    "equal4":.25*(val+quality+mom+lowvol)}
            for label,score in scores.items():
                for top_k in (5,10,20,40):
                    w=rank_long(p,score,signals,mask&np.isfinite(score),top_k,63)
                    out.append(Variant(campaign_id,f"{name}__{label}__top{top_k}",p,w,"open_to_next_open",1,{"score":label,"top_k":top_k,"control_family":"factor_ablation"}))

    elif campaign_id in {"CAM-0610","CAM-0611","CAM-0612"}:
        configs={"CAM-0610":((100,),(150,),(200,)),"CAM-0611":((10,30),(20,50),(50,200)),"CAM-0612":((3,10,21),(5,20,50),(10,50,200))}[campaign_id]
        for name in ("sp500","qqq","etf"):
            p=panels[name]
            for cadence,signals in (("weekly",weekly_indices(p.dates)),("monthly",month_end_indices(p.dates))):
                for top_k in ((3,5,10) if name!="etf" else (1,3,5)):
                    control=_simple_momentum(p,signals,top_k)
                    out.append(Variant(campaign_id,f"{name}__ungated__{cadence}__top{top_k}",p,control,"open_to_next_open",1,{"gate":"none","top_k":top_k,"control_family":"identical_momentum_rank"}))
                    for windows in configs:
                        mas=[moving_average(p,x) for x in windows]
                        condition=(p.adj_close>mas[0]) if len(windows)==1 else ((mas[0]>mas[1]) if len(windows)==2 else ((mas[0]>mas[1])&(mas[1]>mas[2])))
                        w=active_trend_rank(p,condition,signals,top_k,"momentum")
                        out.append(Variant(campaign_id,f"{name}__ma{'_'.join(map(str,windows))}__{cadence}__top{top_k}",p,w,"open_to_next_open",1,{"gate":list(windows),"top_k":top_k,"control_family":"ma_increment"}))

    elif campaign_id in {"CAM-0615","CAM-0616"}:
        for name in ("sp500","qqq","etf"):
            p=panels[name]; signals=month_end_indices(p.dates)
            for horizon in (20,60,126):
                for top_k in ((5,10,20) if name!="etf" else (1,3,5)):
                    score=trailing_return(p,horizon,0)
                    w=rank_long(p,score,signals,liquid_mask(p,.50)&(score>0),top_k,63)
                    out.append(Variant(campaign_id,f"{name}__simple_mom{horizon}__top{top_k}",p,w,"open_to_next_open",1,{"horizon":horizon,"top_k":top_k,"control_family":"optimizer_ablation"}))

    elif campaign_id=="CAM-0617":
        p=panels["etf"]
        allowed=np.array([s not in {"SOXL","SOXS","TQQQ","SQQQ"} for s in p.symbols],dtype=bool)[None,:]
        for history,forecast in ((20,5),(60,5),(60,20),(120,20)):
            raw,report=alpha_combo_weights(p,history,forecast)
            for top_k in (1,3,5,10):
                w=concentrate_positive(raw,top_k,liquid_mask(p,.50)&allowed)
                out.append(Variant(campaign_id,f"etf_unlevered__alpha_M{history}_E{forecast}__top{top_k}",p,w,"open_to_next_open",1,{"history":history,"forecast":forecast,"top_k":top_k,"leveraged_inverse_excluded":True,**report}))

    elif campaign_id in {"CAM-0619","CAM-0620"}:
        p=panels["etf"]; signals=month_end_indices(p.dates); sector=sector_panel_mask(p)
        for formation,skip in ((63,0),(126,0),(126,21),(252,21)):
            score=trailing_return(p,formation,skip)
            for top_k in (1,3,5):
                for absolute in (False,True):
                    mask=sector&np.isfinite(score)
                    if absolute: mask &= score>0
                    w=rank_long(p,score,signals,mask,top_k,63)
                    out.append(Variant(campaign_id,f"sector__mom{formation}_skip{skip}__top{top_k}__abs{int(absolute)}",p,w,"open_to_next_open",1,{"formation":formation,"skip":skip,"top_k":top_k,"absolute_gate":absolute,"control_family":"sector_gate_ablation"}))

    elif campaign_id=="CAM-0623":
        for name in ("sp500","qqq"):
            p=panels[name]; signals=month_end_indices(p.dates); mask=liquid_mask(p,.50)
            scores={"chs_safe":-f[name].chs_logit,"profitability":f[name].profitability,
                    "lowvol":-trailing_vol(p,126),"momentum":trailing_return(p,252,21)}
            for label,score in scores.items():
                for top_k in (5,10,20,40):
                    w=rank_weights(score,mask&np.isfinite(score),signals,mode="long",top_k=top_k,inverse_vol=trailing_vol(p,63))
                    out.append(Variant(campaign_id,f"{name}__{label}__top{top_k}",p,w,"open_to_next_open",1,{"score":label,"top_k":top_k,"control_family":"distress_ablation"}))
    if not out: raise RuntimeError(campaign_id)
    return out
