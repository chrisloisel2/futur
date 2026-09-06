#!/usr/bin/env python3
"""
scripts/audit_episode_budget.py
─────────────────────────────────────────────────────────────────────────────
COMBIEN D'ÉPISODES, ET QUEL EDGE MINIMAL — avant d'écrire une ligne de signal.

Pourquoi avant
──────────────
Quatre des cinq alphas de l'item A1 sont « indécidables » : leur intervalle de
confiance ne pouvait pas exclure l'edge qu'ils cherchaient. Ce n'est pas un
défaut de mesure, c'est un défaut de CONCEPTION — personne n'avait calculé, en
amont, combien d'épisodes leur design produirait ni quel edge minimal ce nombre
rendait visible. Ce script fait ce calcul, et il coûte une heure.

Aveugle au résultat, délibérément
──────────────────────────────────
Il ne construit AUCUN signal. Les paniers sont tirés au hasard : `k` longs et
`k` shorts pris uniformément dans l'univers. Ce qu'on mesure est la DISPERSION
d'un livre dollar-neutre de cette forme — une propriété du marché et du design,
pas d'une prédiction. On peut donc le faire avant de sceller les hypothèses
sans rien contaminer, exactement comme les passes de criblage économique
d'alpha_foundry_v5.

Ce qui pilote le nombre d'épisodes, et ce n'est pas la durée
─────────────────────────────────────────────────────────────
`episodes.decluster` chaîne par lien simple : un livre rééquilibré à un pas
INFÉRIEUR OU ÉGAL à la fenêtre de decluster fond en un seul épisode, quelle que
soit la durée d'historique. Six ans de rééquilibrage quotidien avec une fenêtre
de 24 h donnent UN épisode. Le pas de rééquilibrage est donc le premier
paramètre de conception, avant l'horizon et avant le signal.

L'edge minimal détectable, sous le seuil pré-enregistré
────────────────────────────────────────────────────────
Pas `2,802·σ/√n` — ça, c'est le seuil d'une hypothèse unique. Avec cinq
hypothèses scellées, le seuil de Bonferroni unilatéral vaut 2,33, et pour avoir
80 % de chances de le franchir il faut un edge d'au moins `(t* + 0,8416)·σ/√n`.
Tester cinq choses coûte de la puissance, et ce script le chiffre.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.preregistration import threshold_t

HISTORY = Path("/home/qbee/futur-alpha-foundry-v5/data/alpha_foundry_v5/history/klines_1h")
OUT = ROOT / "reports" / "live_alpha_lab" / "EPISODE_BUDGET.md"
JSON_OUT = ROOT / "reports" / "live_alpha_lab" / "EPISODE_BUDGET.json"

# La fenêtre de decluster du lab. Le pas de rééquilibrage doit lui être
# STRICTEMENT supérieur, sinon tout s'enchaîne (voir tests/test_episodes.py).
CLUSTER_WINDOW_HOURS = 24.0

HORIZON_DAYS = (1.0, 2.0, 3.0)
REBALANCE_DAYS = (1.0, 2.0, 3.0, 5.0)
BASKET_SIZES = (5, 10, 15)
POWER_Z = 0.8416       # 80 % de puissance
N_HYPOTHESES = 5       # ce qui sera scellé


def load_closes() -> pd.DataFrame:
    """Matrice (temps × symbole) des clôtures horaires, grille dense."""
    frames = {}
    for path in sorted(HISTORY.glob("*.parquet")):
        symbol = path.stem
        df = pd.read_parquet(path, columns=["open_time_ms", "close"])
        s = pd.Series(df["close"].to_numpy(dtype=float),
                      index=pd.to_datetime(df["open_time_ms"], unit="ms", utc=True))
        frames[symbol] = s[~s.index.duplicated(keep="last")]
    matrix = pd.DataFrame(frames).sort_index()
    return matrix


def basket_returns(closes: pd.DataFrame, horizon_days: float, rebalance_days: float,
                   k: int, rng) -> Dict[str, float]:
    """Dispersion d'un livre dollar-neutre ALÉATOIRE de k longs / k shorts.

    Aucun signal : les paniers sont tirés uniformément. On mesure ce que
    l'enveloppe du design produit comme bruit, pas ce qu'une prédiction
    produirait comme rendement.
    """
    step = int(round(rebalance_days * 24))
    horizon = int(round(horizon_days * 24))
    index = closes.index
    starts = range(0, len(index) - horizon, step)

    per_episode: List[float] = []
    for i in starts:
        entry = closes.iloc[i]
        exit_ = closes.iloc[i + horizon]
        valid = entry.notna() & exit_.notna() & (entry > 0)
        symbols = list(entry.index[valid])
        if len(symbols) < 2 * k + 5:
            continue
        rets = (exit_[symbols] / entry[symbols] - 1.0).to_numpy(dtype=float) * 1e4
        # UN tirage par rééquilibrage, pas plusieurs moyennés : c'est la
        # sémantique correcte, puisqu'une stratégie prend une décision par
        # rééquilibrage. Moyenner plusieurs paniers réduirait artificiellement
        # σ et surestimerait la résolution du design.
        picks = rng.choice(len(symbols), size=2 * k, replace=False)
        longs, shorts = rets[picks[:k]], rets[picks[k:]]
        per_episode.append(float(longs.mean() - shorts.mean()))

    if len(per_episode) < 10:
        return {}
    values = np.asarray(per_episode, dtype=float)
    n = len(values)
    sigma = float(values.std(ddof=1))
    t_star = threshold_t(N_HYPOTHESES)
    return {
        "n_episodes": n,
        "sigma_bps": round(sigma, 1),
        "se_bps": round(sigma / np.sqrt(n), 2),
        "mde_single_bps": round(2.802 * sigma / np.sqrt(n), 2),
        "mde_preregistered_bps": round((t_star + POWER_Z) * sigma / np.sqrt(n), 2),
        "mean_bps": round(float(values.mean()), 2),
    }


def viable_configs(rows: List[dict]) -> List[dict]:
    """Les configurations dont le pas dépasse STRICTEMENT la fenêtre de
    decluster. Critère purement structurel — aucun résultat n'y entre."""
    return [r for r in rows if r["rebalance_days"] * 24.0 > CLUSTER_WINDOW_HOURS]


