#!/usr/bin/env python3
"""
scripts/test_stablecoin_regime_signal.py
─────────────────────────────────────────────────────────────────────────────
Exécution UNIQUE du protocole pré-enregistré STABLECOIN_REGIME v0
(reports/STABLECOIN_REGIME_PROTOCOL.md, gelé avant tout calcul signal→cible).

Volet A — IC Spearman des 8 features figées vers les 4 cibles primaires
(RV BTC 7/30 j, maxDD 30 j futur du combiné, stress 7 j) + cibles secondaires
directionnelles (BTC/ETH), délai +1 j (variante +2 j), block-bootstrap 90 j.

Volet B — LA règle overlay jugée : RISK_OFF si (F2 < −1,0) OU (F6 < −0,005
≥ 3 j) → gross ×0,5, hystérèse 5 j, coûts 10/20 bps par unité tournée.
Voisinage 3×3 pour stabilité uniquement.

Sorties : reports/STABLECOIN_REGIME_VERDICT.{json,md}
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "stablecoins"
OUT_JSON = ROOT / "reports" / "STABLECOIN_REGIME_VERDICT.json"
OUT_MD = ROOT / "reports" / "STABLECOIN_REGIME_VERDICT.md"

RNG = np.random.default_rng(20260718)
N_BOOT = 2000
BLOCK = 90
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
START_PORT = pd.Timestamp("2023-01-01", tz="UTC")

# Règle jugée (figée)
Z_TH, DEPEG_TH, DEPEG_DAYS, MULT, HYST = -1.0, -0.005, 3, 0.5, 5
COST_X1, COST_X2 = 0.0010, 0.0020          # par unité de notional tourné
NEIGH_Z = [-0.75, -1.0, -1.25]
NEIGH_M = [0.25, 0.5, 0.75]


# ── Données ──────────────────────────────────────────────────────────────────
def load_features() -> pd.DataFrame:
    sup = pd.read_parquet(DATA / "supply_daily.parquet").set_index("date")
    prc = pd.read_parquet(DATA / "prices_daily.parquet").set_index("date")
    df = pd.DataFrame(index=sup.index)

    def z365(s: pd.Series) -> pd.Series:
        m = s.rolling(365, min_periods=180).mean()
        sd = s.rolling(365, min_periods=180).std()
        return (s - m) / sd.replace(0, np.nan)

    ltrio, lusdt = np.log(sup["trio"]), np.log(sup["usdt"])
    df["F1"] = z365(ltrio.diff(7))
    df["F2"] = z365(ltrio.diff(30))
    df["F3"] = z365(lusdt.diff(7))
    df["F4"] = z365(lusdt.diff(30))
    depeg = prc.min(axis=1) - 1.0
    df["F5"] = depeg.reindex(df.index)
    df["F6"] = depeg.rolling(7, min_periods=1).min().reindex(df.index)
    share = sup["usdt"] / sup["trio"]
    df["F7"] = share
    df["F8"] = share.diff(30)
    df["F2b"] = z365(np.log(sup["all_usd"]).diff(30))
    return df


def daily_close(symbol: str) -> pd.Series:
    from src.institutional.engines.legacy_bridge import load_enriched
    df = load_enriched(symbol, required_cols=["close"])
    s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"]
    return s.resample("D").last().dropna()


def combined_equity() -> pd.Series:
    """Combiné 3 jambes, logique exacte de measure_v12_plus_stack_overlay --tapes mh."""
    spec = importlib.util.spec_from_file_location(
        "mvo", ROOT / "scripts" / "measure_v12_plus_stack_overlay.py")
    mvo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mvo)
    legs = {"v12": mvo.v12_equity(), "stack_mh": mvo.stack_equity_daily("mh")}
    b = pd.read_parquet(mvo.DIR / "basis_term_equity_daily.parquet")
    legs["basis"] = pd.Series(b["equity"].values,
                              index=pd.to_datetime(b["date"], utc=True))
    start = max([START_PORT] + [s.index[0] for s in legs.values()])
    end = min(s.index[-1] for s in legs.values())
    idx = pd.date_range(start, end, freq="D", tz="UTC")
    combo = None
    for s in legs.values():
        x = s.sort_index().resample("D").last().ffill().reindex(idx).ffill()
        x = x / x.iloc[0]
        combo = x if combo is None else combo * x
    return combo


# ── Cibles ───────────────────────────────────────────────────────────────────
def fwd_rv(r: pd.Series, h: int) -> pd.Series:
    return (r.rolling(h).std() * np.sqrt(365)).shift(-h)


def fwd_maxdd(eq: pd.Series, h: int) -> pd.Series:
    v = eq.values
    out = np.full(len(v), np.nan)
    for i in range(len(v) - h):
        w = v[i:i + h + 1]
        out[i] = float((w / np.maximum.accumulate(w) - 1.0).min())
    return pd.Series(out, index=eq.index)


def stress_target(eq: pd.Series) -> pd.Series:
    fwd7 = eq.shift(-7) / eq - 1.0
    past7 = eq / eq.shift(7) - 1.0
    thr = past7.rolling(730, min_periods=365).quantile(0.10)
    t = (fwd7 < thr).astype(float)
    return t.where(fwd7.notna() & thr.notna())


# ── Stats ────────────────────────────────────────────────────────────────────
def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    d = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / d) if d > 0 else np.nan

def boot_p(x: np.ndarray, y: np.ndarray) -> float:
    """Block-bootstrap 90 j des paires ; p bilatéral par inversion d'IC."""
    n = len(x)
    if n < 2 * BLOCK:
        return np.nan
    n_blocks = int(np.ceil(n / BLOCK))
    starts = RNG.integers(0, n - BLOCK + 1, size=(N_BOOT, n_blocks))
    ics = np.empty(N_BOOT)
    for b in range(N_BOOT):
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts[b]])[:n]
        ics[b] = spearman(x[idx], y[idx])
    p = 2 * min((ics <= 0).mean(), (ics >= 0).mean())
    return float(max(p, 1.0 / N_BOOT))


