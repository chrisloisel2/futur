import numpy as np,pandas as pd,pytest
from research.alpha_discovery_v31.pipeline import z,enrich,common_mask,context,add_costs,balanced_cap,select_rolling,fit_fold,make_year_fold,unique_names

def frame(n=3000,start='2023-01-01'):
    ts=pd.date_range(start,periods=n,freq='5min',tz='UTC');x=100*np.exp(np.cumsum(np.full(n,1e-5)))
    return pd.DataFrame({'timestamp':ts,'open':x,'close':x*1.0001,'residual_logret_5m':1e-4,'residual_return_15m':3e-4,'residual_return_1h':1.2e-3,'residual_std_30d':.01,'oi':1000+np.arange(n),'oi_delta_pct_1h':.01,'aggressive_buy_usd':120.,'aggressive_sell_usd':80.,'signed_volume':40.,'CVD':np.arange(n)*40.,'funding_rate':1e-4,'funding_rate_percentile_90d':.7,'basis':.001,'basis_z_1d':1.,'basis_z_7d':.5,'volume':1000.,'sum_open_interest_value':1e6+np.arange(n),'count_toptrader_long_short_ratio':1.1,'sum_toptrader_long_short_ratio':1.2,'count_long_short_ratio':1.05,'sum_taker_long_short_vol_ratio':1.1,'trade_count':100.,'large_trade_buy_usd':30.,'large_trade_sell_usd':10.,'avg_trade_size_usd':50.,'p95_trade_size_usd':200.,'buy_vwap':100.1,'sell_vwap':99.9})

def test_z_strict_prior():
    s=pd.Series([1.,2.,3.,4.]+[1.]*300);a=z(s,3,3);s2=s.copy();s2.iloc[3]=20.;b=z(s2,3,3);assert b.iloc[3]>a.iloc[3]

def test_target_excludes_current_bar_and_sigma_prior():
    d=enrich(frame());assert np.isclose(d.target_residual_ret_1h.iloc[0],np.expm1(.0012));assert d.ex_ante_sigma_1h.iloc[:288].isna().all()

def test_common_sampling_independent_of_raw_features():
    a=enrich(frame());m1=common_mask(a);b=frame()
    for c in ['large_trade_buy_usd','large_trade_sell_usd','trade_count','sum_open_interest_value']:b[c]=np.random.default_rng(3).normal(size=len(b))*1e9
    m2=common_mask(enrich(b));assert np.array_equal(m1.to_numpy(),m2.to_numpy())

def test_context_exact_timestamp():
    b=enrich(frame());e=enrich(frame());c=context(b,e);assert len(c)==len(b);assert 'ctx_btc_residual_z_1h' in c and 'market_stress_mean' in c

def test_cost_selection_uses_decision_price_not_entry():
    raw=frame();raw.loc[100,'open']=10000.;d=enrich(raw);o=add_costs(d,.1);assert np.isclose(o.decision_cost_x1.iloc[99],2*.0005+2*.1/d.close.iloc[99]);assert o.realized_cost_x1.iloc[99] < o.decision_cost_x1.iloc[99]

def test_balanced_cap_reduces_dominant_symbol():
    a=pd.DataFrame({'timestamp':pd.date_range('2023-01-01',periods=1000,freq='h',tz='UTC'),'symbol':'A','x':1});b=pd.DataFrame({'timestamp':pd.date_range('2023-01-01',periods=100,freq='h',tz='UTC'),'symbol':'B','x':1});o=balanced_cap(pd.concat([a,b]),200,1);assert o.symbol.value_counts().max()<=110

def test_selector_requires_positive_edge():
    ct=pd.Series(pd.date_range('2023-01-01',periods=500,freq='h',tz='UTC'));tt=pd.Series(pd.date_range('2024-01-01',periods=10,freq='h',tz='UTC'));sel,_=select_rolling(ct,np.linspace(-.01,.01,500),tt,np.array([-.1]*5+[.2]*5),q=.9);assert not sel[:5].any();assert sel[5:].all()

def test_fold_chronology():
    ts=pd.Series(pd.date_range('2021-01-01','2024-12-31',freq='D',tz='UTC'));f=make_year_fold(ts,2024);assert ts[f.fit_mask].max()<ts[f.calib_mask].min()<ts[f.test_mask].min()

def test_unique_names_removes_runner_collision():
    cols=unique_names(['timestamp','ex_ante_sigma_1h','ex_ante_sigma_1h','volume']);assert cols==['timestamp','ex_ante_sigma_1h','volume']

def test_fit_fold_rejects_duplicate_input_columns():
    ts=pd.date_range('2020-01-01','2024-12-31 23:00',freq='h',tz='UTC');n=len(ts);base=pd.DataFrame({'timestamp':ts,'symbol':'A','f':np.arange(n,dtype=float),'target_residual_ret_1h':.001,'target_standardized_1h':.1,'ex_ante_sigma_1h':.01,'decision_cost_x1':.0005,'realized_cost_x1':.0005,'realized_cost_x2':.001});dup=pd.concat([base,base[['f']]],axis=1)
    with pytest.raises(ValueError,match='duplicate column names'):fit_fold(dup,['f'],make_year_fold(base.timestamp,2024))

def test_netaware_model_recovers_large_signal():
    rng=np.random.default_rng(4);ts=pd.date_range('2020-01-01','2024-12-31 23:00',freq='h',tz='UTC');n=len(ts);sig=rng.normal(size=n);target=.004*sig+rng.normal(scale=.001,size=n);d=pd.DataFrame({'timestamp':ts,'symbol':np.where(np.arange(n)%2,'A','B'),'f':sig,'target_residual_ret_1h':target,'target_standardized_1h':target/.01,'ex_ante_sigma_1h':.01,'decision_cost_x1':.0005,'realized_cost_x1':.0005,'realized_cost_x2':.001});r=fit_fold(d,['f'],make_year_fold(d.timestamp,2024),max_train=20000,max_cal=5000,max_test=9000,q=.8);assert r['status']=='OK' and r['ic_spearman']>.5 and r['net_x2_mean']>0
