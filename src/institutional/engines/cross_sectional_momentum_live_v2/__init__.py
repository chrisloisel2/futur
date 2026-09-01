"""
src/institutional/engines/cross_sectional_momentum_live_v2/
─────────────────────────────────────────────────────────────────────────────
CROSS_SECTIONAL_MOMENTUM_LIVE_V2 — Live Alpha Lab, cross-sectional family.
CHALLENGER to CROSS_SECTIONAL_MOMENTUM_LIVE_V1, NOT a modification of it (V1
stays strictly frozen on its frozen-50 universe, untouched -- see
configs/live_alpha_registry.yaml).

Same economic mechanism as V1 and as the true PIT original
(CROSS_SECTIONAL_MOMENTUM_PIT_V1, reports/edge_discovery/alpha_hunt_2026-08-30/
w1_cross_sectional/REPORT.md's M1: 7d->7d cross-sectional raw-return momentum,
long-only, top-quintile, non-overlapping weekly rebalance). The ONLY thing
this alpha changes is the UNIVERSE:

  - V1:  a FIXED 50-symbol list (configs/portfolio_v1_1_parallel_50.yaml),
         majors/large-established-alt biased, with listing eligibility
         resolved live but membership itself frozen.
  - V2:  a DYNAMIC "PIT" candidate universe re-resolved from Binance USDM
         futures' LIVE /fapi/v1/exchangeInfo on every run (every currently
         status=TRADING USDT-margined COIN perpetual, ~500 candidates at
         build time -- see universe.py), then narrowed to the actually
         "liquid altcoin" cohort by the SAME causal trailing-30d liquidity
         mechanism as V1 (signal.py), just with its own threshold (see that
         module's docstring for the rationale).

This directly targets the source report's own honest robustness finding
(REPORT.md, robustness checks): the effect "works mid/liquid [tercile], not
illiquid" and "majors-only alone is insignificant (t=0.72) -- this is an
altcoin breadth effect, not a BTC/ETH phenomenon". V1's frozen-50 sits closer
to "majors" than to the broad liquid-altcoin cross-section the source report
actually measured; V2 is the deliberate attempt to build that broader
cross-section causally, without falling back on the DATA_BLOCKED 312-symbol
data_v2/instrument_master.parquet panel (see CROSS_SECTIONAL_MOMENTUM_PIT_V1,
still untouched, still DATA_BLOCKED).

- `universe.py` — pure filter (no I/O beyond what the caller passes in):
  turns a raw Binance exchangeInfo response into the dynamic candidate
  universe (PERPETUAL / USDT / TRADING / COIN underlying only).
- `signal.py` — pure functions (no I/O): causal trailing return, causal
  trailing liquidity (own threshold, see docstring), cross-sectional
  top-bucket selection, weekly rebalance orchestration. Deliberately NOT
  imported from cross_sectional_momentum_live/signal.py -- this alpha's
  spec (its liquidity threshold in particular) must stand on its own as an
  independently frozen, independently auditable module, not silently follow
  whatever V1's module does in the future.

I/O helpers reused READ-ONLY from sibling modules (no duplicated network
code, same precedent V1 itself set by reusing symbol_resolver.py):
  - src.institutional.data.derivatives_collector.symbol_resolver.fetch_exchange_info
    (generic exchangeInfo GET, no alpha-specific logic)
  - src.institutional.engines.cross_sectional_momentum_live.klines_source
    (generic Binance daily-klines REST client + local parquet cache, no
    alpha-specific logic -- takes an exchange_symbol and a cache_path as
    plain arguments, nothing V1-specific baked in)

See reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V2/freeze_spec.json
for the full honesty accounting of every deviation from V1 and from the true
PIT original, and scripts/run_cross_sectional_momentum_live_v2_shadow.py for
the Mode A runner.
"""
