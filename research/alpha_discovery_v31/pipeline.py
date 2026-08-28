from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

H=12; DAY=288; WEEK=7*DAY; SQRT12=float(np.sqrt(12))
CORE=["residual_z_1h","residual_return_15m","residual_accel_1h","ex_ante_sigma_1h","oi_delta_pct_1h","oi_delta_z_1h","flow_imbalance","flow_imbalance_z","flow_accel_1h","basis_z_1d","basis_curve","funding_centered_rank","volume_log_z"]
RAW=["oi_value_log_z","toptrader_count_log_z","toptrader_sum_log_z","global_ls_log_z","taker_ls_log_z","trade_count_log_z","large_trade_imbalance","large_trade_share","large_trade_imbalance_z","avg_trade_size_log_z","p95_trade_size_log_z","trade_size_tail_ratio","vwap_pressure_bps"]
STATE=["cvd_velocity_z_1h","cvd_accel_z_1h","oi_flow_interaction","residual_flow_interaction","crowding_interaction","forced_flow_interaction","ctx_btc_residual_z_1h","ctx_btc_oi_delta_z_1h","ctx_btc_flow_imbalance_z","ctx_btc_basis_z_1d","ctx_btc_ex_ante_sigma_1h","ctx_eth_residual_z_1h","ctx_eth_oi_delta_z_1h","ctx_eth_flow_imbalance_z","ctx_eth_basis_z_1d","ctx_eth_ex_ante_sigma_1h","market_stress_mean","market_stress_spread"]
FEATURE_GROUPS={"A_CORE":CORE,"B_NORMALIZED":CORE+RAW,"C_STATE":CORE+RAW+STATE}

@dataclass(frozen=True)
class Fold:
    test_year:int; fit_mask:np.ndarray; calib_mask:np.ndarray; test_mask:np.ndarray

def unique_names(names):
    """Preserve order while removing duplicate requested columns."""
    return list(dict.fromkeys(names))

def require_unique_columns(d,where="frame"):
    """Fail closed if an upstream join/selection produced duplicate labels."""
    dup=d.columns[d.columns.duplicated()].unique().tolist()
    if dup:
        raise ValueError(f"{where} contains duplicate column names: {dup}")

def build_forward_target(logret_5m,horizon_bars=H):
    parts=[num(logret_5m).shift(-k) for k in range(1,horizon_bars+1)];m=pd.concat(parts,axis=1);ok=m.notna().all(axis=1);return pd.Series(np.expm1(m.sum(axis=1,min_count=horizon_bars)),index=logret_5m.index).where(ok),ok

def make_year_fold(timestamps,test_year,calibration_months=6,embargo_hours=8):
    ts=pd.to_datetime(timestamps,utc=True);start=pd.Timestamp(f"{test_year}-01-01",tz="UTC");end=pd.Timestamp(f"{test_year+1}-01-01",tz="UTC");cal=start-pd.DateOffset(months=calibration_months);emb=start-pd.Timedelta(hours=embargo_hours);return Fold(test_year,(ts<cal).to_numpy(),((ts>=cal)&(ts<emb)).to_numpy(),((ts>=start)&(ts<end)).to_numpy())

def profit_factor(r):
    x=pd.Series(r).dropna();w=x[x>0].sum();l=-x[x<0].sum();return float(w/l) if l>0 else (float("inf") if w>0 else np.nan)

def num(s): return pd.to_numeric(s,errors="coerce")
def z(s,w=WEEK,mp=DAY):
    x=num(s); h=x.shift(1); m=h.rolling(w,min_periods=mp).mean(); sd=h.rolling(w,min_periods=mp).std(ddof=0)
    return (x-m)/sd.replace(0,np.nan)
def logz(s): return z(np.log1p(num(s).clip(lower=0)))
def ratioz(s):
    x=num(s); return z(np.log(x.where(x>0)))

