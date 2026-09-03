"""W7 round4 — DECLUSTERING FIX for multi-asset expressions.

Bug found in my own first pass: gate._episodes() walks columns independently, so a
dollar-neutral BTC/ETH pair produced 2 'episodes' per actual trade, and a 40-name alt basket
produced 40 'independent' observations per calendar day -- exactly the failure mode the
briefing (§1.2) says burned 4 workers at round 2. Correlated legs of ONE position are ONE
observation. Fix: collapse every multi-asset expression to a single synthetic instrument
(one weighted return stream) BEFORE the gate, so L1 = L2 = calendar days and L3 = contiguous
regime episodes. All multi-asset numbers below supersede the per-asset ones.
"""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, two_arm, causal_z
from prep import load_all, uniform_position, sign_from_first_half
D = os.path.dirname(os.path.abspath(__file__))
opt, dvol, px, ret, fwd = load_all()
res, diag = {}, {}

def collapse(weights: pd.DataFrame, fwdmat: pd.DataFrame, name="BASKET"):
    """One synthetic instrument: unit gross exposure, its realised forward return, and the
    turnover of the underlying legs preserved so the cost charge stays honest."""
    w = weights.fillna(0.0); f = fwdmat.reindex(w.index)
    gross_expo = w.abs().sum(axis=1)
    port_ret = (w*f).sum(axis=1)                       # already in return units
    unit_ret = port_ret.div(gross_expo.replace(0, np.nan))
    pos = (gross_expo > 0).astype(float)*np.sign(port_ret.where(gross_expo > 0, 0.0)).replace(0, 1.0)
    # position sign is meaningless for a neutral basket; keep +1 whenever on, carry the pnl in ret
    pos = (gross_expo > 0).astype(float)
    leg_turnover = w.diff().abs().sum(axis=1).fillna(w.abs().sum(axis=1))
    return pd.DataFrame({name: pos}), pd.DataFrame({name: unit_ret.fillna(0.0)}), leg_turnover

def gate_collapsed(weights, fwdmat, name, notes, extra=None):
    pos, r, turn = collapse(weights, fwdmat)
    out = run_gate(pos, r, name, notes=notes, extra=extra)
    # re-charge cost on TRUE leg turnover instead of the synthetic |dpos|
    gross_daily = (weights.fillna(0.0)*fwdmat.reindex(weights.index)*1e4).sum(axis=1)
    for tag, c in [("", 7.0), ("_stress", 14.0)]:
        net_daily = gross_daily - turn*c
        out[f"portfolio_net_bps_per_day{tag}"] = round(float(net_daily.mean()), 3)
        out[f"portfolio_sharpe{tag}"] = round(float(net_daily.mean()/net_daily.std(ddof=1)*np.sqrt(365.25)), 3)
    on = (weights.abs().sum(axis=1) > 0)
    st = on.ne(on.shift()).cumsum()[on]
    ep = (gross_daily - turn*7.0)[on].groupby(st).sum()
    out["n_independent_L3"] = int(len(ep)); out["L3_unit"] = "contiguous regime episode (basket = ONE observation)"
    out["n_independent_L1"] = out["n_independent_L2"] = int(on.sum())
    out["net_bps"] = round(float(ep.mean()), 2)
    out["t_stat_declustered"] = round(float(ep.mean()/(ep.std(ddof=1)/np.sqrt(len(ep)))), 2) if len(ep) > 2 else None
    rng = np.random.default_rng(20260903)
    bs = np.array([ep.values[rng.integers(0, len(ep), len(ep))].mean() for _ in range(5000)])
    out["bootstrap_ci95"] = [round(float(np.percentile(bs, 2.5)), 2), round(float(np.percentile(bs, 97.5)), 2)]
    epy = pd.Series(ep.values, index=[weights.index[on][st.values == s][0] for s in ep.index])
    out["year_by_year"] = {int(y): {"n_ep": int(len(g)), "net_bps": round(float(g.mean()), 2),
                                    "total_bps": round(float(g.sum()), 1)} for y, g in epy.groupby(epy.index.year)}
    if len(out["year_by_year"]) >= 2:
        best = max(out["year_by_year"], key=lambda y: out["year_by_year"][y]["total_bps"])
        m = epy.index.year != best
        if m.sum() >= 3:
            out["ex_best_year"] = {"dropped_year": int(best), "n_ep": int(m.sum()),
                                   "net_bps": round(float(epy[m].mean()), 2),
                                   "t": round(float(epy[m].mean()/(epy[m].std(ddof=1)/np.sqrt(m.sum()))), 2)}
    from gate import ZSQ
    if len(ep) > 2 and ep.std(ddof=1) > 0 and abs(ep.mean()) > 1e-9:
        nreq = ZSQ*(ep.std(ddof=1)/(0.5*abs(ep.mean())))**2
        last6 = weights.index.max() - pd.Timedelta(days=182)
        rate = sum(1 for d in epy.index if d >= last6)/26.0
        out["n_required"] = round(float(nreq), 1)
        out["event_rate_per_week_last6m"] = round(rate, 3)
        out["eta_forward_confirmation_days"] = round(nreq/rate*7, 1) if rate > 0 else "inf"
        out["eta_forward_confirmation_years"] = round(nreq/rate*7/365.25, 2) if rate > 0 else "inf"
    return out

