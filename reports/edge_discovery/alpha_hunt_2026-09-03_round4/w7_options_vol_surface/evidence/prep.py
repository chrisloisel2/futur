"""W7 round4 — shared data prep. Causal features only."""
import os, numpy as np, pandas as pd
D = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/qbee/futur"

def load_all():
    opt = pd.read_parquet(f"{D}/panel_daily_btc_options.parquet")
    opt.index = pd.to_datetime(opt.index, utc=True).tz_convert("UTC").normalize()
    dv_b = pd.read_parquet(f"{ROOT}/data/options_backfill/deribit/DVOL_BTC_1d.parquet").set_index("ts")
    dv_e = pd.read_parquet(f"{ROOT}/data/options_backfill/deribit/DVOL_ETH_1d.parquet").set_index("ts")
    px = pd.read_parquet(f"{D}/perp_daily_close.parquet")
    px.index = pd.to_datetime(px.index, utc=True).normalize()
    dvol = pd.DataFrame({"dvol_btc": dv_b.close, "dvol_eth": dv_e.close})
    dvol.index = pd.to_datetime(dvol.index, utc=True).normalize()
    ret = px.pct_change()                 # ret[d] = close(d)/close(d-1)-1  (realised, causal)
    fwd = ret.shift(-1)                   # fwd[d] = close(d+1)/close(d)-1  (forward outcome)
    return opt, dvol, px, ret, fwd

def uniform_position(sig: pd.Series, idx, win=252, minp=90, hi=0.80, lo=0.20):
    """UNIFORM, UNTUNED expression rule applied identically to every daily mechanism:
    +1 when the signal is in the top quintile of its own trailing 252d history,
    -1 in the bottom quintile, 0 otherwise. Strictly causal (past-only percentile)."""
    s = sig.reindex(idx)
    pct = s.rolling(win, min_periods=minp).apply(
        lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
    p = pd.Series(0.0, index=idx)
    p[pct > hi] = 1.0
    p[pct < lo] = -1.0
    return p, pct

def sign_from_first_half(pos_raw: pd.Series, fwd: pd.Series):
    """Sign of the relationship learned on the FIRST HALF only (§1.5: a sign chosen after
    seeing the full sample is a refit; here it is fitted in-sample and applied out-of-sample)."""
    n = len(pos_raw); cut = n//2
    a = (pos_raw.iloc[:cut]*fwd.iloc[:cut]).sum()
    return (1.0 if a >= 0 else -1.0), pos_raw.index[cut]
