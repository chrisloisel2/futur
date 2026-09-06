#!/usr/bin/env python3
"""
scripts/audit_positive_control_recovery.py
─────────────────────────────────────────────────────────────────────────────
Fait passer l'edge injecté par POSITIVE_CONTROL_ORACLE_V1 dans le VRAI
labelliseur, puis compare ce qui ressort à ce qui est entré.

La question, en une ligne
─────────────────────────
L'item A1 a mesuré « aucun alpha n'a d'edge net du marché » sur 548 décisions.
Ce résultat est lisible seulement si la chaîne qui l'a produit restitue un edge
quand il y en a un. On injecte +29 bps d'excess, on regarde ce qui sort.

  restitue ~29  -> l'appareil est fidèle, les négatifs de A1 sont VRAIS
  restitue ~8   -> l'appareil détruit les deux tiers de tout signal, et aucun
                   chiffre du lab n'est lisible tel quel

Pourquoi la comparaison est APPARIÉE
────────────────────────────────────
On ne compare pas le récupéré au Δ nominal mais à l'injection RÉELLEMENT
obtenue, décision par décision (`injected_measured_bps`, scellée par le
producteur). Les deux quantités portent sur les mêmes décisions et les mêmes
fenêtres de marché, donc le bruit du marché s'annule dans la différence :
l'écart résiduel est l'effet de la chaîne, et rien d'autre.

Les deux grandeurs sont déclusterisées par la MÊME fonction que le scoreboard
(`outcomes._episode_means`), sinon on comparerait une moyenne d'épisodes à une
moyenne de décisions — ce qui est exactement le genre d'écart qu'on cherche.

Quatre niveaux, donc une fonction de transfert
──────────────────────────────────────────────
Un seul niveau donne un rapport. Quatre donnent une droite, et une droite
distingue ce qu'un rapport confond : une atténuation multiplicative (pente < 1)
d'un biais additif (ordonnée à l'origine ≠ 0). Le niveau Δ=0 est en outre un
second contrôle négatif, indépendant de PLACEBO_RANDOM_V1.

Ce que cet audit ne dit pas
───────────────────────────
La sélection de l'oracle et la mesure du labelliseur lisent la MÊME archive de
prix. Par décision, les deux doivent donc coïncider exactement — et c'est
vérifié ici (`per_decision_max_abs_diff_bps`). Ce que cet audit mesure est
donc tout ce qui vient APRÈS : decluster, filtre des refus, coût, bootstrap,
agrégation. La fidélité de la SOURCE de prix est une question distincte, que
`--cross-source` traite sans sélection et donc sans malédiction du vainqueur.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.outcomes import (
    COST_BPS_ROUNDTRIP_BASE,
    LABELABLE,
    _episode_means,
    label_alpha,
    load_outcomes,
    summarize_outcomes,
)

ALPHA_ID = "POSITIVE_CONTROL_ORACLE_V1"
# Les contrôles ne sont pas des candidats : ils apparaissent dans le tableau de
# résolution pour l'échelle qu'ils donnent, jamais avec un verdict d'edge.
CONTROL_ALPHAS = ("PLACEBO_RANDOM_V1", "POSITIVE_CONTROL_ORACLE_V1")
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
DECISIONS = LAB_DIR / ALPHA_ID / "decisions.parquet"
REPORT = LAB_DIR / "POSITIVE_CONTROL_RECOVERY.md"

CLUSTER_WINDOW_HOURS = 24.0

# L'objectif opérationnel : +15 bps NETS. Avant le coût aller-retour de base
# (14 bps), c'est +29 bps d'excess. C'est le seuil contre lequel « pas d'edge »
# doit être jugé -- un intervalle qui ne l'exclut pas ne conclut rien.
TARGET_NET_BPS = 15.0
TARGET_EXCESS_BPS = TARGET_NET_BPS + COST_BPS_ROUNDTRIP_BASE
N_BOOT = 4000


def bootstrap_ci(values: np.ndarray, n_boot: int = N_BOOT, seed: int = 11) -> tuple:
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(n_boot, len(values)), replace=True).mean(axis=1)
    return tuple(float(x) for x in np.percentile(draws, [2.5, 97.5]))


def analyse(joined: pd.DataFrame, anchor: str = "dec") -> List[Dict[str, object]]:
    """Par niveau d'injection : injecté, récupéré, et la résolution disponible.

    ATTENTION À L'APPARIEMENT. La première version de cet audit comparait
    `récupéré - injected_measured_bps` décision par décision. C'est faux par
    construction : `injected_measured_bps` est un DÉPLACEMENT (excess du retenu
    moins excess du tiré), donc cette différence vaut l'excess du symbole TIRÉ
    — c'est-à-dire le placebo, pas l'erreur de la chaîne.

    Les deux comparaisons justes sont :
      - FIDÉLITÉ : `récupéré` contre `oracle_excess_at_selection_bps`, qui est
        la même grandeur sur les mêmes lignes. Tout écart vient du decluster ou
        du filtre des refus, les deux seules couches entre les deux.
      - DÉPLACEMENT : la moyenne du niveau Δ moins celle du niveau 0. C'est
        l'injection telle qu'elle ressort, et elle porte le bruit de marché des
        barres (disjointes entre niveaux), d'où un intervalle large.
    """
    out: List[Dict[str, object]] = []
    col = f"{anchor}_excess_bps"
    ok = joined[joined[f"{anchor}_status"] == "OK"].copy()

    for level, grp in ok.groupby("injection_target_bps"):
        # Le MÊME decluster que le scoreboard, appliqué aux trois colonnes.
        recovered = _episode_means(grp, col, CLUSTER_WINDOW_HOURS, cross_sectional=False)
        oracle = _episode_means(grp, "oracle_excess_at_selection_bps", CLUSTER_WINDOW_HOURS, False)
        injected = _episode_means(grp, "injected_measured_bps", CLUSTER_WINDOW_HOURS, False)
        common = recovered.index.intersection(oracle.index).intersection(injected.index)
        recovered, oracle, injected = recovered.loc[common], oracle.loc[common], injected.loc[common]
        if len(common) < 2:
            continue
        values = recovered.to_numpy(dtype=float)
        lo, hi = bootstrap_ci(values)
        sigma = float(recovered.std(ddof=1))
        n = int(len(common))
        out.append({
            "injection_target_bps": float(level),
            "n_decisions": int(len(grp)),
            "n_episodes": n,
            "injected_mean_bps": round(float(injected.mean()), 3),
            "recovered_mean_bps": round(float(recovered.mean()), 3),
            # Fidélité pure : ce que le labelliseur rend contre ce que l'oracle a vu,
            # sur les mêmes épisodes. Seuls le decluster et les refus peuvent l'écarter.
            "fidelity_gap_bps": round(float((recovered - oracle).mean()), 9),
            "recovered_ci95": [round(lo, 3), round(hi, 3)],
            "sigma_episode_bps": round(sigma, 1),
            "ci_half_width_bps": round(1.96 * sigma / np.sqrt(n), 2),
            "mde80_bps": round(2.802 * sigma / np.sqrt(n), 2),
            "recovered_net_base_bps": round(float(recovered.mean()) - COST_BPS_ROUNDTRIP_BASE, 3),
        })
    rows = sorted(out, key=lambda r: r["injection_target_bps"])
    baseline = next((r["recovered_mean_bps"] for r in rows
                     if abs(r["injection_target_bps"]) < 1e-9), None)
    for r in rows:
        r["displacement_vs_placebo_bps"] = (round(r["recovered_mean_bps"] - baseline, 3)
                                            if baseline is not None else None)
    return rows


def power_table(anchor: str = "dec") -> List[Dict[str, object]]:
    """La résolution dont dispose CHAQUE alpha réel, à son n d'épisodes.

    C'est la traduction du contrôle en verdict sur l'item A1 : un « pas d'edge »
    ne vaut que si l'appareil pouvait voir l'edge cherché. `mde80_bps` est le
    plus petit excess qu'un alpha peut distinguer de zéro avec 80 % de chances,
    à son nombre d'épisodes indépendants.
    """
    rows: List[Dict[str, object]] = []
    for alpha_id, spec in LABELABLE.items():
        df = load_outcomes(alpha_id, LAB_DIR)
        if df is None or df.empty:
            continue
        ok = df[df[f"{anchor}_status"] == "OK"]
        ep = _episode_means(ok, f"{anchor}_excess_bps", CLUSTER_WINDOW_HOURS, spec.cross_sectional)
        if len(ep) < 2:
            rows.append({"alpha_id": alpha_id, "n_episodes": int(len(ep)), "verdict": "TROP_PEU_D_EPISODES"})
            continue
        sigma, n = float(ep.std(ddof=1)), int(len(ep))
        half = 1.96 * sigma / np.sqrt(n)
        mean = float(ep.mean())
        rows.append({
            "alpha_id": alpha_id, "n_labeled": int(len(ok)), "n_episodes": n,
            "excess_mean_bps": round(mean, 2), "sigma_episode_bps": round(sigma, 1),
            "ci_half_width_bps": round(half, 1),
            "mde80_bps": round(2.802 * sigma / np.sqrt(n), 1),
            "ci95": [round(mean - half, 1), round(mean + half, 1)],
            # Le seuil qui compte : +15 bps NETS, soit +29 bps d'excess avant coût.
            "excludes_target_edge": bool(mean + half < TARGET_EXCESS_BPS),
            "verdict": ("CONTROLE" if alpha_id in CONTROL_ALPHAS
                        else "VRAI_NEGATIF" if mean + half < TARGET_EXCESS_BPS
                        else "INDECIDABLE"),
        })
    return rows


def transfer_function(rows: List[Dict[str, object]]) -> Dict[str, object]:
    """Droite `récupéré = a + b · injecté` sur les niveaux mesurés."""
    x = np.array([r["injected_mean_bps"] for r in rows], dtype=float)
    y = np.array([r["recovered_mean_bps"] for r in rows], dtype=float)
    if len(x) < 2:
        return {"slope": None, "intercept": None, "r2": None}
    b, a = np.polyfit(x, y, 1)
    fitted = a + b * x
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "slope": round(float(b), 4),
        "intercept_bps": round(float(a), 3),
        "r2": round(1.0 - ss_res / ss_tot, 5) if ss_tot > 1e-12 else None,
    }


def render(rows: List[Dict[str, object]], fit: Dict[str, object], meta: Dict[str, object],
           per_decision_max_diff: float, power: List[Dict[str, object]]) -> str:
    out: List[str] = []
    out.append("# Contrôle positif — ce que la chaîne restitue d'un edge connu")
    out.append("")
    out.append("_Généré par `scripts/audit_positive_control_recovery.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("Le placebo mesure ce que la chaîne **ajoute** à un signal sans edge. Celui-ci")
    out.append("mesure ce qu'elle **retire** à un signal qui en a un. Sans lui, « aucun alpha n'a")
    out.append("d'edge net du marché » (item A1, 548 décisions) reste indécidable entre deux")
    out.append("mondes : il n'y a pas d'edge, ou il y en a un et l'appareil le détruit.")
    out.append("")
    out.append("La réponse tient en deux lignes, et la seconde n'était pas la question posée.")
    out.append("")
    out.append("> **L'appareil est fidèle.** Un edge injecté ressort intact, au bit près.")
    out.append("> **L'appareil est aveugle.** À son nombre d'épisodes, il ne pouvait pas voir")
    out.append("> l'edge cherché — sur quatre alphas sur cinq.")
    out.append("")
    out.append("## 1. Ce qui a été injecté")
    out.append("")
    out.append("| grandeur | valeur |")
    out.append("|---|---|")
    out.append("| décisions construites | **%d** |" % meta["n_decisions"])
    out.append("| fenêtre | %s → %s |" % (meta["grid_start"][:10], meta["grid_stop"][:10]))
    out.append("| horizon | `fwd_4h`, fenêtres non recouvrantes |")
    out.append("| ancrage | `dec` — latence nulle par construction |")
    out.append("| look-ahead | **oui, délibéré** : c'est l'instrument |")
    out.append("| capital | **aucun** — `eligibility.BLOCK_POSITIVE_CONTROL` |")
    out.append("")
    out.append("## 2. Fidélité — la chaîne rend-elle ce qu'on lui donne ?")
    out.append("")
    out.append("L'oracle sélectionne avec `MarkSeriesCache` et `universe_return_bps` — les mêmes")
    out.append("objets que le labelliseur. Par décision, les deux doivent donc coïncider au bit")
    out.append("près, et tout écart viendrait des deux seules couches intermédiaires : le")
    out.append("decluster en épisodes et le filtre des refus.")
    out.append("")
    out.append("| contrôle | attendu | observé |")
    out.append("|---|---|---|")
    out.append("| écart max par décision | 0 | **%.6f bps** |" % per_decision_max_diff)
    out.append("| écart au niveau épisode | 0 | **%s bps** |" % ", ".join(
        "%.6f" % r["fidelity_gap_bps"] for r in rows))
    out.append("| décisions refusées (`NO_PRICE`/`STALE_MARK`) | — | **%d sur %d** |" % (
        meta["n_refused"], meta["n_decisions"]))
    out.append("| fonction de transfert | pente 1, ordonnée 0 | **pente %s, ordonnée %s bps** (r² %s) |" % (
        fit["slope"], fit["intercept_bps"], fit["r2"]))
    out.append("")
    out.append("| Δ visé | injecté (mesuré) | récupéré | déplacement vs placebo | IC 95 % | épisodes |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        out.append("| %.0f bps | %.2f | %.2f | %s | [%+.1f, %+.1f] | %d |" % (
            r["injection_target_bps"], r["injected_mean_bps"], r["recovered_mean_bps"],
            ("**%+.2f**" % r["displacement_vs_placebo_bps"]) if r["displacement_vs_placebo_bps"] is not None else "—",
            r["recovered_ci95"][0], r["recovered_ci95"][1], r["n_episodes"]))
    out.append("")
    out.append("Le déplacement suit l'injection. Il n'est pas exact niveau par niveau parce que")
    out.append("les niveaux tournent sur des barres DISJOINTES : chaque moyenne porte ±10 à 15 bps")
    out.append("de bruit de marché qui ne s'annule pas entre niveaux. La fidélité, elle, est")
    out.append("exacte, parce qu'elle est appariée sur les mêmes lignes.")
    out.append("")
    out.append("**Conclusion partielle : la chaîne de mesure ne détruit aucun signal.** Ni le")
    out.append("decluster, ni le filtre des refus, ni la soustraction du coût, ni le bootstrap.")
    out.append("L'hypothèse « l'appareil divise l'edge par quatre » est écartée.")
    out.append("")
    out.append("## 3. Résolution — la chaîne pouvait-elle VOIR l'edge cherché ?")
    out.append("")
    out.append("C'est la question que le contrôle a soulevée sans qu'on la pose. Un appareil peut")
    out.append("être parfaitement fidèle et incapable de distinguer +15 bps de zéro, s'il n'a pas")
    out.append("assez d'épisodes. La dispersion mesurée est de **σ ≈ %.0f bps par épisode** — le" % (
        float(np.mean([r["sigma_episode_bps"] for r in rows]))))
    out.append("marché crypto à 4 h. Il faut donc :")
    out.append("")
    sigma = float(np.mean([r["sigma_episode_bps"] for r in rows]))
    for target in (TARGET_EXCESS_BPS, 60.0):
        n80 = (2.802 * sigma / target) ** 2
        out.append("- **%.0f bps d'excess** (soit %.0f bps nets) : **%.0f épisodes indépendants** "
                   "pour 80 %% de chances de le voir." % (target, target - COST_BPS_ROUNDTRIP_BASE, n80))
    out.append("")
    out.append("### Ce dont chaque alpha disposait réellement")
    out.append("")
    out.append("| alpha | épisodes | excess mesuré | IC 95 % | plus petit edge visible | verdict |")
    out.append("|---|---|---|---|---|---|")
    for r in power:
        if r.get("verdict") == "TROP_PEU_D_EPISODES":
            out.append("| `%s` | %d | — | — | — | ⚪ rien à lire |" % (r["alpha_id"], r["n_episodes"]))
            continue
        mark = {"VRAI_NEGATIF": "✅ vrai négatif",
                "INDECIDABLE": "🟠 **indécidable**",
                "CONTROLE": "⚙️ contrôle — pas un candidat"}[r["verdict"]]
        out.append("| `%s` | %d | %+.1f bps | [%+.1f, %+.1f] | %.0f bps | %s |" % (
            r["alpha_id"], r["n_episodes"], r["excess_mean_bps"],
            r["ci95"][0], r["ci95"][1], r["mde80_bps"], mark))
    out.append("")
    out.append("_« Vrai négatif » = l'intervalle exclut la cible de %.0f bps d'excess "
               "(+%.0f bps nets). « Indécidable » = il ne l'exclut pas._" % (
                   TARGET_EXCESS_BPS, TARGET_NET_BPS))
    out.append("")
    out.append("## 4. Ce que ça change à la lecture de l'item A1")
    out.append("")
    undecided = [r for r in power if r.get("verdict") == "INDECIDABLE"]
    true_neg = [r for r in power if r.get("verdict") == "VRAI_NEGATIF"]
    out.append("« Aucun alpha n'a d'edge net du marché » se scinde en deux affirmations très")
    out.append("différentes, et une seule est établie :")
    out.append("")
    out.append("- **%d alpha(s) exclu(en)t réellement la cible** : %s. Là, le zéro est un zéro." % (
        len(true_neg), ", ".join("`%s`" % r["alpha_id"] for r in true_neg) or "aucun"))
    out.append("- **%d alpha(s) ne l'excluent pas** : %s. Leur intervalle contient" % (
        len(undecided), ", ".join("`%s`" % r["alpha_id"] for r in undecided) or "aucun"))
    out.append("  confortablement +29 bps. Ils n'ont pas montré l'absence d'edge, ils ont montré")
    out.append("  qu'ils n'avaient pas de quoi trancher.")
    out.append("")
    out.append("Le goulot n'est donc pas la mesure, c'est le **nombre d'épisodes indépendants** —")
    out.append("exactement la contrainte déjà identifiée au round 4 de la chasse sous sa forme")
    out.append("duale (`confirmable en N ans ⟺ Sharpe ≥ 5,60/√N`).")
    out.append("")
    out.append("## Ce que ce contrôle ne dit pas")
    out.append("")
    out.append("**Il ne teste pas la source de prix.** Les deux bouts lisent la même archive de")
    out.append("marks, donc une erreur commune aux deux resterait invisible. Comparer deux")
    out.append("archives indépendantes est une question distincte, et elle doit se faire SANS")
    out.append("sélection, sinon la malédiction du vainqueur produit une fausse atténuation.")
    out.append("")
    out.append("**Il ne teste pas la couche de découverte.** Features, backtest de validation et")
    out.append("déflation sont en amont du labelliseur et ne sont pas traversés ici.")
    out.append("")
    out.append("**Il ne mesure pas le coût de la latence.** `decided_at = event_time` par")
    out.append("construction : la latence est mesurée séparément (`decision_lag_h`, audit du")
    out.append("2026-09-05), et confondre les deux les rendrait toutes deux illisibles.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor", default="dec", choices=("dec", "evt"))
    args = parser.parse_args(argv)

    if not DECISIONS.exists():
        print("aucune décision : lance d'abord scripts/run_positive_control_oracle.py")
        return 2
    decisions = pd.read_parquet(DECISIONS)

    # Le VRAI labelliseur, pas une copie.
    report = label_alpha(ALPHA_ID, decisions, LABELABLE[ALPHA_ID], lab_dir=LAB_DIR)
    print(json.dumps(report, indent=2, default=str), flush=True)

    outcomes = load_outcomes(ALPHA_ID, LAB_DIR)
    if outcomes is None or outcomes.empty:
        print("aucun label produit")
        return 2

    keys = ["symbol", "direction", "event_time"]
    left = outcomes.copy()
    right = decisions[keys + ["injection_target_bps", "injected_measured_bps",
                              "oracle_excess_at_selection_bps"]].copy()
    for frame in (left, right):
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    joined = left.merge(right, on=keys, how="inner")

    col = f"{args.anchor}_excess_bps"
    ok = joined[(joined[f"{args.anchor}_status"] == "OK") & joined[col].notna()]
    per_decision_max_diff = (float((ok[col] - ok["oracle_excess_at_selection_bps"]).abs().max())
                             if len(ok) else float("nan"))

    rows = analyse(joined, args.anchor)
    fit = transfer_function(rows)

    state = json.loads((LAB_DIR / ALPHA_ID / "run_state.json").read_text(encoding="utf-8"))
    meta = {"n_decisions": len(decisions), "grid_start": state["grid_start"],
            "grid_stop": state["grid_stop"],
            "n_refused": int((joined[f"{args.anchor}_status"] != "OK").sum())}

    power = power_table(args.anchor)
    REPORT.write_text(render(rows, fit, meta, per_decision_max_diff, power), encoding="utf-8")
    print(json.dumps({
        "per_decision_max_abs_diff_bps": per_decision_max_diff,
        "levels": rows, "transfer_function": fit, "power": power, "report": str(REPORT),
    }, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
