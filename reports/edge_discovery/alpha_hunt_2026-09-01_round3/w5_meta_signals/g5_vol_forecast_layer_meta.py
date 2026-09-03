"""
Group 5: meta-signals using VOL_FORECAST_LAYER_V1's OWN daily forecast output
(reports/live_alpha_lab/VOL_FORECAST_LAYER_V1/decisions.parquet, read-only) as an
ENABLE/DISABLE gate on OTHER base signals (xsmom from Group 3, liq-cascade-repeat from
Group 1). This is distinct from Groups 1/3's own realized-vol proxies: combined_forecast_z
is a FROZEN, already-built institutional forward-RV forecast (options RV/IV spread +
far-OTM put share + block-trade flow, combined; see configs/live_alpha_registry.yaml,
NOT modified here, read-only reference) -- using an existing engine's output as a
cross-strategy regime gate is exactly the "does the vol regime signal from one place
predict when a different alpha's edge is live" mechanism the brief calls out explicitly.
BTC-only forecast (VOL_FORECAST_LAYER_V1 universe=[BTCUSDT]), applied as a market-wide gate
(consistent with how G1/G3 already use BTC vol/return as market-wide regime proxies).
"""
import json
import numpy as np
import pandas as pd

ROOT = "/home/qbee/futur"
SC = "/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5"
OUT = f"{SC}/evidence/g5_vol_forecast_layer_meta.json"

vfl = pd.read_parquet(f"{ROOT}/reports/live_alpha_lab/VOL_FORECAST_LAYER_V1/decisions.parquet")
vfl = vfl[["day", "combined_forecast_z", "forecast_direction", "confidence", "iv_regime", "n_signals_available"]].copy()
vfl["day"] = pd.to_datetime(vfl["day"]).dt.tz_localize(None).dt.normalize()
vfl = vfl[vfl["n_signals_available"] > 0]  # drop days where the layer itself had no signal (n=89 rows)
print("VOL_FORECAST_LAYER_V1 decisions loaded:", len(vfl), "days,", vfl["day"].min(), "->", vfl["day"].max())


def summarize(bps):
    bps = np.asarray(bps, dtype=float)
    bps = bps[~np.isnan(bps)]
    n = len(bps)
    if n == 0:
        return dict(n=0, mean_bps=None, pf=None, t_stat=None, win_rate=None)
    mean = float(np.mean(bps))
    pos = bps[bps > 0].sum(); neg = -bps[bps < 0].sum()
    pf = float(pos / neg) if neg > 0 else (None if pos == 0 else float("inf"))
    t = float(mean / (np.std(bps, ddof=1) / np.sqrt(n))) if n > 1 and np.std(bps, ddof=1) > 0 else None
    return dict(n=n, mean_bps=round(mean, 2), pf=(round(pf, 3) if pf not in (None, float("inf")) else pf),
                t_stat=round(t, 2) if t is not None else None, win_rate=round(float((bps > 0).mean()), 3))


def ab_traintest_continuous(pop, date_col, feature_col, bps_col, test_id, desc):
    pop = pop.dropna(subset=[feature_col, bps_col]).copy().sort_values(date_col)
    mid = pop[date_col].median()
    train = pop[pop[date_col] < mid]; test = pop[pop[date_col] >= mid]
    if len(train) < 4 or len(test) < 4:
        return dict(test_id=test_id, desc=desc, status="SKIPPED_INSUFFICIENT_N", n_train=len(train), n_test=len(test))
    thresh = train[feature_col].median()
    lo_mean = train.loc[train[feature_col] <= thresh, bps_col].mean()
    hi_mean = train.loc[train[feature_col] > thresh, bps_col].mean()
    direction = "<=" if lo_mean >= hi_mean else ">"
    gate_test = (test[feature_col] <= thresh) if direction == "<=" else (test[feature_col] > thresh)
    without_test = summarize(test[bps_col])
    with_test = summarize(test.loc[gate_test, bps_col])
    delta = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None
    print(f"[{test_id}] {desc} (train-picked: {feature_col} {direction} {round(thresh,4)}), n_train={len(train)} n_test={len(test)}")
    print(f"   TEST(OOS) WITHOUT n={without_test['n']} mean={without_test['mean_bps']} | WITH n={with_test['n']} mean={with_test['mean_bps']}  delta={delta}")
    return dict(test_id=test_id, desc=desc, feature=feature_col, direction=direction, threshold=round(float(thresh), 6),
                n_train=len(train), n_test=len(test), without=without_test, with_=with_test, delta_net_bps=delta)


results = {}

