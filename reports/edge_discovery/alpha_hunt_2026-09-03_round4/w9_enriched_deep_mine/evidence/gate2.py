#!/usr/bin/env python3
"""W9 Phase 2 — moteur de gate v2 (declustering 3 niveaux, block-bootstrap, ETA forward, OOS).

Corrections apportees a `gate.py` (v1) apres relecture — TOUTES rendent le gate PLUS STRICT,
aucune ne peut promouvoir un mecanisme :
  (1) t-stat rapporte sur DEUX quantites : rendement demeane (diagnostic conditionnel)
      ET rendement brut net de couts (la quantite reellement tradable).
  (2) coherence de signe imposee : un mecanisme dont l'edge demeane est negatif ne peut
      plus recevoir un verdict positif (bug v1 : t=-9 + net>0 donnait PROMISING).
  (3) politique SHORT du projet appliquee : side=-1 plafonne a PROMISING_NEEDS_VALIDATION,
      livrable uniquement comme SCREEN/GATE ou jambe de spread.
  (4) fenetre OOS 2026 ajoutee, expurgee des periodes de source contaminee (voir audit A8).
  (5) block-bootstrap vectorise (memes blocs = semaines, meme graine).

Toute condition de declenchement n'utilise que de l'information <= t. Sortie au close de t+H.
"""
import numpy as np, pandas as pd, os

OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
RNG_SEED = 20260903
COST = 14.0; COST_STRESS = 28.0
HORIZONS = [1, 4, 8, 24]
DISCOVERY_END = pd.Timestamp("2026-01-01", tz="UTC")
OOS_START = DISCOVERY_END
OOS_END   = pd.Timestamp("2026-06-29", tz="UTC")
N_BOOT = 2000
BONFERRONI_T = 3.0            # 20 tests principaux declares au PREREGISTRATION

# --- perimetre expurge : bascule de source perp -> spot mesuree par le test A8 (voir SEAMS.csv)
SOURCE_SWITCH = {  # symbole -> instant a partir duquel la serie change de source
    "BTCUSDT":  pd.Timestamp("2026-01-01", tz="UTC"),
    "ADAUSDT":  pd.Timestamp("2026-05-20", tz="UTC"),
    "AVAXUSDT": pd.Timestamp("2026-05-20", tz="UTC"),
    "BNBUSDT":  pd.Timestamp("2026-05-20", tz="UTC"),
    "ETHUSDT":  pd.Timestamp("2026-05-20", tz="UTC"),
    "LINKUSDT": pd.Timestamp("2026-05-20", tz="UTC"),
    "SOLUSDT":  pd.Timestamp("2026-05-20", tz="UTC"),
    "DOGEUSDT": pd.Timestamp("2026-05-24", tz="UTC"),
    "XRPUSDT":  pd.Timestamp("2026-05-24", tz="UTC"),
    "DOTUSDT":  pd.Timestamp("2026-06-28", tz="UTC"),
}

def load():
    df = pd.read_parquet(OUT + "/panel.parquet")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    g = df.groupby("symbol", sort=False)["close"]
    for H in HORIZONS:
        df[f"fwd{H}"] = (g.shift(-H) / df["close"] - 1.0) * 1e4      # bps, forward-only
    df["date"] = df["datetime"].dt.floor("D")
    df["week"] = df["datetime"].dt.to_period("W").astype(str)
    df["year"] = df["datetime"].dt.year
    df["utc_hour"] = df["datetime"].dt.hour                          # recalcule, pas la colonne enrichie
    # drapeau de contamination de source (A8)
    bad = np.zeros(len(df), bool)
    for s, t0 in SOURCE_SWITCH.items():
        bad |= (df["symbol"].values == s) & (df["datetime"].values >= np.datetime64(t0))
    df["src_contaminated"] = bad
    return df

def causal_pct(s, win=500):
    """rang percentile roulant CAUSAL (inclut t, jamais t+1)."""
    return s.rolling(win, min_periods=100).rank(pct=True)

def decluster(sub):
    """L1 : au plus un evenement par symbole par fenetre glissante de 24 h (greedy chronologique)."""
    keep = []
    for sym, g in sub.groupby("symbol", sort=False):
        last = None
        for i, t in zip(g.index.values, g["datetime"].values):
            if last is None or (t - last) / np.timedelta64(1, "h") >= 24:
                keep.append(i); last = t
    return sub.loc[keep]

def block_boot(vals, blocks, n=N_BOOT, seed=RNG_SEED):
    """block-bootstrap vectorise : on retire des BLOCS (semaines) entiers, pas des observations."""
    rng = np.random.default_rng(seed)
    ub, inv = np.unique(blocks, return_inverse=True)
    nb = len(ub)
    bsum = np.bincount(inv, weights=vals, minlength=nb)
    bcnt = np.bincount(inv, minlength=nb).astype(float)
    picks = rng.integers(0, nb, size=(n, nb))
    means = bsum[picks].sum(axis=1) / bcnt[picks].sum(axis=1)
    return np.percentile(means, [2.5, 97.5])

