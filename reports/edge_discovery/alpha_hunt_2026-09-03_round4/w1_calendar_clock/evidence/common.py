"""Shared loaders: universe eligibility (PIT) + helpers."""
import numpy as np, pandas as pd, duckdb

SCRATCH = "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w1"
MIN_DV = 1e7        # $10M / day, 30d median, previous-day watermark
BURN_IN_DAYS = 30   # listing burn-in
MIN_XS = 20         # minimum cross-section per event


def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("SET memory_limit='10GB'")
    assert c.execute("SELECT hour(TIMESTAMPTZ '2025-03-01 08:00:00+00')").fetchone()[0] == 8, "TZ NOT UTC"
    return c


def eligibility():
    """(symbol, d) -> eligible bool, using ONLY days strictly before d."""
    c = con()
    df = c.execute(f"""
      SELECT symbol, d, dv_usd, n_bars FROM read_parquet('{SCRATCH}/daily_liquidity.parquet')
      ORDER BY symbol, d
    """).df()
    df["d"] = pd.to_datetime(df["d"], utc=True)
    g = df.groupby("symbol", sort=False)
    # 30d median dollar volume, SHIFTED by one day -> strictly prior information
    df["dv_med30_prev"] = g["dv_usd"].transform(lambda s: s.rolling(30, min_periods=20).median().shift(1))
    first = g["d"].transform("min")
    df["days_since_listing"] = (df["d"] - first).dt.days
    df["eligible"] = (df["dv_med30_prev"] >= MIN_DV) & (df["days_since_listing"] >= BURN_IN_DAYS) & (df["n_bars"] >= 250)
    return df[["symbol", "d", "eligible", "dv_med30_prev", "days_since_listing"]]


def xs_spread(df, event_col, rank_col, ret_cols, n_buckets=5, min_xs=MIN_XS, winsor=None):
    """Cross-sectional dollar-neutral quintile spread per event.
    long = bottom bucket of rank_col, short = top bucket.  Returns (obs, n_ind_L1).
    obs has one row per event with '<ret>_spread' columns in bps."""
    d = df.dropna(subset=[rank_col]).copy()
    if winsor is not None:
        for rc in ret_cols:
            d[rc] = d[rc].clip(-winsor, winsor)
    d = d.dropna(subset=ret_cols, how="all")
    cnt = d.groupby(event_col)[rank_col].transform("size")
    d = d[cnt >= min_xs]
    if len(d) == 0:
        return pd.DataFrame(columns=[event_col]), 0
    d["_r"] = d.groupby(event_col)[rank_col].rank(method="first", pct=True)
    lo = d[d["_r"] <= 1.0 / n_buckets]
    hi = d[d["_r"] > 1.0 - 1.0 / n_buckets]
    out = None
    for rc in ret_cols:
        a = lo.groupby(event_col)[rc].mean()
        b = hi.groupby(event_col)[rc].mean()
        s = ((a - b) * 1e4).rename(rc + "_spread")
        na = lo.groupby(event_col)[rc].size().rename("n_long")
        out = s.to_frame() if out is None else out.join(s, how="outer")
    out = out.join(na, how="left").reset_index()
    used = pd.concat([lo, hi])
    n_L1 = used.assign(_d=used[event_col].dt.floor("D")).drop_duplicates(["symbol", "_d"]).shape[0]
    return out, n_L1
