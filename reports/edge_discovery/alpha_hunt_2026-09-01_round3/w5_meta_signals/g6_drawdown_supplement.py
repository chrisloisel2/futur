"""
Supplementary pass: max-drawdown-in-cumulative-bps (simple additive, sequential order of
occurrence -- standard convention for these non-overlapping-episode backtests in this repo)
for the WITHOUT vs WITH series of the strongest/most-discussed candidates from Groups 1 and 3,
to satisfy the report's bps/PF/drawdown requirement (the main sweep scripts only tracked mean/PF/
t-stat; this reconstructs the same populations to add drawdown without re-deriving everything).
"""
import numpy as np
import pandas as pd

SC = "/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5"


def max_dd(bps_series):
    cum = np.cumsum(bps_series)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    return round(float(dd.min()), 2) if len(dd) else None


# ---------------- G1: liq-cascade-repeat, T1.1 (BTC vol regime enable) ----------------
liq = pd.read_parquet("/home/qbee/futur/data/events/liq_cascade_dataset.parquet").sort_values("event_time").reset_index(drop=True)
base = liq[(liq["kind"] == "LONG_CASCADE") & (liq["n_events_sym_24h"] >= 2)].copy()
COST_RT = 14.0
base["net4h"] = base["fwd_4h"] * 10000.0 - COST_RT


def decluster(df, gap_hours=4.0):
    keep_idx = []
    last_kept_time = {}
    for i, row in df.iterrows():
        sym = row["symbol"]; t = row["event_time"]
        prev = last_kept_time.get(sym)
        if prev is None or (t - prev).total_seconds() / 3600.0 >= gap_hours:
            keep_idx.append(i); last_kept_time[sym] = t
    return df.loc[keep_idx]


base_ind = decluster(base, 4.0).sort_values("event_time").reset_index(drop=True)
n = len(base_ind)
i = n // 2
train, test = base_ind.iloc[:i], base_ind.iloc[i:]
thresh = train["btc_vol_24h"].median() if "btc_vol_24h" in train.columns else None
print("btc_vol_24h present:", "btc_vol_24h" in base_ind.columns)
if "btc_vol_24h" in base_ind.columns:
    lo_mean = train.loc[train["btc_vol_24h"] <= thresh, "net4h"].mean()
    hi_mean = train.loc[train["btc_vol_24h"] > thresh, "net4h"].mean()
    direction = ">" if hi_mean >= lo_mean else "<="
    gate_test = (test["btc_vol_24h"] > thresh) if direction == ">" else (test["btc_vol_24h"] <= thresh)
    print("G1 T1.1: threshold", thresh, "direction", direction)
    print("  WITHOUT(test) maxDD:", max_dd(test["net4h"].values), "n=", len(test))
    print("  WITH(test, gated)  maxDD:", max_dd(test.loc[gate_test, "net4h"].values), "n=", gate_test.sum())
    gate_full = (base_ind["btc_vol_24h"] > thresh) if direction == ">" else (base_ind["btc_vol_24h"] <= thresh)
    print("  WITHOUT(full) maxDD:", max_dd(base_ind["net4h"].values), "n=", len(base_ind))
    print("  WITH(full, gated)  maxDD:", max_dd(base_ind.loc[gate_full, "net4h"].values), "n=", gate_full.sum())

# ---------------- G3: xsmom, T2.1 (BTC vol) and T2.3 (breadth) ----------------
panel = pd.read_parquet(f"{SC}/evidence/daily_ohlcv.parquet")
panel = panel.sort_values(["symbol", "day"]).reset_index(drop=True)
panel["day"] = pd.to_datetime(panel["day"]).dt.tz_localize(None)


