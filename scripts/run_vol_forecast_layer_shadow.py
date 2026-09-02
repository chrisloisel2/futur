#!/usr/bin/env python3
"""
scripts/run_vol_forecast_layer_shadow.py
─────────────────────────────────────────────────────────────────────────────
VOL_FORECAST_LAYER_V1 — Mode A (SIGNAL SHADOW) runner.

PAS une stratégie de trading : combine les trois signaux options
DISCOVERY-stage de reports/edge_discovery/alpha_hunt_2026-08-30/w6_options/
REPORT.md (M2 rv_iv_spread, M6 far_otm_put_share, M17 block_count_24h --
alpha_ids registre OPTIONS_RV_IV_SPREAD_V1 / OPTIONS_FAR_OTM_PUT_SHARE_V1 /
OPTIONS_BLOCK_FLOW_TO_RV_V1) en UN SEUL forecast quotidien de volatilité
réalisée forward, et le journalise -- forecast, RV courante, état IV,
confiance, état funding -- plus un champ `actual_realized_rv` en attente,
rempli plus tard par scripts/backfill_vol_forecast_layer_rv.py une fois
l'horizon de forecast écoulé. AUCUN ordre, AUCUN fill simulé, AUCUNE
décision de sizing/portefeuille -- voir reports/live_alpha_lab/
VOL_FORECAST_LAYER_V1/freeze_spec.json section "part2_design_proposal_NOT_IMPLEMENTED"
pour ce à quoi un vrai overlay de portefeuille RESSEMBLERAIT (documenté, pas
construit).

Univers : BTCUSDT uniquement (options Deribit BTC + perp Binance BTC,
identique à l'univers des 3 alpha_ids OPTIONS_*_V1 sous-jacents,
universe=[BTCUSDT]). Idempotent : relit le ledger existant, ne réémet que
les lignes dont `event_time` (= jour calendaire) n'existe pas déjà.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

from src.institutional.engines.vol_forecast_layer.panel import (
    PANEL_COLUMNS, build_daily_panel,
)

ALPHA_ID = "VOL_FORECAST_LAYER_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
EXPECTED_UNIVERSE = ["BTCUSDT"]


def universe_hash(universe: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def load_registry_entry(alpha_id: str) -> dict:
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    return entries[0]


def load_universe() -> list[str]:
    """Univers FIGÉ : lu depuis l'entrée registry elle-même (pas de config
    YAML séparée -- univers intrinsèquement figé à 1 symbole, comme
    FUNDING_BASIS_DISAGREEMENT_V2). Fail-closed si le registre dérive."""
    entry = load_registry_entry(ALPHA_ID)
    universe = sorted(entry.get("universe") or [])
    if universe != sorted(EXPECTED_UNIVERSE):
        raise RuntimeError(
            f"UNIVERS DÉRIVÉ : registry.universe={universe} != figé {sorted(EXPECTED_UNIVERSE)} "
            "— refus de tourner (règle fail-closed, section 8 de la mission)."
        )
    return universe


def check_registry_freeze(alpha_id: str) -> None:
    """Fail-closed : seul SIGNAL_SHADOW/EXECUTION_SHADOW peut écrire des décisions."""
    entry = load_registry_entry(alpha_id)
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} dans le registre — "
            "seul SIGNAL_SHADOW/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID)
    print(f"[{ALPHA_ID}] univers figé : {universe}, hash={uhash}", flush=True)

    print(f"[{ALPHA_ID}] build_daily_panel() (M2 rv_iv_spread + M6 far_otm_put_share + "
          f"M17 block_count_24h -> combined forecast)…", flush=True)
    panel = build_daily_panel(symbol="BTCUSDT", currency="BTC")
    if panel.empty:
        print(f"[{ALPHA_ID}] panel vide — data manquante "
              f"(features/BTC_daily.parquet absent ?), rien à écrire.")
        return 0

    # univers drift réel : ce runner ne calcule qu'un seul symbole (BTCUSDT),
    # jamais dérivé d'un glob -- rien à revalider dynamiquement ici au-delà
    # du check_registry_freeze ci-dessus, mais on l'affirme explicitement.
    print(f"[{ALPHA_ID}] {len(panel)} jours dans le panel "
          f"({panel['day'].min().date()}..{panel['day'].max().date()})", flush=True)

    now = datetime.now(timezone.utc).isoformat()
    dec = panel[PANEL_COLUMNS].copy()
    dec["engine"] = ALPHA_ID
    dec["universe_hash"] = uhash
    # symbol_col=None : ce panel est market-wide (un seul symbole BTCUSDT
    # fixé par build_daily_panel(), pas une colonne par ligne) -- sentinel
    # "MARKET_WIDE" explicite dans raw_event_id, cf provenance.py.
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", symbol_col=None)
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"

    # idempotence : ne pas dupliquer un `event_time` (jour calendaire) déjà décidé.
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        key_old = set(old["event_time"])
        new_mask = ~dec["event_time"].isin(key_old)
        dec_new = dec[new_mask]
        if dec_new.empty:
            print(f"[{ALPHA_ID}] rien de nouveau (idempotent) — {len(old)} décisions déjà connues.")
            return 0
        out = pd.concat([old, dec_new], ignore_index=True)
        n_new = len(dec_new)
    else:
        out = dec
        n_new = len(dec)

    out = out.sort_values("event_time").reset_index(drop=True)
    out.to_parquet(LEDGER, index=False)
    meta = {
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "mode": "A_SIGNAL_SHADOW",
        "n_pending_actual_realized_rv": int(out["actual_realized_rv"].isna().sum()),
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    print(f"[{ALPHA_ID}] {meta['n_pending_actual_realized_rv']} lignes en attente de backfill RV "
          f"(scripts/backfill_vol_forecast_layer_rv.py)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
