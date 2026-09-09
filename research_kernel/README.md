# research_kernel

A machine that turns a market hypothesis into a falsifiable verdict.

It is not a backtester and it does not trade. It takes one preregistered rule,
applies costs before anything else, measures how many of its events are actually
independent, compares the result to the null its own design generates, raises
the significance bar by the number of hypotheses its family has already spent,
and refuses to promote anything that has not survived a sealed forward window.

> The project must stop producing backtests. It must produce verdicts.

## The chain

```
source of information
  -> economic mechanism
  -> preregistered rule
  -> realistic cost
  -> clean backtest
  -> placebo
  -> multiplicity
  -> sealed forward
  -> paper live
  -> micro-cap live
  -> scaling
```

Until that chain is complete for a given mechanism: no real capital, no
leverage, no automated execution.

## Running one mechanism

```bash
python3 -m research_kernel.run_mechanism mechanisms/lsr_globacct_x_v2/spec.json
```

Out come `verdict.md`, `verdict.json`, `metrics.json`, `decisions.parquet`,
`trades.parquet`, `placebo.json`, `cost_sensitivity.json` and
`data_quality_report.json`, plus one line in the run ledger.

## The gates, in the order they fire

| gate | question | failure status |
| --- | --- | --- |
| 0 | is the data real, point-in-time, gap-free and timestamped? | `DATA_BROKEN` |
| 1 | is there a mechanism, and does the data arrive in time? | `REJECTED` |
| 2 | is the gross edge at least three times the cost? | `COST_WALL` |
| 3 | is it robust across years, symbols and drawdown? | `REJECTED` |
| 4 | does it beat the null its own design generates? | `OVERFIT` |
| 5 | does it clear the bar its family's trial count implies? | `OVERFIT` |
| 6 | is there a sealed forward window? | `PROMISING_NEEDS_FORWARD` |

The order is deliberate. Cost comes before robustness, so a mechanism under the
cost floor dies before anyone has seen a Sharpe ratio and formed an opinion.

## The statuses

`REJECTED`, `DATA_BROKEN`, `COST_WALL`, `OVERFIT`, `PROMISING_NEEDS_FORWARD`,
`SEALED_FORWARD_ACTIVE`, `FORWARD_FAILED`, `PAPER_ELIGIBLE`,
`LIVE_MICRO_ELIGIBLE`.

There is deliberately no `VALIDATED`. `Verdict` raises rather than be built in a
promoted state without a forward seal, a forward result, a positive edge at
double cost and enough independent episodes, so the rule is mechanical and not a
convention.

## Five things this kernel does that a backtester does not

**It computes the t on the gross series.** With a constant cost,
`SE(net) = SE(gross)`, so as the gross tends to zero `|t_net|` tends to
`cost * sqrt(n) / sigma` and grows without bound in `n`. A mechanism with no
edge at all becomes "highly significant" in the direction of its cost simply by
having many episodes. This is not hypothetical: a cross-sectional reversal was
published in this repository at "-27.5 bps, t = -5.89, the most significant
result of the sweep", on a gross of +0.5 bps whose own t was +0.107. Four of the
thirty-one rows of that table were the same artefact. `metrics.json` reports the
net t as `t_stat_net_artefact`, labelled as a diagnostic.

**It makes you choose how to count episodes, in advance.** The naive rule chains
each observation to the previous one, so one symbol sampled every three hours
for ten days becomes **one** episode, and a daily cross-sectional book over six
years also becomes **one**. Moving the window from 24h to 23h changes the sample
size by a factor of 2190. `spec.declustering_rule.method` must name
`first_link`, `complete_link` or `fixed_windows` before any result is seen.

**It derives the significance threshold instead of storing it.**
`threshold_t(n)` reads how many hypotheses the family has sealed and returns the
one-sided Bonferroni bar: 1.64 at one hypothesis, 2.33 at five, 3.10 at
fifty-two, 3.80 at seven hundred. Sealing five more raises the bar on the first
five, mechanically. A threshold written in a file is a threshold someone will
edit on a disappointing evening. Across twenty-seven configurations of **random**
baskets, one reached t = 2.77.

**It compares the result to its own null, not to zero.** The placebo battery
generates the distribution this exact design produces when there is nothing
there, and reports the interval. A design that swings plus or minus nine basis
points on random baskets has shown nothing at plus five.

**It applies a latency budget of horizon over four.** A four-hour mechanism
tolerates one hour of data lag; a sixty-second one tolerates fifteen seconds.
This gate exists because a whole family of mechanisms here was found to be
unexecutable by forty-five hours after its historical numbers were already
believed.

## The modules

| module | responsibility |
| --- | --- |
| `mechanism_spec.py` | the preregistration, its hash, and what it refuses |
| `data_contracts.py` | gate 0, manifests, the latency rule |
| `cost_model.py` | round trips, the cost wall, breakeven capture |
| `declustering.py` | how many independent events there really are |
| `validation.py` | every metric, with the t on the gross |
| `placebo.py` | five nulls, failing closed when one cannot run |
| `multiplicity.py` | the derived threshold, the family ledger, contamination |
| `forward_seal.py` | gate 6, and what voids a seal |
| `verdict.py` | the statuses and the promotion guards |
| `report.py` | metrics.json, the parquets, verdict.md |
| `ledger.py` | the hash-chained record of every run |
| `run_mechanism.py` | the pipeline and the CLI |

## Rules

**No legacy imports.** Nothing here may import `ai/`, `trading-system/`,
`frontend_pipeline/`, `signals/`, `scrapers/`, `legacy/`, `core/`, `risk/` or
`src/futur`. Reading a data file those systems produced is allowed and is the
only permitted contact. Enforced by
`research_kernel/tests/test_no_legacy_imports.py`, which parses every P0 file.

**One mechanism, one directory, one `spec.json`.** No mechanism tested verbally,
no threshold changed by hand, no search without a trace.

**No machine learning before a raw edge.** A model does not create an edge, it
extracts one. Until a raw source shows `gross_edge > 3 x realistic cost`, a
model fits noise.

**Costs first.** Every test reports `gross_edge_bps`, `fees_bps`, `spread_bps`,
`slippage_bps`, `adverse_selection_bps`, `net_edge_bps` and
`net_edge_bps_cost_x2`.

**A sealed forward or nothing.** Promising on history is a hypothesis worth
sealing, never an edge.

## Tests

```bash
python3 -m pytest research_kernel/tests -q
```

They are not decoration. They pin the declustering trap with its measured table,
the derived threshold at five known values, the refusal of a parameter range,
the refusal of a promoted verdict without a seal, the failure-closed placebo, and
the order in which the gates fire.