# ---------------------------------------------------------------------------
# T4.1 -- ENABLE/DISABLE xsmom weekly top-quintile basket (Group 3's base signal,
# same construction/costs) by VOL_FORECAST_LAYER_V1's combined_forecast_z.
# ---------------------------------------------------------------------------
weeks = pd.read_json(f"{SC}/evidence/g3_xsmom_meta.json")  # not used directly; rebuild from panel for a clean merge
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
    return g


panel = panel.groupby("symbol", group_keys=False).apply(add_feat)
panel["liquid"] = panel["qv_30d_median"] >= 1_000_000
COST_RT = 10.0

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
    recs.append(dict(rebal_day=rd, net_bps=top["fwd_7d"].mean() * 10000.0 - COST_RT))
weeks = pd.DataFrame(recs)
weeks["day_norm"] = weeks["rebal_day"].dt.normalize()
weeks = weeks.merge(vfl, left_on="day_norm", right_on="day", how="left")
print("\nxsmom weeks merged with VOL_FORECAST_LAYER_V1:", weeks["combined_forecast_z"].notna().sum(), "/", len(weeks), "matched")

results["T4_1"] = ab_traintest_continuous(weeks, "rebal_day", "combined_forecast_z", "net_bps", "T4.1",
                                           "ENABLE/DISABLE xsmom weekly basket by VOL_FORECAST_LAYER_V1 combined_forecast_z (forward-RV forecast, cross-strategy gate)")

# T4.1b -- same gate, categorical iv_regime (train-picked best category)
pop = weeks.dropna(subset=["iv_regime", "net_bps"]).copy().sort_values("rebal_day")
mid = pop["rebal_day"].median()
train = pop[pop["rebal_day"] < mid]; test = pop[pop["rebal_day"] >= mid]
if len(train) >= 6 and len(test) >= 6:
    cat_means = train.groupby("iv_regime")["net_bps"].mean()
    best_cat = cat_means.idxmax()
    without_test = summarize(test["net_bps"])
    with_test = summarize(test.loc[test["iv_regime"] == best_cat, "net_bps"])
    delta = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None
    print(f"[T4.1b] ENABLE/DISABLE xsmom by VOL_FORECAST_LAYER_V1 iv_regime (train-picked best={best_cat}, train means={cat_means.round(2).to_dict()})")
    print(f"   TEST(OOS) WITHOUT n={without_test['n']} mean={without_test['mean_bps']} | WITH n={with_test['n']} mean={with_test['mean_bps']}  delta={delta}")
    results["T4_1b"] = dict(test_id="T4.1b", desc="ENABLE/DISABLE xsmom weekly basket by VOL_FORECAST_LAYER_V1 iv_regime categorical (train-picked best category)",
                             train_cat_means=cat_means.round(2).to_dict(), best_cat=best_cat, n_train=len(train), n_test=len(test),
                             without=without_test, with_=with_test, delta_net_bps=delta)
else:
    results["T4_1b"] = dict(test_id="T4.1b", status="SKIPPED_INSUFFICIENT_N")

# ---------------------------------------------------------------------------
# T4.2 -- ENABLE/DISABLE liq-cascade-repeat (Group 1's base signal) by
# combined_forecast_z, at event level (merge by calendar day of event_time).
# ---------------------------------------------------------------------------
liq = pd.read_parquet(f"{ROOT}/data/events/liq_cascade_dataset.parquet")
liq = liq.sort_values("event_time").reset_index(drop=True)
base = liq[(liq["kind"] == "LONG_CASCADE") & (liq["n_events_sym_24h"] >= 2)].copy()
COST_RT2 = 14.0
base["net4h"] = base["fwd_4h"] * 10000.0 - COST_RT2


def decluster(df, gap_hours=4.0):
    keep_idx = []
    last_kept_time = {}
    for i, row in df.iterrows():
        sym = row["symbol"]; t = row["event_time"]
        prev = last_kept_time.get(sym)
        if prev is None or (t - prev).total_seconds() / 3600.0 >= gap_hours:
            keep_idx.append(i); last_kept_time[sym] = t
    return df.loc[keep_idx]


base_ind = decluster(base, 4.0)
base_ind = base_ind.copy()
base_ind["day_norm"] = pd.to_datetime(base_ind["event_time"]).dt.tz_localize(None).dt.normalize()
base_ind = base_ind.merge(vfl, left_on="day_norm", right_on="day", how="left")
print(f"\nliq-cascade-repeat independent episodes: raw N_indep={len(base_ind)}, matched to VOL_FORECAST_LAYER_V1: {base_ind['combined_forecast_z'].notna().sum()}")

results["T4_2"] = ab_traintest_continuous(base_ind, "event_time", "combined_forecast_z", "net4h", "T4.2",
                                           "ENABLE/DISABLE liq-cascade-repeat-exhaustion (N_independent) by VOL_FORECAST_LAYER_V1 combined_forecast_z")

with open(OUT, "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nWrote", OUT)
