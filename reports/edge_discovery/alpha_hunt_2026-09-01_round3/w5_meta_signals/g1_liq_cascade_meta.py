"""
Group 1: meta-signals layered on the liq_cascade family (LIQ_CASCADE_REPEAT_V1 /
LIQ_CASCADE_SHORT_SQUEEZE (blocked, for context only) / LIQ_CASCADE_FAR_FROM_LOW_V1).

Base signal used for most tests: LONG_CASCADE "exhaustion" repeat-count>=2 -> LONG @ fwd_4h,
exactly reproducing the entry rule already frozen as LIQ_CASCADE_REPEAT_V1
(src/institutional/engines/liq_cascade/repeat_variant.py::classify_repeat_bucket, per
configs/live_alpha_registry.yaml). Cost model: net = gross_bps - 14 (round-trip taker,
project COST_RT convention, matches W2/registry).

Discipline: decluster first. Two N's: N_raw = every row satisfying the entry filter; N_independent
collapses same-symbol events whose forward-4h holding windows overlap (gap < 4h since the last KEPT
event) into one episode, keeping only the first of each cluster -- this matters a lot here because
"n_events_sym_24h>=2" by construction re-fires on every subsequent cascade in an already-firing
cluster.
"""
import json
import numpy as np
import pandas as pd

pd.set_option("display.width", 200)

OUT = "/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/g1_liq_cascade_meta.json"
COST_RT = 14.0  # bps, round-trip taker, project convention

df_all = pd.read_parquet("/home/qbee/futur/data/events/liq_cascade_dataset.parquet")
df_all = df_all.sort_values("event_time").reset_index(drop=True)


def decluster(df, gap_hours=4.0):
    """Collapse same-symbol rows whose fwd_4h holding windows overlap; keep 1st of each cluster."""
    keep_idx = []
    last_kept_time = {}
    for i, row in df.iterrows():
        sym = row["symbol"]
        t = row["event_time"]
        prev = last_kept_time.get(sym)
        if prev is None or (t - prev).total_seconds() / 3600.0 >= gap_hours:
            keep_idx.append(i)
            last_kept_time[sym] = t
    return df.loc[keep_idx]


def net_bps(fwd_col_vals):
    return fwd_col_vals * 10000.0 - COST_RT


def summarize(bps, label=""):
    bps = np.asarray(bps, dtype=float)
    bps = bps[~np.isnan(bps)]
    n = len(bps)
    if n == 0:
        return dict(n=0, mean_bps=None, pf=None)
    mean = float(np.mean(bps))
    pos = bps[bps > 0].sum()
    neg = -bps[bps < 0].sum()
    pf = float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else None)
    win_rate = float((bps > 0).mean())
    t_stat = float(mean / (np.std(bps, ddof=1) / np.sqrt(n))) if n > 1 and np.std(bps, ddof=1) > 0 else None
    return dict(n=n, mean_bps=round(mean, 2), pf=round(pf, 3) if pf not in (None, float("inf")) else pf,
                win_rate=round(win_rate, 3), t_stat=round(t_stat, 2) if t_stat is not None else None)


def by_year(df, bps_col="net4h", label=""):
    out = {}
    for y, g in df.groupby(df["event_time"].dt.year):
        out[int(y)] = summarize(g[bps_col])
    return out


results = {}

# ---------------------------------------------------------------------------
# Base population: LONG_CASCADE, n_events_sym_24h >= 2 (repeat/exhaustion, matches
# LIQ_CASCADE_REPEAT_V1 entry rule exactly)
# ---------------------------------------------------------------------------
base = df_all[(df_all["kind"] == "LONG_CASCADE") & (df_all["n_events_sym_24h"] >= 2)].copy()
base["net4h"] = net_bps(base["fwd_4h"])
base["net1h"] = net_bps(base["fwd_1h"])
base["net8h"] = net_bps(base["fwd_8h"])

base_raw_n = len(base)
base_decl = decluster(base, gap_hours=4.0)
base_decl["net4h"] = net_bps(base_decl["fwd_4h"])

BASELINE_RAW = summarize(base["net4h"])
BASELINE_DECL = summarize(base_decl["net4h"])

print("=== BASELINE (LIQ_CASCADE_REPEAT_V1 replica, always-enabled) ===")
print("raw N:", base_raw_n, BASELINE_RAW)
print("independent N (decluster gap>=4h):", len(base_decl), BASELINE_DECL)