def ic_test(f: pd.Series, tgt: pd.Series, delay: int) -> dict:
    a = pd.DataFrame({"x": f.shift(delay), "y": tgt}).dropna()
    if len(a) < 400:
        return {"n": len(a), "ic": None}
    x, y = a["x"].values, a["y"].values
    res = {"n": len(a), "window": [str(a.index[0].date()), str(a.index[-1].date())],
           "ic": round(spearman(x, y), 4), "p": boot_p(x, y)}
    tr, te = a[a.index < SPLIT], a[a.index >= SPLIT]
    for name, seg in [("train", tr), ("test", te)]:
        if len(seg) >= 200:
            xs, ys = seg["x"].values, seg["y"].values
            res[f"ic_{name}"] = round(spearman(xs, ys), 4)
            res[f"p_{name}"] = boot_p(xs, ys)
        else:
            res[f"ic_{name}"], res[f"p_{name}"] = None, None
    return res


def retained(r: dict) -> bool:
    return (r.get("ic") is not None and abs(r["ic"]) >= 0.15 and r["p"] < 0.01
            and r.get("ic_train") is not None and r.get("ic_test") is not None
            and np.sign(r["ic_train"]) == np.sign(r["ic_test"])
            and r["p_train"] < 0.05 and r["p_test"] < 0.05)


# ── Overlay ──────────────────────────────────────────────────────────────────
def risk_off_flag(feats: pd.DataFrame, idx: pd.DatetimeIndex,
                  z_th: float) -> pd.Series:
    f2 = feats["F2"].reindex(idx).ffill()
    f6 = feats["F6"].reindex(idx).ffill()
    depeg_persist = (f6 < DEPEG_TH).astype(float).rolling(
        DEPEG_DAYS).sum().eq(DEPEG_DAYS)
    cond = ((f2 < z_th) | depeg_persist).astype(float)
    off = cond.rolling(HYST + 1, min_periods=1).max().fillna(0).astype(bool)
    return off


