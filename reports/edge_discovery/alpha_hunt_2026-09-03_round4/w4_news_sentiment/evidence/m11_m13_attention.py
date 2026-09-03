"""M11 (attention count -> vol/return), M12 (attention dispersion), M13 (CoinGecko trending entry).

ALL of these are capped at DATA_LIMITED by preregistration: 53 distinct collection days,
one market regime (2026 = uniform extreme fear, F&G mean 21.6).
Anchored on RECOVERED COLLECTION TIME, never on the declared pubDate (see M10).
"""
import sys, json, os, re, glob
import numpy as np, pandas as pd
sys.path.insert(0, "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence")
import w4_lib as L

# ---------------- news lake with recovered collection time
rows = []
for p in sorted(glob.glob("/home/qbee/futur/data/news_raw/date=*/part-*.parquet")):
    df = pd.read_parquet(p)
    df["ts_collect"] = pd.Timestamp(os.stat(p).st_mtime, unit="s", tz="UTC")
    rows.append(df)
N = pd.concat(rows, ignore_index=True)
N["ts_collect"] = pd.to_datetime(N["ts_collect"], utc=True)
N["cday"] = N["ts_collect"].dt.floor("D")
N = N.drop_duplicates(subset=["url_hash", "cday"])
rss = N[N.source != "coingecko_trending"].copy()
cg = N[N.source == "coingecko_trending"].copy()

# ---------------- price panel: 5m perp for the 61 enriched symbols, 2026 only
def load_px(sym):
    p = f"/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/symbol={sym}/year=2026/perp_5m.parquet"
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p)
    tc = [c for c in d.columns if "time" in c.lower() or c in ("ts",)][0]
    d["ts"] = pd.to_datetime(d[tc], utc=True)
    return d[["ts", "close"]].dropna().sort_values("ts")

# symbols actually tagged in the news
tagged = (rss.symbols.fillna("").str.split(",").explode().str.strip())
tagged = tagged[tagged != ""].value_counts()
syms = [s for s in tagged.index[:20]]
print("top tagged symbols:", dict(list(tagged.items())[:12]))

daily = {}
for s in syms:
    d = load_px(s)
    if d is None or len(d) == 0:
        continue
    d = d.set_index("ts")["close"].astype(float)
    r5 = d.pct_change()
    day = pd.DataFrame({
        "close": d.resample("1D").last(),
        "rv": r5.resample("1D").std() * np.sqrt(288),   # realized vol, daily
    })
    day["ret_fwd_1d"] = day["close"].shift(-1) / day["close"] - 1.0
    day["rv_fwd_1d"] = day["rv"].shift(-1)
    day["rv_trail_7d"] = day["rv"].rolling(7).mean()     # causal
    daily[s] = day.reset_index().rename(columns={"ts": "cday"})

OUT = {}

# ---------------- M11: attention COUNT (not polarity) -> forward vol / forward return
cnt = (rss.assign(sym=rss.symbols.fillna("").str.split(","))
          .explode("sym"))
cnt["sym"] = cnt["sym"].str.strip()
cnt = cnt[cnt["sym"] != ""]
cnt = cnt.groupby(["cday", "sym"]).size().reset_index(name="n_articles")

recs = []
for s, day in daily.items():
    c = cnt[cnt.sym == s][["cday", "n_articles"]]
    d = day.merge(c, on="cday", how="left")
    d["n_articles"] = d["n_articles"].fillna(0.0)
    d["sym"] = s
    # causal z of attention over a trailing 14d window (strictly before t)
    m14 = d["n_articles"].shift(1).rolling(14, min_periods=7).mean()
    s14 = d["n_articles"].shift(1).rolling(14, min_periods=7).std()
    d["att_z"] = (d["n_articles"] - m14) / s14.replace(0, np.nan)
    recs.append(d)
P = pd.concat(recs, ignore_index=True).dropna(subset=["att_z", "rv_fwd_1d", "rv_trail_7d"])
P["_ts"] = P["cday"]; P["symbol"] = P["sym"]
P["r_bps"] = P["ret_fwd_1d"] * 1e4
P["rv_ratio_fwd"] = P["rv_fwd_1d"] / P["rv_trail_7d"]

