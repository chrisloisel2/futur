# EVENT_SCANNER_V1_PROTOCOL

Pre-registered 2026-08-09, **before** DATA_V2_READY and before any event has
been scanned or looked at. This document freezes the four event families,
their detection thresholds, the four horizons, and the statistics/
classification rules. Nothing in this file may be changed after the scan
has been run once real data is used — that is the entire point of
pre-registration (same discipline already used on this project for
FUNDING_XVENUE_PROTOCOL, HL_METAORDERS_PROTOCOL, stress_gate_dispersion_v2).
If a threshold turns out to be wrong, that is a finding for
EVENT_SCANNER_V2, not a same-run edit.

**Order of operations this protocol depends on** (see memory
project-data-v2-rebuild): the four P0 backfills (OI Vision 5m, perp 5m,
spot 5m, aggTrades flow 1m/5m) must be complete, basis must be rebuilt from
zero against the complete corpus (not the partial 12-20 symbol files built
mid-backfill), and `reports/DATA_V2_READINESS.json` must report
`DATA_V2_READY: true` under `data_v2.validation.validator`'s
`strict_alpha_readiness` mode. Running this scanner before that point
produces numbers that describe an incomplete corpus, not the market — do
not report them as edge findings.

## Input feature frame (per symbol, 5m bars, causal)

Every family below reads from one joined frame per symbol with these
columns (built by `data_v2/events/schema.py::REQUIRED_COLUMNS`):

| column | source |
|---|---|
| `close` | perp_5m |
| `oi` | oi_vision_5m (`sum_open_interest`) |
| `oi_delta_pct_1h` | oi.pct_change(12) |
| `aggressive_buy_usd`, `aggressive_sell_usd`, `signed_volume`, `CVD` | agg_trades_flow_5m |
| `funding_rate` | funding (discrete, forward-filled only between real settlements, never interpolated) |
| `basis` (`perp_spot_basis`), `basis_z_1d`, `basis_z_7d` | data_v2/features/basis.py |
| `residual_return_1h` | cross-sectional regression vs BTC/ETH, see below |
| `liq_long_usd_5m`, `liq_short_usd_5m` | declared liquidations (Bybit/OKX live since 2026-07-04 — DATA_READY_WITH_INFERRED_LIQUIDATIONS per prior audit; optional confirmation signal only, never a gate, per that audit's own design) |

`residual_return_1h`: for BTC/ETH themselves, residual = raw return (no
benchmark to regress against). For every other symbol, a rolling causal
regression of 1h returns against BTC and ETH 1h returns (60-day trailing
window, refit daily, coefficients frozen for the following day —
zero-lookahead by construction) gives `beta_btc`, `beta_eth`; residual =
actual return − (beta_btc × BTC return + beta_eth × ETH return).

## Four families (exactly these, no fifth added mid-scan)

### 1. DELEVERAGING
```
price_residual_1h        <= -1.5 * rolling_std(price_residual_1h, 30d)
oi_delta_pct_1h           <= -3%
aggressive_sell_usd_1h  ranked >= P90 (90th percentile, trailing 30d, same symbol)
volume_1h                ranked >= P80 (trailing 30d, same symbol)
liq_long_usd_5m > 0                                    [optional confirmation, not required to fire]
```
Fires once per symbol per 4h cooldown window (no re-firing on adjacent bars
of the same unwind).

### 2. CROWDING
```
|funding_rate| at last settlement  ranked >= P90 (trailing 90d, same symbol)
|basis_z_1d|                        >= 2.0
oi_delta_pct_1h                     >= +3%
sign(aggressive_buy_usd - aggressive_sell_usd) == sign(funding_rate)
```
i.e. aggressive flow is piling INTO the crowded side, not fading it.

### 3. RELATIVE_VALUE_DISLOCATION
```
|residual_return_1h|                >= 2.0 * rolling_std(residual_return_1h, 30d)
|relative_basis_z|                  >= 2.0   (basis_z_1d minus the cross-sectional median basis_z_1d that bar)
|relative_flow_z|                   >= 2.0   (signed_volume z-scored cross-sectionally that bar)
sign(relative_basis_z) == sign(relative_flow_z) == sign(residual_return_1h)
```

### 4. FORCED_FLOW_REVERSAL
```
liq_long_usd_5m + liq_short_usd_5m  ranked >= P95 (trailing 30d, same symbol)
   OR |signed_volume_5m| ranked >= P95 (trailing 30d) if liquidation feed absent/thin for this symbol
oi_delta_pct_1h                     <= -5%   (collapse, sharper than DELEVERAGING's -3%)
price_residual shock                 |price_residual_15m| >= 2.5 * rolling_std(price_residual_15m, 30d)
```

All percentile/z-score lookbacks are trailing and causal (never include the
current or future bar). All four families are evaluated independently —
an event can belong to more than one family on the same bar; both are
recorded, not deduplicated.

## Four horizons only

`15m`, `1h`, `4h`, `8h` — measured from the triggering bar's
`research_available_at` (data_v2.temporal.available_at), not its raw
timestamp. No other horizon is computed in V1.

## Labels (data_v2/events/labels.py)

Per event, per horizon: `residual_ret_h`, `MFE_h` (max favorable excursion
in residual-return space over [0, h]), `MAE_h` (max adverse excursion),
`time_to_MFE_h`. Direction convention: DELEVERAGING and
FORCED_FLOW_REVERSAL are scored as a LONG the residual reversal (fade the
move); CROWDING is scored as a SHORT the crowded side (fade); RELATIVE_
VALUE_DISLOCATION is scored as fading the dislocation (short the residual
extreme). This direction is fixed by this protocol, not chosen after
seeing which sign was profitable.

## Statistics computed per (family, horizon)

`N`, gross expectancy (mean residual_ret_h), net expectancy (gross − cost),
win_rate, PF (gross profit / gross loss), mean MFE, mean MAE, cost×1 and
cost×2 net expectancy (cost×1 = 2×taker fee + 1 tick slippage estimate per
side; cost×2 doubles that), split by calendar year (2022/2023/2024/2025/
2026 — a year with <20 events is reported but flagged low-N, not hidden),
split by asset tier (BTC, ETH, large alts = top 20 by 30d median quote
volume at event time, small alts = the rest — tier assigned causally at
event time from the same PIT universe, not today's ranking).

## Classification (assigned per family, across all its horizons/years)

- **KILL**: net expectancy (cost×1) <= 0 in the pooled sample, OR sign
  flips between any two of the 4-5 calendar years with >=20 events each, OR
  N < 100 pooled across the full history.
- **WEAK**: net expectancy (cost×1) > 0 pooled AND consistent sign year-
  over-year, but net expectancy (cost×2) <= 0, OR PF < 1.15.
- **CANDIDATE**: net expectancy (cost×2) > 0, PF >= 1.15, consistent sign
  across years with >=20 events, N >= 100 pooled. A CANDIDATE is what
  "merits ML" per this project's standing rule (memory project-data-v2-
  rebuild) — it is not itself a green light to deploy capital (see
  project_new_edges_phase.md's own independence/robustness bar for that
  decision, applied separately once a family reaches CANDIDATE here).

No machine learning anywhere in this scan. No threshold in this document
may be tuned after results are seen — a family that scores KILL under
these exact thresholds is dead under this protocol; re-testing it with
adjusted thresholds is a new, separately-named protocol (V2), not an edit
to this file, matching this project's existing rule against re-testing
NO_EDGE_DEFINITIF findings.
