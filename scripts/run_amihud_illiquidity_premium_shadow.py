#!/usr/bin/env python3
"""
scripts/run_amihud_illiquidity_premium_shadow.py
─────────────────────────────────────────────────────────────────────────────
AMIHUD_ILLIQUIDITY_PREMIUM_V1 — Mode A (SIGNAL SHADOW) runner. First
candidate frozen via the Alpha Validation Factory pipeline (independent
reimplementation, VALIDATED_FOR_FORWARD -- see reports/edge_discovery/
validation_2026-09/AMIHUD_ILLIQUIDITY_PREMIUM/REPORT.md).

Long the most-illiquid-eligible quintile / short the most-liquid quintile
(Amihud 2002 illiquidity premium), non-overlapping weekly rebalance
(Wednesday), 7-calendar-day horizon. See src/institutional/engines/
amihud_illiquidity_live/__init__.py for the full mechanism writeup and the
explicit SHORT-leg policy note (this alpha carries a genuine short leg,
shadow only -- flagged there, not silently decided).

Same live-data situation as CROSS_SECTIONAL_MOMENTUM_LIVE_V2 (the validated
construction used data_v2/normalized, no confirmed continuous live update):
reuses that alpha's ALREADY-BUILT, read-only infrastructure --
klines_source.py (generic Binance daily-klines cache) and universe.py
(dynamic live-universe resolution + PIT eligibility gate). Neither module is
modified here.

Sends NO order, does not even simulate a fill (Mode A pur). Idempotent:
only appends decisions whose (event_time, symbol, direction) key doesn't
already exist -- unlike the long-only cross-sectional alphas, this one can
legitimately emit TWO rows (LONG and SHORT) for the same (event_time,
symbol) if a name somehow appears in the eligible set twice across weeks
(it won't within one rebalance, but the tuple key must include direction to
stay correct for possible future generalizations).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

from src.institutional.data.derivatives_collector.symbol_resolver import fetch_exchange_info
from src.institutional.engines.cross_sectional_momentum_live.klines_source import (
    refresh_symbol_cache)
from src.institutional.engines.cross_sectional_momentum_live_v2.universe import (
    build_pit_eligibility_log, historical_reinclusion_candidates,
    load_listing_calendar, mask_pre_eligibility, resolve_dynamic_liquid_universe,
    resolve_onboard_dates, summarize_pit_log, write_pit_universe_log)
from src.institutional.engines.amihud_illiquidity_live.signal import (
    HORIZON_DAYS, LIQUIDITY_WINDOW_DAYS, MIN_LIQUIDITY_USD, MIN_VALID_DAYS,
    REBALANCE_WEEKDAY, TOP_FRACTION, build_weekly_decisions, weekly_rebalance_dates)

# MIN_LISTING_AGE_DAYS : même constante d'éligibilité que les alphas
# cross-sectional existants (30 jours) -- pas de raison économique de
# diverger pour ce mécanisme.
MIN_LISTING_AGE_DAYS = 30

ALPHA_ID = "AMIHUD_ILLIQUIDITY_PREMIUM_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
CACHE_DIR = OUT_DIR / "klines_cache"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = f"fwd_{HORIZON_DAYS}d"
INTER_SYMBOL_SLEEP_SEC = 0.12
PROGRESS_EVERY = 50


def universe_hash(universe: List[str]) -> str:
    """Audit-only (pas un gate fail-closed -- univers dynamique par
    construction, comme V2)."""
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str) -> None:
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    entry = entries[0]
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} dans le registre — "
            "seul SIGNAL_SHADOW/EXECUTION_SHADOW peut écrire des décisions."
        )


def cache_path_for_symbol(exchange_symbol: str) -> Path:
    return CACHE_DIR / f"{exchange_symbol}.parquet"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    check_registry_freeze(ALPHA_ID)

    exchange_info = fetch_exchange_info()
    candidate_universe = resolve_dynamic_liquid_universe(exchange_info)
    listing_calendar = load_listing_calendar()
    reinclusion_candidates, reinclusion_no_data = historical_reinclusion_candidates(
        listing_calendar, exclude=set(candidate_universe))
    fetch_universe = sorted(set(candidate_universe) | set(reinclusion_candidates))
    uhash = universe_hash(fetch_universe)
    print(f"[{ALPHA_ID}] univers candidat DYNAMIQUE (live exchangeInfo) : "
         f"{len(candidate_universe)} live aujourd'hui + {len(reinclusion_candidates)} délistés "
         f"réintégrés = {len(fetch_universe)} à télécharger, hash={uhash} (audit seulement)",
         flush=True)

    close_cols, vol_cols = {}, {}
    n = len(fetch_universe)
    for i, sym in enumerate(fetch_universe):
        panel = refresh_symbol_cache(sym, cache_path_for_symbol(sym))
        if not panel.empty:
            s = panel.set_index("date")
            close_cols[sym] = s["close"]
            vol_cols[sym] = s["quote_volume"]
        if (i + 1) % PROGRESS_EVERY == 0 or (i + 1) == n:
            print(f"[{ALPHA_ID}] klines fetch progress: {i + 1}/{n} "
                 f"({len(close_cols)} avec donnée jusqu'ici)", flush=True)
        time.sleep(INTER_SYMBOL_SLEEP_SEC)

    if not close_cols:
        print(f"[{ALPHA_ID}] aucune donnée live récupérée — rien à écrire.")
        return 0

    panel_close = pd.DataFrame(close_cols).sort_index()
    panel_vol = pd.DataFrame(vol_cols).sort_index()

    full_idx = pd.date_range(panel_close.index.min(), panel_close.index.max(), freq="D", tz="UTC")
    panel_close = panel_close.reindex(full_idx)
    panel_vol = panel_vol.reindex(full_idx)

    today_utc = pd.Timestamp.now(tz="UTC").floor("D")
    panel_close = panel_close[panel_close.index < today_utc]
    panel_vol = panel_vol[panel_vol.index < today_utc]

    rebal_dates = weekly_rebalance_dates(panel_close.index, REBALANCE_WEEKDAY)
    onboard_df = resolve_onboard_dates(fetch_universe, listing_calendar, panel_close)

    pit_log = build_pit_eligibility_log(
        rebal_dates, fetch_universe, onboard_df, panel_close, panel_vol,
        min_listing_age_days=MIN_LISTING_AGE_DAYS, min_liquidity_usd=MIN_LIQUIDITY_USD,
        liquidity_window=LIQUIDITY_WINDOW_DAYS, lookback=HORIZON_DAYS,
    )
    write_pit_universe_log(pit_log, OUT_DIR / "pit_universe_log.parquet")
    pit_summary = summarize_pit_log(pit_log)
    (OUT_DIR / "pit_universe_summary.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_listing_age_days": MIN_LISTING_AGE_DAYS, "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "n_rebalance_dates": len(pit_summary), "rebalances": pit_summary,
    }, indent=2))

    masked_close, masked_vol = mask_pre_eligibility(
        panel_close, panel_vol, onboard_df, min_listing_age_days=MIN_LISTING_AGE_DAYS)

    dec = build_weekly_decisions(
        masked_close, masked_vol, min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=TOP_FRACTION,
    )
    n_rebalances = dec["event_time"].nunique() if not dec.empty else 0
    print(f"[{ALPHA_ID}] {len(dec)} décisions (LONG+SHORT, quintile illiquidité) sur "
         f"{masked_close.shape[1]} symboles (univers PIT masqué), {n_rebalances} rebalances hebdo",
         flush=True)

    if dec.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    runtime_symbols = set(dec["symbol"].unique())
    if not runtime_symbols.issubset(set(fetch_universe)):
        extra = runtime_symbols - set(fetch_universe)
        raise RuntimeError(f"INCOHÉRENCE INTERNE : symboles hors univers résolu ce run: {extra}")
    if not set(dec["direction"].unique()) <= {"LONG", "SHORT"}:
        raise RuntimeError(f"Direction inattendue émise: {set(dec['direction'].unique())}")

    now = datetime.now(timezone.utc).isoformat()
    dec = dec.copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", "symbol")
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"
    dec["event_time"] = pd.to_datetime(dec["event_time"], utc=True)

    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        old["event_time"] = pd.to_datetime(old["event_time"], utc=True)
        key_old = set(zip(old["event_time"], old["symbol"], old["direction"]))
        new_mask = [not ((et, sy, di) in key_old)
                   for et, sy, di in zip(dec["event_time"], dec["symbol"], dec["direction"])]
        dec_new = dec[new_mask]
        out = pd.concat([old, dec_new], ignore_index=True)
    else:
        dec_new = dec
        out = dec

    out.to_parquet(LEDGER, index=False)
    print(f"[{ALPHA_ID}] {len(dec_new)} nouvelles décisions écrites ({len(out)} total) -> {LEDGER}",
         flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