def _period_mask(df, period):
    if period == "discovery":
        return (df["datetime"] < DISCOVERY_END)
    if period == "oos":
        return (df["datetime"] >= OOS_START) & (df["datetime"] < OOS_END) & (~df["src_contaminated"])
    raise ValueError(period)

def evaluate(name, df, mask, side, H, note="", period="discovery", family=None,
             prereg=True, refit=False):
    per = _period_mask(df, period)
    d = df[mask & df[f"fwd{H}"].notna() & per].copy()
    pop = df[df[f"fwd{H}"].notna() & per]
    base = dict(mechanism=name, family=family or name.split("_")[0], horizon_h=H, side=int(side),
                period=period, prereg=bool(prereg), refit=bool(refit), note=note,
                deliverable_form="SHORT_SCREEN_OR_SPREAD_LEG_ONLY" if side < 0 else "LONG_STANDALONE_OK")
    if len(d) < 50:
        return dict(base, verdict="DATA_LIMITED", n_raw=int(len(d)), gate_fail="N_brut<50")
    d["ret"] = d[f"fwd{H}"] * side
    ctrl_uncond = float((pop[f"fwd{H}"] * side).mean())
    daymean = pop.groupby("date")[f"fwd{H}"].mean() * side
    d["ret_dm"] = d["ret"] - d["date"].map(daymean).values
    n_raw = len(d)
    L1 = decluster(d)
    L2   = L1.groupby("date")["ret_dm"].mean()          # edge conditionnel (demeane cross-section/jour)
    L2r  = L1.groupby("date")["ret"].mean()             # rendement brut tradable
    L3   = L1.groupby("week")["ret_dm"].mean()
    n1, n2, n3 = len(L1), len(L2), len(L3)
    if n2 < 100:
        return dict(base, verdict="DATA_LIMITED", n_raw=int(n_raw), n_independent_L1=n1,
                    n_independent_L2=n2, n_independent_L3=n3, gate_fail="N_independant_L2<100")
    mu = float(L2.mean());  sd = float(L2.std(ddof=1))
    t_dm = mu / (sd / np.sqrt(n2)) if sd > 0 else 0.0
    gross = float(L2r.mean()); sdr = float(L2r.std(ddof=1))
    net = gross - COST; net28 = gross - COST_STRESS
    t_net = (net) / (sdr / np.sqrt(n2)) if sdr > 0 else 0.0        # t sur la quantite TRADABLE
    wk = L1.groupby("date")["week"].first().reindex(L2.index).values
    ci   = block_boot(L2.values, wk)
    ci_n = block_boot((L2r - COST).values, wk)
    yby = {}
    for y, g in L1.groupby("year"):
        gl2 = g.groupby("date")["ret_dm"].mean()
        gl2r = g.groupby("date")["ret"].mean()
        if len(gl2) >= 5:
            yby[int(y)] = dict(n_L2=int(len(gl2)), edge_bps=round(float(gl2.mean()), 2),
                               net_bps=round(float(gl2r.mean() - COST), 2))
    if yby:
        best = max(yby, key=lambda k: yby[k]["edge_bps"])
        rest = [k for k in yby if k != best]
        w = sum(yby[k]["n_L2"] for k in rest)
        ex_best = round(sum(yby[k]["edge_bps"] * yby[k]["n_L2"] for k in rest) / w, 2) if w else None
        ex_best_net = round(sum(yby[k]["net_bps"] * yby[k]["n_L2"] for k in rest) / w, 2) if w else None
        worst_ratio = min(yby[k]["edge_bps"] for k in yby) / mu if mu > 0 else None
    else:
        best = ex_best = ex_best_net = worst_ratio = None
    # n_required : power 80 %, alpha 5 %, edge haircute 50 % (definition du PREREGISTRATION, sur L2 demeane)
    n_req = int(np.ceil((1.96 + 0.84) ** 2 * sd ** 2 / (0.5 * abs(mu)) ** 2)) if mu != 0 else 10 ** 9
    # variante honnete : meme calcul sur le P&L NET reellement tradable
    n_req_net = int(np.ceil((1.96 + 0.84) ** 2 * sdr ** 2 / (0.5 * abs(net)) ** 2)) if net > 0 else None
    # event_rate : episodes L2/semaine sur les 6 derniers mois de la fenetre (conservateur)
    end = DISCOVERY_END if period == "discovery" else OOS_END
    cut = end - pd.Timedelta(days=182)
    recent = L1[L1["datetime"] >= cut]
    rate = (recent.groupby("date").ngroups / 26.0) if len(recent) else 0.0
    eta_w = n_req / rate if rate > 0 else float("inf")
    eta_wn = (n_req_net / rate) if (rate > 0 and n_req_net) else float("inf")
    eta_y = eta_w / 52.0
    # ---- verdict (seuils du PREREGISTRATION + garde de coherence de signe)
    if mu <= 0 or net <= 0:
        v = "DEAD"; fail = "edge demeane <=0 ou net <=0"
    elif abs(t_dm) < 2.0:
        v = "WEAK"; fail = "t_declusterise < 2.0"
    elif net28 <= 0:
        v = "COST_FRAGILE"; fail = "meurt au stress 28 bps"
    elif ex_best is not None and ex_best <= 0:
        v = "REGIME_DEPENDENT"; fail = "edge nul hors meilleure annee"
    elif eta_y >= 3.0:
        v = "UNCONFIRMABLE_IN_HORIZON"; fail = f"ETA {eta_y:.1f} ans >= 3"
    elif t_dm >= 2.5 and ci[0] > 0 and net28 > 0:
        v = "VALIDATED_FOR_FORWARD"; fail = ""
    else:
        v = "PROMISING_NEEDS_VALIDATION"; fail = "t<2.5 ou IC95 touche 0"
    # politique SHORT du projet (mai 2026) : pas de short directionnel standalone
    if side < 0 and v == "VALIDATED_FOR_FORWARD":
        v = "PROMISING_NEEDS_VALIDATION"
        fail = "SHORT standalone interdit (politique projet) : livrable seulement en SCREEN/GATE ou jambe de spread"
    # un mecanisme refite ne peut jamais depasser PROMISING_NEEDS_VALIDATION
    if refit and v == "VALIDATED_FOR_FORWARD":
        v = "PROMISING_NEEDS_VALIDATION"; fail = "REFIT declare : plafonne par la regle anti-refit du PREREGISTRATION"
    return dict(base,
        n_raw=int(n_raw), n_independent_L1=int(n1), n_independent_L2=int(n2), n_independent_L3=int(n3),
        gross_bps=round(gross, 2), net_bps=round(net, 2), net_bps_stress28=round(net28, 2),
        edge_vs_control_bps=round(mu, 2), control_uncond_bps=round(ctrl_uncond, 2),
        t_stat_declustered=round(float(t_dm), 2), t_stat_net_tradable=round(float(t_net), 2),
        passes_bonferroni=bool(abs(t_dm) >= BONFERRONI_T),
        bootstrap_ci95=[round(float(ci[0]), 2), round(float(ci[1]), 2)],
        bootstrap_ci95_net=[round(float(ci_n[0]), 2), round(float(ci_n[1]), 2)],
        year_by_year=yby, best_year=best, ex_best_year=ex_best, ex_best_year_net=ex_best_net,
        worst_year_ratio=round(float(worst_ratio), 2) if worst_ratio is not None else None,
        n_required=int(n_req), n_required_on_net=n_req_net,
        event_rate_per_week=round(rate, 2),
        eta_forward_days=round(eta_w * 7, 1) if np.isfinite(eta_w) else None,
        eta_forward_years=round(eta_y, 2) if np.isfinite(eta_y) else None,
        eta_forward_years_on_net=round(eta_wn / 52.0, 2) if np.isfinite(eta_wn) else None,
        verdict=v, gate_fail=fail)


