from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ScanConfig
from .registry import FeatureSpec


def build_features(
    bars: pd.DataFrame,
    config: ScanConfig,
    specs: list[FeatureSpec],
    *,
    symbol_local: bool = False,
) -> tuple[pd.DataFrame, list[FeatureSpec]]:
    """Build the clean-room core library, always from past/completed bars."""
    wanted={s.name for s in specs if s.status=="requested"}
    active_lookbacks={int(s.lookback) for s in specs if s.lookback in config.lookbacks}
    if any(s.family=="normalization" for s in specs): active_lookbacks.update({1,10})
    active_lead_lags={int(s.lookback) for s in specs if s.family=="lead_lag" and s.lookback is not None}
    active_lookbacks.update(active_lead_lags)
    x = bars.sort_values(["symbol", "bar_start_ts"], kind="stable").copy()
    g = x.groupby("symbol", sort=False)
    x["_ret"] = g["close"].pct_change(); x["_prev_close"] = g["close"].shift(); x["_range"] = x.high - x.low
    x["_session_open"] = x.groupby(["symbol", "session_date"], sort=False)["open"].transform("first")
    x["_session_high"] = x.groupby(["symbol", "session_date"], sort=False)["high"].cummax(); x["_session_low"] = x.groupby(["symbol", "session_date"], sort=False)["low"].cummin()
    x["_cum_pv"] = (x.close * x.volume).groupby([x.symbol, x.session_date], sort=False).cumsum(); x["_cum_vol"] = x.volume.groupby([x.symbol, x.session_date], sort=False).cumsum()
    x["return_since_open"] = x.close / x._session_open - 1; x["distance_session_vwap"] = x.close / (x._cum_pv / x._cum_vol.replace(0,np.nan)) - 1
    x["distance_session_high"] = x.close / x._session_high - 1; x["distance_session_low"] = x.close / x._session_low - 1
    x["bar_range_pct"] = x._range / x.close.replace(0,np.nan); x["body_to_range"] = (x.close-x.open).abs()/x._range.replace(0,np.nan)
    x["upper_wick_to_range"] = (x.high-x[["open","close"]].max(axis=1)) / x._range.replace(0,np.nan); x["lower_wick_to_range"] = (x[["open","close"]].min(axis=1)-x.low) / x._range.replace(0,np.nan)
    x["close_location"] = (x.close-x.low)/x._range.replace(0,np.nan)
    x["bar_return"] = x.close / x.open.replace(0, np.nan) - 1; x["bar_log_return"] = np.log(x.close / x.open.replace(0, np.nan))
    x["body_pct"] = (x.close-x.open)/x.open.replace(0,np.nan); x["absolute_body_pct"] = x.body_pct.abs()
    prev_high=g.high.shift(); prev_low=g.low.shift(); x["inside_bar"]=(x.high<=prev_high)&(x.low>=prev_low); x["outside_bar"]=(x.high>=prev_high)&(x.low<=prev_low)
    x["higher_high"]=x.high>prev_high; x["higher_low"]=x.low>prev_low; x["lower_high"]=x.high<prev_high; x["lower_low"]=x.low<prev_low
    x["session_range_position"]=(x.close-x._session_low)/(x._session_high-x._session_low).replace(0,np.nan)
    x["cumulative_volume"]=x._cum_vol; x["current_volume_share"]=x.volume/x._cum_vol.replace(0,np.nan)
    x["_session_vwap"]=x._cum_pv/x._cum_vol.replace(0,np.nan); x["vwap_slope"]=x.groupby(["symbol","session_date"])._session_vwap.pct_change()
    side=np.sign(x.close-x._session_vwap); x["vwap_cross"]=(side.groupby(x.symbol).diff().abs()>0).astype(float)
    x["consecutive_positive_bars"]=_run_length(x._ret.gt(0),x.symbol); x["consecutive_negative_bars"]=_run_length(x._ret.lt(0),x.symbol)
    local=x.bar_start_ts.dt.tz_convert("America/New_York"); x["minutes_since_open"]=(local.dt.hour*60+local.dt.minute)-570; x["minute_of_session"]=x.minutes_since_open; x["minutes_until_close"]=390-x.minutes_since_open
    x["day_of_week"]=local.dt.dayofweek; x["month"]=local.dt.month; x["quarter"]=local.dt.quarter
    for n in sorted(active_lookbacks):
        columns_before=set(x.columns)
        r=g["close"].pct_change(n); roll_ret=g["_ret"].rolling(n, min_periods=n)
        x[f"return_{n}"]=r.reset_index(level=0,drop=True); x[f"log_return_{n}"]=np.log1p(r)
        rv=roll_ret.std().reset_index(level=0,drop=True); x[f"realized_vol_{n}"]=rv; tail_min=max(1,min(n,max(2,n//3))); x[f"downside_vol_{n}"]=x._ret.where(x._ret<0).groupby(x.symbol).rolling(n,min_periods=tail_min).std().reset_index(level=0,drop=True); x[f"upside_vol_{n}"]=x._ret.where(x._ret>0).groupby(x.symbol).rolling(n,min_periods=tail_min).std().reset_index(level=0,drop=True); x[f"return_vol_ratio_{n}"]=x[f"return_{n}"]/rv.replace(0,np.nan); x[f"return_consistency_{n}"]=(x._ret>0).groupby(x.symbol).rolling(n,min_periods=n).mean().reset_index(level=0,drop=True)
        x[f"positive_return_sum_{n}"]=x._ret.clip(lower=0).groupby(x.symbol).rolling(n,min_periods=n).sum().reset_index(level=0,drop=True); x[f"negative_return_sum_{n}"]=x._ret.clip(upper=0).groupby(x.symbol).rolling(n,min_periods=n).sum().reset_index(level=0,drop=True)
        x[f"volume_sum_{n}"]=g.volume.rolling(n,min_periods=n).sum().reset_index(level=0,drop=True); x[f"volume_mean_{n}"]=g.volume.rolling(n,min_periods=n).mean().reset_index(level=0,drop=True)
        x[f"relative_volume_{n}"]=x.volume/x[f"volume_mean_{n}"].replace(0,np.nan); x[f"dollar_volume_mean_{n}"]=(x.close*x.volume).groupby(x.symbol).rolling(n,min_periods=n).mean().reset_index(level=0,drop=True)
        x[f"largest_positive_bar_{n}"]=g._ret.rolling(n,min_periods=n).max().reset_index(level=0,drop=True); x[f"largest_negative_bar_{n}"]=g._ret.rolling(n,min_periods=n).min().reset_index(level=0,drop=True)
        high=g.high.rolling(n,min_periods=n).max().reset_index(level=0,drop=True); low=g.low.rolling(n,min_periods=n).min().reset_index(level=0,drop=True); prior_high=high.groupby(x.symbol).shift(); prior_low=low.groupby(x.symbol).shift()
        x[f"distance_rolling_high_{n}"]=x.close/high-1; x[f"distance_rolling_low_{n}"]=x.close/low-1; x[f"range_position_{n}"]=(x.close-low)/(high-low).replace(0,np.nan)
        range_mean=g._range.rolling(n,min_periods=n).mean().reset_index(level=0,drop=True); x[f"range_mean_{n}"]=range_mean; x[f"range_ratio_{n}"]=x._range/range_mean.replace(0,np.nan)
        x[f"breakout_magnitude_{n}"]=(x.close/prior_high-1).clip(lower=0); x[f"breakdown_magnitude_{n}"]=(x.close/prior_low-1).clip(upper=0)
        half=max(1,n//2); short_vol=g.volume.rolling(half,min_periods=half).mean().reset_index(level=0,drop=True); short_rv=g._ret.rolling(half,min_periods=max(1,half)).std().reset_index(level=0,drop=True)
        x[f"volume_acceleration_{n}"]=short_vol/x[f"volume_mean_{n}"].replace(0,np.nan)-1; x[f"volatility_acceleration_{n}"]=short_rv/rv.replace(0,np.nan)-1
        pv=(x.close*x.volume).groupby(x.symbol).rolling(n,min_periods=n).sum().reset_index(level=0,drop=True); vv=g.volume.rolling(n,min_periods=n).sum().reset_index(level=0,drop=True); x[f"rolling_vwap_distance_{n}"]=x.close/(pv/vv.replace(0,np.nan))-1
        x[f"volume_range_ratio_{n}"]=x.volume/(x._range.replace(0,np.nan)); x[f"return_volume_product_{n}"]=x._ret*x.volume; x[f"return_outlier_score_{n}"]=x._ret/rv.replace(0,np.nan)
        if not symbol_local:
            for base in ["return","relative_volume","realized_vol","range_position","return_vol_ratio"]: x[f"{base}_rank_{n}"]=x.groupby("bar_start_ts")[f"{base}_{n}"].rank(pct=True)
        for column in set(x.columns)-columns_before:
            if pd.api.types.is_float_dtype(x[column]): x[column]=x[column].astype(np.float32)
    daily=x.groupby(["symbol","session_date"],sort=False).agg(session_open=("open","first"),session_close=("close","last"))
    daily["previous_close"]=daily.groupby(level=0).session_close.shift()
    completed_return=daily.groupby(level=0).session_close.pct_change()
    # At any decision in session D, only returns through D-1 are known.
    # The unshifted value uses D's final close and is future leakage.
    daily["previous_session_return"]=completed_return.groupby(level=0).shift(1)
    x=x.join(daily[["previous_close","previous_session_return"]],on=["symbol","session_date"])
    x["overnight_gap"]=x._session_open/x.previous_close-1
    for sessions in [2,5]:
        if f"session_return_{sessions}" not in wanted: continue
        daily[f"session_return_{sessions}"]=daily.groupby(level=0).session_close.shift(1)/daily.groupby(level=0).session_close.shift(sessions+1)-1
        x=x.join(daily[[f"session_return_{sessions}"]],on=["symbol","session_date"])
    if not symbol_local and config.benchmark_symbol in set(x.symbol):
        market=x.loc[x.symbol.eq(config.benchmark_symbol),["bar_start_ts","_ret"]].rename(columns={"_ret":"market_return_1"}); x=x.merge(market,on="bar_start_ts",how="left"); x["stock_minus_market_return_1"]=x._ret-x.market_return_1
    elif not symbol_local: x["market_return_1"]=np.nan; x["stock_minus_market_return_1"]=np.nan
    if not symbol_local:
        x["universe_breadth_positive"]=(x._ret>0).groupby(x.bar_start_ts).transform("mean"); x["universe_return_dispersion"]=x.groupby("bar_start_ts")._ret.transform("std")
        for n in sorted(active_lookbacks):
            benchmark=x.loc[x.symbol.eq(config.benchmark_symbol),["bar_start_ts",f"return_{n}"]].drop_duplicates("bar_start_ts").set_index("bar_start_ts")[f"return_{n}"]
            x[f"market_return_{n}"]=x.bar_start_ts.map(benchmark); x[f"stock_minus_market_return_{n}"]=x[f"return_{n}"]-x[f"market_return_{n}"]
        x["market_up"]=(x.market_return_1>0).astype(float); market_vol=x.loc[x.symbol.eq(config.benchmark_symbol),["bar_start_ts","realized_vol_10"]].rename(columns={"realized_vol_10":"_market_vol"}) if "realized_vol_10" in x else pd.DataFrame()
        if not market_vol.empty:
            market_vol=market_vol.sort_values("bar_start_ts").drop_duplicates("bar_start_ts")
            market_vol["_market_vol_median"]=market_vol._market_vol.expanding(min_periods=100).median()
            x=x.merge(market_vol,on="bar_start_ts",how="left"); x["high_market_vol"]=(x._market_vol>x._market_vol_median).astype(float)
        else: x["high_market_vol"]=np.nan
    for minutes in config.opening_windows_minutes:
        if not any(name.endswith(f"_{minutes}m") and name.startswith("opening_") or name.startswith("distance_opening_") and name.endswith(f"_{minutes}m") for name in wanted): continue
        if minutes < 5:  # source is 5-minute bars; 1m/3m windows are not fabricated
            continue
        bars_n=max(1,int(np.ceil(minutes/5))); pos=x.groupby(["symbol","session_date"]).cumcount(); grp=x.groupby(["symbol","session_date"],sort=False)
        open0=grp.open.transform("first"); close_n=grp.close.transform(lambda s:s.iloc[min(bars_n-1,len(s)-1)]); high_n=grp.high.transform(lambda s:s.iloc[:bars_n].max()); low_n=grp.low.transform(lambda s:s.iloc[:bars_n].min()); vol_n=grp.volume.transform(lambda s:s.iloc[:bars_n].sum()); ret_n=grp._ret.transform(lambda s:s.iloc[:bars_n].std())
        valid=pos>=bars_n-1; prefix=f"_{minutes}m"; x[f"opening_return{prefix}"]=(close_n/open0-1).where(valid); x[f"opening_range{prefix}"]=((high_n-low_n)/open0).where(valid); x[f"opening_volume{prefix}"]=vol_n.where(valid); x[f"opening_realized_vol{prefix}"]=ret_n.where(valid); x[f"opening_close_location{prefix}"]=((close_n-low_n)/(high_n-low_n).replace(0,np.nan)).where(valid); x[f"distance_opening_high{prefix}"]=(x.close/high_n-1).where(valid); x[f"distance_opening_low{prefix}"]=(x.close/low_n-1).where(valid); x[f"opening_breakout{prefix}"]=(x.close>high_n).where(valid).astype(float); x[f"opening_breakdown{prefix}"]=(x.close<low_n).where(valid).astype(float)
    for sessions in [20,60]:
        if not ({f"tod_relative_volume_{sessions}",f"tod_cumulative_relative_volume_{sessions}"}&wanted): continue
        keys=[x.symbol,x.minutes_since_open]
        baseline=x.volume.groupby(keys,sort=False).transform(lambda s:s.shift().rolling(sessions,min_periods=max(5,sessions//4)).mean())
        cumulative_baseline=x.cumulative_volume.groupby(keys,sort=False).transform(lambda s:s.shift().rolling(sessions,min_periods=max(5,sessions//4)).mean())
        x[f"tod_relative_volume_{sessions}"]=x.volume/baseline.replace(0,np.nan); x[f"tod_cumulative_relative_volume_{sessions}"]=x.cumulative_volume/cumulative_baseline.replace(0,np.nan)
    if not symbol_local:
        breadth_change=x.groupby("bar_start_ts").universe_breadth_positive.first().diff(); dispersion_change=x.groupby("bar_start_ts").universe_return_dispersion.first().diff()
        for lag in sorted(active_lead_lags):
            x[f"lagged_market_return_{lag}"]=x.groupby("symbol").market_return_1.shift(lag); x[f"lagged_breadth_change_{lag}"]=x.bar_start_ts.map(breadth_change.shift(lag)); x[f"lagged_dispersion_change_{lag}"]=x.bar_start_ts.map(dispersion_change.shift(lag)); x[f"unreacted_market_move_{lag}"]=x[f"lagged_market_return_{lag}"]-x[f"return_{lag}"]
    for base in ["return_1","relative_volume_10","realized_vol_10","range_position_10","distance_session_vwap"]:
        if base not in x: continue
        for window in [1560,4680]:
            if not ({f"{base}_z_{window}",f"{base}_mean_ratio_{window}",f"{base}_median_diff_{window}"}&wanted): continue
            mean=x.groupby("symbol")[base].rolling(window,min_periods=max(100,window//5)).mean().reset_index(level=0,drop=True); std=x.groupby("symbol")[base].rolling(window,min_periods=max(100,window//5)).std().reset_index(level=0,drop=True); median=x.groupby("symbol")[base].rolling(window,min_periods=max(100,window//5)).median().reset_index(level=0,drop=True); x[f"{base}_z_{window}"]=(x[base]-mean)/std.replace(0,np.nan)
            if base in {"relative_volume_10","realized_vol_10","range_position_10"}: x[f"{base}_mean_ratio_{window}"]=x[base]/mean.replace(0,np.nan)
            x[f"{base}_median_diff_{window}"]=x[base]-median
    requested={s.name:s for s in specs}; built=[]
    for name,spec in requested.items():
        if spec.status=="requested" and name in x: built.append(spec)
    identifiers=["symbol","session_date","bar_start_ts","bar_end_ts","available_at_ts","open","close"]
    keep=identifiers+[s.name for s in built]
    # Projection is the memory boundary: temporary dependencies and unrelated
    # features never enter target construction or statistical scanning.
    result=x.loc[:,list(dict.fromkeys(keep))].copy()
    for column in [s.name for s in built]:
        if pd.api.types.is_float_dtype(result[column]): result[column]=result[column].astype(np.float32)
    return result, built


def _run_length(mask: pd.Series, symbols: pd.Series) -> pd.Series:
    blocks=(mask.ne(mask.shift())|symbols.ne(symbols.shift())).cumsum()
    return mask.astype(int).groupby(blocks).cumsum()
