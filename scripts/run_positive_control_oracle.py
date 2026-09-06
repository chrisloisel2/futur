#!/usr/bin/env python3
"""
scripts/run_positive_control_oracle.py
─────────────────────────────────────────────────────────────────────────────
POSITIVE_CONTROL_ORACLE_V1 — un alpha dont l'edge est CONNU parce qu'il est
injecté, et qui traverse exactement la même chaîne de mesure que les vrais.

Pourquoi il existe
──────────────────
PLACEBO_RANDOM_V1 (item D3) mesure ce que la chaîne AJOUTE à un signal sans
edge. Il ne peut pas mesurer ce qu'elle RETIRE à un signal qui en a un — un
placebo à zéro reste à zéro qu'on l'atténue de 10 % ou de 90 %.

Or c'est cette seconde question qui décide de la lecture de l'item A1
(« aucun alpha n'a d'edge net du marché », 548 décisions). Deux mondes très
différents produisent ce même résultat :

  A. il n'y a pas d'edge dans les signaux          -> les négatifs sont vrais
  B. il y en a un et la chaîne le détruit          -> les négatifs ne veulent rien dire

Aucune statistique SUR LES ALPHAS RÉELS ne peut les distinguer, puisque dans
les deux cas la sortie est nulle. Un edge injecté, si : on met +29 bps
d'excess dans la chaîne et on regarde ce qui sort à l'autre bout.

Comment l'edge est injecté
──────────────────────────
C'est le placebo, plus un déplacement mesuré. À chaque barre :

  1. tirage uniforme d'un symbole j  (exactement le placebo)
  2. cible = excess_réalisé(j) + Δ
  3. on retient le symbole dont l'excess réalisé est le plus proche de la cible

Δ est l'edge injecté. La dispersion, elle, est héritée de la vraie coupe
transversale — un contrôle dont toutes les décisions vaudraient exactement
+29 bps testerait la moyenne mais pas l'intervalle de confiance.

L'injection RÉELLEMENT obtenue est mesurée et scellée par décision
(`injected_measured_bps` = excess du retenu − excess du tirage), jamais
supposée égale à Δ : quand la cible dépasse le maximum de la coupe, le plus
proche est le maximum et l'injection est plus faible que demandée. On compare
donc toujours le récupéré à l'injecté MESURÉ, pas au Δ nominal.

Quatre niveaux Δ ∈ {0, 10, 29, 60} bps sur des barres DISJOINTES :
  - Δ=0 redonne le tirage uniforme, donc un second contrôle négatif
    indépendant de PLACEBO_RANDOM_V1 ;
  - quatre points donnent la fonction de transfert de la chaîne (pente et
    ordonnée à l'origine) au lieu d'un seul rapport, ce qui distingue une
    atténuation multiplicative d'un biais additif.
  - disjoints parce que deux niveaux sur la même barre pourraient retenir le
    même symbole, donc la même `decision_key`, et le ledger scellé n'en
    garderait qu'une.

Ce qu'il teste, et ce qu'il ne teste pas
────────────────────────────────────────
La sélection utilise `outcomes.MarkSeriesCache` et `outcomes.universe_return_bps`
— LES MÊMES objets que le labelliseur, pas une réimplémentation. Un contrôle
qui recalculerait le prix de son côté ne contrôlerait pas la chaîne, il
contrôlerait une copie de la chaîne.

Conséquence assumée : par décision, le récupéré DOIT être identique à
l'injecté au bit près. Ce que ce contrôle mesure vraiment est donc tout ce qui
vient APRÈS la mesure par décision — decluster en épisodes, filtre des refus
(NO_PRICE / STALE_MARK), soustraction du coût, bootstrap, agrégation. C'est
exactement la couche qui a produit le verdict de l'item A1.

Ce qu'il NE teste PAS : la source de prix elle-même (les deux bouts sont
mesurés sur la même archive), ni la couche de découverte en amont (features,
backtest de validation, déflation). L'accord entre archives de prix est une
question distincte, traitée sans sélection par
`scripts/audit_positive_control_recovery.py --cross-source`.

Look-ahead, et pourquoi il est ici légitime
────────────────────────────────────────────
Cet alpha choisit ses décisions EN CONNAISSANT leur rendement. C'est
inadmissible pour un candidat et c'est la définition même d'un instrument de
mesure : on injecte une quantité connue pour lire ce que l'appareil en
restitue. Trois garde-fous, parce qu'un contrôle positif affiche par
construction un rendement magnifique et qu'il ne doit jamais pouvoir être lu
comme un résultat :

  1. `scientific_status: POSITIVE_CONTROL` -> `eligibility.BLOCK_POSITIVE_CONTROL`,
     porte placée AVANT toute consultation du registre de validation ;
  2. `uses_lookahead=True` sur CHAQUE ligne du ledger ;
  3. `data_live: false` au registre.

`decided_at = event_time` : latence nulle par construction. Le coût de la
latence est une question séparée, déjà mesurée par `decision_lag_h` et
l'audit du 2026-09-05 ; la confondre avec l'atténuation de mesure rendrait les
deux illisibles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yaml

from src.institutional.live_alpha_lab.outcomes import (
    MIN_BENCHMARK_SYMBOLS,
    MarkSeriesCache,
    benchmark_universe,
    universe_return_bps,
)
from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

ALPHA_ID = "POSITIVE_CONTROL_ORACLE_V1"
HORIZON = "fwd_4h"
HORIZON_HOURS = 4.0
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"

# Les niveaux d'injection, en bps d'EXCESS (net de l'univers, avant coût).
# 29 bps est le point qui compte : moins le coût aller-retour de base (14 bps),
# il vaut les +15 bps nets qui sont l'objectif opérationnel.
INJECTION_LEVELS_BPS: Tuple[float, ...] = (0.0, 10.0, 29.0, 60.0)

# Fenêtres NON RECOUVRANTES : un pas égal à l'horizon. Des barres qui se
# chevauchent ne donneraient pas plus d'épisodes indépendants — le decluster
# les fusionnerait — elles ne feraient que gonfler le compte brut.
BAR_STEP_HOURS = 4.0

# Décisions par barre. Au-dessus de 1 pour resserrer l'intervalle ; plafonné
# par le decluster (symbole, 24 h) de toute façon.
DECISIONS_PER_BAR = 4

DIRECTION = "LONG"


def load_universe() -> List[str]:
    """Univers FIGÉ, jamais un glob() (bug d'universe-drift, 2026-08-30)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: List[str]) -> str:
    return hashlib.sha256("|".join(universe).encode()).hexdigest()[:16]


def mark_history_span(symbol: str = "BTCUSDT") -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Bornes de l'archive de marks, lues sur les partitions du collecteur."""
    base = (ROOT / "data" / "derivatives_raw" / "exchange=binance" / "market=usdm"
            / "stream=open_interest" / f"symbol={symbol}")
    days = sorted(d.name.split("=", 1)[1] for d in base.glob("date=*") if d.is_dir())
    if not days:
        return None, None
    return (pd.Timestamp(days[0], tz="UTC"), pd.Timestamp(days[-1], tz="UTC"))


def build_grid(start: pd.Timestamp, stop: pd.Timestamp) -> List[pd.Timestamp]:
    """Barres dont la fenêtre [t, t+H] tient entièrement dans l'archive."""
    step = pd.Timedelta(hours=BAR_STEP_HOURS)
    horizon = pd.Timedelta(hours=HORIZON_HOURS)
    bars, t = [], start.ceil("h")
    while t + horizon <= stop:
        bars.append(t)
        t = t + step
    return bars


def realized_excess_at_bar(bar: pd.Timestamp, universe: List[str],
                           cache: MarkSeriesCache) -> Tuple[Dict[str, float], Optional[float], int]:
    """Excess réalisé de chaque symbole sur [bar, bar+H], et la référence.

    Exactement la grandeur que le labelliseur produira plus tard dans
    `dec_excess_bps` : mêmes prix (`MarkSeriesCache`), même référence
    (`universe_return_bps`), même signe. C'est ce qui fait de cet objet un
    contrôle de la chaîne plutôt qu'un contrôle d'une copie de la chaîne.
    """
    exit_at = bar + pd.Timedelta(hours=HORIZON_HOURS)
    bench_bps, bench_n = universe_return_bps(bar, exit_at, cache)
    if bench_bps is None:
        return {}, None, bench_n

    excess: Dict[str, float] = {}
    for symbol in universe:
        entry, _, _ = cache.at(symbol, bar)
        exit_px, _, _ = cache.at(symbol, exit_at)
        if not entry or not exit_px or entry <= 0:
            continue
        gross = (exit_px / entry - 1.0) * 10_000.0
        # LONG : excess = brut - référence. Même formule que outcomes._anchor_leg.
        excess[symbol] = gross - bench_bps
    return excess, bench_bps, bench_n


def select_for_bar(bar: pd.Timestamp, excess: Dict[str, float], level_bps: float,
                   n: int) -> List[dict]:
    """Le placebo, plus un déplacement de `level_bps`.

    Tirage uniforme déterministe (même famille de graine que le placebo, mais
    dérivée d'un autre alpha_id, donc un autre flux), puis on retient le
    symbole dont l'excess réalisé est le plus proche de (excess du tirage + Δ).
    Sans remise dans la barre : deux fois le même symbole sur la même fenêtre
    serait la même preuve comptée deux fois.
    """
    symbols = sorted(excess)
    if len(symbols) < 2:
        return []
    values = np.array([excess[s] for s in symbols], dtype=float)

    seed = int(hashlib.sha256(f"{ALPHA_ID}|{bar.isoformat()}|{level_bps}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    draws = rng.choice(len(symbols), size=min(n, len(symbols)), replace=False)

    picked: List[dict] = []
    taken: set = set()
    for draw_idx in draws:
        target = values[draw_idx] + float(level_bps)
        order = np.argsort(np.abs(values - target), kind="stable")
        choice = next((int(i) for i in order if int(i) not in taken), None)
        if choice is None:
            break
        taken.add(choice)
        picked.append({
            "event_time": bar,
            "symbol": symbols[choice],
            "direction": DIRECTION,
            "injection_target_bps": float(level_bps),
            "draw_symbol": symbols[int(draw_idx)],
            "oracle_excess_of_draw_bps": float(values[int(draw_idx)]),
            "oracle_excess_at_selection_bps": float(values[choice]),
            # L'injection RÉELLE, jamais supposée égale à Δ : quand la cible
            # dépasse le maximum de la coupe, le plus proche est le maximum.
            "injected_measured_bps": float(values[choice] - values[int(draw_idx)]),
            "cross_section_n": len(symbols),
            "draw_seed": seed,
        })
    return picked


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (défaut : début de l'archive + 1 j)")
    parser.add_argument("--stop", default=None, help="YYYY-MM-DD (défaut : fin de l'archive)")
    parser.add_argument("--decisions-per-bar", type=int, default=DECISIONS_PER_BAR)
    args = parser.parse_args(argv)

    universe = load_universe()
    uhash = universe_hash(universe)
    cache = MarkSeriesCache()

    first, last = mark_history_span()
    if first is None:
        print("aucune archive de marks : rien à contrôler", flush=True)
        return 2
    start = pd.Timestamp(args.start, tz="UTC") if args.start else first + pd.Timedelta(days=1)
    stop = pd.Timestamp(args.stop, tz="UTC") if args.stop else last
    bars = build_grid(start, stop)
    if not bars:
        print(f"grille vide entre {start} et {stop}", flush=True)
        return 2

    print(f"[{ALPHA_ID}] {len(bars)} barres de {start.date()} à {stop.date()}, "
          f"{len(INJECTION_LEVELS_BPS)} niveaux d'injection", flush=True)

    rows: List[dict] = []
    skipped_bench = 0
    for index, bar in enumerate(bars):
        # Niveaux sur barres DISJOINTES -- voir l'en-tête.
        level = INJECTION_LEVELS_BPS[index % len(INJECTION_LEVELS_BPS)]
        excess, bench, bench_n = realized_excess_at_bar(bar, universe, cache)
        if bench is None or len(excess) < MIN_BENCHMARK_SYMBOLS:
            skipped_bench += 1
            continue
        for rec in select_for_bar(bar, excess, level, int(args.decisions_per_bar)):
            rec["oracle_bench_bps"] = float(bench)
            rec["oracle_bench_n_symbols"] = int(bench_n)
            rows.append(rec)
        if (index + 1) % 100 == 0:
            print(f"  ... {index + 1}/{len(bars)} barres, {len(rows)} décisions", flush=True)

    if not rows:
        print("aucune décision produite (archive trop clairsemée)", flush=True)
        return 2

    now = pd.Timestamp(datetime.now(timezone.utc))
    dec = pd.DataFrame(rows)
    # Latence NULLE par construction : le contrôle isole la chaîne de mesure.
    # Le coût de la latence est mesuré ailleurs (decision_lag_h, audit
    # 2026-09-05) et les confondre rendrait les deux illisibles.
    dec["decided_at"] = dec["event_time"]
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["tier"] = "shadow"
    dec["provenance"] = "FORWARD_LIVE"
    # Le drapeau qui doit rendre toute lecture erronée impossible, ligne à ligne.
    dec["uses_lookahead"] = True
    dec["built_at"] = now.isoformat()
    for key, value in spec_provenance(ALPHA_ID).items():
        dec[key] = value
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", "symbol")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dec.to_parquet(LEDGER, index=False)

    by_level = dec.groupby("injection_target_bps")["injected_measured_bps"].agg(["count", "mean"])
    (OUT_DIR / "run_state.json").write_text(json.dumps({
        "alpha_id": ALPHA_ID, "built_at": now.isoformat(),
        "mode": "POSITIVE_CONTROL_NO_CAPITAL", "uses_lookahead": True,
        "grid_start": str(start), "grid_stop": str(stop),
        "n_bars": len(bars), "n_bars_skipped_benchmark": skipped_bench,
        "n_decisions_total": len(dec),
        "universe_hash": uhash, "universe_size": len(universe),
        "injection_levels_bps": list(INJECTION_LEVELS_BPS),
        "injected_measured_by_level": {
            str(k): {"n": int(v["count"]), "mean_bps": round(float(v["mean"]), 3)}
            for k, v in by_level.iterrows()},
    }, indent=2), encoding="utf-8")

    print(f"[{ALPHA_ID}] {len(dec)} décisions écrites -> {LEDGER}", flush=True)
    print(by_level.to_string(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
