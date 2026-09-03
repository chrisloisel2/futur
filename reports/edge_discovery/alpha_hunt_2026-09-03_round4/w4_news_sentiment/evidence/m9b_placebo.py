"""M9b — RANDOM-TIME PLACEBO for the news-latency kill test.

The strongest possible form of the kill: if the absolute BTC move in [0,+30min] after a
news story is indistinguishable from the absolute move after a RANDOM timestamp drawn
from the same window, then the news feed carries no timing information at all.
1000 placebo draws of the same size, same calendar span, same 5m price series.
"""
import sys, json, os, re, glob
import numpy as np, pandas as pd

news_dir = "/home/qbee/futur/data/news_raw"
rows = []
for p in sorted(glob.glob(f"{news_dir}/date=*/part-*.parquet")):
    df = pd.read_parquet(p)
    df["ts_collect"] = pd.Timestamp(os.stat(p).st_mtime, unit="s", tz="UTC")
    rows.append(df)
N = pd.concat(rows, ignore_index=True)
N["ts_declared"] = pd.to_datetime(N["ts"], utc=True)
N["ts_collect"] = pd.to_datetime(N["ts_collect"], utc=True)
rss = N[N.source != "coingecko_trending"]

b5 = pd.read_parquet("/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/symbol=BTCUSDT/year=2026/perp_5m.parquet")
tc = [c for c in b5.columns if "time" in c.lower() or c in ("ts",)][0]
b5["ts"] = pd.to_datetime(b5[tc], utc=True)
px = b5.set_index("ts")["close"].astype(float).sort_index()
grid = px.resample("5min").last().ffill()

T0 = pd.Timestamp("2026-07-10", tz="UTC")
T1 = min(grid.index.max() - pd.Timedelta("7h"), N.ts_collect.max())
valid = grid.index[(grid.index >= T0 + pd.Timedelta("7h")) & (grid.index <= T1)]

def abs_move(anchors, lo, hi):
    idx = pd.DatetimeIndex(anchors)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    idx = idx.floor("5min")
    a = grid.reindex(idx + pd.Timedelta(minutes=lo), method="ffill").values
    b = grid.reindex(idx + pd.Timedelta(minutes=hi), method="ffill").values
    r = (b / a - 1.0) * 1e4
    r = r[np.isfinite(r)]
    return float(np.mean(np.abs(r))), float(np.mean(r)), len(r)

btc_news = rss[(rss.symbols.fillna("").str.contains("BTCUSDT")) &
               (rss.ts_declared >= T0) & (rss.ts_declared <= T1)].copy()
def toks(t):
    return frozenset(w.lower() for w in re.findall(r"[A-Za-z]{5,}", str(t))[:8])
btc_news["_tok"] = btc_news.title.map(toks)
btc_news = btc_news.sort_values("ts_declared")
keep, seen = [], []
for _, r in btc_news.iterrows():
    dup = any(len(r._tok & s[0]) >= 3 and abs((r.ts_declared - s[1]).total_seconds()) < 86400 for s in seen)
    keep.append(not dup)
    if not dup:
        seen.append((r._tok, r.ts_declared))
ind = btc_news[keep]
print("independent BTC stories in window:", len(ind))

rng = np.random.default_rng(20260903)
OUT = {"n_independent_stories": int(len(ind)), "placebo_draws": 1000, "windows": {}}
for anchor_name, col in [("declared_pubDate", "ts_declared"), ("collection_time", "ts_collect")]:
    OUT["windows"][anchor_name] = {}
    for lo, hi in [(-30, 0), (0, 30), (0, 60), (0, 120)]:
        obs_abs, obs_sig, n = abs_move(ind[col].values, lo, hi)
        pl = []
        for _ in range(1000):
            samp = rng.choice(valid.values, size=n, replace=False)
            pl.append(abs_move(samp, lo, hi)[0])
        pl = np.array(pl)
        pval = float((pl >= obs_abs).mean())
        OUT["windows"][anchor_name][f"[{lo},{hi}]"] = {
            "n": n, "observed_mean_abs_bps": round(obs_abs, 2),
            "placebo_mean_abs_bps": round(float(pl.mean()), 2),
            "placebo_p05_p95": [round(float(np.percentile(pl, 5)), 2), round(float(np.percentile(pl, 95)), 2)],
            "excess_over_placebo_bps": round(obs_abs - float(pl.mean()), 2),
            "p_value_one_sided": pval,
            "observed_mean_signed_bps": round(obs_sig, 2)}

json.dump(OUT, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m9b_placebo_results.json", "w"), indent=1)
print(json.dumps(OUT, indent=1))
