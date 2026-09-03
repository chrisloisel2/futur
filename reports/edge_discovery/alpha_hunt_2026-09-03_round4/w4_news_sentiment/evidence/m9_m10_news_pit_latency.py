"""M10 (PIT audit) + M9 (news->price latency, THE KILL TEST).

The collector (`src/institutional/data/news_collector/collector.py::_parse_rss`) sets
    ts = parsedate_to_datetime(pubDate)
i.e. the SOURCE-DECLARED publication time, and persists NO collection-time column.
The partition key is derived from that same declared ts. So `ts` is NOT point-in-time.

Collection time is recovered from the parquet file's mtime (the file is written by
`_write` immediately after the fetch, via tmp.replace(final) -> mtime == write time).
The filename's HHMMSS (`datetime.now(utc)` at write) is used as a cross-check.
"""
import sys, json, re, os, glob
import numpy as np, pandas as pd
sys.path.insert(0, "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence")
import w4_lib as L

parts = sorted(glob.glob("/home/qbee/futur/data/news_raw/date=*/part-*.parquet"))
rows = []
for p in parts:
    df = pd.read_parquet(p)
    st = os.stat(p)
    df["_collect_mtime"] = pd.Timestamp(st.st_mtime, unit="s", tz="UTC")
    m = re.search(r"part-(\d{6})-", os.path.basename(p))
    df["_fname_hhmmss"] = m.group(1) if m else None
    df["_part_date"] = os.path.basename(os.path.dirname(p)).replace("date=", "")
    rows.append(df)
N = pd.concat(rows, ignore_index=True)
N["ts_declared"] = pd.to_datetime(N["ts"], utc=True)
N["ts_collect"] = pd.to_datetime(N["_collect_mtime"], utc=True)
N["lag_min"] = (N["ts_collect"] - N["ts_declared"]).dt.total_seconds() / 60.0

