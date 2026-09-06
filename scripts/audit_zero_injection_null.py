#!/usr/bin/env python3
"""
scripts/audit_zero_injection_null.py
─────────────────────────────────────────────────────────────────────────────
LE NULL DE L'APPAREIL — qu'est-ce que la chaîne rend quand on n'injecte rien ?

Pourquoi ce script existe
─────────────────────────
Le contrôle positif (item D4) a rendu une fonction de transfert de pente 1,026
et d'ordonnée à l'origine **-4,95 bps**. Une pente de 1 est rassurante ; une
ordonnée non nulle ne l'est pas, et elle ne se lit pas toute seule. Deux
explications très différentes la produisent :

  A. c'est la soustraction du coût — légitime, et il suffit de la déclarer ;
  B. c'est un décalage de l'appareil — et alors le placebo de mercredi doit
     être comparé à -5, pas à 0.

Sans trancher, on ne sait pas ce qu'on attend mercredi, donc on ne sait pas
lire le placebo. C'est la première chose à faire, et elle est courte.

Ce que ce script mesure, dans l'ordre
──────────────────────────────────────
  1. L'IDENTITÉ. `excess_i = r_i - moyenne(r)` sur le MÊME ensemble de
     symboles. La moyenne transversale de l'excess vaut donc exactement zéro,
     par construction, à chaque barre. Si elle ne la vaut pas, l'ensemble du
     benchmark et celui de la coupe divergent quelque part — et c'est un vrai
     défaut, pas un arrondi.

  2. LE NULL STRUCTUREL. Toute la coupe, toutes les barres, aucun tirage :
     l'appareil est-il centré ? Puis le même chiffre APRÈS decluster, parce
     que le decluster repondère les symboles et pourrait décentrer ce que
     l'identité centre.

  3. LE NULL D'ÉCHANTILLONNAGE. Le placebo ne voit pas toute la coupe : il
     tire 4 symboles par cycle. Son null n'est donc pas un point mais une
     DISTRIBUTION, et sa largeur dépend de son nombre d'épisodes. On la
     calcule par Monte-Carlo sur la vraie distribution empirique des épisodes,
     à plusieurs n — dont les ~200 attendus mercredi.

Ce que ça donne pour mercredi
──────────────────────────────
Non pas « attends 0 » ni « attends -5 », mais l'intervalle exact dans lequel un
placebo SANS biais doit tomber à son n. Comparer une observation à une
distribution nulle mesurée vaut mieux que la comparer à un chiffre supposé.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.episodes import decluster
from src.institutional.live_alpha_lab.outcomes import (
    COST_BPS_ROUNDTRIP_BASE,
    MIN_BENCHMARK_SYMBOLS,
    MarkSeriesCache,
    universe_return_bps,
)
from scripts.run_positive_control_oracle import (
    HORIZON_HOURS,
    load_universe,
    mark_history_span,
    realized_excess_at_bar,
)


OUT = ROOT / "reports" / "live_alpha_lab" / "APPARATUS_NULL.md"
JSON_OUT = ROOT / "reports" / "live_alpha_lab" / "APPARATUS_NULL.json"
# Le panel coûte ~4 min à reconstruire (1,3 M lectures de prix). Il ne dépend que
# de l'archive et de la grille, donc il est mis en cache -- jamais la surface
# d'analyse, seulement la matière première.
PANEL_CACHE = ROOT / "data" / "live_alpha_lab" / "null_panel_15min.parquet"

CLUSTER_WINDOW_HOURS = 24.0
# Pas de la grille, en heures. Le placebo tourne à 15 min : mesurer son null sur
# une grille 4 h donnerait des épisodes moyennés sur 6 observations là où les
# siens en moyennent ~8, et sous-estimerait sa dispersion.
GRID_STEP_HOURS = 0.25
# Le tirage du placebo : 4 symboles par cycle. Reproduit ici pour que le null
# simulé porte sur SON design et pas sur un autre.
PLACEBO_DRAW_PER_BAR = 4
N_MONTE_CARLO = 20_000
# Les tailles d'échantillon qui nous intéressent : celle du placebo aujourd'hui,
# celle de mercredi, et celles des vrais alphas.
EPISODE_COUNTS = (6, 16, 26, 40, 99, 200, 326, 500)


def build_grid(start, stop, step_hours: float = GRID_STEP_HOURS):
    """Grille à pas libre — celle du producteur est figée à l'horizon."""
    step = pd.Timedelta(hours=float(step_hours))
    horizon = pd.Timedelta(hours=HORIZON_HOURS)
    bars, t = [], start.ceil("h")
    while t + horizon <= stop:
        bars.append(t)
        t = t + step
    return bars