def enrich(frame):
    require_unique_columns(frame,"raw symbol frame")
    d=frame.sort_values("timestamp").reset_index(drop=True).copy()
    need=["open","close","residual_logret_5m","residual_return_15m","residual_return_1h","residual_std_30d","oi_delta_pct_1h","aggressive_buy_usd","aggressive_sell_usd","CVD","funding_rate_percentile_90d","basis_z_1d","basis_z_7d","volume","sum_open_interest_value","count_toptrader_long_short_ratio","sum_toptrader_long_short_ratio","count_long_short_ratio","sum_taker_long_short_vol_ratio","trade_count","large_trade_buy_usd","large_trade_sell_usd","avg_trade_size_usd","p95_trade_size_usd","buy_vwap","sell_vwap"]
    for c in need:
        if c not in d: d[c]=np.nan
    lr=num(d.residual_logret_5m); hist=lr.shift(1); d["ex_ante_sigma_1h"]=hist.rolling(WEEK,min_periods=DAY).std(ddof=0)*SQRT12
    denom=num(d.residual_std_30d).replace(0,np.nan).fillna(d.ex_ante_sigma_1h)
    d["residual_z_1h"]=num(d.residual_return_1h)/denom
    d["residual_accel_1h"]=num(d.residual_return_1h)-num(d.residual_return_1h).shift(H)
    d["oi_delta_z_1h"]=z(d.oi_delta_pct_1h)
    buy,sell=num(d.aggressive_buy_usd),num(d.aggressive_sell_usd); flow=buy+sell
    d["flow_imbalance"]=(buy-sell)/flow.replace(0,np.nan); d["flow_imbalance_z"]=z(d.flow_imbalance); d["flow_accel_1h"]=d.flow_imbalance-d.flow_imbalance.shift(H)
    d["basis_curve"]=num(d.basis_z_1d)-num(d.basis_z_7d); d["funding_centered_rank"]=num(d.funding_rate_percentile_90d)-.5; d["volume_log_z"]=logz(d.volume)
    d["oi_value_log_z"]=logz(d.sum_open_interest_value); d["toptrader_count_log_z"]=ratioz(d.count_toptrader_long_short_ratio); d["toptrader_sum_log_z"]=ratioz(d.sum_toptrader_long_short_ratio); d["global_ls_log_z"]=ratioz(d.count_long_short_ratio); d["taker_ls_log_z"]=ratioz(d.sum_taker_long_short_vol_ratio); d["trade_count_log_z"]=logz(d.trade_count)
    lb,ls=num(d.large_trade_buy_usd),num(d.large_trade_sell_usd); lt=lb+ls
    d["large_trade_imbalance"]=(lb-ls)/lt.replace(0,np.nan); d["large_trade_share"]=lt/flow.replace(0,np.nan); d["large_trade_imbalance_z"]=z(d.large_trade_imbalance); d["avg_trade_size_log_z"]=logz(d.avg_trade_size_usd); d["p95_trade_size_log_z"]=logz(d.p95_trade_size_usd); d["trade_size_tail_ratio"]=num(d.p95_trade_size_usd)/num(d.avg_trade_size_usd).replace(0,np.nan)
    mid=(num(d.buy_vwap)+num(d.sell_vwap))/2; d["vwap_pressure_bps"]=1e4*(num(d.buy_vwap)-num(d.sell_vwap))/mid.replace(0,np.nan)
    vel=num(d.CVD).diff().rolling(H,min_periods=H).sum(); d["cvd_velocity_z_1h"]=z(vel); d["cvd_accel_z_1h"]=z(vel-vel.shift(H))
    d["oi_flow_interaction"]=d.oi_delta_z_1h*d.flow_imbalance_z; d["residual_flow_interaction"]=d.residual_z_1h*d.flow_imbalance_z; d["crowding_interaction"]=d.funding_centered_rank*num(d.basis_z_1d)*d.oi_delta_z_1h; d["forced_flow_interaction"]=-d.residual_z_1h*-d.oi_delta_z_1h*-d.flow_imbalance_z
    d["common_stress_score"]=pd.concat([d.residual_z_1h.abs(),d.oi_delta_z_1h.abs(),d.flow_imbalance_z.abs(),num(d.basis_z_1d).abs()],axis=1).max(axis=1,skipna=True)
    target,complete=build_forward_target(lr,H); d["target_residual_ret_1h"]=target; d["target_path_complete_1h"]=complete; d["target_standardized_1h"]=target/d.ex_ante_sigma_1h.replace(0,np.nan); d["entry_price"]=num(d.open).shift(-1)
    return d

