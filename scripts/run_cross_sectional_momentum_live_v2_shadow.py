#!/usr/bin/env python3
"""
scripts/run_cross_sectional_momentum_live_v2_shadow.py
─────────────────────────────────────────────────────────────────────────────
CROSS_SECTIONAL_MOMENTUM_LIVE_V2 — Mode A (SIGNAL SHADOW) runner. CHALLENGER
to CROSS_SECTIONAL_MOMENTUM_LIVE_V1 (frozen-50, majors-biased universe, left
STRICTLY untouched — see configs/live_alpha_registry.yaml, do not edit that
entry from this script or anywhere else).

SAME economic mechanism (7d->7d cross-sectional raw-return momentum,
long-only, top-quintile, non-overlapping weekly rebalance) as V1 and as the
true PIT original (CROSS_SECTIONAL_MOMENTUM_PIT_V1, still DATA_BLOCKED) — the
ONLY thing this alpha changes vs V1 is the UNIVERSE: instead of a fixed
50-symbol list, the eligible candidate universe is resolved fresh every run
from Binance USDM futures' LIVE /fapi/v1/exchangeInfo (PERPETUAL / USDT /
TRADING / underlyingType=COIN — see
src/institutional/engines/cross_sectional_momentum_live_v2/universe.py),
producing a much larger (~500 candidates at build time, narrowed by the
causal liquidity filter in signal.py) "PIT dynamique" cross-section — a
direct attempt at the source report's own honest robustness finding that
this effect lives in the BROAD liquid-altcoin set, not the majors-heavy
frozen-50 (see CROSS_SECTIONAL_MOMENTUM_LIVE_V1/freeze_spec.json's caveat,
and this alpha's own freeze_spec.json under reports/live_alpha_lab/
CROSS_SECTIONAL_MOMENTUM_LIVE_V2/).

DIFFERENT alpha_id, DIFFERENT universe, DIFFERENT ledger, DIFFERENT
freeze_timestamp from V1 — CORRELATED (same correlation_family,
CROSS_SECTIONAL_XSMOM, see registry notes for the portfolio dedup
implication) but tracked completely separately, per explicit user
instruction. Sends NO order, does not even simulate a fill (Mode A pur).

Universe construction is genuinely NOT frozen (that IS the point of this
challenger) — so, unlike V1, there is NO universe-hash fail-closed drift
guard here: a changing eligible universe from run to run is expected and
correct, not an error to guard against. What IS still fail-closed here:
  - the registry entry must exist and be SIGNAL_SHADOW/EXECUTION_SHADOW
    before any decision is written (same discipline as every other Mode A
    runner in this repo);
  - every decision's symbol must belong to the candidate set actually
    resolved THIS run (an internal-consistency check against a bug in this
    script, not a check against drift from a frozen list — there is no
    frozen list here);
  - direction must always be LONG (SHORT_REJECTED, hard project rule).

Idempotent: reads the existing ledger, only appends decisions whose
(event_time, symbol) key doesn't already exist.

API load note (documented per the mission's explicit request — see
freeze_spec.json for the fuller accounting): resolving ~500 candidate
symbols' daily klines is up to ~500-1000 REST calls to
/fapi/v1/klines on the FIRST (backfill) run (most recently-listed
symbols need only 1 page; only long-lived majors need 2). Binance's public
USDM futures REST enforces a weight-based limit (~2400 request-weight/minute
per IP at the time of writing); a `limit=1500` klines call costs weight 10.
klines_source.py already sleeps 0.15s between pages within one symbol; this
script additionally sleeps INTER_SYMBOL_SLEEP_SEC between symbols so the
whole backfill is naturally paced well under that budget (network round-trip
latency alone already spaces calls out further). Every subsequent run is
incremental (only new days per symbol, typically 0-1 new row/symbol/day since
the previous run) — cheap, no different from V1's steady-state cost.
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

from src.institutional.live_alpha_lab.provenance import spec_provenance

from src.institutional.data.derivatives_collector.symbol_resolver import fetch_exchange_info
# Generic Binance daily-klines REST client + local parquet cache, reused
# READ-ONLY from the V1 module — no alpha-specific logic lives there (see
# cross_sectional_momentum_live_v2/__init__.py docstring for the full
# reuse-vs-duplicate rationale). V1's own files are not modified by this
# import.
from src.institutional.engines.cross_sectional_momentum_live.klines_source import (
    refresh_symbol_cache)
from src.institutional.engines.cross_sectional_momentum_live_v2.universe import (
    resolve_dynamic_liquid_universe)
from src.institutional.engines.cross_sectional_momentum_live_v2.signal import (
    LIQUIDITY_WINDOW_DAYS, LOOKBACK_DAYS, MIN_LIQUIDITY_USD, REBALANCE_WEEKDAY,
    TOP_FRACTION, build_weekly_decisions)

ALPHA_ID = "CROSS_SECTIONAL_MOMENTUM_LIVE_V2"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
CACHE_DIR = OUT_DIR / "klines_cache"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_7d"
INTER_SYMBOL_SLEEP_SEC = 0.12   # see module docstring's "API load note"
PROGRESS_EVERY = 50


def universe_hash(universe: List[str]) -> str:
    """Audit-only (NOT a fail-closed drift gate — see module docstring):
    hash of THIS run's resolved candidate universe, recorded on every
    decision row and in run_state.json/_universe_resolution.json for
    traceability, even though nothing is gated on it staying constant."""
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str) -> None:
    """Fail-closed : la spec figée dans live_alpha_registry.yaml doit exister
    et operational_status doit autoriser l'écriture — sinon on n'écrit rien."""
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

    # -- dynamic PIT candidate universe: resolved fresh from LIVE
    #    exchangeInfo every run, NOT a fixed list (see universe.py).
    exchange_info = fetch_exchange_info()
    candidate_universe = resolve_dynamic_liquid_universe(exchange_info)
    uhash = universe_hash(candidate_universe)
    (OUT_DIR / "_universe_resolution.json").write_text(json.dumps({
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "n_candidates": len(candidate_universe),
        "universe_hash": uhash,
        "filter": "contractType=PERPETUAL, quoteAsset=USDT, status=TRADING, underlyingType=COIN",
        "note": "audit trail only -- NOT a frozen-hash drift gate (see runner docstring); a changing "
                "candidate set run-to-run is expected and correct for this alpha.",
        "candidates": candidate_universe,
    }, indent=2))
    print(f"[{ALPHA_ID}] univers candidat DYNAMIQUE (live exchangeInfo) : "
          f"{len(candidate_universe)} symboles, hash={uhash} (audit seulement, PAS un frozen-hash gate)",
          flush=True)

    # -- data: incremental REST top-up of a small local daily-bar cache per
    #    symbol (date/close/quote_volume only), same generic loader as V1
    #    (klines_source.py, reused read-only). Small inter-symbol sleep to
    #    stay clear of the weight-based rate limit on a large first backfill
    #    (see module docstring's "API load note").
    close_cols, vol_cols = {}, {}
    n = len(candidate_universe)
    for i, sym in enumerate(candidate_universe):
        cpath = cache_path_for_symbol(sym)
        panel = refresh_symbol_cache(sym, cpath)
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

    # reindex to a gap-free daily calendar so trailing_return's .shift(7)
    # (position-based) equals exactly 7 CALENDAR days.
    full_idx = pd.date_range(panel_close.index.min(), panel_close.index.max(), freq="D", tz="UTC")
    panel_close = panel_close.reindex(full_idx)
    panel_vol = panel_vol.reindex(full_idx)

    # drop the current, not-yet-closed UTC day.
    today_utc = pd.Timestamp.now(tz="UTC").floor("D")
    panel_close = panel_close[panel_close.index < today_utc]
    panel_vol = panel_vol[panel_vol.index < today_utc]

    dec = build_weekly_decisions(
        panel_close, panel_vol,
        min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=TOP_FRACTION,
        lookback=LOOKBACK_DAYS, liquidity_window=LIQUIDITY_WINDOW_DAYS,
        rebalance_weekday=REBALANCE_WEEKDAY,
    )
    n_rebalances = dec["event_time"].nunique() if not dec.empty else 0
    print(f"[{ALPHA_ID}] {len(dec)} décisions LONG (top-quintile, {LOOKBACK_DAYS}j->fwd_7d) "
          f"sur {panel_close.shape[1]} symboles avec donnée live, {n_rebalances} rebalances hebdo",
          flush=True)

    if dec.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    # internal-consistency sanity check (NOT a frozen-drift gate — see
    # module docstring): every emitted symbol must belong to THIS run's
    # resolved candidate set.
    runtime_symbols = set(dec["symbol"].unique())
    if not runtime_symbols.issubset(set(candidate_universe)):
        extra = runtime_symbols - set(candidate_universe)
        raise RuntimeError(f"INCOHÉRENCE INTERNE : symboles hors univers candidat résolu ce run: {extra}")
    if (dec["direction"] != "LONG").any():
        raise RuntimeError(
            "Direction != LONG émise par CROSS_SECTIONAL_MOMENTUM_LIVE_V2 — interdit "
            "(SHORT_REJECTED, mécanisme long-only par construction) — refus d'écrire."
        )

    now = datetime.now(timezone.utc).isoformat()
    dec = dec.copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"
    dec["event_time"] = pd.to_datetime(dec["event_time"], utc=True)

    # idempotence : ne pas dupliquer une clé (event_time, symbol) déjà décidée.
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        old["event_time"] = pd.to_datetime(old["event_time"], utc=True)
        key_old = set(zip(old["event_time"], old["symbol"]))
        new_mask = [not ((et, sy) in key_old) for et, sy in zip(dec["event_time"], dec["symbol"])]
        dec_new = dec[new_mask]
        if dec_new.empty:
            print(f"[{ALPHA_ID}] rien de nouveau (idempotent) — {len(old)} décisions déjà connues.")
            return 0
        out = pd.concat([old, dec_new], ignore_index=True)
        n_new = len(dec_new)
    else:
        out = dec
        n_new = len(dec)

    out.to_parquet(LEDGER, index=False)
    meta = {
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "n_candidates_this_run": len(candidate_universe),
        "n_symbols_with_data": len(close_cols),
        "n_decisions_total": len(out), "n_decisions_new": n_new,
        "n_rebalances_this_run": n_rebalances,
        "mode": "A_SIGNAL_SHADOW",
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
