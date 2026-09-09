# Seeding the multiplicity ledger

The threshold every mechanism must clear is derived from how many hypotheses its
family has already spent. A ledger that starts empty would therefore hand the
first mechanism of a family a bar of 1.64, as if this repository had never
looked at that family before. It has.

`crowd_positioning` was seeded on 2026-09-10 with two entries.

## The contamination record — charges no trial

Burned period: **2020-09-01 to 2026-09-10**, the whole span of the Binance
Vision metrics archive and of the live positioning archive.

Reason recorded: this repository has swept crowd positioning across that span,
including an iteration over 86 derived positioning signals. Multiplicity applies
to tests on the same data; looking at 2022-2025 does not inflate the
false-positive rate of a test run on 2027. What it inflates is **selection**, and
selection is paid by promoting few finalists on fresh data. So a contamination
charges nothing and instead burns the period: `seal_forward` refuses a window
that overlaps it.

## The trial seed — 87 trials

Recorded as `historical_sweep_crowd_positioning_v0`, 87 trials: the 86 derived
positioning signals of the second iteration, plus the one contrarian rule that
survived it. The threshold for the family moves from 1.64 to **3.25**.

That number is an accounting of what was actually tried, not a precise census of
every look ever taken. It is deliberately on the low side: a family that has
been swept harder than 87 times deserves a higher bar, not a lower one, and
raising it later is a one-line record that mechanically re-prices every seal in
the family through `retroactive_penalty()`.

## Reproducing it

```python
from research_kernel.multiplicity import MultiplicityLedger

led = MultiplicityLedger("reports/research_kernel/multiplicity_ledger.json")
led.record_contamination(
    family="crowd_positioning",
    burned_periods=[("2020-09-01", "2026-09-10")],
    reason="...",
    recorded_at="2026-09-10T00:00:00Z",
)
led.record_trial(
    family="crowd_positioning",
    mechanism_id="historical_sweep_crowd_positioning_v0",
    rules_hash="seed:crowd_positioning:86_derived_signals_plus_survivor",
    n_trials=87,
    recorded_at="2026-09-10T00:00:00Z",
    note="...",
)
```

## The other families

`microstructure` and `cross_exchange` are **not** seeded. This repository has
looked at liquidation cascades and execution economics, but not at
top-of-book imbalance or at Binance-against-OKX dislocation on this capture,
which did not exist before 2026-08-31. Their first mechanism therefore faces the
one-hypothesis bar of 1.64, correctly.

Before widening a search in any family, price the widening first:

```python
led.current_threshold("microstructure", extra=5)
```

It quotes what five more hypotheses would cost the five already sealed, before
the cost is paid.
