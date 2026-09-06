#!/usr/bin/env python3
"""
scripts/probe_spread_cross_section.py
─────────────────────────────────────────────────────────────────────────────
Sonde de SPREAD sur le frozen-50 : un appel REST par cycle, une ligne par
symbole, append-only.

Pourquoi elle existe
────────────────────
Le simulateur applique `FIXED_SLIPPAGE_BPS = 2.0` par jambe à TOUS les
symboles, dans TOUS les régimes. `execution_adapter.py` le dit lui-même :
« pas de bid/ask réel dans derivatives_raw (seulement mark_price) ». Le coût
d'exécution est donc la seule pièce du PnL qui ne soit adossée à aucune
observation -- sur des alts, et sur des mécanismes qui tradent pendant les
cascades.

Mesuré le 2026-09-06 (coupe transversale unique, marché calme) : le spread
aller-retour médian des alts du frozen-50 vaut 1,71 bps, soit 0,86 bps par
jambe -- l'hypothèse de 2 bps est donc CONSERVATRICE pour l'alt médian. Mais
la queue de la distribution la dépasse déjà en marché calme : ARUSDT 6,61 bps,
IMXUSDT 7,43 bps aller-retour, soit 3,3 et 3,7 bps par jambe. Or ARUSDT est
le symbole le PLUS tradé du lab (30 décisions labellisées sur 548). Une
constante unique est donc à la fois trop pessimiste au centre et trop
optimiste là où le lab engage le plus de capital.

Une coupe instantanée ne suffit pas à corriger ça : il faut une DISTRIBUTION,
par symbole, couvrant plusieurs régimes. C'est ce que cette sonde accumule, à
partir de maintenant.

Ce qu'elle ne peut pas faire, et qu'il faut savoir en la lisant
──────────────────────────────────────────────────────────────
Cadencée sur le cycle (15 min), elle sous-échantillonne par construction les
quelques secondes d'une cascade. Elle mesure le NIVEAU par symbole et sa
dérive lente entre régimes, pas le pic instantané d'un événement. Pour le pic,
seule la vraie bande BBO répond -- et elle n'existe que pour BTC/ETH/SOL
(data/microstructure_reduced), c'est-à-dire 37 des 548 décisions labellisées.

Coût : 1 requête HTTP et ~50 lignes par cycle (~5 000 lignes/jour, quelques
centaines de Ko par mois). Aucun ordre, aucune écriture hors de son propre
répertoire.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
OUT_ROOT = ROOT / "data" / "spread_probe"
ENDPOINT = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"

# Un seul appel pour TOUT le carnet de l'exchange, filtré ensuite localement :
# 50 appels séparés donneraient 50 instants différents, donc une coupe
# transversale qui n'en est pas une (et 50× le quota de rate limit).
TIMEOUT_SEC = 15


def load_universe() -> set:
    """Univers FIGÉ, jamais un glob() — même règle que
    scripts/collect_oi_metrics_5m.py::load_universe (bug d'universe-drift
    corrigé le 2026-08-30, cf. tests/test_universe_drift_guard.py)."""
    return set(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def probe(universe: set) -> pd.DataFrame:
    req = urllib.request.Request(ENDPOINT, headers={"User-Agent": "futur-spread-probe"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode())
    now = datetime.now(timezone.utc)
    rows = []
    for d in payload:
        symbol = d.get("symbol")
        if symbol not in universe:
            continue
        try:
            bid, ask = float(d["bidPrice"]), float(d["askPrice"])
            bid_qty, ask_qty = float(d.get("bidQty", 0)), float(d.get("askQty", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if bid <= 0 or ask <= 0 or ask < bid:
            # carnet croisé ou vide : on NE l'écrit pas comme un spread de 0,
            # ce serait le point le plus optimiste de toute la distribution.
            continue
        mid = (bid + ask) / 2.0
        rows.append({
            "probe_at": now, "symbol": symbol,
            "bid_price": bid, "ask_price": ask,
            "bid_qty": bid_qty, "ask_qty": ask_qty,
            "mid_price": mid,
            "spread_bps": (ask - bid) / mid * 1e4,
            # profondeur au meilleur limite, en dollars : dit si le spread
            # observé est atteignable pour la taille du lab ou seulement pour
            # une poussière.
            "top_bid_notional_usd": bid * bid_qty,
            "top_ask_notional_usd": ask * ask_qty,
            "venue": "binance_usdm", "source_stream": "rest_bookTicker",
        })
    return pd.DataFrame(rows)


def main() -> int:
    universe = load_universe()
    try:
        df = probe(universe)
    except Exception as exc:
        # Une sonde ratée n'est pas un incident : le cycle suivant réessaie
        # dans 15 min. On ne fabrique surtout pas une ligne « spread inconnu ».
        print(f"[spread] ✗ sonde échouée ({type(exc).__name__}: {exc}) — "
              f"aucune ligne écrite, retry au prochain cycle", flush=True)
        return 0
    if df.empty:
        print("[spread] ✗ 0 symbole coté — aucune ligne écrite", flush=True)
        return 0

    day = df["probe_at"].iloc[0].strftime("%Y-%m-%d")
    out_dir = OUT_ROOT / f"date={day}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Un fichier par sonde (jamais de réécriture d'un fichier existant) : même
    # contrat append-only que le reste du lab.
    out = out_dir / f"part-{df['probe_at'].iloc[0]:%H%M%S}-{uuid.uuid4().hex[:8]}.parquet"
    df.to_parquet(out, index=False)

    med = df["spread_bps"].median()
    p90 = df["spread_bps"].quantile(0.90)
    print(f"[spread] ✓ {len(df)}/{len(universe)} symboles — "
          f"spread A/R médian {med:.3f} bps, p90 {p90:.3f} bps -> {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
