#!/usr/bin/env python3
"""
scripts/run_cross_sectional_momentum_live_shadow.py
─────────────────────────────────────────────────────────────────────────────
CROSS_SECTIONAL_MOMENTUM_LIVE_V1 — Mode A (SIGNAL SHADOW) runner.

Live Alpha Lab (see configs/live_alpha_registry.yaml alpha_id:
CROSS_SECTIONAL_MOMENTUM_LIVE_V1). Computes the signal, writes one decision
row per (rebalance date, selected symbol) — sends NO order, does not even
simulate a fill (Mode A pur).

DIFFERENT alpha_id, DIFFERENT data, from CROSS_SECTIONAL_MOMENTUM_PIT_V1 (the
original spec, still DATA_BLOCKED, untouched): that entry needs the PIT
312-symbol data_v2/normalized panel, which has no confirmed continuous live
update. This is a deliberately lighter-weight, live-data-only reconstruction:
direct Binance USDM futures daily klines (real close + real quote-volume),
polled on demand into a small local parquet cache (see
src/institutional/engines/cross_sectional_momentum_live/klines_source.py) —
no L2, no 1h/5m granularity, no new collector daemon. See
reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V1/freeze_spec.json for
the full accounting of every deviation from reports/edge_discovery/
alpha_hunt_2026-08-30/w1_cross_sectional/REPORT.md (universe size, data
source, cost model).

Univers : configs/portfolio_v1_1_parallel_50.yaml — FIGÉ (same file as
LIQ_CASCADE_REPEAT_V1 / SHORT_COVERING_CONTINUATION_V1). Never derived from a
glob() on data/ (see tests/test_universe_drift_guard.py). Runtime universe
must match the frozen hash — fail closed otherwise. Listing eligibility is
resolved against LIVE Binance exchangeInfo via
src/institutional/data/derivatives_collector/symbol_resolver.py (read-only
reuse, not modified) — this is the "universe PIT / listing eligibility" check
for this alpha, in place of the DATA_BLOCKED 312-symbol instrument_master.

Idempotent: reads the existing ledger, only appends decisions whose
(event_time, symbol) key doesn't already exist.

Long-only by construction (SHORT_REJECTED) — hard-enforced below (refuses to
write if any non-LONG direction ever appears).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import spec_provenance

from src.institutional.data.derivatives_collector.symbol_resolver import (
    fetch_exchange_info, resolve_universe)
from src.institutional.engines.cross_sectional_momentum_live.klines_source import (
    refresh_symbol_cache)
from src.institutional.engines.cross_sectional_momentum_live.signal import (
    LIQUIDITY_WINDOW_DAYS, LOOKBACK_DAYS, MIN_LIQUIDITY_USD, REBALANCE_WEEKDAY,
    TOP_FRACTION, build_weekly_decisions)

ALPHA_ID = "CROSS_SECTIONAL_MOMENTUM_LIVE_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
CACHE_DIR = OUT_DIR / "klines_cache"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_7d"


def load_universe() -> List[str]:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/, voir docstring."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: List[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str, expected_hash: str) -> None:
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


def cache_path_for_symbol(canonical: str) -> Path:
    return CACHE_DIR / f"{canonical}.parquet"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID, uhash)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    # -- listing eligibility: LIVE exchangeInfo (status=TRADING), never a
    #    guessed/static mapping. Reuses symbol_resolver.py READ-ONLY (same
    #    module the derivatives collector uses) instead of the DATA_BLOCKED
    #    312-symbol instrument_master.parquet.
    exchange_info = fetch_exchange_info()
    resolved = resolve_universe(universe, exchange_info)
    resolution_trace = [
        {"canonical": r.canonical_asset, "exchange_symbol": r.exchange_symbol,
         "status": r.instrument_status, "eligible": r.eligible, "reason": r.eligibility_reason}
        for r in resolved
    ]
    (OUT_DIR / "_symbol_resolution.json").write_text(json.dumps(resolution_trace, indent=2))
    eligible = [r for r in resolved if r.eligible]
    n_excluded = len(resolved) - len(eligible)
    if n_excluded:
        excluded_str = ", ".join(f"{r.canonical_asset}({r.instrument_status})"
                                  for r in resolved if not r.eligible)
        print(f"[{ALPHA_ID}] {n_excluded}/{len(universe)} symboles exclus (listing eligibility live) : "
              f"{excluded_str}", flush=True)

    # -- data: incremental REST top-up of a small local daily-bar cache per
    #    symbol (date/close/quote_volume only) -- no L2, no 1h/5m storage, no
    #    new daemon (klines_source.py docstring has the full data-source
    #    investigation/rationale).
    close_cols, vol_cols = {}, {}
    for r in eligible:
        cpath = cache_path_for_symbol(r.canonical_asset)
        panel = refresh_symbol_cache(r.exchange_symbol, cpath)
        if panel.empty:
            continue
        s = panel.set_index("date")
        close_cols[r.canonical_asset] = s["close"]
        vol_cols[r.canonical_asset] = s["quote_volume"]

    if not close_cols:
        print(f"[{ALPHA_ID}] aucune donnée live récupérée — rien à écrire.")
        return 0

    panel_close = pd.DataFrame(close_cols).sort_index()
    panel_vol = pd.DataFrame(vol_cols).sort_index()

    # reindex to a gap-free daily calendar so trailing_return's .shift(7)
    # (position-based) equals exactly 7 CALENDAR days, and so a fetch glitch
    # on one day never silently telescopes later shifts (see signal.py).
    full_idx = pd.date_range(panel_close.index.min(), panel_close.index.max(), freq="D", tz="UTC")
    panel_close = panel_close.reindex(full_idx)
    panel_vol = panel_vol.reindex(full_idx)

    # drop the current, not-yet-closed UTC day -- a rebalance decision must
    # never use an in-progress candle as if it were a closed daily bar.
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
          f"sur {panel_close.shape[1]} symboles avec donnée live, {n_rebalances} rebalances hebdo", flush=True)

    if dec.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    # fail-closed universe drift check (même discipline que les autres runners).
    runtime_symbols = set(dec["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        extra = runtime_symbols - set(universe)
        raise RuntimeError(f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé: {extra}")
    if (dec["direction"] != "LONG").any():
        raise RuntimeError(
            "Direction != LONG émise par CROSS_SECTIONAL_MOMENTUM_LIVE_V1 — interdit "
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
        "universe_size": len(universe), "n_symbols_eligible": len(eligible),
        "n_symbols_excluded": n_excluded, "n_decisions_total": len(out),
        "n_decisions_new": n_new, "n_rebalances_this_run": n_rebalances,
        "mode": "A_SIGNAL_SHADOW",
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