# cross-check: does the filename HHMMSS agree with the mtime time-of-day?
fn_tod = pd.to_numeric(N["_fname_hhmmss"], errors="coerce")
fn_sec = (fn_tod // 10000) * 3600 + ((fn_tod // 100) % 100) * 60 + (fn_tod % 100)
mt_sec = N["ts_collect"].dt.hour * 3600 + N["ts_collect"].dt.minute * 60 + N["ts_collect"].dt.second
delta = ((fn_sec - mt_sec + 43200) % 86400) - 43200
OUT = {}
OUT["M10_pit_audit"] = {
    "n_rows_total": int(len(N)),
    "n_unique_url_hash": int(N.url_hash.nunique()),
    "ts_column_semantics": "SOURCE-DECLARED pubDate (collector `_parse_rss` uses parsedate_to_datetime(pubDate)); "
                           "NOT collection time. No collection-time column is persisted.",
    "collection_time_recovery": "file mtime of the parquet part (written via tmp.replace at fetch time)",
    "filename_vs_mtime_agreement_sec_abs_median": float(np.nanmedian(np.abs(delta))),
    "declared_range": [str(N.ts_declared.min()), str(N.ts_declared.max())],
    "collect_range": [str(N.ts_collect.min()), str(N.ts_collect.max())],
    "lag_declared_to_collect_min": {
        "p05": round(float(N.lag_min.quantile(.05)), 1),
        "p25": round(float(N.lag_min.quantile(.25)), 1),
        "median": round(float(N.lag_min.median()), 1),
        "p75": round(float(N.lag_min.quantile(.75)), 1),
        "p95": round(float(N.lag_min.quantile(.95)), 1),
        "max_days": round(float(N.lag_min.max()) / 1440, 1)},
}
rss = N[N.source != "coingecko_trending"]
OUT["M10_pit_audit"]["lag_RSS_only_min"] = {
    "n": int(len(rss)),
    "median": round(float(rss.lag_min.median()), 1),
    "p25": round(float(rss.lag_min.quantile(.25)), 1),
    "p75": round(float(rss.lag_min.quantile(.75)), 1),
    "p95": round(float(rss.lag_min.quantile(.95)), 1),
    "frac_lag_gt_30min": round(float((rss.lag_min > 30).mean()), 3),
    "frac_lag_gt_120min": round(float((rss.lag_min > 120).mean()), 3)}
# backlog partitions: rows whose declared date is >7d before collection
OUT["M10_pit_audit"]["backlog_rows_declared_gt_7d_before_collection"] = int((N.lag_min > 7 * 1440).sum())
OUT["M10_pit_audit"]["continuous_coverage_days"] = int(
    (N.ts_collect.dt.floor("D").nunique()))
OUT["M10_pit_audit"]["per_source_counts"] = {k: int(v) for k, v in N.source.value_counts().items()}

# ------------------------------------------------ M9: news -> price latency (KILL TEST)
# BTC 5m closes from the data_v2 PIT panel would be ideal; the 1h enriched bar is enough
# to answer "did the move already happen before the timestamp?", and we ALSO use 5m perp.
btc5 = []
for y in (2026,):
    p = f"/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/symbol=BTCUSDT/year={y}/perp_5m.parquet"
    if os.path.exists(p):
        btc5.append(pd.read_parquet(p))
b5 = pd.concat(btc5, ignore_index=True)
tcol = [c for c in b5.columns if "time" in c.lower() or c in ("ts", "open_time")][0]
b5["ts"] = pd.to_datetime(b5[tcol], utc=True)
b5 = b5[["ts", "close"]].dropna().sort_values("ts").set_index("ts")
px = b5["close"].astype(float)

def cum_ret_window(anchor, lo_min, hi_min):
    """cumulative BTC return from anchor+lo_min to anchor+hi_min, in bps."""
    a = px.reindex(px.index.union([anchor + pd.Timedelta(minutes=lo_min)])).ffill().reindex(
        [anchor + pd.Timedelta(minutes=lo_min)]).iloc[0]
    b = px.reindex(px.index.union([anchor + pd.Timedelta(minutes=hi_min)])).ffill().reindex(
        [anchor + pd.Timedelta(minutes=hi_min)]).iloc[0]
    if not np.isfinite(a) or not np.isfinite(b) or a == 0:
        return np.nan
    return (b / a - 1.0) * 1e4

# BTC-relevant RSS articles only, inside the continuous window, declustered by story
btc_news = rss[(rss.symbols.fillna("").str.contains("BTCUSDT")) &
               (rss.ts_declared >= pd.Timestamp("2026-07-10", tz="UTC")) &
               (rss.lag_min.between(-60, 24 * 60))].copy()
# L3 decluster: one story per 24h per title-token cluster (a story reprinted by 4 feeds = N=1)
def toks(t):
    return frozenset(w.lower() for w in re.findall(r"[A-Za-z]{5,}", str(t))[:8])
btc_news["_tok"] = btc_news.title.map(toks)
btc_news = btc_news.sort_values("ts_declared")
keep, seen = [], []
for i, r in btc_news.iterrows():
    dup = any((r._tok & s[0]) and len(r._tok & s[0]) >= 3 and
              abs((r.ts_declared - s[1]).total_seconds()) < 86400 for s in seen)
    keep.append(not dup)
    if not dup:
        seen.append((r._tok, r.ts_declared))
btc_news["_indep"] = keep
ind = btc_news[btc_news._indep]

prof = {}
for label, anchor_col in [("declared_pubDate", "ts_declared"), ("collection_time", "ts_collect")]:
    seg = {}
    for lo, hi in [(-360, -120), (-120, -30), (-30, 0), (0, 30), (0, 60), (0, 120), (0, 360)]:
        vals = [cum_ret_window(t, lo, hi) for t in ind[anchor_col]]
        vals = np.array([v for v in vals if np.isfinite(v)])
        # sign-agnostic: news is not directional, so measure ABSOLUTE move (attention/vol proxy)
        seg[f"[{lo},{hi}]"] = {
            "n": int(len(vals)),
            "mean_abs_bps": round(float(np.mean(np.abs(vals))), 1) if len(vals) else None,
            "mean_signed_bps": round(float(np.mean(vals)), 1) if len(vals) else None,
            "t_signed": round(float(np.mean(vals) / (np.std(vals, ddof=1) / np.sqrt(len(vals)))), 2)
                        if len(vals) > 2 and np.std(vals, ddof=1) > 0 else None}
    prof[label] = seg
OUT["M9_news_to_price_latency"] = {
    "n_btc_rss_raw": int(len(btc_news)),
    "n_btc_rss_independent_story_L3": int(ind._indep.sum()),
    "cluster_ratio_raw_over_indep": round(len(btc_news) / max(1, int(ind._indep.sum())), 2),
    "window_profile_abs_and_signed_bps": prof,
    "interpretation_key": "if mean_abs_bps in [-120,-30] >= mean_abs_bps in [0,+30], the move is "
                          "already complete BEFORE the timestamp and the feed is a lagging description"}

json.dump(OUT, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m9_m10_results.json", "w"), indent=1, default=str)
print(json.dumps(OUT, indent=1, default=str))
