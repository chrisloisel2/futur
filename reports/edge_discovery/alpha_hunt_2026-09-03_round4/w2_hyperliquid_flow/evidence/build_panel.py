#!/usr/bin/env python
"""W2_HYPERLIQUID_FLOW — step 1: build the TWAP episode table + the Binance 5m price matrix.

Outputs (all in SCRATCH, never under reports/):
  twap_ep.parquet     one row per unique HL TWAP order (dedup on user,coin,create_ms)
  panel.npz           dense 5-min matrices OPEN/CLOSE/QVOL [n_sym x n_bars] + market log index
Re-executable: python evidence/build_panel.py
"""
import os, sys, json, numpy as np, duckdb

REPO = "/home/qbee/futur"
SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
PERP = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance"
os.makedirs(SC, exist_ok=True)

GRID_T0 = 1704067200000          # 2024-01-01T00:00:00Z
STEP    = 300_000                # 5 min
GRID_T1 = 1785542400000          # 2026-08-01T00:00:00Z (panel Binance s'arrete la)
NB      = (GRID_T1 - GRID_T0) // STEP

con = duckdb.connect()
con.execute("PRAGMA memory_limit='6GB'; PRAGMA threads=4;")


def build_twap():
    f = f"{REPO}/data/hyperliquid/twap/*/*.parquet"
    con.execute(f"""
    create or replace view raw_t as select
      json_extract_string(raw,'$.state.user')                       usr,
      json_extract_string(raw,'$.state.coin')                       coin,
      cast(json_extract_string(raw,'$.state.timestamp') as bigint)  create_ms,
      json_extract_string(raw,'$.state.side')                       side,
      cast(json_extract_string(raw,'$.state.sz') as double)         sz,
      cast(json_extract_string(raw,'$.state.executedSz') as double) exec_sz,
      cast(json_extract_string(raw,'$.state.executedNtl') as double)exec_ntl,
      cast(json_extract_string(raw,'$.state.minutes') as double)    mins,
      json_extract_string(raw,'$.state.reduceOnly')                 ro,
      json_extract_string(raw,'$.state.randomize')                  rnd,
      json_extract_string(raw,'$.status.status')                    st,
      cast(json_extract_string(raw,'$.time') as bigint)*1000        rec_ms
    from read_parquet('{f}')""")
    con.execute("""
    create or replace table ep as
    select usr, coin, create_ms,
      any_value(side) side, max(sz) sz, max(mins) mins,
      any_value(ro) ro, any_value(rnd) rnd,
      -- terminal fields: DESCRIPTIVE ONLY, never a feature (not PIT at create_ms)
      max(case when st not in ('activated','waitingForTrigger') then exec_sz  end) fin_exec_sz,
      max(case when st not in ('activated','waitingForTrigger') then exec_ntl end) fin_exec_ntl,
      max(case when st not in ('activated','waitingForTrigger') then rec_ms   end) end_ms,
      any_value(case when st not in ('activated','waitingForTrigger') then st end)  final_st
    from raw_t group by usr, coin, create_ms""")
    con.execute(f"copy ep to '{SC}/twap_ep.parquet' (format parquet)")
    print("twap episodes:", con.execute("select count(*) from ep").fetchone()[0])


def coin_map():
    syms = set(x.split('=')[1] for x in os.listdir(PERP) if x.startswith('symbol='))
    coins = [r[0] for r in con.execute(
        f"select distinct coin from read_parquet('{SC}/twap_ep.parquet')").fetchall()]
    m = {}
    for c in coins:
        if c.startswith('@') or c.startswith('xyz:') or '/' in c:
            continue
        if c.startswith('k') and c[1:].isupper() and len(c) > 2 and ('1000'+c[1:]+'USDT') in syms:
            m[c] = '1000'+c[1:]+'USDT'; continue
        if c+'USDT' in syms:
            m[c] = c+'USDT'
    json.dump(m, open(f"{SC}/coin_map.json", "w"))
    print("coins mapped:", len(m))
    return m


def build_panel(symbols):
    ns = len(symbols)
    OPEN = np.full((ns, NB), np.nan, np.float32)
    CLOSE = np.full((ns, NB), np.nan, np.float32)
    QVOL = np.zeros((ns, NB), np.float32)
    sum_r = np.zeros(NB); cnt_r = np.zeros(NB)
    for i, s in enumerate(symbols):
        files = []
        for y in (2024, 2025, 2026):
            p = f"{PERP}/symbol={s}/year={y}/perp_5m.parquet"
            if os.path.exists(p): files.append(p)
        if not files:
            continue
        df = con.execute(
            "select epoch_ms(timestamp) t, open, close, quote_asset_volume q from read_parquet(?) "
            "where timestamp >= to_timestamp(?) and timestamp < to_timestamp(?) order by t",
            [files, GRID_T0/1000, GRID_T1/1000]).df()
        if not len(df):
            continue
        idx = ((df.t.values - GRID_T0) // STEP).astype(np.int64)
        ok = (idx >= 0) & (idx < NB)
        idx = idx[ok]
        OPEN[i, idx] = df.open.values[ok]
        CLOSE[i, idx] = df.close.values[ok]
        QVOL[i, idx] = np.nan_to_num(df.q.values[ok])
        # market return contribution (close-to-close on consecutive grid bars only)
        c = CLOSE[i]
        prev = np.roll(c, 1); prev[0] = np.nan
        with np.errstate(all='ignore'):
            r = np.log(c/prev)
        good = np.isfinite(r) & (np.abs(r) < 0.5)
        sum_r[good] += r[good]; cnt_r[good] += 1
        if i % 25 == 0:
            print(f"  {i}/{ns} {s}", flush=True)
    mkt_r = np.where(cnt_r >= 20, sum_r/np.maximum(cnt_r, 1), 0.0)
    mkt_idx = np.cumsum(mkt_r)
    np.savez_compressed(f"{SC}/panel.npz", OPEN=OPEN, CLOSE=CLOSE, QVOL=QVOL,
                        mkt_idx=mkt_idx, cnt_r=cnt_r,
                        symbols=np.array(symbols), grid_t0=GRID_T0, step=STEP, nb=NB)
    print("panel written", os.path.getsize(f"{SC}/panel.npz")/1e6, "MB")


if __name__ == "__main__":
    if not os.path.exists(f"{SC}/twap_ep.parquet") or "--force" in sys.argv:
        build_twap()
    m = coin_map()
    build_panel(sorted(set(m.values())))
