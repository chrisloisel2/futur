"""
Group 2: meta-signals layered on cross-sectional momentum (base signal reproduces W1's
M1/#1 finding: raw 7d trailing return, quintile rank, TOP QUINTILE LONG-ONLY -- W1 found the
edge concentrates almost entirely in top-decile continuation, so long-only captures most of it
and sidesteps SHORT_REJECTED; this is also the shape closest to CROSS_SECTIONAL_MOMENTUM_LIVE_V2
in the live registry). Universe: liquid cohort, trailing-30d median daily quote-volume >= $1M
(causal, lagged 1 day). Weekly non-overlapping rebalance. Cost: 5bps taker one-way (project
convention, matches W1) -> 10bps round trip per single-name long-only trade.

WITHOUT = trade the top-quintile basket every week, unconditionally (current best-known policy).
WITH = gate/size the SAME weekly basket by a regime meta-signal, direction+threshold chosen on
a TRAIN half (first ~half of history by time) and evaluated OOS on the TEST half (honest,
avoids picking the flattering direction after seeing the whole sample).
"""
import json
import numpy as np
import pandas as pd

OUT = "/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/g3_xsmom_meta.json"
COST_RT = 10.0  # bps, long-only single-name round trip, 5bps taker one-way x2

panel = pd.read_parquet("/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/daily_ohlcv.parquet")
panel = panel.sort_values(["symbol", "day"]).reset_index(drop=True)
panel["day"] = pd.to_datetime(panel["day"]).dt.tz_localize(None)

# gap-aware causal features per symbol
def add_symbol_features(g):
    g = g.sort_values("day").reset_index(drop=True)
    day_gap7 = (g["day"] - g["day"].shift(7)).dt.days
    day_gap_fwd7 = (g["day"].shift(-7) - g["day"]).dt.days
    g["tret_7d"] = np.where(day_gap7 == 7, g["close"] / g["close"].shift(7) - 1, np.nan)
    g["fwd_7d"] = np.where(day_gap_fwd7 == 7, g["close"].shift(-7) / g["close"] - 1, np.nan)
    g["qv_30d_median"] = g["quote_volume"].rolling(30, min_periods=15).median().shift(1)
    g["ret_1d"] = g["close"] / g["close"].shift(1) - 1
    return g

panel = panel.groupby("symbol", group_keys=False).apply(add_symbol_features)

LIQUID_USD = 1_000_000
panel["liquid"] = panel["qv_30d_median"] >= LIQUID_USD

# ---------------------------------------------------------------------------
# market/regime signals (BTC proxy + cross-sectional dispersion + breadth), all causal
# ---------------------------------------------------------------------------
btc = panel[panel["symbol"] == "BTCUSDT"][["day", "ret_1d"]].rename(columns={"ret_1d": "btc_ret_1d"}).sort_values("day")
btc["btc_rvol_20d"] = btc["btc_ret_1d"].rolling(20, min_periods=10).std().shift(1)  # trailing realized vol, causal (uses info up to and incl yesterday)
btc = btc[["day", "btc_rvol_20d"]]

# cross-sectional dispersion and breadth per day among liquid names (uses same-day ret_1d
# for the dispersion MEASURE -- but the traded decision only uses this dispersion measured
# on the rebalance day itself via ret_1d, which is same-day-causal at the day's close, same
# timing convention as tret_7d itself: both known at close[t], acted on for [t, t+7])
daily_xs = panel[panel["liquid"]].groupby("day").agg(
    xs_dispersion=("ret_1d", lambda x: x.std()),
    xs_breadth_pos=("ret_1d", lambda x: (x > 0).mean()),
    n_liquid=("ret_1d", "count"),
).reset_index()
daily_xs["xs_dispersion_7d_avg"] = daily_xs["xs_dispersion"].rolling(7, min_periods=4).mean()
daily_xs["xs_breadth_7d_avg"] = daily_xs["xs_breadth_pos"].rolling(7, min_periods=4).mean()

panel = panel.merge(btc, on="day", how="left").merge(daily_xs[["day", "xs_dispersion_7d_avg", "xs_breadth_7d_avg", "n_liquid"]], on="day", how="left")

# ---------------------------------------------------------------------------
# weekly non-overlapping rebalance grid, matching W1: start from earliest liquid-universe day
# ---------------------------------------------------------------------------
start_day = panel.loc[panel["liquid"], "day"].min()
end_day = panel["day"].max() - pd.Timedelta(days=7)
rebal_days = pd.date_range(start_day, end_day, freq="7D")

