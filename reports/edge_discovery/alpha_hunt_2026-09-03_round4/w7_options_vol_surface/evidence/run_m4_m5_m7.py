"""W7 round4 — M4 (pin / strike magnet), M5 (VRP -> cross-sectional alt tilt), M7 (block delta flow -> perp)."""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, two_arm, causal_z, ZSQ
from prep import load_all, uniform_position
D = os.path.dirname(os.path.abspath(__file__))
opt, dvol, px, ret, fwd = load_all()
res, diag = {}, {}

# ============================ M4 — strike magnet / pin risk ============================
sn = pd.read_parquet(f"{D}/strike_notional.parquet")
sn["day"] = pd.to_datetime(sn.day, utc=True).dt.normalize()
sn["expiry"] = pd.to_datetime(sn.expiry, utc=True)
# monthly/quarterly expiries = Deribit standard, 08:00 UTC last Friday of month
exp_all = sorted(sn.expiry.unique())
monthly = [e for e in pd.DatetimeIndex(exp_all)
           if e.weekday() == 4 and (e + pd.Timedelta(days=7)).month != e.month]
diag["M4_n_monthly_expiries"] = len(monthly)
idx = opt.index.intersection(fwd.index)
spot = px["BTCUSDT"].reindex(idx)
rows = []
for e in monthly:
    # causal OI-at-strike proxy: cumulative traded notional for THIS expiry, up to day d only
    sub = sn[(sn.expiry == e)]
    if len(sub) == 0: continue
    for lag in (1, 2, 3):
        d = (e.normalize() - pd.Timedelta(days=lag))
        if d not in idx: continue
        hist = sub[sub.day <= d]
        if len(hist) < 5: continue
        agg = hist.groupby("strike").notional_btc.sum()
        magnet = float(agg.idxmax())
        S = float(spot.loc[d])
        if not np.isfinite(S) or S <= 0: continue
        rows.append({"day": d, "expiry": e, "lag": lag, "magnet": magnet, "spot": S,
                     "gap": (magnet - S)/S, "fwd": float(fwd["BTCUSDT"].get(d, np.nan))})
pin = pd.DataFrame(rows)
diag["M4_n_observations"] = len(pin)
if len(pin):
    p1 = pin[pin.lag == 1].dropna(subset=["fwd"])
    diag["M4_gap_describe"] = {k: round(float(v), 4) for k, v in p1.gap.describe().items()}
    # §1.3 two-arm: does price move TOWARD the magnet more than a coin flip on the same days?
    toward = (np.sign(p1.gap) == np.sign(p1.fwd)).mean() if len(p1) else np.nan
    diag["M4_share_moves_toward_magnet_lag1"] = round(float(toward), 3)
    diag["M4_binom_p"] = None
    for lag in (1, 2, 3):
        sub = pin[pin.lag == lag].dropna(subset=["fwd"]).set_index("day")
        if len(sub) < 10: continue
        pos = pd.Series(0.0, index=idx); pos.loc[sub.index] = np.sign(sub.gap).values
        res[f"M4_pin_magnet_lag{lag}d"] = run_gate(
            pd.DataFrame({"BTCUSDT": pos}), fwd[["BTCUSDT"]].reindex(idx),
            f"M4 — BTC perp toward max-traded-notional strike, {lag}d before monthly Deribit expiry",
            notes=("OI-at-strike PROXIED by cumulative traded notional for that expiry (no OI in "
                   "this dataset). Direction preregistered (toward the magnet), not fitted."))

# ============================ M5 — VRP -> cross-sectional alt beta tilt ============================
rv30 = (ret["BTCUSDT"].rolling(30, min_periods=20).std()*np.sqrt(365.25)*100.0)   # backward, causal
vrp = (dvol["dvol_btc"] - rv30).dropna()
common = vrp.index.intersection(fwd.index)
alts = [c for c in px.columns if c not in ("BTCUSDT",)]
# causal trailing 60d beta of each alt to BTC
bt = ret["BTCUSDT"]
cov = ret[alts].rolling(60, min_periods=40).cov(bt)
var = bt.rolling(60, min_periods=40).var()
beta = cov.div(var, axis=0)
rk = beta.rank(axis=1, pct=True)
hi = (rk > 0.70).astype(float); lo = (rk < 0.30).astype(float)
w = (hi.div(hi.sum(axis=1).replace(0, np.nan), axis=0)
     - lo.div(lo.sum(axis=1).replace(0, np.nan), axis=0)).fillna(0.0)   # dollar-neutral hi-beta minus lo-beta