# L3 for the news block = calendar day (a market-wide news day is one episode)
P["_ep"] = P["cday"].rank(method="dense").astype(int)
hi = P[P.att_z >= 1.0]; lo = P[P.att_z < 1.0]
def _t(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    return float(np.mean(x) / (np.std(x, ddof=1) / np.sqrt(len(x)))) if len(x) > 2 else None
OUT["M11_attention_count"] = {
    "n_symbol_days": int(len(P)), "n_symbols": int(P.sym.nunique()),
    "n_distinct_days_L2": int(P.cday.nunique()),
    "vol_test": {
        "hypothesis": "attention predicts VOL (plausible)",
        "spike_n": int(len(hi)), "spike_rv_ratio_mean": round(float(hi.rv_ratio_fwd.mean()), 3),
        "calm_n": int(len(lo)), "calm_rv_ratio_mean": round(float(lo.rv_ratio_fwd.mean()), 3),
        "spread": round(float(hi.rv_ratio_fwd.mean() - lo.rv_ratio_fwd.mean()), 3),
        "t_on_daily_means": round(_t(hi.groupby("cday").rv_ratio_fwd.mean() -
                                    lo.groupby("cday").rv_ratio_fwd.mean().reindex(
                                        hi.groupby("cday").rv_ratio_fwd.mean().index)) or np.nan, 2)},
    "direction_test": {
        "hypothesis": "attention does NOT predict direction (implausible that it would)",
        "spike_net_bps": round(float(hi.r_bps.mean()) - L.COST, 2),
        "calm_net_bps": round(float(lo.r_bps.mean()) - L.COST, 2),
        "spread_bps": round(float(hi.r_bps.mean() - lo.r_bps.mean()), 2),
        "t_spike_daymeans": round(_t(hi.groupby("cday").r_bps.mean()) or np.nan, 2)},
}
gate_hi = L.run_gate(hi, "r_bps", "M11_attention_spike_long")
OUT["M11_attention_count"]["gate_spike_long"] = gate_hi

# ---------------- M12: attention DISPERSION (HHI / entropy) -> market-wide regime
disp = []
for d_, g in cnt.groupby("cday"):
    w = g.n_articles.values.astype(float); w = w / w.sum()
    disp.append({"cday": d_, "hhi": float((w ** 2).sum()),
                 "entropy": float(-(w * np.log(w + 1e-12)).sum()), "n_names": int(len(w))})
disp = pd.DataFrame(disp)
cgd = cg.groupby("cday").apply(lambda g: g.url.nunique()).reset_index(name="cg_n_trending")
disp = disp.merge(cgd, on="cday", how="left")
btc = daily.get("BTCUSDT")
if btc is not None:
    dd = disp.merge(btc[["cday", "rv", "rv_fwd_1d", "rv_trail_7d", "ret_fwd_1d"]], on="cday", how="inner").dropna()
    dd["rv_ratio_fwd"] = dd.rv_fwd_1d / dd.rv_trail_7d
    med = dd.hhi.median()
    conc = dd[dd.hhi > med]; broad = dd[dd.hhi <= med]
    OUT["M12_attention_dispersion"] = {
        "n_days": int(len(dd)),
        "hhi_median": round(float(med), 3),
        "concentrated_fwd_rvratio": round(float(conc.rv_ratio_fwd.mean()), 3),
        "dispersed_fwd_rvratio": round(float(broad.rv_ratio_fwd.mean()), 3),
        "rvratio_spread": round(float(conc.rv_ratio_fwd.mean() - broad.rv_ratio_fwd.mean()), 3),
        "concentrated_fwd_btc_net_bps": round(float(conc.ret_fwd_1d.mean() * 1e4) - L.COST, 2),
        "dispersed_fwd_btc_net_bps": round(float(broad.ret_fwd_1d.mean() * 1e4) - L.COST, 2),
        "btc_spread_bps": round(float((conc.ret_fwd_1d.mean() - broad.ret_fwd_1d.mean()) * 1e4), 2),
        "t_conc_btc": round(_t(conc.ret_fwd_1d.values * 1e4) or np.nan, 2)}

# ---------------- M13: CoinGecko trending ENTRY -> forward return
cg2 = cg.copy()
cg2["coin"] = cg2.url.str.replace("coingecko://trending/", "", regex=False)
cg2["sym"] = cg2.symbols.fillna("").str.split(",").str[0].str.strip()
member = cg2.groupby(["cday", "coin"]).first().reset_index()[["cday", "coin", "sym"]]
member = member.sort_values(["coin", "cday"])
member["prev"] = member.groupby("coin")["cday"].shift(1)
member["is_entry"] = (member.prev.isna()) | ((member.cday - member.prev).dt.days >= 7)
ent = member[member.is_entry & (member.sym != "")]
recs = []
for _, r in ent.iterrows():
    d = daily.get(r.sym)
    if d is None:
        continue
    row = d[d.cday == r.cday]
    if len(row) and np.isfinite(row.ret_fwd_1d.values[0]):
        recs.append({"cday": r.cday, "symbol": r.sym, "r_bps": float(row.ret_fwd_1d.values[0]) * 1e4})
E = pd.DataFrame(recs)
if len(E):
    E["_ts"] = E["cday"]; E["_ep"] = E["cday"].rank(method="dense").astype(int)
    peer = P[["cday", "sym", "r_bps"]].rename(columns={"sym": "symbol"})
    pm = peer.groupby("cday").r_bps.mean().rename("peer_bps").reset_index()
    E = E.merge(pm, on="cday", how="left")
    E["excess_bps"] = E.r_bps - E.peer_bps
    OUT["M13_coingecko_trending_entry"] = {
        "n_entries_raw": int(len(E)), "n_distinct_days": int(E.cday.nunique()),
        "n_distinct_symbols": int(E.symbol.nunique()),
        "raw_net_bps": round(float(E.r_bps.mean()) - L.COST, 2),
        "excess_vs_peer_bps": round(float(E.excess_bps.mean()), 2),
        "t_excess_daymeans": round(_t(E.groupby("cday").excess_bps.mean()) or np.nan, 2),
        "gate": L.run_gate(E, "r_bps", "M13_trending_entry_long")}
else:
    OUT["M13_coingecko_trending_entry"] = {"n_entries_raw": 0, "note": "no mappable entries"}

json.dump(OUT, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m11_m13_results.json", "w"), indent=1, default=str)
print(json.dumps(OUT, indent=1, default=str))
