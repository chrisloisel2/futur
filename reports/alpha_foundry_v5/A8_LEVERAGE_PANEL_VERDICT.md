# A8 (leverage topology) — support audit verdict: THIN_SUPPORT, budget 0

## What was built

`scripts/build_a8_leverage_panel.py` consolidated `data/derivatives_raw`
(Binance's combined open_interest/mark/index/funding snapshot stream, ~5min
native cadence, 2026-06-28→2026-08-29, 47 symbols) plus Bybit+OKX
`force_order` liquidations into a single historical panel: 592,615 rows, 93
columns, matching A8's `required_column_patterns` exactly
(`open_interest`, `funding`, `basis_bps` via mark/index, `liquidation_*`).
Frozen: `DATASET_MANIFEST_A8_LEVERAGE_PANEL_V1.json`,
`FEATURE_SET_A8_leverage_panel_v1.json` (77/93 columns selected).

Two real bugs in the shared pipeline were found and fixed while doing this
(both independent of A8 itself, affecting anyone using the newer
`data_planes` builders): `alpha_foundry_v5/provenance.py` only recognized
the *older* `planes/derivatives.py`'s clock column name
(`derivatives__available_ts_ns`), never the newer `data_planes/
derivatives.py`'s (`deriv__available_ts_ns`) -- no tensor built by the
newer pipeline could ever have passed provenance sealing before this.

## Support audit verdict

`scripts/alpha_foundry_v5_support_audit.py` --labs A8: **THIN_SUPPORT**,
`recommended_max_hypothesis_tests: 0`.

- `data_ready: true`, `diversity_ok: true` (all three chronological thirds
  present, both up/down regimes present) -- the data itself is fine.
- `groups_pass: false` -- **both** required evidence groups
  (`oi_economic_changes`, `basis_economic_changes`) need `min_venues: 2`.
  This panel has real open_interest/mark/index/funding from **Binance
  only** -- `events_by_venue: {"binance": 592530, "bybit": 0, "okx": 0}`.
  Bybit and OKX only contributed liquidations here (`data/derivatives_raw`
  has no `stream=open_interest` for them, only `stream=force_order`).

This is not a sample-size or diversity problem -- it is a real,
single-venue-only limitation of what's backfilled. The mechanism support
policy correctly refuses to allocate discovery budget until at least one
more venue corroborates the OI/basis evidence, guarding against mistaking
one exchange's idiosyncrasies for a genuine cross-market leverage
mechanism.

## What would unblock this

Combined OI+mark+index+funding history from at least one more venue
(Bybit or OKX), in the same per-snapshot format Binance already has here.
Not present anywhere in the existing `~350GB` already-collected data
(checked: `data/derivatives_backfill/{bybit,okx}` has funding-only, not
the joined OI+mark+index+funding needed) -- would need either a
historical backfill from that venue's REST API or, more simply, a
recurring live collector matching Binance's `stream=open_interest`
pattern going forward (the 28h Market Physics V3 collection running now
captures OI for OKX/Bybit at the tick level in a different schema, not
this one, and only for the 28h window).

## Verdict

**Not launched. THIN_SUPPORT, budget 0 — correctly refused, not a bug to
route around.** Do not force a discovery run against policy by lowering
`min_venues` without deciding that's a real methodology choice, not a
convenience hack.