w = w.reindex(common).fillna(0.0)
fwd_alt = fwd[alts].reindex(common)
res["M5_beta_tilt_unconditional_CONTROL"] = run_gate(
    w, fwd_alt, "M5 control — unconditional high-beta minus low-beta alt tilt (NO options input)",
    notes="Control arm: the §1.3 baseline the VRP-conditioned versions must beat.")
raw_pos, vrp_pct = uniform_position(vrp.reindex(common), common)
diag["M5_vrp_describe"] = {k: round(float(v), 2) for k, v in vrp.describe().items()}
for tag, gate_mask in [("high_vrp_only", (vrp_pct > 0.80)), ("low_vrp_only", (vrp_pct < 0.20))]:
    wc = w.mul(gate_mask.reindex(common).fillna(False).astype(float), axis=0)
    res[f"M5_beta_tilt_{tag}"] = run_gate(
        wc, fwd_alt, f"M5 — high-beta minus low-beta alt tilt, active only when VRP {tag}",
        notes="VRP = DVOL_BTC - trailing-30d realised vol of BTC (causal). Compare to the control arm.")
# arm vs arm on identical population: daily tilt pnl under high vs low VRP
tilt_pnl = (w*fwd_alt*1e4).sum(axis=1)
diag["M5_arm_vs_arm"] = two_arm(tilt_pnl[(vrp_pct > 0.80).reindex(common).fillna(False)],
                                tilt_pnl[(vrp_pct < 0.20).reindex(common).fillna(False)],
                                "high_vrp", "low_vrp")
# also: VRP regime -> outright alt beta (risk-on/off timing)
mkt = fwd[alts].mean(axis=1).reindex(common)
diag["M5_riskon_arm_vs_arm"] = two_arm((mkt[(vrp_pct > 0.80).reindex(common).fillna(False)]*1e4).dropna(),
                                       (mkt[(vrp_pct < 0.20).reindex(common).fillna(False)]*1e4).dropna(),
                                       "high_vrp", "low_vrp")

# ============================ M7 — block delta flow -> perp (dealer delta hedge) ============================
hb = pd.read_parquet(f"{D}/hourly_block_flow.parquet")
hb.index = pd.to_datetime(hb.index, utc=True)
ph = pd.read_parquet(f"{D}/perp_hourly_close_core.parquet")
ph.index = pd.to_datetime(ph.index, utc=True)
hr = ph["BTCUSDT"].pct_change()
diag["M7_hours_with_blocks"] = int(len(hb))
for H in (1, 4):
    fh = (ph["BTCUSDT"].shift(-H)/ph["BTCUSDT"] - 1.0)          # forward H-hour return
    ix = ph.index
    flow = hb["blk_delta_flow"].reindex(ix).fillna(0.0)
    z = causal_z(flow, 24*90, 24*30)
    # dealer must BUY perp when customers are net long delta => preregistered sign = +1
    pos = pd.Series(0.0, index=ix)
    pos[z > 1.0] = 1.0; pos[z < -1.0] = -1.0
    res[f"M7_block_delta_flow_fwd{H}h"] = run_gate(
        pd.DataFrame({"BTCUSDT": pos}), pd.DataFrame({"BTCUSDT": fh}),
        f"M7 — delta-weighted Deribit BLOCK flow (hourly, |z|>1) -> BTC perp, {H}h horizon",
        notes=("Direction preregistered from the hedging mechanism (customer net-long delta => "
               "dealer buys perp), NOT fitted. Distinct from W6-M3 (raw notional block flow, daily)."))
    sub = pd.DataFrame({"z": z, "f": fh*1e4}).dropna()
    diag[f"M7_ic_fwd{H}h"] = round(float(sub.z.corr(sub.f, method="spearman")), 4)
    diag[f"M7_n_hours_fwd{H}h"] = len(sub)

json.dump({"results": res, "diagnostics": diag}, open(f"{D}/results_m4_m5_m7.json","w"), indent=1, default=str)
print(json.dumps(diag, indent=1, default=str)); print()
for k, v in res.items():
    print(f"{k:44s} net={v['net_bps']:>8} stress={v['net_bps_stress28']:>8} t={v['t_stat_declustered']:>6} "
          f"SR={v['sharpe_annual_net']:>7} L3={v['n_independent_L3']:>5} ETA_y={v['eta_forward_confirmation_years']}")
