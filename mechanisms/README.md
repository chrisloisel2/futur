# mechanisms/

One directory per hypothesis. Nothing is tested outside one of these.

```
mechanisms/<mechanism_id>/
├── spec.json            the preregistration, hashed into rules_hash
├── preregistration.md   the same thing in prose, written before the run
├── run.py               reads data, produces decisions; decides nothing else
├── results/
│   ├── verdict.md            the readable answer
│   ├── verdict.json          the machine answer
│   ├── metrics.json          every number behind it
│   ├── decisions.parquet     one row per decision
│   ├── trades.parquet        one row per trade
│   ├── placebo.json          the null this design generates
│   ├── cost_sensitivity.json net edge at 1x, 1.5x, 2x, 3x cost
│   └── data_quality_report.json
└── notes.md             running commentary, never a result
```

## Running one

```bash
python3 -m research_kernel.run_mechanism mechanisms/<id>/spec.json
```

The first run of a rule records a trial in its family's multiplicity ledger.
That is deliberate: looking at the data is a trial whether or not it is called a
discovery, and the significance threshold every mechanism in that family must
clear moves up accordingly.

## What a `run.py` may do

Read files, compute the rule, return decisions. It may not compute metrics,
apply costs, decide a status, or import anything from the frozen tree. Shared
readers live in `mechanisms/_common/` and hold no rule.

## The contract

```python
def build(spec: MechanismSpec, ctx: RunContext) -> MechanismRun
```

`MechanismRun.decisions` needs `timestamp`, `symbol`, `side`, `gross_bps`, with
`gross_bps` already signed by the side taken. Supply `panel` (`timestamp`,
`symbol`, `fwd_bps`) or the shuffle placebos cannot run, and a placebo that did
not run fails the gate.

Raise `DataUnavailable` when the data is not there. That produces a
`DATA_BROKEN` verdict, which is an honest answer, not a crash.

## Current inventory

| mechanism | family | status |
| --- | --- | --- |
| `lsr_globacct_x_v2` | crowd_positioning | see `results/verdict.md` |
| `microstructure_imbalance_v1` | microstructure | see `results/verdict.md` |
| `cross_exchange_dislocation_v1` | cross_exchange | see `results/verdict.md` |