def context(btc,eth):
    cols=["timestamp","residual_z_1h","oi_delta_z_1h","flow_imbalance_z","basis_z_1d","ex_ante_sigma_1h","common_stress_score"]
    b=btc[cols].rename(columns={c:"ctx_btc_"+c for c in cols if c!="timestamp"}); e=eth[cols].rename(columns={c:"ctx_eth_"+c for c in cols if c!="timestamp"})
    c=b.merge(e,on="timestamp",how="outer",validate="one_to_one").sort_values("timestamp"); bs=num(c.ctx_btc_common_stress_score); es=num(c.ctx_eth_common_stress_score); c["market_stress_mean"]=pd.concat([bs,es],axis=1).mean(axis=1); c["market_stress_spread"]=bs-es
    return c.drop(columns=["ctx_btc_common_stress_score","ctx_eth_common_stress_score"])

def common_mask(d,stress=2.,background_hours=4):
    ts=pd.to_datetime(d.timestamp,utc=True); bg=(ts.dt.minute==0)&(ts.dt.hour%background_hours==0); x=num(d.common_stress_score)>=stress; cross=x&~x.shift(1,fill_value=False)
    return (bg|cross)&d.target_path_complete_1h.fillna(False)&d.ex_ante_sigma_1h.gt(0)

def cost(price,tick,fee=.0005):
    p=num(price); return (2*fee+2*float(tick)/p).where(p>0) if tick is not None and np.isfinite(tick) else pd.Series(np.nan,index=p.index)

def add_costs(d,tick,fee=.0005):
    o=d.copy(); o["decision_cost_x1"]=cost(o.close,tick,fee); o["realized_cost_x1"]=cost(o.entry_price,tick,fee); o["decision_cost_x2"]=2*o.decision_cost_x1; o["realized_cost_x2"]=2*o.realized_cost_x1; return o

