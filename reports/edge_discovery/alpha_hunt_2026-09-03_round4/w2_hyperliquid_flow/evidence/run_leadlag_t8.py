#!/usr/bin/env python
"""W2 -- T8/T9: how far ahead of Binance does Hyperliquid actually move, and is that
distance reachable with this project's stack?

Data: data/microstructure_reduced/raw/bbo, venues binance / hyperliquid / okx,
BTC/ETH/SOL, one BBO update per venue event.  Two clocks are used on purpose:

  event_ts_ns    the venue's own timestamp -> "true" market lead-lag
  receive_ts_ns  our collector's local clock -> the only lead-lag a live system could act on

OKX is carried as a control venue: if HL "leads" Binance by construction (clock offset,
staler quotes), OKX should show the same artefact.

Outputs: leadlag_t8_results.json
Re-executable: .venv/bin/python evidence/run_leadlag_t8.py
"""
import os, sys, glob, json, numpy as np, duckdb

REPO = "/home/qbee/futur"
OUT = os.path.dirname(os.path.abspath(__file__))
BBO = f"{REPO}/data/microstructure_reduced/raw/bbo"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
VENUES = ["binance", "hyperliquid", "okx"]
GRID_MS = 100                                   # 100 ms grid
DAY_MS = 86_400_000
NG = DAY_MS//GRID_MS
MAXLAG = 300                                    # +-30 s in 100 ms steps
HOR_S = [1, 5, 15, 30, 60, 300]                 # forward horizons for the tradability test

con = duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2;")