def select_design(rows: List[dict]) -> Optional[dict]:
    """La règle de choix du design, isolée pour être TESTABLE.

    Elle ne lit que `mde_preregistered_bps`, lui-même fonction de σ et de n
    seulement. Elle ne touche jamais `mean_bps` ni aucun t-stat — c'est ce qui
    rend le choix aveugle au résultat, donc gratuit en budget d'essais.

    `tests/test_episode_budget_blindness.py` le vérifie en permutant les
    moyennes et en montrant que le choix ne bouge pas. Une affirmation
    d'aveuglement doit être un test, pas une phrase.
    """
    viable = viable_configs(rows)
    if not viable:
        return None
    return min(viable, key=lambda r: r["mde_preregistered_bps"])


def render(rows: List[dict], meta: dict) -> str:
    out: List[str] = []
    t_star = threshold_t(N_HYPOTHESES)
    out.append("# Budget d'épisodes — combien la famille en produit, et quel edge devient visible")
    out.append("")
    out.append("_Généré par `scripts/audit_episode_budget.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("Calculé **avant** d'écrire le moindre signal, et **aveugle au résultat** : les")
    out.append("paniers sont tirés au hasard. Ce qui est mesuré ici est la dispersion d'un livre")
    out.append("dollar-neutre de cette forme — une propriété du marché et du design, pas d'une")
    out.append("prédiction. C'est le calcul qui manquait aux quatre alphas indécidables.")
    out.append("")
    out.append("| grandeur | valeur |")
    out.append("|---|---|")
    out.append("| historique | %s → %s |" % (meta["start"], meta["stop"]))
    out.append("| symboles | %d |" % meta["n_symbols"])
    out.append("| barres horaires | %s |" % f"{meta['n_bars']:,}".replace(",", " "))
    out.append("| fenêtre de decluster du lab | %.0f h |" % CLUSTER_WINDOW_HOURS)
    out.append("| hypothèses à sceller | %d → seuil `t > %.2f` |" % (N_HYPOTHESES, t_star))
    out.append("")
    out.append("## Le premier paramètre de conception n'est pas l'horizon, c'est le pas")
    out.append("")
    out.append("`episodes.decluster` chaîne par lien simple. Un rééquilibrage à un pas")
    out.append("**inférieur ou égal** à la fenêtre de 24 h fond tout l'historique en un seul")
    out.append("épisode — six ans compris. Les lignes concernées sont marquées ⛔ : leur nombre")
    out.append("d'épisodes NOMINAL est celui de la colonne, mais leur nombre RÉEL est 1.")
    out.append("")
    out.append("| horizon | pas | panier | épisodes | σ / épisode | erreur-type | edge min. détectable |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rows:
        chained = r["rebalance_days"] * 24.0 <= CLUSTER_WINDOW_HOURS
        flag = " ⛔" if chained else ""
        out.append("| %.0f j | %.0f j%s | %d/%d | %s | %.0f bps | %.1f bps | **%.1f bps** |" % (
            r["horizon_days"], r["rebalance_days"], flag, r["k"], r["k"],
            ("~~%d~~ → **1**" % r["n_episodes"]) if chained else str(r["n_episodes"]),
            r["sigma_bps"], r["se_bps"], r["mde_preregistered_bps"]))
    out.append("")
    out.append("_Le nombre d'épisodes décroît quand le panier grandit : un panier de 15/15 exige")
    out.append("35 symboles cotés, ce que les premières années de l'historique ne fournissent pas")
    out.append("toujours. C'est un arbitrage réel entre diversification et longueur d'échantillon._")
    out.append("")
    out.append("_« Edge min. détectable » = l'excess moyen par épisode qu'il faut pour avoir")
    out.append("80 %% de chances de franchir le seuil pré-enregistré `t > %.2f`, soit" % t_star)
    out.append("`(t* + 0,84)·σ/√n`. Ce n'est PAS `1,96·σ/√n` : tester cinq hypothèses coûte de")
    out.append("la puissance, et ce coût est déjà compté ici._")
    out.append("")
    best = select_design(rows)
    if best is not None:
        out.append("## Ce que ça impose au design")
        out.append("")
        out.append("**Le pas de rééquilibrage doit être ≥ 2 jours.** À un jour, le decluster fond")
        out.append("l'historique entier en un épisode et aucune hypothèse n'est décidable — quel")
        out.append("que soit son edge.")
        out.append("")
        out.append("Sous cette contrainte, la configuration la plus résolvante est **horizon %.0f j,")
        out.append("pas %.0f j, panier %d/%d** : %d épisodes, σ = %.0f bps, et un edge de")
        out.append("**%.1f bps par épisode** devient détectable au seuil des cinq hypothèses.")
        out[-3] = out[-3] % best["horizon_days"]
        out[-2] = out[-2] % (best["rebalance_days"], best["k"], best["k"],
                             best["n_episodes"], best["sigma_bps"])
        out[-1] = out[-1] % best["mde_preregistered_bps"]
        out.append("")
        out.append("À comparer à la cible opérationnelle de **+15 bps nets**, soit +29 bps d'excess")
        out.append("avant coût : %s" % (
            "cette configuration a la résolution nécessaire, et de loin."
            if best["mde_preregistered_bps"] < 29.0 else
            "cette configuration N'A PAS la résolution nécessaire — il faut allonger "
            "l'historique, élargir l'univers ou accepter un edge plus gros."))
        out.append("")
        out.append("Le panier le plus large réduit σ par diversification ; le pas le plus court")
        out.append("compatible avec le decluster maximise n. Les deux vont dans le même sens, ce")
        out.append("qui est rare et qu'il faut prendre.")
    out.append("")
    out.append("## Une démonstration involontaire, et elle vaut d'être lue")
    out.append("")
    best_noise = max(rows, key=lambda r: abs(r["mean_bps"]) / max(r["se_bps"], 1e-9))
    t_noise = abs(best_noise["mean_bps"]) / max(best_noise["se_bps"], 1e-9)
    out.append("Les %d configurations ci-dessus tirent des paniers **au hasard**. Leur moyenne" % len(rows))
    out.append("devrait donc être nulle. La plus extrême affiche pourtant %+.1f bps avec une" % best_noise["mean_bps"])
    out.append("erreur-type de %.1f, soit **t = %.2f** (horizon %.0f j, pas %.0f j, panier %d/%d)." % (
        best_noise["se_bps"], t_noise, best_noise["horizon_days"],
        best_noise["rebalance_days"], best_noise["k"], best_noise["k"]))
    out.append("")
    out.append("Ce t franchit le seuil d'une hypothèse unique (1,64) %s le seuil des cinq" % (
        "et même" if t_noise > t_star else "mais pas"))
    out.append("hypothèses (%.2f). Sur du bruit pur, sans aucun signal." % t_star)
    out.append("")
    out.append("C'est la démonstration la plus courte de pourquoi le seuil doit être dérivé du")
    out.append("nombre d'essais : il a suffi d'en regarder %d pour en sortir un qui a l'air" % len(rows))
    out.append("d'une découverte. Le round 4 en a regardé ~700.")
    out.append("")
    out.append("## Ce que ce budget ne dit pas")
    out.append("")
    out.append("**La colonne `moyenne` n'est pas un résultat.** C'est le rendement d'un panier")
    out.append("aléatoire : du bruit, dont la seule fonction ici est la démonstration ci-dessus.")
    out.append("")
    out.append("**Il ne dit pas qu'il y a un edge.** σ est la dispersion d'un panier ALÉATOIRE :")
    out.append("elle borne ce qu'on pourra VOIR, pas ce qu'on trouvera. Un vrai signal aura une")
    out.append("dispersion différente — plus faible s'il sélectionne des noms corrélés, plus")
    out.append("forte s'il se concentre sur les extrêmes.")
    out.append("")
    out.append("**Il suppose des épisodes indépendants.** Avec un pas strictement supérieur à la")
    out.append("fenêtre, les fenêtres de détention ne se recouvrent pas — mais la corrélation")
    out.append("transversale résiduelle entre épisodes voisins n'est pas nulle pour autant. Le")
    out.append("n effectif est donc une borne HAUTE.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default=str(HISTORY))
    args = parser.parse_args(argv)

    closes = load_closes()
    print("matrice %d barres × %d symboles" % closes.shape, flush=True)
    rng = np.random.default_rng(20260906)

    rows: List[dict] = []
    for horizon in HORIZON_DAYS:
        for rebalance in REBALANCE_DAYS:
            if rebalance < horizon:
                continue  # fenêtres de détention recouvrantes : pas d'épisodes indépendants
            for k in BASKET_SIZES:
                stats = basket_returns(closes, horizon, rebalance, k, rng)
                if not stats:
                    continue
                rows.append({"horizon_days": horizon, "rebalance_days": rebalance,
                             "k": k, **stats})
                print("  h=%.0fj pas=%.0fj k=%d -> %s" % (horizon, rebalance, k, stats), flush=True)

    meta = {"start": str(closes.index.min())[:10], "stop": str(closes.index.max())[:10],
            "n_symbols": int(closes.shape[1]), "n_bars": int(closes.shape[0])}
    OUT.write_text(render(rows, meta), encoding="utf-8")
    JSON_OUT.write_text(json.dumps({"meta": meta, "configs": rows,
                                    "cluster_window_hours": CLUSTER_WINDOW_HOURS,
                                    "n_hypotheses": N_HYPOTHESES,
                                    "threshold_t": threshold_t(N_HYPOTHESES)},
                                   indent=2), encoding="utf-8")
    print("écrit -> %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
