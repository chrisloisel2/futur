"""W5/s02 - H1: is the probe's markout mechanically negative by construction?

Derivation (BUY): limit = bid_place. Fill requires ask_fill < limit, i.e. ask_fill <= limit - tick.
mid_fill = ask_fill - spread_fill/2 <= limit - tick - spread_fill/2.
Under a martingale after the fill, E[mid_60s] = mid_fill, so
    E[adv_bps_60s | fill] <= -(tick_bps + half_spread_bps).
That is a MECHANICAL FLOOR: it contains zero information about adverse selection.
"""
import duckdb, os, numpy as np, pandas as pd, json
S = os.environ["W5_SCRATCH"]; P = f"{S}/probe.parquet"
con = duckdb.connect(); con.execute("PRAGMA threads=8")
ticks = pd.read_csv(f"{S}/ticks.csv")[["symbol", "tick_bps"]]
out = {}

# --- per-symbol observed markout vs mechanical floor ---
d = con.execute(f"""
SELECT symbol,
       count(*) n, avg(CASE WHEN filled THEN 1 ELSE 0 END) fill_rate,
       avg(spread_bps) spread_bps,
       avg(adv_bps_60s)  FILTER (WHERE filled) adv60,
       avg(adv_bps_300s) FILTER (WHERE filled) adv300,
       avg(spread_bps)   FILTER (WHERE filled) spread_filled
FROM read_parquet('{P}') GROUP BY symbol ORDER BY symbol
""").df().merge(ticks, on="symbol")
d["floor_bps"] = -(d.tick_bps + d.spread_filled / 2.0)
d["excess_vs_floor60"] = d.adv60 - d.floor_bps      # >0 = better than mechanical floor
d["recovery_60_300"]   = d.adv300 - d.adv60         # >0 = price came back = benign
print("=== H1a: observed markout vs MECHANICAL FLOOR (per symbol) ===")
print(d[["symbol","n","fill_rate","spread_filled","tick_bps","adv60","adv300",
         "floor_bps","excess_vs_floor60","recovery_60_300"]].round(3).to_string())

from scipy.stats import spearmanr
rho_floor = spearmanr(d.adv60, d.floor_bps)
rho_spr   = spearmanr(d.adv60, -d.spread_filled)
print(f"\ncross-symbol Spearman(adv60, mechanical_floor) = {rho_floor.correlation:.3f} p={rho_floor.pvalue:.2e}")
print(f"cross-symbol Spearman(adv60, -spread)          = {rho_spr.correlation:.3f} p={rho_spr.pvalue:.2e}")
# R^2 of floor explaining cross-symbol markout
b, a = np.polyfit(d.floor_bps, d.adv60, 1)
r2 = np.corrcoef(d.floor_bps, d.adv60)[0,1]**2
print(f"cross-symbol OLS adv60 = {a:.3f} + {b:.3f} * floor   R2={r2:.3f}")
out["h1a_per_symbol"] = d.round(4).to_dict("records")
out["h1a_spearman_floor"] = [float(rho_floor.correlation), float(rho_floor.pvalue)]
out["h1a_spearman_negspread"] = [float(rho_spr.correlation), float(rho_spr.pvalue)]
out["h1a_ols_floor"] = {"intercept": float(a), "slope": float(b), "r2": float(r2)}

# --- pooled + within-symbol OLS of adv60 on spread ---
print("\n=== H1b: OLS adv_bps_60s ~ spread_bps ===")
rows = []
pooled = con.execute(f"""
SELECT regr_slope(adv_bps_60s, spread_bps) s, regr_intercept(adv_bps_60s, spread_bps) i,
       regr_r2(adv_bps_60s, spread_bps) r2, count(*) n
FROM read_parquet('{P}') WHERE filled""").df().iloc[0]
print(f"POOLED : slope={pooled.s:.3f} intercept={pooled.i:.3f} R2={pooled.r2:.3f} n={int(pooled.n)}")
ws = con.execute(f"""
SELECT symbol, regr_slope(adv_bps_60s, spread_bps) s, regr_intercept(adv_bps_60s, spread_bps) i,
       regr_r2(adv_bps_60s, spread_bps) r2, count(*) n
FROM read_parquet('{P}') WHERE filled GROUP BY symbol ORDER BY symbol""").df()
print(ws.round(3).to_string())
out["h1b_pooled"] = {k: float(pooled[k]) for k in ["s","i","r2","n"]}
out["h1b_within_symbol"] = ws.round(4).to_dict("records")

# --- H1c: markout normalised by the floor. If markout/floor ~ 1, it is 100% mechanical ---
d["ratio_obs_over_floor"] = d.adv60 / d.floor_bps
print("\n=== H1c: adv60 / mechanical_floor (1.0 = fully explained by construction) ===")
print(d[["symbol","adv60","floor_bps","ratio_obs_over_floor"]].round(3).to_string())
print(f"\nmedian ratio = {d.ratio_obs_over_floor.median():.3f}  mean = {d.ratio_obs_over_floor.mean():.3f}")
out["h1c_ratio_median"] = float(d.ratio_obs_over_floor.median())

json.dump(out, open(f"{S}/h1.json","w"), indent=1, default=str)
d.round(4).to_csv(f"{S}/h1_per_symbol.csv", index=False)