def add_feat(g):
    g = g.sort_values("day").reset_index(drop=True)
    day_gap7 = (g["day"] - g["day"].shift(7)).dt.days
    day_gap_fwd7 = (g["day"].shift(-7) - g["day"]).dt.days
    g["tret_7d"] = np.where(day_gap7 == 7, g["close"] / g["close"].shift(7) - 1, np.nan)
    g["fwd_7d"] = np.where(day_gap_fwd7 == 7, g["close"].shift(-7) / g["close"] - 1, np.nan)
    g["qv_30d_median"] = g["quote_volume"].rolling(30, min_periods=15).median().shift(1)
    g["ret_1d"] = g["close"] / g["close"].shift(1) - 1
    return g


panel = panel.groupby("symbol", group_keys=False).apply(add_feat)
panel["liquid"] = panel["qv_30d_median"] >= 1_000_000
btc = panel[panel["symbol"] == "BTCUSDT"][["day", "ret_1d"]].rename(columns={"ret_1d": "btc_ret_1d"}).sort_values("day")
btc["btc_rvol_20d"] = btc["btc_ret_1d"].rolling(20, min_periods=10).std().shift(1)
btc = btc[["day", "btc_rvol_20d"]]
daily_xs = panel[panel["liquid"]].groupby("day").agg(xs_breadth_pos=("ret_1d", lambda x: (x > 0).mean())).reset_index()
daily_xs["xs_breadth_7d_avg"] = daily_xs["xs_breadth_pos"].rolling(7, min_periods=4).mean()
panel = panel.merge(btc, on="day", how="left").merge(daily_xs[["day", "xs_breadth_7d_avg"]], on="day", how="left")

COST_RT2 = 10.0
start_day = panel.loc[panel["liquid"], "day"].min()
end_day = panel["day"].max() - pd.Timedelta(days=7)
rebal_days = pd.date_range(start_day, end_day, freq="7D")
recs = []
for rd in rebal_days:
    day_rows = panel[(panel["day"] == rd) & panel["liquid"] & panel["tret_7d"].notna() & panel["fwd_7d"].notna()]
    if len(day_rows) < 10:
        continue
    q80 = day_rows["tret_7d"].quantile(0.8)
    top = day_rows[day_rows["tret_7d"] >= q80]
    if len(top) == 0:
        continue
    recs.append(dict(rebal_day=rd, net_bps=top["fwd_7d"].mean() * 10000.0 - COST_RT2,
                      btc_rvol_20d=day_rows["btc_rvol_20d"].iloc[0], xs_breadth_7d_avg=day_rows["xs_breadth_7d_avg"].iloc[0]))
weeks = pd.DataFrame(recs)

for feat, label in [("btc_rvol_20d", "T2.1"), ("xs_breadth_7d_avg", "T2.3")]:
    pop = weeks.dropna(subset=[feat]).sort_values("rebal_day").reset_index(drop=True)
    mid = pop["rebal_day"].median()
    train = pop[pop["rebal_day"] < mid]; test = pop[pop["rebal_day"] >= mid]
    thresh = train[feat].median()
    lo_mean = train.loc[train[feat] <= thresh, "net_bps"].mean(); hi_mean = train.loc[train[feat] > thresh, "net_bps"].mean()
    direction = ">" if hi_mean >= lo_mean else "<="
    gate_test = (test[feat] > thresh) if direction == ">" else (test[feat] <= thresh)
    gate_full = (pop[feat] > thresh) if direction == ">" else (pop[feat] <= thresh)
    print(f"\nG3 {label} ({feat} {direction} {round(thresh,4)}):")
    print("  WITHOUT(test) maxDD:", max_dd(test["net_bps"].values), "n=", len(test))
    print("  WITH(test, gated)  maxDD:", max_dd(test.loc[gate_test, "net_bps"].values), "n=", gate_test.sum())
    print("  WITHOUT(full) maxDD:", max_dd(pop["net_bps"].values), "n=", len(pop))
    print("  WITH(full, gated)  maxDD:", max_dd(pop.loc[gate_full, "net_bps"].values), "n=", gate_full.sum())
