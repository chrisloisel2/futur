# Research kernel scoreboard

Every verdict the kernel has produced. Generated from
`reports/research_kernel/run_ledger.jsonl`, which is hash-chained: an entry
removed or edited breaks verification.

Last updated: 2026-09-10.

## Verdicts

| mechanism | family | status | gross bps | cost bps | net bps | t on gross | family bar | independent episodes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lsr_globacct_x_v2` | crowd_positioning | COST_WALL | +9.60 | 16.00 | -6.40 | 2.04 | 3.25 | 848 |
| `microstructure_imbalance_v1` | microstructure | COST_WALL | +0.78 | 12.55 | -11.77 | 4.30 | 1.64 | 232 |
| `cross_exchange_dislocation_v1` | cross_exchange | COST_WALL | +1.97 | 24.00 | -22.03 | 65.0 | 1.64 | 934 |

Nothing has reached `PROMISING_NEEDS_FORWARD`. No forward window is sealed. No
mechanism is `PAPER_ELIGIBLE`, so the paper engine trades nothing, which is the
correct state and not a fault.

## The one number that decides all three

| mechanism | break-even cost | three-times-wall cost | assumed cost |
| --- | --- | --- | --- |
| `lsr_globacct_x_v2` | 9.60 | 3.20 | 16.00 |
| `microstructure_imbalance_v1` | 0.78 | 0.26 | 12.55 |
| `cross_exchange_dislocation_v1` | 1.97 | 0.66 | 24.00 |

Two of the three signals are statistically strong and economically absent. The
imbalance drift carries a t of 4.3 and the cross-venue reversion a t of 65, and
both are more than an order of magnitude below their own execution cost. This is
the failure mode the kernel exists to catch, and it caught it at gate 2, before
any Sharpe ratio was computed or looked at.

## Distance from viability

| mechanism | factor between cost and break-even |
| --- | --- |
| `lsr_globacct_x_v2` | 1.7x |
| `cross_exchange_dislocation_v1` | 12.2x |
| `microstructure_imbalance_v1` | 16.0x |

Only the first is within reach of an execution change, and reaching it means a
new hypothesis with a maker fill rate that has been measured, not assumed.

## What is undecidable rather than negative

`lsr_globacct_x_v2` carries a gross of 9.6 basis points with a standard error of
4.7 against a family bar of 3.25, so the smallest edge this sample can resolve
is 15.3 basis points. Even at zero cost, this design cannot separate 9.6 from
noise at the bar the `crowd_positioning` family has already paid for. It is not
a dead mechanism; it is an unanswerable question at this sample size.

## Multiplicity ledger

| family | sealed trials | threshold |
| --- | --- | --- |
| crowd_positioning | 88 | 3.25 |
| microstructure | 1 | 1.64 |
| cross_exchange | 1 | 1.64 |

`crowd_positioning` is seeded with 87 trials for the sweeps this repository ran
before the kernel existed, and its whole history from 2020-09-01 to 2026-09-10
is recorded as burned. The sealer will physically refuse a forward window
overlapping that period.

## What this scoreboard does not say

It does not rank mechanisms by profitability, because none is profitable. It
does not carry a Sharpe ratio in the summary, because a Sharpe on a
cost-negative mechanism is a number that invites the wrong conversation.