def day_grid(venue, sym, day, clock):
    """last mid per 100ms bucket on `clock`, forward-filled over the whole day."""
    files = sorted(glob.glob(f"{BBO}/venue={venue}/symbol={sym}/date={day}/*.jsonl.gz"))
    if not files:
        return None
    g = np.full(NG, np.nan)
    t0 = None
    for f in files:
        try:
            df = con.execute(f"""
              select cast({clock}/{GRID_MS*1_000_000} as bigint) b,
                     last((bid_price+ask_price)/2.0 order by {clock}) mid
              from read_json_auto('{f}', columns={{'bid_price':'DOUBLE','ask_price':'DOUBLE',
                   'event_ts_ns':'BIGINT','receive_ts_ns':'BIGINT'}})
              where bid_price>0 and ask_price>0 group by 1""").df()
        except Exception as e:
            print("  skip", os.path.basename(f), e); continue
        if not len(df):
            continue
        b = df.b.values.astype(np.int64)
        if t0 is None:
            t0 = (b.min()//NG)*NG
        i = b - t0
        ok = (i >= 0) & (i < NG)
        g[i[ok]] = df.mid.values[ok]
    # HL publishes ~8 BBO updates/s vs Binance ~400/s, so a 50% bucket-coverage guard silently
    # drops the HL grid on the thinner symbols. 3% is enough to forward-fill a faithful mid.
    if t0 is None or np.isfinite(g).sum() < NG*0.03:
        return None
    # forward fill
    idx = np.where(np.isfinite(g), np.arange(NG), 0)
    np.maximum.accumulate(idx, out=idx)
    g = g[idx]
    first = np.argmax(np.isfinite(g))
    g[:first] = np.nan
    return t0, g


def xcorr(rl, rb, maxlag):
    """corr(rl[t], rb[t+k]) for k in [-maxlag, maxlag]. k>0 => HL LEADS Binance."""
    out = np.full(2*maxlag+1, np.nan)
    for j, k in enumerate(range(-maxlag, maxlag+1)):
        if k >= 0:
            a, b = rl[:len(rl)-k], rb[k:]
        else:
            a, b = rl[-k:], rb[:len(rb)+k]
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() < 500:
            continue
        out[j] = np.corrcoef(a[m], b[m])[0, 1]
    return out


days = sorted(os.path.basename(d).split("=")[1]
              for d in glob.glob(f"{BBO}/venue=binance/symbol=BTCUSDT/date=*"))
print("days:", days)

results = {"days": days, "grid_ms": GRID_MS, "per_symbol": {}}
for sym in SYMS:
    per_clock = {}
    for clock in ("event_ts_ns", "receive_ts_ns"):
        acc = {v: [] for v in VENUES if v != "binance"}
        trade = {h: {"num": 0.0, "n": 0, "hit": 0} for h in HOR_S}
        offs = []
        for day in days:
            gr = {}
            for v in VENUES:
                r = day_grid(v, sym, day, clock)
                if r is not None:
                    gr[v] = r
            if "binance" not in gr:
                continue
            t0 = gr["binance"][0]
            # align all venues on binance's bucket origin
            base = {}
            for v, (tv, g) in gr.items():
                sh = int(tv - t0)
                if sh == 0:
                    base[v] = g
                elif 0 < sh < NG:
                    base[v] = np.r_[np.full(sh, np.nan), g[:NG-sh]]
                elif -NG < sh < 0:
                    base[v] = np.r_[g[-sh:], np.full(-sh, np.nan)]
            if "binance" not in base:
                continue
            # 1-second log returns on the 100ms grid (step of 10 buckets)
            def ret(g, step=10):
                r = np.full(len(g), np.nan)
                with np.errstate(all="ignore"):
                    r[step:] = np.log(g[step:]/g[:-step])
                return np.where(np.isfinite(r) & (np.abs(r) < 0.05), r, np.nan)
            rb = ret(base["binance"])
            for v in acc:
                if v not in base:
                    continue
                acc[v].append(xcorr(ret(base[v]), rb, MAXLAG))
            # ---- tradability: does an HL 1s move predict Binance over the next H seconds?
            if "hyperliquid" in base:
                rl = ret(base["hyperliquid"])
                bb, hl = base["binance"], base["hyperliquid"]
                # dislocation z (T9): (mid_HL - mid_BIN)/mid_BIN in bps
                with np.errstate(all="ignore"):
                    disl = (hl/bb-1.0)*1e4
                offs.append(np.nanmedian(disl))
                sig = rl
                big = np.isfinite(sig) & (np.abs(sig) > np.nanquantile(np.abs(sig), 0.99))
                for h in HOR_S:
                    n = h*10
                    with np.errstate(all="ignore"):
                        fwd = np.full(len(bb), np.nan)
                        fwd[:-n] = np.log(bb[n:]/bb[:-n])*1e4
                    m = big & np.isfinite(fwd)
                    if m.sum():
                        s = np.sign(sig[m])*fwd[m]
                        trade[h]["num"] += float(np.nansum(s))
                        trade[h]["n"] += int(np.isfinite(s).sum())
                        trade[h]["hit"] += int((s > 0).sum())
            print(f"  {sym} {clock} {day} done", flush=True)
        cl = {}
        for v, xs in acc.items():
            if not xs:
                continue
            X = np.nanmean(np.vstack(xs), axis=0)
            k = np.arange(-MAXLAG, MAXLAG+1)
            fin = np.isfinite(X)
            if not fin.any():
                continue
            kbest = int(k[fin][np.nanargmax(X[fin])])
            cl[v] = {
                "argmax_lag_ms": kbest*GRID_MS,
                "argmax_corr": round(float(np.nanmax(X[fin])), 4),
                "corr_at_lag0": round(float(X[MAXLAG]), 4),
                "corr_profile_ms": {str(kk*GRID_MS): (round(float(X[MAXLAG+kk]), 4)
                                    if np.isfinite(X[MAXLAG+kk]) else None)
                                    for kk in (-100, -50, -20, -10, -5, -2, -1, 0,
                                               1, 2, 5, 10, 20, 50, 100, 300)},
            }
        cl["tradability_hl_move_then_binance"] = {
            str(h): {"gross_bps": round(trade[h]["num"]/trade[h]["n"], 3) if trade[h]["n"] else None,
                     "net_bps_14": round(trade[h]["num"]/trade[h]["n"]-14, 3) if trade[h]["n"] else None,
                     "n_obs_overlapping": trade[h]["n"],
                     "hit_rate": round(trade[h]["hit"]/trade[h]["n"], 4) if trade[h]["n"] else None}
            for h in HOR_S}
        cl["median_hl_minus_binance_bps_per_day"] = [round(float(o), 2) for o in offs]
        per_clock[clock] = cl
    results["per_symbol"][sym] = per_clock
    json.dump(results, open(f"{OUT}/leadlag_t8_results.json", "w"), indent=1, default=str)
    print("saved", sym, flush=True)
print("DONE")