results["baseline_repeat_exhaustion"] = dict(
    n_raw=base_raw_n, n_independent=len(base_decl),
    raw=BASELINE_RAW, independent=BASELINE_DECL,
    by_year_independent=by_year(base_decl),
)

# use the DECLUSTERED population as the "N_independent" population for all gating tests below;
# also keep raw for reference. Gating tests are computed on both raw and independent pops.

def ab_enable_disable(df_pop, gate_bool, test_id, desc, pop_label="independent"):
    """WITHOUT = trade all of df_pop. WITH = trade only where gate_bool True."""
    without = summarize(df_pop["net4h"])
    with_ = summarize(df_pop.loc[gate_bool, "net4h"])
    excluded = summarize(df_pop.loc[~gate_bool, "net4h"])
    delta = None
    if with_["mean_bps"] is not None and without["mean_bps"] is not None:
        delta = round(with_["mean_bps"] - without["mean_bps"], 2)
    rec = dict(test_id=test_id, desc=desc, pop=pop_label,
               without=without, with_=with_, excluded=excluded, delta_net_bps=delta)
    print(f"[{test_id}] {desc}")
    print(f"   WITHOUT n={without['n']} mean={without['mean_bps']} PF={without['pf']}")
    print(f"   WITH    n={with_['n']} mean={with_['mean_bps']} PF={with_['pf']}  delta={delta}")
    print(f"   excluded n={excluded['n']} mean={excluded['mean_bps']}")
    return rec


def ab_enable_disable_traintest(df_pop, feature_col, test_id, desc, bps_col="net4h"):
    """Direction-honest version of ab_enable_disable: split df_pop by time median into
    train/test. On TRAIN, compute the feature's own median and check which side (<=med
    or >=med) has the higher mean bps -- that decision (direction) is fixed. Apply the
    TRAIN-derived threshold+direction to TEST only, report TEST WITH vs WITHOUT as the
    primary (OOS-honest) delta. Full-sample (train+test, same train-derived rule) also
    reported for context, flagged as including the fitting period."""
    pop = df_pop.dropna(subset=[feature_col]).copy()
    mid_time = pop["event_time"].median()
    train = pop[pop["event_time"] < mid_time]
    test = pop[pop["event_time"] >= mid_time]
    thresh = train[feature_col].median()
    low_mean = train.loc[train[feature_col] <= thresh, bps_col].mean()
    high_mean = train.loc[train[feature_col] > thresh, bps_col].mean()
    direction = "<=" if low_mean >= high_mean else ">"
    gate_test = (test[feature_col] <= thresh) if direction == "<=" else (test[feature_col] > thresh)
    gate_full = (pop[feature_col] <= thresh) if direction == "<=" else (pop[feature_col] > thresh)

    without_test = summarize(test[bps_col])
    with_test = summarize(test.loc[gate_test, bps_col])
    excluded_test = summarize(test.loc[~gate_test, bps_col])
    delta_test = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None

    without_full = summarize(pop[bps_col])
    with_full = summarize(pop.loc[gate_full, bps_col])
    delta_full = round(with_full["mean_bps"] - without_full["mean_bps"], 2) if with_full["mean_bps"] is not None and without_full["mean_bps"] is not None else None

    print(f"[{test_id}] {desc}  (train-picked direction: {feature_col} {direction} {round(thresh,4)})")
    print(f"   TEST(OOS)  WITHOUT n={without_test['n']} mean={without_test['mean_bps']} | WITH n={with_test['n']} mean={with_test['mean_bps']}  delta={delta_test}")
    print(f"   FULL(train+test, context only) WITHOUT mean={without_full['mean_bps']} | WITH n={with_full['n']} mean={with_full['mean_bps']}  delta={delta_full}")
    return dict(test_id=test_id, desc=desc, feature=feature_col, direction=direction, threshold=round(float(thresh), 6),
                n_train=len(train), n_test=len(test),
                test_without=without_test, test_with=with_test, test_excluded=excluded_test, delta_net_bps_oos=delta_test,
                full_without=without_full, full_with=with_full, delta_net_bps_full_context=delta_full,
                gate_test_mask=None)

