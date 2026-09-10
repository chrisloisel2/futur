# PROJECT TRUTH — 2026-09-10

**Validated sleeves: 0.** Tradable edges: 0. Live edges: 0. Capital deployable: **false.**

No candidate in the history of this repository has passed all of: internal gates,
sealed window at its family threshold, forward window, costs, robustness,
multiplicity, executable path. Several passed some. None passed all.

## The only surviving hypothesis — and its three statuses, which are not one

Crowd positioning on Binance USDT-M perps (`count_long_short_ratio`, contrarian).
Three *rules* exist for this one *mechanism*, and they do not share a verdict:

| rule | where | verdict |
|---|---|---|
| sweep rule `lsr_globacct_x \| mkt \| h3 k8 hd1` (top-120, close→close, 3-day) | `tools/alpha_sweep_v4.py`, `reports/loop/` | **INDECIDABLE** — in-sample t 4.06, sealed-window t 2.399 against an operative two-sided bar of 2.955 (n=16, harness re-chose the side: defect I13). Passes cribles 5/6/7 (286.6 effective episodes in the top decile). Arithmetic Sharpe 2.43 IS / **1.66 OOS**; half of it is momentum. |
| kernel rule `lsr_globacct_x_v2` (top-100, 15v15, open→close, 2-day step) | `mechanisms/lsr_globacct_x_v2/` | **COST_WALL** — gross 9.60 bps < 3 × cost 16.00 bps. Family `crowd_positioning`, 88 trials, threshold 3.254. Data 2021-12-02 → 2026-08-12. |
| forward rule `FORWARD_CROWD_POSITIONING_V1` (deciles, daily, capacity universe) | `reports/loop/prereg/`, orphan branch `prereg/forward-crowd-positioning-v1` @ `0de75a9` | **SEALED_FORWARD_ACTIVE** — n=1, one-sided threshold 1.6449, window from 2026-09-09, **one look, not before 2028-12-06** (819 usable days). Code pinned (tag `prereg-forward-v1-code`). NO_LIVE. |

Status of the mechanism as a whole: **INDECIDABLE / FORWARD_WATCH_ONLY / NO_LIVE.**
It is not tradable and it is not validated. Nobody looks at the forward window before the date.

## Dead / rejected (measured, not assumed)

| family | how it died |
|---|---|
| basis perp/spot | 16 signals, payers written first: best t 2.49 < placebo median 2.81; best one is ρ 0.69 with positioning |
| CME segmentation | `CME_SEGMENTATION_V1`, preregistered n=2, one look: t_net 1.577 < 1.960; gross would pass, net was the criterion; secondary prediction (post-ETF compression) consistent; DD −30 % > 25 % |
| Bitfinex margin funding | examined under the sealed selection rule, refused at condition 3: same payer as perp funding |
| funding (level, cross-venue spread, term structure) | measured in all three forms; nothing |
| amihud / illiquidity | is the cost model (ρ 0.964 with `symbol_cost_bps`); capacity-blocked; `roll_spread` is an alphabetical list |
| cross-exchange dislocation | `cross_exchange_dislocation_v1`: **COST_WALL**, gross 1.97 bps < 3 × 24.00 |
| microstructure imbalance | `microstructure_imbalance_v1`: **COST_WALL**, gross 0.78 bps < 3 × 12.55 |
| liquidation cascades | inexecutable by architecture: 45–48 h data lag against a 4 h horizon |
| SHORT | SHORT_REJECTED, three approaches |
| event scanner | 4/4 KILL |
| hunt rounds 1–4 | ~700 mechanisms, 0 validated |
| options VRP, stablecoin regime | measured before this rebuild, never promoted |
| `close_location_20` | the best number the project ever produced (t 3.28) was a 50-symbol artefact: sign flips on 696 symbols |

## What bounds everything

- The library's 138 effective trials (rank-content dedup) match the placebo's implied N to
  the unit (median max|t| 2.8069 ⟹ N = 138). Nothing ever exceeded that noise median.
- Maximum of the book: arithmetic Sharpe **1.66** out of sample. Identity found twice here:
  *confirmable in N years ⟺ Sharpe ≥ 5.60/√N.* The historical shortcut ("one look on a
  never-loaded source") was tried honestly on the only class where it was valid (CME) and
  failed. The calendar is the forward calendar.
- The counts 16 (sealed reads) and 138 are **reconstructions from artefacts** — lower
  bounds. From 2026-09-09 the look ledger is written *before* the look (`tools/look_ledger.py`,
  hash-chained), so the count is exact going forward, never retroactively.

## Two ledgers, one truth

| ledger | governs | file |
|---|---|---|
| kernel multiplicity ledger | trials per **family** on historical data; burned periods; family threshold | `reports/research_kernel/multiplicity_ledger.json` |
| look ledger | every **sealed/forward look**, with the external witness (orphan branch pushed alone) | `reports/loop/LOOK_LEDGER.jsonl` |

They agree on doctrine (multiplicity applies to tests on the same data; a fresh window
restarts the count) and on the crowd-positioning burn. One declared overlap: the kernel
burns the family through 2026-09-10; the forward seal starts 2026-09-09. Two days of 819,
for which no return was computable at seal time. Declared, not re-sealed.

**Operational rule for 2028-12-06:** the single look on `FORWARD_CROWD_POSITIONING_V1` is
recorded in **both** ledgers — `tools/look_ledger.py` (before the computation, witness
required) and `MultiplicityLedger.record_trial('crowd_positioning', ...)` with the forward
window as the data it was run on. One look, two records, one truth.

## Current project asset

Validation infrastructure (`research_kernel/`, 200 tests, gates G0–G8), the look ledger
with external witnesses, the defect history (21 instrument defects closed, four of which
produced false numbers), the falsification system, and a positioning hypothesis with one
clean chance in 2028.

## Current project missing

A structural edge. An executable alpha. A deployable sleeve.

Until that changes: **no live, no leverage, no ML, no dashboard, no optimisation.**
The question is no longer "which signal works" but "which structural advantage exists
before costs, survives costs, placebo, multiplicity, then the forward."