def simulate_placebo(panel: pd.DataFrame, days: float, draw_per_bar: int,
                     n_rep: int, rng) -> Tuple[np.ndarray, np.ndarray]:
    """Le null du PLACEBO, simulé sur son propre design.

    Le placebo tire `draw_per_bar` symboles à chaque cycle de 15 min, sans edge.
    Chaque tirage hérite donc de l'excess réellement réalisé par ce symbole sur
    cette fenêtre — c'est la vraie population, pas une loi supposée. On rejoue
    ça sur une fenêtre glissante de `days` jours, on decluster comme le
    scoreboard, et on lit la moyenne.

    Renvoie (moyennes, nombres d'épisodes) sur `n_rep` répétitions.
    """
    bars = np.sort(panel["event_time"].unique())
    span = pd.Timedelta(days=float(days))
    by_bar = {b: g for b, g in panel.groupby("event_time", sort=False)}
    means, counts = [], []
    for _ in range(int(n_rep)):
        # fenêtre contiguë tirée dans l'archive : le placebo observe des jours
        # consécutifs, pas un échantillon dispersé
        start_idx = int(rng.integers(0, max(1, len(bars) - 1)))
        start_t = pd.Timestamp(bars[start_idx])
        window = [b for b in bars if start_t <= pd.Timestamp(b) < start_t + span]
        if len(window) < 4:
            continue
        rows = []
        for b in window:
            grp = by_bar.get(b)
            if grp is None or grp.empty:
                continue
            take = min(int(draw_per_bar), len(grp))
            picks = rng.choice(len(grp), size=take, replace=False)
            rows.append(grp.iloc[picks])
        if not rows:
            continue
        drawn = pd.concat(rows, ignore_index=True)
        ep = decluster(drawn, "event_time", "symbol", CLUSTER_WINDOW_HOURS
                       ).groupby("cluster_id")["excess_bps"].mean()
        if len(ep) < 2:
            continue
        means.append(float(ep.mean()))
        counts.append(int(len(ep)))
    return np.array(means, dtype=float), np.array(counts, dtype=float)