def diff_arms(name, df, mask_a, mask_b, side, H, period="discovery", note=""):
    """Test de DIFFERENCE entre deux bras sur la meme population (regle §1.3 du briefing).
    L'unite de declustering est le jour ; on compare les moyennes journalieres appariees."""
    per = _period_mask(df, period)
    ok = df[f"fwd{H}"].notna() & per
    pop = df[ok]
    daymean = pop.groupby("date")[f"fwd{H}"].mean() * side
    out = {}
    for tag, m in (("A", mask_a), ("B", mask_b)):
        d = df[m & ok].copy()
        d["ret"] = d[f"fwd{H}"] * side
        d["ret_dm"] = d["ret"] - d["date"].map(daymean).values
        L1 = decluster(d)
        out[tag] = (L1.groupby("date")["ret_dm"].mean(), L1.groupby("date")["week"].first())
    a, wa = out["A"]; b, wb = out["B"]
    idx = a.index.intersection(b.index)
    if len(idx) < 100:
        return dict(mechanism=name, horizon_h=H, period=period, verdict="DATA_LIMITED",
                    n_paired_days=int(len(idx)), note=note)
    diff = (a.reindex(idx) - b.reindex(idx)).values
    wk = wa.reindex(idx).values
    mu = float(np.mean(diff)); sd = float(np.std(diff, ddof=1)); n = len(diff)
    t = mu / (sd / np.sqrt(n)) if sd > 0 else 0.0
    ci = block_boot(diff, wk)
    return dict(mechanism=name, horizon_h=H, period=period, side=int(side), n_paired_days=int(n),
                arm_A_bps=round(float(a.reindex(idx).mean()), 2),
                arm_B_bps=round(float(b.reindex(idx).mean()), 2),
                diff_bps=round(mu, 2), t_stat_declustered=round(float(t), 2),
                bootstrap_ci95=[round(float(ci[0]), 2), round(float(ci[1]), 2)],
                verdict=("DIFF_SIGNIFICATIVE" if abs(t) >= 2.0 and ci[0] * ci[1] > 0 else "DIFF_NON_SIGNIFICATIVE"),
                note=note)