# ---------- M6 pair, collapsed ----------
zratio = causal_z(dvol["dvol_eth"]/dvol["dvol_btc"], 252, 60)
cm = zratio.dropna().index.intersection(fwd.index)
pf = pd.DataFrame({"BTCUSDT": fwd["BTCUSDT"].reindex(cm), "ETHUSDT": fwd["ETHUSDT"].reindex(cm)})
leg = 0.5*(pf["BTCUSDT"] - pf["ETHUSDT"])
raw_pos, _ = uniform_position(zratio.reindex(cm), cm)
sgn, cut = sign_from_first_half(raw_pos, leg)
w = pd.DataFrame({"BTCUSDT": 0.5*sgn*raw_pos, "ETHUSDT": -0.5*sgn*raw_pos}).loc[cut:]
res["M6_dvol_ratio_pair_COLLAPSED"] = gate_collapsed(w, pf.reindex(w.index),
    "M6 — DVOL_ETH/DVOL_BTC -> BTC/ETH dollar-neutral pair (ONE instrument, correct declustering)",
    f"Supersedes the per-asset count. Sign from first half ({sgn:+.0f}), OOS from {cut.date()}.",
    extra={"sign_learned": sgn, "oos_start": str(cut.date())})

# ---------- M5 basket + tilt, collapsed ----------
rv30 = ret["BTCUSDT"].rolling(30, min_periods=20).std()*np.sqrt(365.25)*100.0
vrp = (dvol["dvol_btc"] - rv30).dropna()
common = vrp.index.intersection(fwd.index)
alts = [c for c in px.columns if c != "BTCUSDT"]
_, vrp_pct = uniform_position(vrp.reindex(common), common)
bw = pd.DataFrame(1.0/len(alts), index=common, columns=alts)
fa = fwd[alts].reindex(common)
res["M5_high_vrp_long_basket_COLLAPSED"] = gate_collapsed(
    bw.mul((vrp_pct > 0.80).reindex(common).fillna(False).astype(float), axis=0), fa,
    "M5 — equal-weight alt basket LONG while VRP in top quintile (ONE instrument)",
    "Supersedes F2_M5_high_vrp_long, which counted 40 correlated alts as 40 observations/day.")
res["M5_low_vrp_short_basket_COLLAPSED"] = gate_collapsed(
    bw.mul(-(vrp_pct < 0.20).reindex(common).fillna(False).astype(float), axis=0), fa,
    "M5 — equal-weight alt basket SHORT while VRP in bottom quintile (ONE instrument)", "")
cov = ret[alts].rolling(60, min_periods=40).cov(ret["BTCUSDT"]); var = ret["BTCUSDT"].rolling(60, min_periods=40).var()
rk = cov.div(var, axis=0).rank(axis=1, pct=True)
hi = (rk > 0.70).astype(float); lo = (rk < 0.30).astype(float)
tw = (hi.div(hi.sum(axis=1).replace(0, np.nan), axis=0) - lo.div(lo.sum(axis=1).replace(0, np.nan), axis=0)).reindex(common).fillna(0.0)
res["M5_beta_tilt_unconditional_CONTROL_COLLAPSED"] = gate_collapsed(tw, fa,
    "M5 control — unconditional high-beta minus low-beta alt tilt (ONE instrument)", "")
res["M5_beta_tilt_high_vrp_COLLAPSED"] = gate_collapsed(
    tw.mul((vrp_pct > 0.80).reindex(common).fillna(False).astype(float), axis=0), fa,
    "M5 — beta tilt active only when VRP top quintile (ONE instrument)", "")

# ---------- M7 gross significance ----------
hb = pd.read_parquet(f"{D}/hourly_block_flow.parquet"); hb.index = pd.to_datetime(hb.index, utc=True)
ph = pd.read_parquet(f"{D}/perp_hourly_close_core.parquet"); ph.index = pd.to_datetime(ph.index, utc=True)
ix = ph.index; f1 = ph["BTCUSDT"].pct_change().shift(-1)*1e4
z = causal_z(hb["blk_delta_flow"].reindex(ix).fillna(0.0), 24*90, 24*30)
raw = pd.Series(0.0, index=ix); raw[z > 1.0] = 1.0; raw[z < -1.0] = -1.0
g = (raw*f1).dropna()
on = raw != 0
st = on.ne(on.shift()).cumsum()[on]
epg = (raw*f1)[on].groupby(st).sum().dropna()
diag["M7_gross_significance"] = {
    "n_episodes": int(len(epg)), "gross_bps_per_episode": round(float(epg.mean()), 3),
    "t_stat_declustered_GROSS": round(float(epg.mean()/(epg.std(ddof=1)/np.sqrt(len(epg)))), 2),
    "cost_floor_bps_round_trip": 14.0,
    "verdict_note": "gross effect is real and correctly signed but ~5x below the cost floor"}

json.dump({"results": res, "diagnostics": diag}, open(f"{D}/results_collapsed.json","w"), indent=1, default=str)
print(json.dumps(diag, indent=1, default=str)); print()
for k, v in res.items():
    print(f"{k:48s} net={v['net_bps']:>8} t={v['t_stat_declustered']:>6} SR={v.get('portfolio_sharpe'):>7} "
          f"SRstress={v.get('portfolio_sharpe_stress'):>7} L2={v['n_independent_L2']:>5} L3={v['n_independent_L3']:>5} ETA_y={v['eta_forward_confirmation_years']}")
