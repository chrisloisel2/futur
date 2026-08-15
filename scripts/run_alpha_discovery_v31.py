#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from research.alpha_discovery_v31.pipeline import FEATURE_GROUPS,enrich,context,common_mask,add_costs,cost,fit_fold,summarize,make_year_fold
from scripts.run_alpha_discovery_v3 import load_symbol_frame
from data_v2.events.residuals import _causal_2factor_betas,_freeze_daily,BETA_WINDOW_BARS

PANEL_DIR=ROOT/'data_v2/normalized/event_feature_panel/venue=binance'; IM=ROOT/'data_v2/instruments/instrument_master.parquet'; OUT=ROOT/'reports/alpha_discovery_v31'

def betas_for_symbol(frame,btc,eth):
    if frame['symbol'].iloc[0] in ('BTCUSDT','ETHUSDT') if 'symbol' in frame else False:
        return pd.Series(0.,index=frame.index),pd.Series(0.,index=frame.index)
    idx=pd.DatetimeIndex(pd.to_datetime(frame.timestamp,utc=True)); y=pd.Series(np.log(pd.to_numeric(frame.close,errors='coerce')/pd.to_numeric(frame.close,errors='coerce').shift(12)).to_numpy(),index=idx)
    br=pd.Series(np.log(pd.to_numeric(btc.close,errors='coerce')/pd.to_numeric(btc.close,errors='coerce').shift(12)).to_numpy(),index=pd.DatetimeIndex(pd.to_datetime(btc.timestamp,utc=True))).reindex(idx)
    er=pd.Series(np.log(pd.to_numeric(eth.close,errors='coerce')/pd.to_numeric(eth.close,errors='coerce').shift(12)).to_numpy(),index=pd.DatetimeIndex(pd.to_datetime(eth.timestamp,utc=True))).reindex(idx)
    b1,b2=_causal_2factor_betas(y,br,er,BETA_WINDOW_BARS,BETA_WINDOW_BARS); return _freeze_daily(b1).reset_index(drop=True),_freeze_daily(b2).reset_index(drop=True)