records = []
for rd in rebal_days:
    day_rows = panel[(panel["day"] == rd) & panel["liquid"] & panel["tret_7d"].notna() & panel["fwd_7d"].notna()]
    if len(day_rows) < 10:
        continue
    q80 = day_rows["tret_7d"].quantile(0.8)
    top = day_rows[day_rows["tret_7d"] >= q80]
    if len(top) == 0:
        continue
    basket_gross = top["fwd_7d"].mean() * 10000.0
    basket_net = basket_gross - COST_RT
    meta_row = day_rows[["btc_rvol_20d", "xs_dispersion_7d_avg", "xs_breadth_7d_avg", "n_liquid"]].iloc[0]
    records.append(dict(rebal_day=rd, n_universe=len(day_rows), n_top=len(top),
                         net_bps=basket_net, gross_bps=basket_gross,
                         btc_rvol_20d=meta_row["btc_rvol_20d"], xs_dispersion_7d_avg=meta_row["xs_dispersion_7d_avg"],
                         xs_breadth_7d_avg=meta_row["xs_breadth_7d_avg"]))

weeks = pd.DataFrame(records)
print("Rebalance weeks built:", len(weeks), "date range", weeks["rebal_day"].min(), "->", weeks["rebal_day"].max())


def summarize(bps):
    bps = np.asarray(bps, dtype=float)
    bps = bps[~np.isnan(bps)]
    n = len(bps)
    if n == 0:
        return dict(n=0, mean_bps=None, pf=None)
    mean = float(np.mean(bps))
    pos = bps[bps > 0].sum(); neg = -bps[bps < 0].sum()
    pf = float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else None)
    t_stat = float(mean / (np.std(bps, ddof=1) / np.sqrt(n))) if n > 1 and np.std(bps, ddof=1) > 0 else None
    return dict(n=n, mean_bps=round(mean, 2), pf=round(pf, 3) if pf not in (None, float("inf")) else pf,
                t_stat=round(t_stat, 2) if t_stat is not None else None)

BASELINE = summarize(weeks["net_bps"])
print("BASELINE (always-enabled top-quintile weekly basket):", BASELINE)

results = {}
results["baseline_xsmom_top_quintile"] = dict(n_weeks=len(weeks), stats=BASELINE)


def ab_traintest(df_weeks, feature_col, test_id, desc):
    pop = df_weeks.dropna(subset=[feature_col]).copy()
    mid = pop["rebal_day"].median()
    train = pop[pop["rebal_day"] < mid]
    test = pop[pop["rebal_day"] >= mid]
    thresh = train[feature_col].median()
    low_mean = train.loc[train[feature_col] <= thresh, "net_bps"].mean()
    high_mean = train.loc[train[feature_col] > thresh, "net_bps"].mean()
    direction = "<=" if low_mean >= high_mean else ">"
    gate_test = (test[feature_col] <= thresh) if direction == "<=" else (test[feature_col] > thresh)
    gate_full = (pop[feature_col] <= thresh) if direction == "<=" else (pop[feature_col] > thresh)
    without_test = summarize(test["net_bps"])
    with_test = summarize(test.loc[gate_test, "net_bps"])
    without_full = summarize(pop["net_bps"])
    with_full = summarize(pop.loc[gate_full, "net_bps"])
    delta_test = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None
    delta_full = round(with_full["mean_bps"] - without_full["mean_bps"], 2) if with_full["mean_bps"] is not None and without_full["mean_bps"] is not None else None
    print(f"[{test_id}] {desc} (train-picked: {feature_col} {direction} {round(thresh,6)})")
    print(f"   TEST(OOS) WITHOUT n={without_test['n']} mean={without_test['mean_bps']} | WITH n={with_test['n']} mean={with_test['mean_bps']}  delta={delta_test}")
    print(f"   FULL(context) WITHOUT mean={without_full['mean_bps']} | WITH n={with_full['n']} mean={with_full['mean_bps']}  delta={delta_full}")
    return dict(test_id=test_id, desc=desc, feature=feature_col, direction=direction, threshold=round(float(thresh), 6),
                n_train=len(train), n_test=len(test),
                test_without=without_test, test_with=with_test, delta_net_bps_oos=delta_test,
                full_without=without_full, full_with=with_full, delta_net_bps_full_context=delta_full)

# T2.1 -- ENABLE/DISABLE by BTC realized-vol regime (momentum-crash hypothesis)
results["T2_1"] = ab_traintest(weeks, "btc_rvol_20d", "T2.1", "ENABLE/DISABLE cross-sectional momentum basket by BTC 20d realized-vol regime")

# T2.2 -- ENABLE/DISABLE by cross-sectional dispersion regime
results["T2_2"] = ab_traintest(weeks, "xs_dispersion_7d_avg", "T2.2", "ENABLE/DISABLE by cross-sectional return-dispersion regime (trailing 7d avg)")

# T2.3 -- ENABLE/DISABLE by market breadth regime (trending vs choppy)
results["T2_3"] = ab_traintest(weeks, "xs_breadth_7d_avg", "T2.3", "ENABLE/DISABLE by market breadth regime (trailing 7d avg % names positive)")