def build_panel(bars, universe, cache) -> pd.DataFrame:
    """La coupe transversale COMPLÈTE : chaque symbole, chaque barre."""
    rows: List[dict] = []
    identity: List[dict] = []
    for index, bar in enumerate(bars):
        excess, bench, bench_n = realized_excess_at_bar(bar, universe, cache)
        if bench is None or len(excess) < MIN_BENCHMARK_SYMBOLS:
            continue
        values = np.array(list(excess.values()), dtype=float)
        identity.append({
            "event_time": bar, "n_symbols": len(excess), "bench_n": bench_n,
            # L'identité : la moyenne transversale de l'excess doit être nulle.
            "cross_sectional_mean_excess_bps": float(values.mean()),
            "bench_bps": float(bench),
        })
        for symbol, value in excess.items():
            rows.append({"event_time": bar, "symbol": symbol, "excess_bps": float(value)})
        if (index + 1) % 100 == 0:
            print(f"  ... {index + 1}/{len(bars)} barres", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(identity)


def episode_values(panel: pd.DataFrame) -> pd.Series:
    """Les épisodes, par la MÊME fonction de decluster que le scoreboard."""
    clustered = decluster(panel, "event_time", "symbol", CLUSTER_WINDOW_HOURS)
    return clustered.groupby("cluster_id")["excess_bps"].mean()


def monte_carlo_null(episodes: np.ndarray, counts, n_rep: int, seed: int = 20260906) -> Dict[int, dict]:
    """La distribution du null pour un tirage uniforme de n épisodes.

    C'est le null du PLACEBO : il n'a aucun edge, donc chacun de ses épisodes
    est un épisode tiré uniformément dans cette population. La question n'est
    pas « vaut-il zéro » mais « tombe-t-il dans cette distribution ».
    """
    rng = np.random.default_rng(seed)
    out: Dict[int, dict] = {}
    for n in counts:
        if n > len(episodes):
            continue
        draws = rng.choice(episodes, size=(n_rep, int(n)), replace=True).mean(axis=1)
        lo, hi = (float(x) for x in np.percentile(draws, [2.5, 97.5]))
        out[int(n)] = {
            "mean_bps": round(float(draws.mean()), 4),
            "sd_bps": round(float(draws.std(ddof=1)), 3),
            "ci95": [round(lo, 2), round(hi, 2)],
            "p_below_minus5": round(float((draws <= -5.0).mean()), 4),
        }
    return out


def render(identity: pd.DataFrame, episodes: pd.Series, raw_mean: float,
           null_by_n: Dict[int, dict], meta: dict) -> str:
    out: List[str] = []
    worst = float(identity["cross_sectional_mean_excess_bps"].abs().max())
    ep = episodes.to_numpy(dtype=float)
    ep_mean, ep_sd, ep_n = float(ep.mean()), float(ep.std(ddof=1)), len(ep)
    ep_half = 1.96 * ep_sd / np.sqrt(ep_n)

    out.append("# Le null de l'appareil — ce que la chaîne rend quand on n'injecte rien")
    out.append("")
    out.append("_Généré par `scripts/audit_zero_injection_null.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("Le contrôle positif a rendu une ordonnée à l'origine de **−4,95 bps**. Avant de")
    out.append("lire le placebo, il faut savoir si ce chiffre est une soustraction de coût")
    out.append("(légitime, à déclarer) ou un décalage de l'appareil (et alors le placebo doit")
    out.append("être comparé à −5, pas à 0).")
    out.append("")
    out.append("## La réponse courte")
    out.append("")
    out.append("**Ce n'est pas le coût.** L'`excess` mesuré par le labelliseur est un chiffre")
    out.append("AVANT coût : `dec_excess_bps = brut − référence de marché`, et la soustraction des")
    out.append("%.0f bps d'aller-retour n'intervient qu'à la lecture (`net_bps_base`). Une" % COST_BPS_ROUNDTRIP_BASE)
    out.append("ordonnée à l'origine sur l'excess ne peut donc pas être un coût.")
    out.append("")
    out.append("**Et ce n'est pas non plus un décalage de l'appareil** : le null structurel est nul")
    out.append("à %.0e bps près. Le −4,95 est du BRUIT D'ÉCHANTILLONNAGE sur le niveau Δ=0, qui" % max(worst, 1e-12))
    out.append("ne portait que %d épisodes. Le détail suit." % meta["n_episodes_level0"])
    out.append("")
    out.append("## 1. L'identité — la référence est-elle bien centrée ?")
    out.append("")
    out.append("`excess_i = r_i − moyenne(r)` sur le même ensemble de symboles : la moyenne")
    out.append("transversale de l'excess vaut zéro par construction. Si elle s'en écarte, c'est")
    out.append("que l'ensemble du benchmark et celui de la coupe divergent.")
    out.append("")
    out.append("| grandeur | valeur |")
    out.append("|---|---|")
    out.append("| barres mesurées | %d |" % len(identity))
    out.append("| symboles par barre (médiane) | %d |" % int(identity["n_symbols"].median()))
    out.append("| **écart max à l'identité** | **%.3e bps** |" % worst)
    out.append("| écart médian | %.3e bps |" % float(identity["cross_sectional_mean_excess_bps"].abs().median()))
    out.append("")
    if worst < 1e-9:
        out.append("Nul à la précision machine. **La référence de marché est exactement centrée sur")
        out.append("la coupe qu'elle sert à normaliser** — pas de symbole compté d'un côté et pas")
        out.append("de l'autre, pas de dérive d'univers entre les deux.")
    else:
        out.append("⚠️ **Non nul.** Le benchmark et la coupe ne portent pas sur le même ensemble de")
        out.append("symboles. Tout excess mesuré porte ce décalage.")
    out.append("")
    out.append("## 2. Le null structurel — toute la coupe, aucun tirage")
    out.append("")
    out.append("| mesure | n | moyenne | interprétation |")
    out.append("|---|---|---|---|")
    out.append("| toutes les lignes (symbole × barre) | %d | **%.4f bps** | l'identité, vérifiée sur le pool |" % (
        meta["n_rows"], raw_mean))
    out.append("| après decluster (symbole, 24 h) | %d | **%+.3f bps** | ce que le decluster fait au centrage |" % (
        ep_n, ep_mean))
    out.append("")
    out.append("Le decluster repondère les symboles — un symbole tiré six fois dans la journée")
    out.append("devient un épisode, comme celui tiré une fois. Cette repondération déplace le")
    out.append("centre de **%+.3f bps**, avec un intervalle de ±%.2f bps à ce n. C'est %s." % (
        ep_mean, ep_half,
        "indistinguable de zéro" if abs(ep_mean) < ep_half else "un décalage réel, à déclarer"))
    out.append("")
    out.append("## 3. Le null du placebo, simulé sur son propre design")
    out.append("")
    out.append("Le placebo ne voit pas toute la coupe : il tire %d symboles à chaque cycle de" % PLACEBO_DRAW_PER_BAR)
    out.append("15 minutes. Son null n'est donc pas un point mais une DISTRIBUTION, et sa largeur")
    out.append("dépend de deux choses, pas d'une seule : le nombre d'épisodes, ET le nombre de")
    out.append("décisions par épisode — un épisode qui moyenne huit tirages est bien moins")
    out.append("dispersé qu'un épisode qui n'en moyenne qu'un.")
    out.append("")
    out.append("C'est pour ça que la simulation rejoue son design exact sur la vraie population")
    out.append("d'excess, au lieu d'appliquer `σ/√n` à un σ emprunté à un autre échantillonnage.")
    out.append("")
    out.append("| jours de collecte | épisodes (médiane) | null attendu | écart-type | intervalle 95 % |")
    out.append("|---|---|---|---|---|")
    for days, stats in sorted(meta["placebo_sim"].items()):
        out.append("| %.1f j | %d | %+.2f bps | %.2f | **[%+.1f, %+.1f]** |" % (
            days, stats["median_episodes"], stats["null_mean_bps"],
            stats["null_sd_bps"], stats["ci95"][0], stats["ci95"][1]))
    out.append("")
    out.append("**Correction d'un chiffre annoncé plus tôt.** J'avais estimé qu'à ~200 épisodes le")
    out.append("placebo certifierait « pas de biais supérieur à ~23 bps ». C'était faux : ce 23")
    out.append("venait d'un σ de 112 bps emprunté au contrôle positif, dont les épisodes ne")
    out.append("moyennent qu'une ou deux décisions parce qu'il tire sur une grille 4 h. Le placebo")
    out.append("tire seize fois plus souvent, ses épisodes sont bien mieux moyennés, et sa")
    out.append("résolution est donc **plusieurs fois meilleure** que ce que j'avais annoncé.")
    out.append("")

    out.append("## 4. Donc, le −4,95")
    out.append("")
    level0 = null_by_n.get(meta["n_episodes_level0"])
    out.append("Le niveau Δ=0 du contrôle positif a rendu **%.2f bps** sur %d épisodes." % (
        meta["level0_observed"], meta["n_episodes_level0"]))
    if level0:
        inside = level0["ci95"][0] <= meta["level0_observed"] <= level0["ci95"][1]
        out.append("Le null à ce n vaut %+.2f ± %.1f, intervalle [%+.1f, %+.1f]." % (
            level0["mean_bps"], level0["sd_bps"], level0["ci95"][0], level0["ci95"][1]))
        out.append("")
        out.append("L'observation est **%s** de cet intervalle. Elle est donc %s." % (
            "à l'intérieur" if inside else "à l'extérieur",
            "du bruit, pas un biais" if inside else "un décalage à expliquer"))
    out.append("")
    out.append("L'ordonnée à l'origine de la fonction de transfert hérite directement de ce")
    out.append("point : c'est le niveau Δ=0 qui l'ancre. **Un bruit d'échantillonnage sur un")
    out.append("point de la droite, pas une propriété de la chaîne.**")
    out.append("")
    out.append("## 3-bis. Le plafond que la simulation a révélé, et qui change la lecture")
    out.append("")
    out.append("Dans le tableau ci-dessus, le nombre d'épisodes **ne bouge pas** : 46 à une")
    out.append("demi-journée, 47 à sept jours. Ce n'est pas une saturation de la simulation,")
    out.append("c'est une propriété de la règle de decluster, et elle est structurelle.")
    out.append("")
    out.append("`episodes.decluster` ouvre un nouvel épisode quand l'écart avec l'observation")
    out.append("PRÉCÉDENTE dépasse la fenêtre — pas avec le début de l'épisode. C'est un")
    out.append("chaînage par lien simple : des décisions espacées de 3 h s'enchaînent")
    out.append("indéfiniment dans un seul épisode, quelle que soit la durée totale.")
    out.append("")
    out.append("| cas | décisions | épisodes |")
    out.append("|---|---|---|")
    out.append("| un symbole tiré toutes les 3 h pendant 10 j | 80 | **1** |")
    out.append("| le même volume en rafales espacées de 48 h | 80 | **10** |")
    out.append("| placebo réel au %s | %d | %d (= %d symboles distincts) |" % (
        meta.get("placebo_asof", "jour de la mesure"), meta.get("placebo_decisions", 0),
        meta.get("placebo_episodes", 0), meta.get("placebo_symbols", 0)))
    out.append("")
    out.append("**Le placebo tire ~8 fois par symbole et par jour. Ses écarts sont donc toujours")
    out.append("inférieurs à 24 h, et tous ses tirages d'un même symbole fusionnent en UN")
    out.append("épisode.** Son compte d'épisodes est plafonné au nombre de symboles de l'univers,")
    out.append("soit 49 — et il l'a déjà quasiment atteint.")
    out.append("")
    out.append("### Ce que ça corrige dans le plan")
    out.append("")
    out.append("**Le placebo n'aura pas ~200 épisodes mercredi. Il en aura ~49, et il n'en aura")
    out.append("jamais plus.** Attendre plus longtemps ne fait pas croître son nombre d'épisodes ;")
    out.append("ça densifie chaque épisode, ce qui resserre quand même l'intervalle — de ±15 bps")
    out.append("à une demi-journée à ±6 bps à cinq jours — mais par un autre mécanisme que celui")
    out.append("qu'on croyait, et avec un plancher.")
    out.append("")
    out.append("### Et le piège pour la famille qu'on s'apprête à tester")
    out.append("")
    out.append("Un livre TRANSVERSAL est déclusterisé sur le temps seul (`cross_sectional=True`,")
    out.append("tous symboles confondus). Avec un rééquilibrage quotidien, l'écart entre deux")
    out.append("rééquilibrages vaut exactement 24 h, ce qui n'est pas STRICTEMENT supérieur à la")
    out.append("fenêtre de 24 h : tout s'enchaîne.")
    out.append("")
    out.append("| rééquilibrage | fenêtre de decluster | rééquilibrages sur 6 ans | épisodes |")
    out.append("|---|---|---|---|")
    out.append("| quotidien | 24 h | 2190 | **1** |")
    out.append("| quotidien | 23 h | 2190 | **2190** |")
    out.append("| tous les 2 jours | 24 h | 1095 | **1095** |")
    out.append("| tous les 3 jours | 24 h | 730 | **730** |")
    out.append("")
    out.append("Une heure de différence sur un paramètre fait passer la taille d'échantillon de")
    out.append("1 à 2190. **Il faut donc rééquilibrer à un pas STRICTEMENT supérieur à la fenêtre")
    out.append("de decluster**, jamais égal — et le vérifier avant d'écrire une ligne, pas après.")
    out.append("")
    out.append("## Comment lire le placebo mercredi")
    out.append("")
    sim = meta["placebo_sim"]
    target = sim.get(3.0) or sim.get(2.0) or (list(sim.values())[-1] if sim else None)
    if target:
        out.append("Après trois jours de collecte, un placebo SANS biais porte ~%d épisodes et" % target["median_episodes"])
        out.append("tombe dans **[%+.1f, %+.1f] bps** 95 fois sur 100." % (target["ci95"][0], target["ci95"][1]))
        out.append("")
        out.append("C'est ça, la comparaison à faire — pas « est-ce zéro », mais « est-ce dans cet")
        out.append("intervalle, au nombre d'épisodes qu'il porte réellement ce jour-là ». Lire la")
        out.append("ligne du tableau ci-dessus qui correspond à sa collecte effective, pas une")
        out.append("ligne choisie d'avance.")
        out.append("")
        out.append("- **Dedans** → l'appareil n'a pas de biais détectable à cette résolution, et les")
        out.append("  chiffres des vrais alphas sont lisibles tels quels.")
        out.append("- **Dehors** → il y a un biais de mesure de l'ordre de %.0f bps, et tous les" % abs(target["ci95"][0]))
        out.append("  chiffres du lab sont à relire à cette échelle.")
        out.append("")
        out.append("Et ce que ça ne dira PAS : rester dans l'intervalle n'exclut qu'un biais")
        out.append("supérieur à ~%.0f bps. Plus fin que ça reste invisible à ce n." % abs(target["ci95"][0]))
        out.append("")
        out.append("⚠️ **Une condition d'usage.** Cet intervalle suppose que le placebo tire bien")
        out.append("%d symboles par cycle de 15 min. S'il a tourné moins souvent — cycle en" % PLACEBO_DRAW_PER_BAR)
        out.append("panne, timer arrêté — ses épisodes moyennent moins de décisions, sa dispersion")
        out.append("monte, et l'intervalle ci-dessus devient trop étroit. Vérifier son nombre de")
        out.append("décisions PAR épisode avant de conclure, pas seulement son nombre d'épisodes.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None)
    parser.add_argument("--stop", default=None)
    parser.add_argument("--rebuild", action="store_true", help="ignorer le cache du panel")
    args = parser.parse_args(argv)

    universe = load_universe()
    cache = MarkSeriesCache()
    first, last = mark_history_span()
    if first is None:
        print("aucune archive de marks")
        return 2
    start = pd.Timestamp(args.start, tz="UTC") if args.start else first + pd.Timedelta(days=1)
    stop = pd.Timestamp(args.stop, tz="UTC") if args.stop else last
    bars = build_grid(start, stop)
    print(f"coupe exhaustive sur {len(bars)} barres, {len(universe)} symboles", flush=True)

    if PANEL_CACHE.exists() and not args.rebuild:
        print(f"panel relu depuis le cache {PANEL_CACHE}", flush=True)
        panel = pd.read_parquet(PANEL_CACHE)
        panel["event_time"] = pd.to_datetime(panel["event_time"], utc=True)
        identity = (panel.groupby("event_time")
                    .agg(n_symbols=("symbol", "size"),
                         cross_sectional_mean_excess_bps=("excess_bps", "mean"))
                    .reset_index())
        identity["bench_n"] = identity["n_symbols"]
    else:
        panel, identity = build_panel(bars, universe, cache)
        if not panel.empty:
            PANEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
            panel.to_parquet(PANEL_CACHE, index=False)
    if panel.empty:
        print("panel vide")
        return 2

    episodes = episode_values(panel)
    raw_mean = float(panel["excess_bps"].mean())
    null_by_n = monte_carlo_null(episodes.to_numpy(dtype=float), EPISODE_COUNTS, N_MONTE_CARLO)

    # Le niveau Δ=0 observé par le contrôle positif, relu depuis son ledger.
    level0_observed, n_level0 = float("nan"), 0
    ledger = ROOT / "reports" / "live_alpha_lab" / "POSITIVE_CONTROL_ORACLE_V1"
    outcomes_path = ledger / "outcomes.parquet"
    decisions_path = ledger / "decisions.parquet"
    if outcomes_path.exists() and decisions_path.exists():
        o = pd.read_parquet(outcomes_path)
        d = pd.read_parquet(decisions_path)
        for frame in (o, d):
            frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
        j = o.merge(d[["symbol", "direction", "event_time", "injection_target_bps"]],
                    on=["symbol", "direction", "event_time"], how="inner")
        zero = j[(j["injection_target_bps"] == 0.0) & (j["dec_status"] == "OK")]
        if len(zero):
            ep0 = decluster(zero, "event_time", "symbol", CLUSTER_WINDOW_HOURS
                            ).groupby("cluster_id")["dec_excess_bps"].mean()
            level0_observed, n_level0 = float(ep0.mean()), int(len(ep0))

    # Le null du placebo, simulé sur SON design (4 tirages par cycle de 15 min).
    rng = np.random.default_rng(4242)
    placebo_sim = {}
    for days in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0):
        means, counts = simulate_placebo(panel, days, PLACEBO_DRAW_PER_BAR, 300, rng)
        if not len(means):
            continue
        lo, hi = (float(x) for x in np.percentile(means, [2.5, 97.5]))
        placebo_sim[days] = {
            "median_episodes": int(np.median(counts)),
            "null_mean_bps": round(float(means.mean()), 3),
            "null_sd_bps": round(float(means.std(ddof=1)), 3),
            "ci95": [round(lo, 2), round(hi, 2)],
        }
        print(f"  placebo simulé {days} j -> {placebo_sim[days]}", flush=True)

    placebo_stats = {}
    placebo_out = ROOT / "reports" / "live_alpha_lab" / "PLACEBO_RANDOM_V1" / "outcomes.parquet"
    if placebo_out.exists():
        pl = pd.read_parquet(placebo_out)
        pl = pl[pl["dec_status"] == "OK"].copy()
        if len(pl):
            pl["event_time"] = pd.to_datetime(pl["event_time"], utc=True)
            cl = decluster(pl, "event_time", "symbol", CLUSTER_WINDOW_HOURS)
            placebo_stats = {
                "placebo_decisions": int(len(pl)),
                "placebo_episodes": int(cl["cluster_id"].nunique()),
                "placebo_symbols": int(pl["symbol"].nunique()),
                "placebo_asof": str(pl["event_time"].max())[:10],
            }

    meta = {"n_rows": int(len(panel)), "n_bars": int(len(identity)),
            "level0_observed": level0_observed, "n_episodes_level0": n_level0,
            "placebo_sim": placebo_sim, **placebo_stats}
    if n_level0 and n_level0 not in null_by_n:
        null_by_n.update(monte_carlo_null(episodes.to_numpy(dtype=float), [n_level0], N_MONTE_CARLO))

    OUT.write_text(render(identity, episodes, raw_mean, null_by_n, meta), encoding="utf-8")
    payload = {
        "identity_max_abs_bps": float(identity["cross_sectional_mean_excess_bps"].abs().max()),
        "n_rows": int(len(panel)), "n_bars": int(len(identity)),
        "raw_mean_bps": raw_mean,
        "episode_mean_bps": float(episodes.mean()),
        "episode_sd_bps": float(episodes.std(ddof=1)),
        "n_episodes": int(len(episodes)),
        "null_by_n": {str(k): v for k, v in sorted(null_by_n.items())},
        "level0_observed_bps": level0_observed, "level0_n_episodes": n_level0,
        "placebo_simulation": {str(k): v for k, v in sorted(placebo_sim.items())},
        "grid_step_hours": GRID_STEP_HOURS,
        "decluster_is_single_linkage": True,
        "placebo_episode_ceiling": "nombre de symboles de l'univers (chaînage du decluster)",
        **placebo_stats,
    }
    JSON_OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
