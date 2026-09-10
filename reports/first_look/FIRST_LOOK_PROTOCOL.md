# First Look Protocol — P2 Edge Data

## Scope

No price join has been performed yet. Datasets eligible for first look:

1. `official_event_tape` — `data_lake/events/official_event_tape.jsonl`
2. `forced_flow_liquidations` — `data_lake/events/liquidations/` (live partitions)

## Rule

A first look is allowed only after the dataset snapshot is **frozen and hashed**, and only
through a **sealed preregistration** (orphan branch pushed alone, `tools/look_ledger.py
--seal-orphan`, recorded in `reports/loop/LOOK_LEDGER.jsonl` *before* any computation).

    collect → preregister → freeze snapshot → look once

Never: collect → look → adjust → believe.

## Snapshot freeze

`python3 -m data_lake.collectors.first_look_gate --freeze <dataset> <name>` writes
`data_lake/first_look/<name>/SNAPSHOT_MANIFEST.json` with: input dataset path, row count,
min/max event timestamp, sha256 of the frozen input file, git commit sha, working-tree state.
It **refuses** to freeze while the gate is closed. The fields *exact hypothesis, exact horizons,
exact cost model, exact promotion / rejection criteria* are left empty in the manifest and may
only be filled by the sealed preregistration that references it.

## Gate — forced flow (`--status`)

First look allowed when, on the live tape:

- `n_events >= 300`
- `enriched_ratio >= 95 %` (spread / imbalance *before* the event present)
- `bbo_age >= 0` **everywhere** (and `mark_age >= 0`): the "before" state precedes the event
- `stream_delay` measured (Binance throttle ≈ 1 s, visible in every record)
- no post-event book contamination (all return fields still `null`; no negative age)
- snapshot hashed (freeze)

Reason in **valid enriched events**, not in calendar time: at the first window's rate
(≈ 51 events / 10 min) the count is reached in about an hour.

## Event tape first look

Allowed hypotheses (each is one mechanism spec, family `news`):

1. Listing continuation
2. Listing mean reversion
3. Delisting forced pressure
4. Cross-venue lag after official publication

Allowed horizons: 1 min · 5 min · 15 min · 1 h · 6 h · 24 h — declared as one primary horizon
plus at most two sensitivities per spec (kernel rule); the horizon is **not** chosen after
seeing results.

Minimum gross threshold: **30 bps before costs**. No optimization allowed.

## Forced liquidation first look

Allowed hypotheses (family `liquidation`):

1. Cascade continuation after a large liquidation
2. Exhaustion reversal after an extreme liquidation
3. Cross-venue lag after a liquidation burst

Allowed horizons: 5 s · 30 s · 5 min · 30 min (same primary + ≤ 2 sensitivities rule).

Minimum gross threshold: **10 bps before costs**.

Trigger threshold: at least 300 liquidation events; event enrichment must have `bbo_age >= 0`;
no post-event book contamination. No optimization allowed.

## Multiplicity

Every sealed hypothesis raises the family bar for all others
(`research_kernel.multiplicity.threshold_t`, one-sided Bonferroni). Four event hypotheses sealed
together are judged at `threshold_t(4)`, not at 1.64. Buckets (notional, side, symbol, venue,
spread / imbalance before) are **descriptive** in the first look, never selected on.

## Verdicts

Each hypothesis must produce exactly one of:

- `REJECTED_NO_GROSS` — gross below the minimum threshold
- `REJECTED_COST_WALL` — gross below 3 × measured cost (P1: 9.5–11.3 bps round trip at VIP0)
- `INDECIDABLE` — passes gross and cost but not the family threshold / placebo / robustness
- `FORWARD_SEAL_REQUIRED` — passes everything on the frozen snapshot; no promotion without a
  sealed forward window

A verdict is written once, into the mechanism's `results/verdict.md` and into both ledgers.
