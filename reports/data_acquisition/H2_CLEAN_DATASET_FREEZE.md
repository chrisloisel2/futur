# H2 CLEAN DATASET FREEZE — P10 (2026-09-11T23:15:17 UTC)

Freeze `4f077025c30f6bfd…` at commit `1f55ec3f03b2`. Assembled from what P6–P9 wrote; no download, no price joined, no return computed, no alpha verdict, no budget consumed. `capital_deployable` remains **false**.

## Decision

**NO_TEST** — 0 clean events against a threshold of 80.

Blockers, in the order they must be cleared:

1. only 0 clean events (threshold 80)
2. execution cost is unknown: no read-only API key, so the fee actually charged on this account was never read

If the two liftable gaps were closed, **129** events would be clean, distributed as {'OTHER_VENUE_FIRST': 123, 'TRUE_BINANCE_PERP_FIRST': 6}.

## Inputs

| input | state |
|---|---|
| Vision window manifests (P6) | 174 |
| announcement bodies (P7) | 230 |
| execution report (P8) | present; actual fee known: **False** |
| cross-venue precedence (P9) | 174 assets |

## Classification

| class | events | kind |
|---|---|---|
| `BAD_TIMESTAMP` | 8 | blocking |
| `PROVIDER_NEEDED` | 7 | blocking |
| `INSUFFICIENT_EXECUTION_DATA` | 159 | blocking |

Eligible: **0**. Blocked: 174. Dominant blocker: `INSUFFICIENT_EXECUTION_DATA`.

## What is frozen

- eligible event list (0 ids)
- excluded list with a reason per event (167)
- provider-needed list (7)
- per event: the sha256 of its Vision window manifest and of its announcement body payload
- `no_alpha_test: true`, `no_return_computed: true`, `capital_deployable: false`