def overlay_sim(eq: pd.Series, feats: pd.DataFrame, z_th: float, mult: float,
                cost: float) -> dict:
    r = eq.pct_change().fillna(0.0)
    off = risk_off_flag(feats, eq.index, z_th)
    e = pd.Series(np.where(off.shift(1).fillna(False), mult, 1.0), index=eq.index)
    switch = e.diff().abs().fillna(0.0)
    r_ov = e * r - switch * cost
    eq_ov = (1 + r_ov).cumprod()

    def stats(equity: pd.Series) -> dict:
        rr = equity.pct_change().dropna()
        yrs = (equity.index[-1] - equity.index[0]).days / 365.25
        dd = float(((equity - equity.cummax()) / equity.cummax()).min())
        return {"roi_ann": round(float(equity.iloc[-1] ** (1 / yrs) - 1), 4),
                "maxdd": round(dd, 4),
                "sharpe": round(float(rr.mean() / max(rr.std(), 1e-12)
                                      * np.sqrt(365)), 2)}

    base = stats(eq / eq.iloc[0])
    ov = stats(eq_ov)
    grp = (off != off.shift(1)).cumsum()[off]
    episodes = []
    for _, g in off[off].groupby(grp):
        d0, d1 = g.index[0], g.index[-1]
        delta = float((r_ov - r).loc[d0:d1].sum())
        episodes.append({"start": str(d0.date()), "end": str(d1.date()),
                         "days": len(g), "delta_vs_base": round(delta, 5)})
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"base": base, "overlay": ov, "episodes": episodes,
            "n_episodes": len(episodes),
            "improved_episodes": sum(1 for x in episodes if x["delta_vs_base"] > 0),
            "switches_per_year": round(float((switch > 0).sum() / yrs), 1),
            "off_days_pct": round(float(off.mean()), 3)}


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    feats = load_features()
    btc, eth = daily_close("BTCUSDT"), daily_close("ETHUSDT")
    eq = combined_equity()
    r_btc = btc.pct_change()

    targets_primary = {
        "rv_btc_7": fwd_rv(r_btc, 7), "rv_btc_30": fwd_rv(r_btc, 30),
        "dd_combo_30": fwd_maxdd(eq, 30), "stress_combo_7": stress_target(eq)}
    targets_secondary = {
        "dir_btc_7": np.sign(btc.shift(-7) / btc - 1),
        "dir_btc_30": np.sign(btc.shift(-30) / btc - 1),
        "dir_eth_7": np.sign(eth.shift(-7) / eth - 1),
        "dir_eth_30": np.sign(eth.shift(-30) / eth - 1)}

    fcols = [f"F{i}" for i in range(1, 9)]
    ics: dict = {"primary": {}, "secondary": {}, "delay2_primary": {}, "F2b": {}}
    for tname, tgt in targets_primary.items():
        ics["primary"][tname] = {f: ic_test(feats[f], tgt, 1) for f in fcols}
        ics["delay2_primary"][tname] = {
            f: {"ic": ic_test(feats[f], tgt, 2).get("ic")} for f in fcols}
        ics["F2b"][tname] = ic_test(feats["F2b"], tgt, 1)
    for tname, tgt in targets_secondary.items():
        ics["secondary"][tname] = {f: ic_test(feats[f], tgt, 1) for f in fcols}

    n_tests = len(fcols) * len(targets_primary)
    bonf = 0.05 / n_tests
    family_pass = any(r.get("p") is not None and r["p"] < bonf
                      for t in ics["primary"].values() for r in t.values())
    retained_list = [(t, f) for t, d in ics["primary"].items()
                     for f, r in d.items() if retained(r)]
    crit7 = any(f in ("F2", "F6") for _, f in retained_list)

    main_x1 = overlay_sim(eq, feats, Z_TH, MULT, COST_X1)
    main_x2 = overlay_sim(eq, feats, Z_TH, MULT, COST_X2)
    neigh = {f"z{z}_m{m}": overlay_sim(eq, feats, z, m, COST_X1)
             for z in NEIGH_Z for m in NEIGH_M}

    b, o1, o2 = main_x1["base"], main_x1["overlay"], main_x2["overlay"]
    c1 = (abs(o1["maxdd"]) <= abs(b["maxdd"]) * 0.80
          and abs(o2["maxdd"]) <= abs(b["maxdd"]) * 0.85)
    c2 = (o1["roi_ann"] >= b["roi_ann"] * 0.85 and o2["roi_ann"] >= b["roi_ann"] * 0.85)
    c3 = o1["sharpe"] >= b["sharpe"]
    c4 = (main_x1["n_episodes"] >= 5 and main_x1["improved_episodes"]
          >= 0.5 * main_x1["n_episodes"])
    c5 = main_x1["switches_per_year"] <= 24
    ok_cells = sum(1 for v in neigh.values()
                   if abs(v["overlay"]["maxdd"]) < abs(v["base"]["maxdd"])
                   and v["overlay"]["roi_ann"] >= v["base"]["roi_ann"] * 0.80)
    no_catastrophe = all(v["overlay"]["roi_ann"] >= v["base"]["roi_ann"] * 0.80
                         for v in neigh.values())
    c6 = ok_cells >= 6 and no_catastrophe
    criteria = {"c1_dd": c1, "c2_roi": c2, "c3_sharpe": c3, "c4_episodes": c4,
                "c5_turnover": c5, "c6_neighborhood": c6, "c7_stat": crit7}
    verdict = "PASS" if all(criteria.values()) else "NO_EDGE"

    out = {"protocol": "reports/STABLECOIN_REGIME_PROTOCOL.md",
           "run_date": "2026-07-18", "single_run": True,
           "portfolio_window": [str(eq.index[0].date()), str(eq.index[-1].date())],
           "n_tests_primary": n_tests, "bonferroni_p": bonf,
           "family_pass_bonferroni": bool(family_pass),
           "retained_features": [f"{f}->{t}" for t, f in retained_list],
           "criteria": {k: bool(v) for k, v in criteria.items()},
           "verdict": verdict,
           "overlay_main_x1": main_x1, "overlay_main_x2": main_x2,
           "neighborhood": {k: {"base": v["base"], "overlay": v["overlay"],
                                "n_episodes": v["n_episodes"]}
                            for k, v in neigh.items()},
           "ic": ics}
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str))

    L = [f"# STABLECOIN_REGIME v0 — VERDICT : {verdict}\n",
         f"Protocole gelé : `reports/STABLECOIN_REGIME_PROTOCOL.md` — exécution unique 2026-07-18.\n",
         f"Fenêtre portefeuille : {out['portfolio_window'][0]} → {out['portfolio_window'][1]}\n",
         "## Critères figés\n"]
    L += [f"- {k} : {'PASS' if v else 'FAIL'}" for k, v in criteria.items()]
    L += ["\n## Overlay (règle jugée, coûts ×1 / ×2)\n",
          f"- base : ROI {b['roi_ann']:+.2%}/an, maxDD {b['maxdd']:.2%}, Sharpe {b['sharpe']}",
          f"- overlay ×1 : ROI {o1['roi_ann']:+.2%}/an, maxDD {o1['maxdd']:.2%}, Sharpe {o1['sharpe']}",
          f"- overlay ×2 : ROI {o2['roi_ann']:+.2%}/an, maxDD {o2['maxdd']:.2%}, Sharpe {o2['sharpe']}",
          f"- épisodes RISK_OFF : {main_x1['n_episodes']} (améliorés : {main_x1['improved_episodes']}), "
          f"bascules/an : {main_x1['switches_per_year']}, jours off : {main_x1['off_days_pct']:.1%}",
          f"\n## Volet statistique\n",
          f"- Bonferroni p<{bonf:.4f} sur {n_tests} tests primaires : "
          f"{'atteint' if family_pass else 'NON atteint'}",
          f"- features retenues (|IC|≥0,15, p<0,01, signe train=test) : "
          f"{out['retained_features'] or 'aucune'}",
          "\nDétail complet : `reports/STABLECOIN_REGIME_VERDICT.json`."]
    OUT_MD.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
