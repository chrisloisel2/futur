"""
src/institutional/engines/liq_cascade/btc_lead_variant.py
─────────────────────────────────────────────────────────────────────────────
BTC_LEAD_ALT_CASCADE_V1 — fade des cascades de liquidation ALT conditionné à
un CHOC BTC contemporain.

Origine : candidat BTC_LEAD_ALT_CASCADE, découvert au round 3
(reports/edge_discovery/alpha_hunt_2026-09-01_round3/w1_event_sequences/REPORT.md,
w1_a12) puis VALIDÉ INDÉPENDAMMENT
(reports/edge_discovery/validation_2026-09/BTC_LEAD_ALT_CASCADE/REPORT.md,
verdict VALIDATED_FOR_FORWARD, recommended_next_step FREEZE_AND_LAUNCH_SHADOW).

Mécanisme économique
────────────────────
Un choc BTC précède/co-occurre avec une cascade alt : la cascade alt
« expliquée » par BTC est un overshoot de contagion (déleveraging forcé corrélé)
qui se retourne quand la pression passe, pas une rupture idiosyncratique.
Mesuré, correctement déclusterisé au niveau ÉPISODE cross-symbole (gap < 4h) :
  bras shock      +46.87bps net14 (t_L3 3.32, N_L3 259, N_raw 2 485)
  bras no_shock   +17.52bps net14
  shock − no_shock +29.35bps (Welch 2.04, P(diff<=0)=0.019)
Contrôle économique décisif (split signé non préenregistré) : choc BTC BAISSIER
+46.4 (t 3.23) vs choc HAUSSIER −8.9 (t −1.58) — le sens économique se vérifie
sur un split non utilisé pour construire la règle. La spec figée trade le choc
en valeur ABSOLUE (c'est ce qui a été préenregistré et validé) ; le split signé
est porté dans la colonne `btc_shock_sign` de chaque décision pour être
auditable en forward, jamais utilisé comme filtre.

Ce module ne modifie ni detector.py ni dataset.py : il consomme la sortie de
build_event_dataset() (qui porte déjà `btc_ret_30m`, feature causale : variation
BTC sur les 30 min PRÉCÉDANT l'événement, as-of backward) et n'ajoute que la
règle de choc + un filtre.

Spec FIGÉE (constantes du validateur, reprises à l'identique)
─────────────────────────────────────────────────────────────
  Population A : kind == LONG_CASCADE, symbol != BTCUSDT (BTC ne peut pas se
                 « précéder » lui-même), event_time >= 2022-01-01 UTC,
                 btc_ret_30m non nul.
  shock(t)     := |btc_ret_30m| >= q90(t), où q90(t) est le 90e centile de
                 |btc_ret_30m| sur les événements de la population A dans la
                 fenêtre STRICTEMENT antérieure [t − 365j, t), avec au moins
                 200 événements antérieurs exigés (sinon l'événement est
                 ÉCARTÉ, jamais imputé).
  Trade        : LONG le bras shock, horizon fwd_4h, une jambe par événement.

⚠ Le centile est un SEUIL GLISSANT CAUSAL par définition (c'est la spec
préenregistrée, pas une constante ajustée) : le recalculer à chaque événement à
partir du seul passé N'EST PAS une dérive de spec — c'est la spec. La version
in-sample de la découverte a été reléguée en perturbation (BLA-P1) par le
validateur et n'est PAS ce qui tourne ici.

⚠ Les filtres `label_full == True` et `fwd_4h non nul` du rapport de
validation sont des filtres de LABEL (restreindre l'échantillon aux événements
dont l'issue est mesurable), PAS des critères d'entrée. Les appliquer au moment
de la décision serait un look-ahead : en live on décide à event_time sans
connaître fwd_4h. Ils ne sont donc PAS appliqués ici. Ils sont appliqués
uniquement dans le contrôle de fidélité (tests/) qui rejoue la population du
validateur sur son parquet historique.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SHOCK_QUANTILE = 0.90
LOOKBACK_DAYS = 365
MIN_PRIOR_EVENTS = 200
POPULATION_START = pd.Timestamp("2022-01-01", tz="UTC")
POPULATION_KIND = "LONG_CASCADE"
EXCLUDED_SYMBOL = "BTCUSDT"


def population_a(events: pd.DataFrame) -> pd.DataFrame:
    """Population A du validateur, SANS les filtres de label (voir docstring)."""
    if events.empty:
        return events.copy()
    df = events.copy()
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    mask = (
        (df["kind"] == POPULATION_KIND)
        & (df["symbol"] != EXCLUDED_SYMBOL)
        & (df["event_time"] >= POPULATION_START)
        & df["btc_ret_30m"].notna()
    )
    return df[mask].sort_values("event_time", kind="mergesort").copy()


def rolling_causal_q90(pop: pd.DataFrame) -> pd.Series:
    """q90(t) sur |btc_ret_30m| dans [t − 365j, t), NaN si < 200 événements
    antérieurs. `pop` doit être trié par event_time.

    Reprend À L'IDENTIQUE la convention du validateur
    (validation_2026-09/_lib/exp_v2_cascade.py::causal_shock_flag) :
    `prior = v[lo:i]` sur le tableau trié, avec `lo` = premier index tel que
    t[lo] >= t[i] − 365j. L'événement courant (index i) n'est jamais dans son
    propre seuil. Les ex-aequo à `t` qui précèdent i dans l'ordre trié SONT
    comptés comme antérieurs — sans conséquence sur le résultat car
    `btc_ret_30m` est une feature BTC indexée sur le temps : tous les
    événements d'un même instant portent la même valeur, la fenêtre est donc
    invariante à l'ordre des ex-aequo (vérifié : 4 ordres de tri distincts
    donnent exactement les 2 485 / 24 065 / 200 du rapport de validation).
    Une borne droite `searchsorted(side='left')` excluant tous les ex-aequo
    donnerait 2 467 — ce n'est PAS la spec validée.
    """
    if pop.empty:
        return pd.Series(dtype="float64", index=pop.index)
    ts = pop["event_time"].values.astype("datetime64[ns]")
    absret = pop["btc_ret_30m"].abs().to_numpy(dtype="float64")
    lookback = np.timedelta64(LOOKBACK_DAYS, "D")

    out = np.full(len(pop), np.nan)
    lo = 0
    for i in range(len(pop)):
        while ts[lo] < ts[i] - lookback:
            lo += 1
        if i - lo < MIN_PRIOR_EVENTS:
            continue
        out[i] = np.quantile(absret[lo:i], SHOCK_QUANTILE)
    return pd.Series(out, index=pop.index, dtype="float64")


def classify_shock(pop: pd.DataFrame) -> pd.DataFrame:
    """Ajoute `btc_q90_365d`, `btc_shock` (bool ou NaN si historique
    insuffisant), `btc_shock_sign` (DOWN/UP, audit uniquement)."""
    df = pop.copy()
    if df.empty:
        df["btc_q90_365d"] = pd.Series(dtype="float64")
        df["btc_shock"] = pd.Series(dtype="object")
        df["btc_shock_sign"] = pd.Series(dtype="object")
        return df
    df["btc_q90_365d"] = rolling_causal_q90(df)
    absret = df["btc_ret_30m"].abs()
    eligible = df["btc_q90_365d"].notna()
    shock = pd.Series(pd.NA, index=df.index, dtype="object")
    shock[eligible] = (absret[eligible] >= df.loc[eligible, "btc_q90_365d"])
    df["btc_shock"] = shock
    df["btc_shock_sign"] = np.where(df["btc_ret_30m"] < 0, "DOWN", "UP")
    return df


def select_tradeable_btc_lead(events: pd.DataFrame) -> pd.DataFrame:
    """Filtre les events tradeables par BTC_LEAD_ALT_CASCADE_V1 : population A,
    historique suffisant, bras shock. Ajoute `direction` fixe LONG."""
    pop = population_a(events)
    if pop.empty:
        out = pop.copy()
        for c in ("btc_q90_365d", "btc_shock", "btc_shock_sign", "direction"):
            out[c] = pd.Series(dtype="object")
        return out
    df = classify_shock(pop)
    tradeable = df[df["btc_shock"].eq(True)].copy()
    tradeable["direction"] = "LONG"
    return tradeable
