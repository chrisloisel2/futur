"""
src/institutional/engines/liq_cascade/density_variant.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE_REPEAT_SYSTEMIC_V1 — conditionnement du repeat-cascade par la
DENSITÉ DE CASCADES À L'ÉCHELLE DU MARCHÉ.

Origine : candidat LIQ_REPEAT_DENSITY, découvert au round 3
(reports/edge_discovery/alpha_hunt_2026-09-01_round3/w4_regime_conditional/REPORT.md,
test A6) puis VALIDÉ INDÉPENDAMMENT
(reports/edge_discovery/validation_2026-09/LIQ_REPEAT_DENSITY/REPORT.md,
verdict VALIDATED_FOR_FORWARD).

Mécanisme économique
────────────────────
Une cascade répétée n'a pas la même signification selon qu'elle est isolée ou
qu'elle accompagne un flush de tout le marché. Un flush market-wide épuise les
vendeurs forcés plus vite qu'une répétition idiosyncratique sur un seul nom.
Mesuré, correctement déclusterisé au niveau ÉPISODE cross-symbole :
  systémique  +22,12bps (t=3,48, N=1 165 épisodes indépendants)
  isolé       −13,35bps (t=−1,80, N=1 322)
Le parent LIQ_CASCADE_REPEAT_V1 trade les DEUX buckets — il mélange donc un
bucket payant et un bucket perdant. Cette variante ne trade que le systémique.

Ce module ne modifie ni detector.py ni dataset.py ni repeat_variant.py : il
consomme la sortie de build_event_dataset() et celle de select_tradeable(),
et n'ajoute qu'un classificateur de densité + un filtre.

Spec FIGÉE (constantes du validateur, jamais recalculées au runtime)
───────────────────────────────────────────────────────────────────
  DENSITY_60M(t, sym) = nombre de symboles DISTINCTS AUTRES que `sym` ayant
  au moins un event LONG_CASCADE dont l'event_time tombe dans la fenêtre
  STRICTEMENT antérieure (t − 60min, t).

Trois choix de construction, tous fixés par le validateur AVANT tout calcul de
rendement, et repris ici à l'identique :
  - fenêtre 60 minutes (le rapport de découverte utilisait 30 min ; le
    validateur a délibérément choisi un autre ancrage et le mécanisme survit) ;
  - comptage de symboles DISTINCTS, pas d'events bruts — sinon le train
    d'events d'un seul symbole gonflerait artificiellement la « densité » ;
  - LONG_CASCADE uniquement (pas SHORT_SQUEEZE) — l'hypothèse économique porte
    sur un flush baissier market-wide ; mélanger les squeezes (régime de stress
    opposé) brouillerait le mécanisme. C'est aussi cohérent avec le blocage
    explicite de SHORT_SQUEEZE dans repeat_variant.py (convention de signe
    non résolue).

⚠ SEUIL EN DUR, DÉLIBÉRÉMENT. `DENSITY_SYSTEMIC_MIN = 1` est la MÉDIANE
mesurée par le validateur sur la population du signal de base éligible
(>= 2022-01-01). Elle n'est PAS recalculée à chaque run : une médiane
recalculée dériverait avec les données entrantes, ce qui ferait bouger la spec
en silence sous le freeze_timestamp — exactement ce que la discipline du
registre interdit. Un changement de ce seuil = nouvel alpha_id, nouveau freeze,
track record remis à zéro.
"""
from __future__ import annotations

import bisect

import pandas as pd

# Médiane de DENSITY_60M sur la population éligible (>=2022-01-01) mesurée par
# le validateur : 33,8% des lignes du signal de base ont une densité de 0 et
# 19,3% exactement 1. Systémique = `>= 1`, isolé = `< 1` (donc densité nulle).
DENSITY_SYSTEMIC_MIN = 1
DENSITY_WINDOW_MINUTES = 60
DENSITY_KIND = "LONG_CASCADE"


def compute_density_60m(all_events: pd.DataFrame, targets: pd.DataFrame) -> pd.Series:
    """DENSITY_60M pour chaque ligne de `targets`, calculée sur `all_events`.

    `all_events` : sortie complète de build_event_dataset() (tous symboles,
    tous kinds) — c'est la population de référence du marché.
    `targets`    : les lignes à classer (typiquement la sortie de
                   repeat_variant.select_tradeable()).

    CAUSALITÉ : la fenêtre est ouverte des deux côtés côté droit —
    `t − 60min < event_time < t` — donc elle n'inclut JAMAIS l'événement
    lui-même ni aucun événement postérieur. Implémenté par bisect sur des
    timestamps triés (bisect_left sur la borne droite = exclut les ex-aequo à
    `t`), pas par un merge de fenêtre qui pourrait inclure l'instant courant.

    Retourne une Series alignée sur l'index de `targets`.
    """
    if targets.empty:
        return pd.Series(dtype="int64", index=targets.index)

    src = all_events[all_events["kind"] == DENSITY_KIND]
    if src.empty:
        return pd.Series(0, index=targets.index, dtype="int64")

    # Un index de timestamps triés par symbole : pour un symbole donné, on veut
    # savoir s'il a AU MOINS un event dans la fenêtre — d'où le comptage de
    # symboles distincts et non d'events.
    by_symbol = {
        sym: sorted(pd.to_datetime(grp["event_time"], utc=True).tolist())
        for sym, grp in src.groupby("symbol")
    }

    window = pd.Timedelta(minutes=DENSITY_WINDOW_MINUTES)
    out = []
    for t, own_symbol in zip(pd.to_datetime(targets["event_time"], utc=True),
                             targets["symbol"]):
        lo = t - window
        n = 0
        for sym, stamps in by_symbol.items():
            if sym == own_symbol:
                continue    # « symboles AUTRES » — le symbole cible s'exclut
            # bisect_right(lo) : premier index strictement après lo
            # bisect_left(t)   : premier index >= t, donc exclut t lui-même
            if bisect.bisect_left(stamps, t) > bisect.bisect_right(stamps, lo):
                n += 1
        out.append(n)
    return pd.Series(out, index=targets.index, dtype="int64")


def classify_density_regime(density: int) -> str:
    return "systemic" if density >= DENSITY_SYSTEMIC_MIN else "isolated"


def select_tradeable_systemic(all_events: pd.DataFrame, base_tradeable: pd.DataFrame) -> pd.DataFrame:
    """Restreint le signal de base (repeat-cascade exhaustion) au seul régime
    systémique. Ajoute `density_60m` et `density_regime` pour que chaque
    décision porte la valeur qui l'a justifiée (auditable a posteriori)."""
    if base_tradeable.empty:
        out = base_tradeable.copy()
        out["density_60m"] = pd.Series(dtype="int64")
        out["density_regime"] = pd.Series(dtype="object")
        return out
    df = base_tradeable.copy()
    df["density_60m"] = compute_density_60m(all_events, df)
    df["density_regime"] = df["density_60m"].apply(classify_density_regime)
    return df[df["density_regime"] == "systemic"].copy()
