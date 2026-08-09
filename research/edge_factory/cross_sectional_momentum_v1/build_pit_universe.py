#!/usr/bin/env python3
"""
research/edge_factory/cross_sectional_momentum_v1/build_pit_universe.py
─────────────────────────────────────────────────────────────────────────────
Reconstruit un univers POINT-IN-TIME crypto-only pour MOMENTUM_CRYPTO_V1,
suite à l'audit du 2026-07-21 (QUARANTINE_2026-07-21.md) : l'univers
CRYPTO_32 précédent était un snapshot du 2026-06-30 appliqué à tout
l'historique -- biais de survivance.

Réutilise SANS DUPLIQUER `scripts/backtest_ctrend_v1.py::load_panel()` et
`build_membership()` (même discipline PIT déjà validée pour CTREND v1,
commit 859ebad : volume médian 30j décalé t-1, historique >=31j, jamais
l'univers d'aujourd'hui appliqué au passé) — seule nouveauté : exclusion
des perps tokenisés actions/ETF/commodités AVANT le classement, pas après,
pour que le cutoff top-N ne soit jamais influencé par des noms hors
périmètre.

Exclusion : fetch /fapi/v1/exchangeInfo pour TOUS les symboles actuellement
listés (pas seulement le top-50 précédent) et exclut tout underlyingType
!= COIN. Les symboles délistés n'apparaissent plus dans exchangeInfo, mais
le produit "tokenisé actions/ETF/commodités" est récent (onboardDate
2025-2026 pour la plupart des noms déjà identifiés) : le risque qu'un
symbole délisté avant l'existence de ce produit soit mal classé est nul.

    .venv/bin/python research/edge_factory/cross_sectional_momentum_v1/build_pit_universe.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_ctrend_v1 as ctrend  # noqa: E402  -- réutilise load_panel/build_membership/Config

UNIVERSE_SIZE = 30
MIN_MEDIAN_QV = 5e6
OUT_DIR = ROOT / "research/edge_factory/cross_sectional_momentum_v1/results"


def fetch_non_coin_symbols() -> list:
    """Tous les symboles USDT-M dont underlyingType != COIN, via l'API
    publique Binance (aucune auth). Retourne une liste triée pour un
    manifeste reproductible."""
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    non_coin = []
    for s in data.get("symbols", []):
        symbol = s.get("symbol", "")
        if symbol.endswith("USDT") and s.get("underlyingType") != "COIN":
            non_coin.append(symbol)
    return sorted(set(non_coin))


def main() -> None:
    close, qv = ctrend.load_panel()
    non_coin = fetch_non_coin_symbols()
    excluded_present = sorted(set(close.columns) & set(non_coin))

    close_crypto = close.drop(columns=excluded_present, errors="ignore")
    qv_crypto = qv.drop(columns=excluded_present, errors="ignore")

    cfg = ctrend.Config(universe_size=UNIVERSE_SIZE, min_median_qv=MIN_MEDIAN_QV)
    member = ctrend.build_membership(close_crypto, qv_crypto, cfg)

    ever_member = member.any(axis=0)
    ever_member_symbols = sorted(ever_member[ever_member].index.tolist())

    n_per_day = member.sum(axis=1)

    manifest = {
        "date": pd.Timestamp.utcnow().date().isoformat(),
        "method": "reuses scripts/backtest_ctrend_v1.py load_panel()+build_membership(), "
                 "same PIT discipline as CTREND v1 (859ebad)",
        "universe_size": UNIVERSE_SIZE,
        "min_median_qv": MIN_MEDIAN_QV,
        "total_symbols_in_full_panel": int(close.shape[1]),
        "excluded_tokenized_macro_present_in_panel": excluded_present,
        "n_excluded": len(excluded_present),
        "n_symbols_ever_member": len(ever_member_symbols),
        "symbols_ever_member": ever_member_symbols,
        "date_range": [str(member.index.min()), str(member.index.max())],
        "n_members_per_day_describe": n_per_day.describe().to_dict(),
        "n_days_below_universe_size": int((n_per_day < UNIVERSE_SIZE).sum()),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "PIT_UNIVERSE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str))
    member.to_parquet(OUT_DIR / "pit_universe_membership.parquet")
    close_crypto[ever_member_symbols].to_parquet(OUT_DIR / "pit_universe_close.parquet")
    qv_crypto[ever_member_symbols].to_parquet(OUT_DIR / "pit_universe_qv.parquet")

    print(json.dumps({k: v for k, v in manifest.items() if k != "symbols_ever_member"},
                     indent=2, default=str))
    print(f"n_symbols_ever_member={len(ever_member_symbols)}")
    print(f"-> {OUT_DIR}/PIT_UNIVERSE_MANIFEST.json, pit_universe_{{membership,close,qv}}.parquet")


if __name__ == "__main__":
    main()
