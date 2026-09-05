"""Shared, BUG-FIXED clock helpers for W1.

### The bug this file exists to prevent
`clock_map.py` (first pass) built its 6h momentum signal with

    h.set_index("hour_end").groupby("symbol")[col].rolling("6h", min_periods=6).sum()

DuckDB's `.df()` returns a `datetime64[us, UTC]` index. Under pandas 2.0.3 an *offset*
rolling window on a non-nanosecond datetime index silently degenerates into an EXPANDING
window: the "6h" offset is compared at nanosecond scale against microsecond values, so
every window starts at row 0. Verified numerically in `verify_b2_vs_clockmap.py`:
`roll6.iloc[37] == series.iloc[:38].sum()` exactly. The signal was therefore the cumulative
residual return since the symbol's first eligible bar, not a 6h momentum — correlation with
the true 6h signal = 0.035. Every arm of the first clock map is void.

`resid_roll_hours()` below never uses an offset window. It uses an integer window plus an
explicit contiguity guard, and `assert_roll_ok()` cross-checks it against a nanosecond-index
reference. Any script on this axis must use it.
"""
import numpy as np, pandas as pd


def resid_roll_hours(df, hours, sym_col="symbol", t_col="hour_end", val_col="resid_logret_hour",
                     out_col=None):
    """Causal rolling sum of `val_col` over exactly `hours` CONTIGUOUS hourly bars,
    ending at (and including) each row's own bar. NaN where the bars are not contiguous
    or the symbol changes. `df` is returned sorted by (symbol, t)."""
    out_col = out_col or f"resid{hours}"
    d = df.sort_values([sym_col, t_col]).reset_index(drop=True)
    v = d[val_col].to_numpy(dtype="float64")
    s = pd.Series(v).rolling(hours, min_periods=hours).sum().to_numpy()
    same_sym = d[sym_col].to_numpy() == d[sym_col].shift(hours - 1).to_numpy()
    dt_ok = (d[t_col] - d[t_col].shift(hours - 1)).to_numpy() == np.timedelta64(hours - 1, "h")
    s = np.where(same_sym & dt_ok, s, np.nan)
    d[out_col] = s
    return d


def assert_roll_ok(d, hours, sym_col="symbol", t_col="hour_end", val_col="resid_logret_hour",
                   out_col=None, n_check=3):
    """Independent verification against a nanosecond-index offset window (which IS correct)."""
    out_col = out_col or f"resid{hours}"
    syms = d[sym_col].dropna().unique()[:n_check]
    for sym in syms:
        g = d[d[sym_col] == sym].set_index(t_col)[val_col]
        g.index = g.index.astype("datetime64[ns, UTC]")
        ref = g.rolling(f"{hours}h", min_periods=hours).sum()
        got = d[d[sym_col] == sym].set_index(t_col)[out_col]
        got.index = got.index.astype("datetime64[ns, UTC]")
        j = pd.concat([ref.rename("ref"), got.rename("got")], axis=1).dropna()
        assert len(j) > 100, f"{sym}: too few overlapping points to verify"
        m = float((j["ref"] - j["got"]).abs().max())
        assert m < 1e-9, f"{sym}: rolling mismatch max|diff|={m}"
    return True


def load_hourly(scratch, con_fn, eligibility_fn):
    """Eligible hourly panel + the two price roles used everywhere on this axis."""
    c = con_fn()
    h = c.execute(f"""SELECT symbol, hour_end, close_at_hour_end, close_first5, dv_usd,
                             resid_logret_hour, n_bars
                      FROM read_parquet('{scratch}/hourly.parquet')
                      WHERE close_at_hour_end IS NOT NULL AND n_bars >= 10""").df()
    h["hour_end"] = pd.to_datetime(h["hour_end"], utc=True)
    h["d"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.floor("D")
    h["hb"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.hour
    h = h.merge(eligibility_fn(), on=["symbol", "d"], how="left")
    h = h[h["eligible"].fillna(False)].copy()
    # price AT a clock instant T  = close of the bar ending at T (the :55 5m bar)
    px_at = h[["symbol", "hour_end", "close_at_hour_end"]].rename(
        columns={"hour_end": "T", "close_at_hour_end": "p"})
    # price 5m AFTER a clock instant T = close of the first 5m bar of the hour starting at T
    ent = h[["symbol", "hour_end", "close_first5"]].copy()
    ent["T"] = ent["hour_end"] - pd.Timedelta(hours=1)
    ent = ent[["symbol", "T", "close_first5"]].rename(columns={"close_first5": "p5"})
    return h, px_at, ent


def paired_contrast(series_by_arm, a, b, block_ci):
    """Arm A minus arm B on the SAME calendar days. Never 'arm A is positive'."""
    j = pd.concat([series_by_arm[a].rename("a"), series_by_arm[b].rename("b")], axis=1).dropna()
    if len(j) < 30:
        return None
    v = (j["a"] - j["b"]).to_numpy()
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    ci, _ = block_ci(v, n_boot=4000)
    return dict(comparison=f"{a} minus {b}", diff_bps=round(float(v.mean()), 3),
                n_paired_days=int(len(v)), t=round(float(t), 3),
                ci95=[round(ci[0], 2), round(ci[1], 2)])