def balanced_cap(d,n,seed):
    if len(d)<=n:return d.copy()
    w=d.copy(); w["__y"]=pd.to_datetime(w.timestamp,utc=True).dt.year; groups=list(w.groupby(["symbol","__y"],sort=True)); q=max(1,n//len(groups)); rng=np.random.default_rng(seed); parts=[]
    for _,g in groups:
        parts.append(g if len(g)<=q else g.iloc[np.sort(rng.choice(len(g),q,replace=False))])
    return pd.concat(parts,ignore_index=True).drop(columns="__y").sort_values("timestamp").reset_index(drop=True)

def spearman(p,y):
    p,y=pd.Series(p),pd.Series(y); v=p.notna()&y.notna(); return float(p[v].rank().corr(y[v].rank())) if v.sum()>2 else np.nan

def select_rolling(cal_ts,cal_edge,test_ts,test_edge,q=.95,days=30,min_hist=200):
    ct,tt=pd.to_datetime(cal_ts,utc=True).reset_index(drop=True),pd.to_datetime(test_ts,utc=True).reset_index(drop=True); ce,te=np.asarray(cal_edge,float),np.asarray(test_edge,float); base=max(0.,float(np.nanquantile(ce[np.isfinite(ce)],q))) if np.isfinite(ce).any() else 0.
    allts=pd.concat([ct,tt],ignore_index=True); s=pd.Series(np.r_[ce,te],index=pd.DatetimeIndex(allts)); thr=s.rolling(f"{days}D",closed="left",min_periods=min_hist).quantile(q).iloc[len(ce):].to_numpy(float); thr=np.where(np.isfinite(thr),np.maximum(thr,0),base); thr=np.maximum(thr,base*.5); return np.isfinite(te)&(te>0)&(te>=thr),thr

def fit_fold(d,features,fold,max_train=600000,max_cal=300000,max_test=600000,q=.95,seed=31):
    from sklearn.ensemble import HistGradientBoostingRegressor
    require_unique_columns(d,"alpha discovery dataset")
    features=unique_names(features)
    cols=unique_names(features+["timestamp","symbol","target_residual_ret_1h","target_standardized_1h","ex_ante_sigma_1h","decision_cost_x1","realized_cost_x1","realized_cost_x2"])
    fit,cal,test=[d.loc[m,cols].replace([np.inf,-np.inf],np.nan).dropna(subset=["target_standardized_1h","ex_ante_sigma_1h"]) for m in [fold.fit_mask,fold.calib_mask,fold.test_mask]]
    if min(len(fit),len(cal),len(test))<200:return {"test_year":fold.test_year,"status":"INSUFFICIENT_ROWS","n_fit":len(fit),"n_calib":len(cal),"n_test":len(test)}
    used=[c for c in features if c in fit.columns and fit[c].notna().mean()>=.10]
    if not used:return {"test_year":fold.test_year,"status":"NO_USABLE_FEATURES"}
    fit,cal,test=balanced_cap(fit,max_train,seed+fold.test_year),balanced_cap(cal,max_cal,seed+100+fold.test_year),balanced_cap(test,max_test,seed+200+fold.test_year)
    model=HistGradientBoostingRegressor(learning_rate=.04,max_iter=160,max_leaf_nodes=15,min_samples_leaf=80,l2_regularization=2.,random_state=seed); model.fit(fit[used],fit.target_standardized_1h)
    cp=model.predict(cal[used])*cal.ex_ante_sigma_1h.to_numpy(); tp=model.predict(test[used])*test.ex_ante_sigma_1h.to_numpy(); ce=np.abs(cp)-cal.decision_cost_x1.to_numpy(); te=np.abs(tp)-test.decision_cost_x1.to_numpy(); sel,thr=select_rolling(cal.timestamp,ce,test.timestamp,te,q=q)
    valid=np.asarray(sel,bool)&np.isfinite(test.realized_cost_x1.to_numpy())&np.isfinite(test.realized_cost_x2.to_numpy()); gross=np.sign(tp[valid])*test.target_residual_ret_1h.to_numpy()[valid]; n1=gross-test.realized_cost_x1.to_numpy()[valid]; n2=gross-test.realized_cost_x2.to_numpy()[valid]; shares=test.symbol.reset_index(drop=True).iloc[np.flatnonzero(valid)].value_counts(normalize=True)
    return {"test_year":fold.test_year,"status":"OK","n":int(valid.sum()),"selection_rate":float(valid.mean()),"gross_mean":float(np.mean(gross)) if len(gross) else np.nan,"net_x1_mean":float(np.mean(n1)) if len(n1) else np.nan,"net_x2_mean":float(np.mean(n2)) if len(n2) else np.nan,"pf_x1":profit_factor(pd.Series(n1)),"pf_x2":profit_factor(pd.Series(n2)),"ic_spearman":spearman(tp,test.target_residual_ret_1h.to_numpy()),"max_symbol_share":float(shares.max()) if len(shares) else np.nan,"symbol_hhi":float((shares**2).sum()) if len(shares) else np.nan,"median_selector_threshold_bps":float(np.nanmedian(thr)*1e4),"selected_expected_edge_bps":float(np.nanmean(te[valid])*1e4) if valid.any() else np.nan,"n_fit":len(fit),"n_calib":len(cal),"n_test":len(test),"used_features":used}

def summarize(fs):
    ok=[f for f in fs if f.get("status")=="OK"]; n=sum(f.get("n",0) for f in ok)
    if not ok:return {"status":"NO_VALID_FOLDS"}
    def wm(k):
        a=[(f.get(k,np.nan),f.get("n",0)) for f in ok if np.isfinite(f.get(k,np.nan)) and f.get("n",0)>0]; return float(sum(v*n for v,n in a)/sum(n for _,n in a)) if a else np.nan
    return {"status":"OK","folds_ok":len(ok),"selected_n":int(n),"median_ic_spearman":float(np.nanmedian([f.get("ic_spearman",np.nan) for f in ok])),"median_pf_x1":float(np.nanmedian([f.get("pf_x1",np.nan) for f in ok])),"median_pf_x2":float(np.nanmedian([f.get("pf_x2",np.nan) for f in ok])),"median_selection_rate":float(np.nanmedian([f.get("selection_rate",np.nan) for f in ok])),"median_max_symbol_share":float(np.nanmedian([f.get("max_symbol_share",np.nan) for f in ok])),"pooled_gross_mean":wm("gross_mean"),"pooled_net_x1_mean":wm("net_x1_mean"),"pooled_net_x2_mean":wm("net_x2_mean"),"positive_net_x1_years":sum(f.get("net_x1_mean",np.nan)>0 for f in ok),"positive_net_x2_years":sum(f.get("net_x2_mean",np.nan)>0 for f in ok)}