# =============================================================================
# TEST 1.1 -- ENABLE/DISABLE by BTC vol regime (btc_vol_24h, causal, already in dataset).
# Direction NOT assumed a priori (calm vs stormy both economically plausible) -- picked on
# train half, validated OOS on test half (see ab_enable_disable_traintest).
# =============================================================================
results["T1_1"] = ab_enable_disable_traintest(base_decl, "btc_vol_24h", "T1.1",
    "ENABLE/DISABLE by BTC vol regime (direction train-picked: calm vs stormy)")
_t1_gate_full = base_decl["btc_vol_24h"] > results["T1_1"]["threshold"]
results["T1_1"]["by_year_with_gate_full_context"] = by_year(base_decl.loc[_t1_gate_full])
results["T1_1"]["by_year_without_full_context"] = by_year(base_decl)

# =============================================================================
# TEST 1.2 -- ENABLE/DISABLE by market-wide breadth (n_events_mktwide_30m): isolated
# single-symbol cascade vs market-wide multi-symbol cascade cluster. Direction train-picked.
# =============================================================================
results["T1_2"] = ab_enable_disable_traintest(base_decl, "n_events_mktwide_30m", "T1.2",
    "ENABLE/DISABLE by market-wide cascade breadth (direction train-picked: isolated vs market-wide)")

# =============================================================================
# TEST 1.3 -- SELECT_ASSET: within concurrent-episode windows (>=2 symbols firing same
# day), does prioritizing by liquidity (vol_24h) pick the better one?
# =============================================================================
pop = base_decl.copy()
pop["date"] = pop["event_time"].dt.floor("D")
grp_sizes = pop.groupby("date")["symbol"].transform("count")
concurrent = pop[grp_sizes >= 2].copy()
selected_rows = []
unselected_rows = []
for d, g in concurrent.groupby("date"):
    top = g.loc[g["vol_24h"].idxmax()]
    rest = g.drop(g["vol_24h"].idxmax())
    selected_rows.append(top)
    unselected_rows.extend(rest.to_dict("records"))
sel_df = pd.DataFrame(selected_rows)
unsel_df = pd.DataFrame(unselected_rows)
sel_stats = summarize(sel_df["net4h"]) if len(sel_df) else summarize([])
unsel_stats = summarize(unsel_df["net4h"]) if len(unsel_df) else summarize([])
naive_stats = summarize(concurrent["net4h"])  # WITHOUT = take everything concurrently (no selection / equal-weight all)
delta = round(sel_stats["mean_bps"] - naive_stats["mean_bps"], 2) if sel_stats["mean_bps"] is not None and naive_stats["mean_bps"] is not None else None
print(f"[T1.3] SELECT_ASSET by vol_24h (liquidity) on concurrent days: selected n={sel_stats['n']} mean={sel_stats['mean_bps']} | naive(all) n={naive_stats['n']} mean={naive_stats['mean_bps']} | unselected n={unsel_stats['n']} mean={unsel_stats['mean_bps']} | delta(selected vs naive-all)={delta}")
results["T1_3"] = dict(test_id="T1.3", desc="SELECT_ASSET: on days with >=2 concurrent repeat-cascade candidates, pick the highest-vol_24h symbol",
                        n_concurrent_days=int(concurrent["date"].nunique()),
                        selected=sel_stats, unselected=unsel_stats, naive_all=naive_stats, delta_net_bps=delta)

# =============================================================================
# TEST 1.4 -- SELECT_HORIZON: does funding_z30 bucket predict whether fwd_1h/4h/8h
# captures more of the edge? Train (first half by time) picks best horizon per bucket,
# test (second half) evaluates out-of-sample.
# =============================================================================
pop = base_decl.dropna(subset=["funding_z30"]).copy()
pop["net1h"] = net_bps(pop["fwd_1h"])
pop["net4h"] = net_bps(pop["fwd_4h"])
pop["net8h"] = net_bps(pop["fwd_8h"])
pop["fz_bucket"] = pd.cut(pop["funding_z30"], bins=[-np.inf, -1.0, 1.0, np.inf], labels=["neg", "mid", "pos"])
mid_time = pop["event_time"].median()
train = pop[pop["event_time"] < mid_time]
test = pop[pop["event_time"] >= mid_time]
best_horizon = {}
for b, g in train.groupby("fz_bucket", observed=True):
    means = {"1h": g["net1h"].mean(), "4h": g["net4h"].mean(), "8h": g["net8h"].mean()}
    best_horizon[str(b)] = max(means, key=means.get)