# T2.4 -- REDUCE/INCREASE_RISK: size top-quintile basket by BTC vol regime (defensive
# sizing overlay -- reduce basket size, not gate to zero, in stormy BTC-vol weeks)
pop = weeks.dropna(subset=["btc_rvol_20d"]).copy()
med = pop["btc_rvol_20d"].median()
size = np.where(pop["btc_rvol_20d"] > med, 0.5, 1.5)
size = size * (len(size) / size.sum())
without_w = summarize(pop["net_bps"])
with_weighted_mean = float((size * pop["net_bps"]).sum() / size.sum())
delta = round(with_weighted_mean - without_w["mean_bps"], 2)
print(f"[T2.4] REDUCE/INCREASE_RISK: half-size in high-BTC-vol weeks, 1.5x in low-vol weeks: WITHOUT={without_w['mean_bps']} WITH={round(with_weighted_mean,2)} delta={delta}")
results["T2_4"] = dict(test_id="T2.4", desc="REDUCE/INCREASE_RISK: size xsmom basket inversely to BTC 20d realized vol (0.5x high-vol / 1.5x low-vol weeks, mean-1-normalized)",
                        n=len(pop), without=without_w, with_=dict(mean_bps=round(with_weighted_mean, 2)), delta_net_bps=delta)

# T2.5 -- SELECT_HORIZON: does BTC vol regime predict whether 7d vs 14d horizon captures
# more of the momentum edge? (W1 flagged H14 weaker in aggregate; test if regime explains it)
_sym_groups = {s: g.sort_values("day").reset_index(drop=True) for s, g in panel.groupby("symbol")}
_sym_day_idx = {s: pd.Series(g.index.values, index=g["day"].values) for s, g in _sym_groups.items()}

def build_horizon_weeks(hz_days):
    recs = []
    for rd in rebal_days:
        day_rows = panel[(panel["day"] == rd) & panel["liquid"] & panel["tret_7d"].notna()]
        if len(day_rows) < 10:
            continue
        q80 = day_rows["tret_7d"].quantile(0.8)
        top_syms = day_rows.loc[day_rows["tret_7d"] >= q80, "symbol"].tolist()
        if not top_syms:
            continue
        fwd_rets = []
        for s in top_syms:
            g = _sym_groups[s]
            idx_map = _sym_day_idx[s]
            if rd.to_datetime64() not in idx_map.index:
                continue
            i = int(idx_map.loc[rd.to_datetime64()])
            if i + hz_days >= len(g):
                continue
            d0 = g.iloc[i]["day"]; d1 = g.iloc[i + hz_days]["day"]
            if (d1 - d0).days != hz_days:
                continue
            fwd_rets.append(g.iloc[i + hz_days]["close"] / g.iloc[i]["close"] - 1)
        if not fwd_rets:
            continue
        recs.append(dict(rebal_day=rd, net_bps=np.mean(fwd_rets) * 10000.0 - COST_RT))
    return pd.DataFrame(recs)

weeks_h14 = build_horizon_weeks(14)
merged_h = weeks[["rebal_day", "net_bps", "btc_rvol_20d"]].rename(columns={"net_bps": "net7d"}).merge(
    weeks_h14.rename(columns={"net_bps": "net14d"}), on="rebal_day", how="inner")
pop = merged_h.dropna(subset=["btc_rvol_20d"]).copy()
mid = pop["rebal_day"].median()
train = pop[pop["rebal_day"] < mid]; test = pop[pop["rebal_day"] >= mid]
med_train = train["btc_rvol_20d"].median()
mean7_lo = train.loc[train["btc_rvol_20d"] <= med_train, "net7d"].mean()
mean14_lo = train.loc[train["btc_rvol_20d"] <= med_train, "net14d"].mean()
mean7_hi = train.loc[train["btc_rvol_20d"] > med_train, "net7d"].mean()
mean14_hi = train.loc[train["btc_rvol_20d"] > med_train, "net14d"].mean()
pick_lo = "7d" if mean7_lo >= mean14_lo else "14d"
pick_hi = "7d" if mean7_hi >= mean14_hi else "14d"
def apply_pick(row):
    bucket_lo = row["btc_rvol_20d"] <= med_train
    pick = pick_lo if bucket_lo else pick_hi
    return row["net7d"] if pick == "7d" else row["net14d"]
test_with = test.apply(apply_pick, axis=1)
without_test = summarize(test["net7d"])
with_test = summarize(test_with)
delta = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None
print(f"[T2.5] SELECT_HORIZON by BTC vol regime: train picks lo-vol->{pick_lo}, hi-vol->{pick_hi}")
print(f"   TEST(OOS) WITHOUT(fixed 7d) n={without_test['n']} mean={without_test['mean_bps']} | WITH(picked) n={with_test['n']} mean={with_test['mean_bps']}  delta={delta}")
results["T2_5"] = dict(test_id="T2.5", desc="SELECT_HORIZON: BTC-vol-regime picks {7d,14d} on train half, evaluated OOS on test half",
                        train_picks=dict(low_vol=pick_lo, high_vol=pick_hi), n_train=len(train), n_test=len(test),
                        without=without_test, with_=with_test, delta_net_bps=delta)

with open(OUT, "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nWrote", OUT)
