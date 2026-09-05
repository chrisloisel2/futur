#!/usr/bin/env python3
"""
scripts/collect_oi_metrics_5m.py
─────────────────────────────────────────────────────────────────────────────
Collecteur 5 m des métriques dérivées Binance USDM — la QUEUE FRAÎCHE de la
série que le détecteur de cascades consomme.

Pourquoi ce fichier existe
──────────────────────────
`src/institutional/engines/liq_cascade/detector.py::load_metrics` lit
`data/derivatives_backfill/binance_vision_metrics/`, un backfill d'archives
QUOTIDIENNES Binance Vision, structurellement en retard de 1 à 2 jours.
Conséquence mesurée le 2026-09-05 : la famille cascade de liquidation
(4 alphas figés) découvrait ses événements 45 à 48 h après coup, pour un
horizon de détention de 4 h — 100 % de ses décisions forward arrivaient
périmées, donc aucun capital ne pouvait y être engagé. Voir
`reports/live_alpha_lab/DECISION_LATENCY_AUDIT_2026-09-05.md`.

Ce collecteur ne REMPLACE pas Vision : les endpoints `futures/data` ne
retiennent qu'environ 30 jours. Il en complète la QUEUE. Le raccordement se
fait dans `load_metrics`, qui concatène Vision (historique) et ces fichiers
(les derniers jours), dédupliqué sur `create_time` avec priorité à Vision.

Fidélité — vérifiée, pas supposée
─────────────────────────────────
Sur 8 symboles de l'univers figé × 133 barres 5 m de recouvrement :
`sumOpenInterest` est IDENTIQUE à 100 %, et le prix implicite
`sumOpenInterestValue / sumOpenInterest` — le seul champ que le déclencheur
figé utilise via `px_ret_30m` — a un écart médian ET maximum de 0,000000 bps.
Les écarts résiduels sur `sumOpenInterestValue` (1,2e-16 à 2,2e-16 en relatif)
sont l'epsilon du float64, c'est-à-dire l'arrondi de parsing de la chaîne.
Le déclencheur (`detect_cascades`) n'utilise QUE l'OI et ce prix implicite :
la spec figée n'est donc pas touchée.

⚠ CHAQUE ENDPOINT A SA PROPRE CONVENTION D'HORODATAGE. Mesuré, pas déduit :
`openInterestHist` et les trois ratios de positionnement sont décalés de
+5 min par rapport à `create_time` de Vision (donc −5 min à appliquer), tandis
que `takerlongshortRatio` est déjà aligné. Appliquer un décalage uniforme
produirait une série silencieusement fausse : sur l'OI, l'erreur médiane passe
de 0,000000 à 76,2 unités, et sur le ratio taker de 0,000123 à 0,45.
C'est la raison d'être de la colonne `offset_min` ci-dessous.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
OUT_DIR = ROOT / "data" / "derivatives_live_metrics"
STATE_PATH = OUT_DIR / "_collector_state.json"

BASE = "https://fapi.binance.com/futures/data"

# colonne Vision -> (endpoint, champ JSON, décalage en minutes à appliquer au
# timestamp de l'API pour retomber sur `create_time` de Vision).
# Les décalages sont MESURÉS (balayage −15..+15 min contre l'archive Vision),
# jamais supposés — voir le docstring.
FIELDS = {
    "sum_open_interest":                ("openInterestHist", "sumOpenInterest", -5),
    "sum_open_interest_value":          ("openInterestHist", "sumOpenInterestValue", -5),
    "sum_taker_long_short_vol_ratio":   ("takerlongshortRatio", "buySellRatio", 0),
    "sum_toptrader_long_short_ratio":   ("topLongShortPositionRatio", "longShortRatio", -5),
    "count_long_short_ratio":           ("globalLongShortAccountRatio", "longShortRatio", -5),
    "count_toptrader_long_short_ratio": ("topLongShortAccountRatio", "longShortRatio", -5),
}
# Ordre des colonnes du parquet Vision, reproduit à l'identique pour que le
# concat dans load_metrics n'ait aucune colonne à réconcilier.
VISION_COLUMNS = [
    "create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
    "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
]

LIMIT = 500              # maximum de l'API = 41,7 h par appel à la maille 5 m
SLEEP_BETWEEN_CALLS = 0.15
MIN_FREE_DISK_GB = 15.0  # même plancher que le cycle du lab


def free_gb() -> float:
    return shutil.disk_usage(str(ROOT)).free / (1024 ** 3)


def load_universe() -> list:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/ (bug d'universe-drift
    corrigé le 2026-08-30, cf tests/test_universe_drift_guard.py)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def fetch(endpoint: str, symbol: str, limit: int = LIMIT) -> list:
    url = f"{BASE}/{endpoint}?symbol={symbol}&period=5m&limit={limit}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def fetch_symbol(symbol: str) -> pd.DataFrame:
    """Une trame 5 m au schéma Vision pour un symbole, ou vide si indisponible.

    Chaque endpoint est aligné avec SON PROPRE décalage puis joint sur
    `create_time` en OUTER : une barre présente chez l'un et absente chez
    l'autre est conservée avec un NaN, jamais silencieusement supprimée -- le
    déclencheur ne dépend que de l'OI, et supprimer la barre le priverait
    d'un événement pour une simple lacune de feature descriptive.
    """
    by_endpoint = {}
    for endpoint in {ep for ep, _, _ in FIELDS.values()}:
        try:
            by_endpoint[endpoint] = fetch(endpoint, symbol)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            # Un symbole renommé côté Binance (MKR/PEPE/RNDR sont connus) renvoie
            # une erreur : on la remonte telle quelle plutôt que de deviner un
            # mapping de substitution.
            print(f"[oi5m] {symbol} {endpoint}: {type(exc).__name__} {exc}", flush=True)
            by_endpoint[endpoint] = None
        time.sleep(SLEEP_BETWEEN_CALLS)

    if by_endpoint.get("openInterestHist") is None:
        return pd.DataFrame()   # sans OI il n'y a pas de barre exploitable

    out = None
    for col, (endpoint, field, offset) in FIELDS.items():
        payload = by_endpoint.get(endpoint)
        if not payload:
            continue
        df = pd.DataFrame(payload)
        if field not in df.columns or "timestamp" not in df.columns:
            continue
        frame = pd.DataFrame({
            "create_time": (pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                            + pd.Timedelta(minutes=offset)),
            col: pd.to_numeric(df[field], errors="coerce"),
        })
        out = frame if out is None else out.merge(frame, on="create_time", how="outer")

    if out is None or out.empty:
        return pd.DataFrame()
    out["symbol"] = symbol
    for col in VISION_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[VISION_COLUMNS].sort_values("create_time").reset_index(drop=True)


def merge_into_ledger(symbol: str, fresh: pd.DataFrame) -> tuple:
    """Append-only avec déduplication sur `create_time`.

    Les lignes DÉJÀ écrites font foi : l'API peut republier une barre récente
    avec une valeur légèrement révisée, et réécrire le passé casserait la
    reproductibilité d'une décision déjà prise sur l'ancienne valeur.
    """
    path = OUT_DIR / f"{symbol}_metrics_5m_live.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        known = set(old["create_time"])
        new = fresh[~fresh["create_time"].isin(known)]
        if new.empty:
            return 0, len(old)
        merged = pd.concat([old, new], ignore_index=True).sort_values("create_time")
        merged.to_parquet(path, index=False)
        return len(new), len(merged)
    fresh.to_parquet(path, index=False)
    return len(fresh), len(fresh)


def main() -> int:
    ap = argparse.ArgumentParser(description="Collecte 5 m des métriques dérivées (queue fraîche).")
    ap.add_argument("--symbols", default=None, help="liste séparée par des virgules (défaut : univers figé)")
    args = ap.parse_args()

    free = free_gb()
    if free < MIN_FREE_DISK_GB:
        print(f"[oi5m] ✗ {free:.1f}GB libres < plancher {MIN_FREE_DISK_GB}GB — collecte annulée", flush=True)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = args.symbols.split(",") if args.symbols else load_universe()

    started = datetime.now(timezone.utc)
    n_new_total, unavailable, latest = 0, [], None
    for symbol in symbols:
        fresh = fetch_symbol(symbol)
        if fresh.empty:
            unavailable.append(symbol)
            continue
        n_new, n_total = merge_into_ledger(symbol, fresh)
        n_new_total += n_new
        latest = max(latest, fresh["create_time"].max()) if latest is not None else fresh["create_time"].max()

    lag_min = ((datetime.now(timezone.utc) - latest).total_seconds() / 60) if latest is not None else None
    state = {
        "last_run": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "symbols_requested": len(symbols),
        "symbols_unavailable": unavailable,
        "rows_new": n_new_total,
        "latest_bar": latest.isoformat() if latest is not None else None,
        "latest_bar_lag_min": round(lag_min, 1) if lag_min is not None else None,
        "free_gb": round(free, 2),
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))
    print(f"[oi5m] {len(symbols) - len(unavailable)}/{len(symbols)} symboles, "
          f"{n_new_total} barres neuves, dernière barre {latest} "
          f"(retard {state['latest_bar_lag_min']} min)", flush=True)
    if unavailable:
        print(f"[oi5m] indisponibles : {', '.join(unavailable)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