with_bps = []
without_bps = []
for _, row in test.iterrows():
    b = str(row["fz_bucket"])
    h = best_horizon.get(b, "4h")
    with_bps.append(row[f"net{h}"])
    without_bps.append(row["net4h"])
with_stats = summarize(with_bps)
without_stats = summarize(without_bps)
delta = round(with_stats["mean_bps"] - without_stats["mean_bps"], 2) if with_stats["mean_bps"] is not None and without_stats["mean_bps"] is not None else None
print(f"[T1.4] SELECT_HORIZON by funding_z30 bucket (train/test split): train-picked horizons={best_horizon}")
print(f"   WITHOUT(fixed 4h) test n={without_stats['n']} mean={without_stats['mean_bps']}")
print(f"   WITH(picked horizon) test n={with_stats['n']} mean={with_stats['mean_bps']}  delta={delta}")
results["T1_4"] = dict(test_id="T1.4", desc="SELECT_HORIZON: funding_z30 bucket picks best of {1h,4h,8h} on train half, evaluated OOS on test half",
                        train_best_horizon=best_horizon, n_train=len(train), n_test=len(test),
                        without=without_stats, with_=with_stats, delta_net_bps=delta)

# =============================================================================
# TEST 1.5 -- REDUCE/INCREASE_RISK: size position by |oi_drop_z| magnitude (bigger
# deleveraging shock = bigger conviction). WITHOUT = flat size=1. WITH = size normalized
# to mean 1, floor 0.3 / cap 2.5.
# =============================================================================
pop = base_decl.dropna(subset=["oi_drop_z"]).copy()
mag = pop["oi_drop_z"].abs()
size = (mag / mag.mean()).clip(0.3, 2.5)
size = size * (len(size) / size.sum())  # renormalize mean to 1 exactly
without_w = summarize(pop["net4h"])
with_weighted_mean = float((size * pop["net4h"]).sum() / size.sum())
with_pf_pos = (size * pop["net4h"]).clip(lower=0).sum()
with_pf_neg = -(size * pop["net4h"]).clip(upper=0).sum()
with_pf = float(with_pf_pos / with_pf_neg) if with_pf_neg > 0 else None
delta = round(with_weighted_mean - without_w["mean_bps"], 2)
print(f"[T1.5] REDUCE/INCREASE_RISK by |oi_drop_z| magnitude: WITHOUT(flat) mean={without_w['mean_bps']} | WITH(size-weighted) mean={round(with_weighted_mean,2)} PF={round(with_pf,3) if with_pf else None} delta={delta}")
results["T1_5"] = dict(test_id="T1.5", desc="REDUCE/INCREASE_RISK: size by |oi_drop_z| magnitude (mean-1-normalized, clipped 0.3-2.5)",
                        n=len(pop), without=without_w,
                        with_=dict(mean_bps=round(with_weighted_mean, 2), pf=round(with_pf, 3) if with_pf else None),
                        delta_net_bps=delta)

# =============================================================================
# TEST 1.6 -- ENABLE/DISABLE by time-of-day session (hour_utc): Asia(0-8) / EU(8-16) / US(16-24)
# =============================================================================
pop = base_decl.dropna(subset=["hour_utc"]).copy()
def session(h):
    if h < 8: return "ASIA"
    if h < 16: return "EU"
    return "US"
pop["session"] = pop["hour_utc"].apply(session)
mid_time = pop["event_time"].median()
train = pop[pop["event_time"] < mid_time]
test = pop[pop["event_time"] >= mid_time]
train_sess_stats = {s: summarize(g["net4h"]) for s, g in train.groupby("session")}
best_sess = max([s for s in train_sess_stats if train_sess_stats[s]["mean_bps"] is not None], key=lambda s: train_sess_stats[s]["mean_bps"])
without_test = summarize(test["net4h"])
with_test = summarize(test.loc[test["session"] == best_sess, "net4h"])
delta_test = round(with_test["mean_bps"] - without_test["mean_bps"], 2) if with_test["mean_bps"] is not None and without_test["mean_bps"] is not None else None
full_sess_stats = {s: summarize(g["net4h"]) for s, g in pop.groupby("session")}
print(f"[T1.6] ENABLE/DISABLE by session, train-picked best={best_sess} (train stats: {train_sess_stats})")
print(f"   TEST(OOS) WITHOUT n={without_test['n']} mean={without_test['mean_bps']} | WITH({best_sess}) n={with_test['n']} mean={with_test['mean_bps']}  delta={delta_test}")
results["T1_6"] = dict(test_id="T1.6", desc="ENABLE/DISABLE by UTC session (Asia/EU/US), best session picked on train half, evaluated OOS on test half",
                        train_stats=train_sess_stats, best_session_picked=best_sess,
                        full_sample_by_session_context=full_sess_stats,
                        test_without=without_test, test_with=with_test, delta_net_bps_oos=delta_test)