def add_hedged_costs(d,symbol,ticks,btc,eth):
    o=add_costs(d,ticks.get(symbol))
    if symbol in ('BTCUSDT','ETHUSDT'): o['beta_btc']=0.;o['beta_eth']=0.;o['hedge_gross_notional']=1.;return o
    raw=load_symbol_frame(symbol); b1,b2=betas_for_symbol(raw.assign(symbol=symbol),btc,eth); b1=b1.reindex(o.index).fillna(0).abs();b2=b2.reindex(o.index).fillna(0).abs()
    btc_by_ts=btc.set_index('timestamp');eth_by_ts=eth.set_index('timestamp'); ts=pd.to_datetime(o.timestamp,utc=True)
    bclose=pd.Series(ts.map(btc_by_ts.close),index=o.index);eclose=pd.Series(ts.map(eth_by_ts.close),index=o.index)
    bopen=pd.Series(ts.map(btc_by_ts.open.shift(-1)),index=o.index);eopen=pd.Series(ts.map(eth_by_ts.open.shift(-1)),index=o.index)
    o['decision_cost_x1']=o.decision_cost_x1+b1*cost(bclose,ticks.get('BTCUSDT'))+b2*cost(eclose,ticks.get('ETHUSDT'))
    o['realized_cost_x1']=o.realized_cost_x1+b1*cost(bopen,ticks.get('BTCUSDT'))+b2*cost(eopen,ticks.get('ETHUSDT'))
    o['decision_cost_x2']=2*o.decision_cost_x1;o['realized_cost_x2']=2*o.realized_cost_x1;o['beta_btc']=b1;o['beta_eth']=b2;o['hedge_gross_notional']=1+b1+b2
    return o

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--symbols');ap.add_argument('--test-years',default='2023,2024,2025,2026');ap.add_argument('--background-hours',type=int,default=4);ap.add_argument('--stress-threshold',type=float,default=2.0);ap.add_argument('--selection-quantile',type=float,default=.95);ap.add_argument('--max-train-rows',type=int,default=600000);ap.add_argument('--max-calib-rows',type=int,default=300000);ap.add_argument('--max-test-rows',type=int,default=600000);ap.add_argument('--out',default=str(OUT/'RESULTS.json'));a=ap.parse_args()
    allsyms=sorted(p.name.split('=',1)[1] for p in PANEL_DIR.glob('symbol=*') if p.is_dir()); syms=allsyms if not a.symbols else [x.strip() for x in a.symbols.split(',') if x.strip()]
    im=pd.read_parquet(IM);ticks=dict(zip(im.symbol,pd.to_numeric(im.tick_size,errors='coerce')))
    btc0=load_symbol_frame('BTCUSDT');eth0=load_symbol_frame('ETHUSDT')
    if btc0 is None or eth0 is None: raise SystemExit('BTC/ETH context missing')
    btc=enrich(btc0);eth=enrich(eth0);ctx=context(btc,eth); parts=[]; feats=sorted(set(sum(FEATURE_GROUPS.values(),[])))
    for i,s in enumerate(syms,1):
        f=load_symbol_frame(s)
        if f is None or f.empty:continue
        e=enrich(f);e=e.merge(ctx,on='timestamp',how='left',validate='many_to_one');e=add_hedged_costs(e,s,ticks,btc0,eth0);m=common_mask(e,a.stress_threshold,a.background_hours)
        cols=['timestamp','target_residual_ret_1h','target_standardized_1h','ex_ante_sigma_1h','decision_cost_x1','decision_cost_x2','realized_cost_x1','realized_cost_x2','beta_btc','beta_eth','hedge_gross_notional']+feats; cols=[c for c in cols if c in e];x=e.loc[m,cols].copy();x['symbol']=s;parts.append(x);print(f'[{i:3}/{len(syms)}] {s:14} rows={len(e):8,d} candidates={len(x):7,d}',flush=True)
    if not parts:raise SystemExit('No candidate rows')
    d=pd.concat(parts,ignore_index=True).sort_values('timestamp').reset_index(drop=True);years=[int(x) for x in a.test_years.split(',')]
    result={'protocol':{'version':'3.1','target':'next-bar residual 1h standardized by strict-prior 7d volatility','common_sampling':'A-only fixed cadence + first core stress crossing','selection':'rolling causal predicted gross edge minus decision-time x1 cost','costs':'main leg + abs(beta) BTC/ETH hedge legs; realized at next-open; x2 stress','feature_groups':FEATURE_GROUPS,'test_years':years},'dataset':{'rows':len(d),'symbols':int(d.symbol.nunique()),'start':str(d.timestamp.min()),'end':str(d.timestamp.max())},'groups':{}}
    for g,features in FEATURE_GROUPS.items():
        fs=[]
        for y in years:
            r=fit_fold(d,features,make_year_fold(d.timestamp,y),a.max_train_rows,a.max_calib_rows,a.max_test_rows,a.selection_quantile);fs.append(r);print(g,y,r.get('status'),'IC=',r.get('ic_spearman'),'N=',r.get('n'),'netx1=',r.get('net_x1_mean'),'netx2=',r.get('net_x2_mean'),flush=True)
        result['groups'][g]={'folds':fs,'summary':summarize(fs)}
    A=result['groups']['A_CORE']['summary'];B=result['groups']['B_NORMALIZED']['summary'];C=result['groups']['C_STATE']['summary'];result['ablation']={'B_minus_A_IC':B.get('median_ic_spearman',np.nan)-A.get('median_ic_spearman',np.nan),'C_minus_B_IC':C.get('median_ic_spearman',np.nan)-B.get('median_ic_spearman',np.nan),'C_minus_A_net_x1':C.get('pooled_net_x1_mean',np.nan)-A.get('pooled_net_x1_mean',np.nan),'rule':'No tuning from test results; full run only after smoke passes invariants.'}
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,default=lambda x:None if isinstance(x,float) and not np.isfinite(x) else x));print('Wrote',out)
if __name__=='__main__':main()