# =============================================================================
# TEST 1.7 -- ENABLE/DISABLE by dist_low_24h (does "far from local low" ALSO gate the
# repeat-exhaustion sleeve -- i.e. does combining the two known liq_cascade features help).
# Pre-specified hypothesis direction (far-from-low = better, per LIQ_CASCADE_FAR_FROM_LOW_V1's
# own finding) but threshold train-picked, evaluated OOS via the same helper.
# =============================================================================
results["T1_7"] = ab_enable_disable_traintest(base_decl, "dist_low_24h", "T1.7",
    "ENABLE/DISABLE by dist_low_24h (combining LIQ_CASCADE_FAR_FROM_LOW_V1's own feature onto the repeat-exhaustion sleeve)")

# =============================================================================
# TEST 1.8 -- ENABLE/DISABLE by day-of-week (dow)
# =============================================================================
pop = base_decl.dropna(subset=["dow"]).copy()
dow_stats = {int(d): summarize(g["net4h"]) for d, g in pop.groupby("dow")}
weekday_gate = pop["dow"].isin([0, 1, 2, 3, 4])  # Mon-Fri if 0=Mon convention (pandas dt.dayofweek)
without_all = summarize(pop["net4h"])
weekday_stats = summarize(pop.loc[weekday_gate, "net4h"])
weekend_stats = summarize(pop.loc[~weekday_gate, "net4h"])
print(f"[T1.8] by dow: {dow_stats}")
results["T1_8"] = dict(test_id="T1.8", desc="ENABLE/DISABLE weekday vs weekend (dow field, convention TBD -- see raw by_dow)",
                        by_dow=dow_stats, without_all=without_all, weekday=weekday_stats, weekend=weekend_stats,
                        delta_net_bps=round(weekday_stats["mean_bps"] - without_all["mean_bps"], 2) if weekday_stats["mean_bps"] is not None else None)

# =============================================================================
# TEST 1.9 -- rescue test: does ANY regime gate turn ONSET (1st occurrence, currently net
# negative per W2/registry) into a positive-edge sleeve? ENABLE_ALPHA on a currently-dead base.
# =============================================================================
onset = df_all[(df_all["kind"] == "LONG_CASCADE") & (df_all["n_events_sym_24h"] == 0)].copy()
onset["net4h"] = net_bps(onset["fwd_4h"])
onset_decl = decluster(onset, gap_hours=4.0)
onset_decl["net4h"] = net_bps(onset_decl["fwd_4h"])
onset_baseline = summarize(onset_decl["net4h"])
gate_calm = onset_decl["btc_vol_24h"] <= onset_decl["btc_vol_24h"].median()
onset_calm = summarize(onset_decl.loc[gate_calm, "net4h"])
gate_farlow = onset_decl["dist_low_24h"] >= onset_decl["dist_low_24h"].median()
onset_farlow = summarize(onset_decl.loc[gate_farlow, "net4h"])
gate_isolated = onset_decl["n_events_mktwide_30m"] <= onset_decl["n_events_mktwide_30m"].median()
onset_isolated = summarize(onset_decl.loc[gate_isolated, "net4h"])
print(f"[T1.9] ONSET rescue test: baseline={onset_baseline}")
print(f"   calm-BTC-vol gate: {onset_calm}")
print(f"   far-from-low gate: {onset_farlow}")
print(f"   isolated(low mktwide breadth) gate: {onset_isolated}")
results["T1_9"] = dict(test_id="T1.9", desc="ENABLE_ALPHA rescue test on ONSET (1st occurrence, currently net-negative baseline) via 3 candidate gates",
                        n_raw=len(onset), n_independent=len(onset_decl),
                        baseline=onset_baseline, calm_btc_vol_gate=onset_calm, far_from_low_gate=onset_farlow,
                        isolated_breadth_gate=onset_isolated)

# =============================================================================
# TEST 1.10 -- REDUCE_RISK by ls_ratio_z (crowding at entry): hypothesis = crowded
# long/short ratio extremity at cascade time predicts worse forward outcome -> reduce size
# =============================================================================
pop = base_decl.dropna(subset=["ls_ratio_z"]).copy()
crowding = pop["ls_ratio_z"].abs()
size = (1.0 / (1.0 + crowding)).clip(lower=0.2)
size = size * (len(size) / size.sum())
without_w = summarize(pop["net4h"])
with_weighted_mean = float((size * pop["net4h"]).sum() / size.sum())
delta = round(with_weighted_mean - without_w["mean_bps"], 2)
print(f"[T1.10] REDUCE_RISK by ls_ratio_z crowding (inverse-size): WITHOUT={without_w['mean_bps']} WITH={round(with_weighted_mean,2)} delta={delta}")
results["T1_10"] = dict(test_id="T1.10", desc="REDUCE_RISK: inverse-size by |ls_ratio_z| crowding at entry",
                         n=len(pop), without=without_w, with_=dict(mean_bps=round(with_weighted_mean, 2)), delta_net_bps=delta)

# =============================================================================
# TEST 1.11 -- generalization check: does the BTC-vol-regime gate (T1.1, best on the main
# repeat family) ALSO improve the SHORT_SQUEEZE_EXHAUSTION sibling? (context/caveat only --
# LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 is BLOCKED per registry, sign convention unresolved
# -- this test treats it purely as SHORT_SQUEEZE kind + n_events_sym_24h>=2 -> LONG per the
# same rule structure as W2's rank-3 finding, NOT a recommendation to unblock it)
# =============================================================================
ss_base = df_all[(df_all["kind"] == "SHORT_SQUEEZE") & (df_all["n_events_sym_24h"] >= 2)].copy()
ss_base["net4h"] = net_bps(ss_base["fwd_4h"])
ss_decl = decluster(ss_base, gap_hours=4.0)
ss_decl["net4h"] = net_bps(ss_decl["fwd_4h"])
ss_baseline = summarize(ss_decl["net4h"])
# transfer the RULE learned on the primary base (T1.1: direction+threshold fit on
# LONG_CASCADE-repeat train half) onto the sibling population, unrefit -- a genuine
# generalization test, not a fresh in-sample split on the sibling itself.
t11_direction = results["T1_1"]["direction"]
t11_thresh = results["T1_1"]["threshold"]
gate = (ss_decl["btc_vol_24h"] <= t11_thresh) if t11_direction == "<=" else (ss_decl["btc_vol_24h"] > t11_thresh)
ss_with = summarize(ss_decl.loc[gate, "net4h"])
delta = round(ss_with["mean_bps"] - ss_baseline["mean_bps"], 2) if ss_with["mean_bps"] is not None and ss_baseline["mean_bps"] is not None else None
print(f"[T1.11] Generalization: T1.1's btc_vol_24h rule ({t11_direction} {round(t11_thresh,4)}, unrefit) applied to SHORT_SQUEEZE_EXHAUSTION (blocked sibling, context only): baseline={ss_baseline} with={ss_with} delta={delta}")
results["T1_11"] = dict(test_id="T1.11", desc="Generalization check (context only, sibling BLOCKED): T1.1's train-fit btc_vol_24h rule, unrefit, transferred to SHORT_SQUEEZE repeat>=2",
                         transferred_rule=dict(feature="btc_vol_24h", direction=t11_direction, threshold=t11_thresh),
                         n_raw=len(ss_base), n_independent=len(ss_decl), baseline=ss_baseline, with_=ss_with, delta_net_bps=delta)

# =============================================================================
# TEST 1.12 -- ENABLE/DISABLE by taker_z extremity (does strong aggressive-taker selling
# at the cascade itself predict a cleaner exhaustion, vs a "quiet" OI-drop without taker
# confirmation). Direction train-picked (not assumed) via helper on |taker_z|.
# =============================================================================
pop = base_decl.dropna(subset=["taker_z"]).copy()
pop["abs_taker_z"] = pop["taker_z"].abs()
results["T1_12"] = ab_enable_disable_traintest(pop, "abs_taker_z", "T1.12",
    "ENABLE/DISABLE by |taker_z| extremity (aggressive-flow confirmation at cascade)")

with open(OUT, "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nWrote", OUT)
