# execution_engine/

Empty on purpose, and not wired to anything.

Real orders become possible only after a mechanism reaches
`LIVE_MICRO_ELIGIBLE`, which requires, without exception:

- thirty days of paper live, minimum;
- at least three hundred independent episodes;
- a positive net edge that stays positive at double the modelled cost;
- a profit factor of at least 1.25;
- an acceptable paper drawdown;
- zero data faults and zero impossible fills in the journal;
- measured latency compatible with the horizon;
- a complete journal;
- a kill switch that has been tested.

`research_kernel.verdict.Verdict` refuses to be constructed with that status
unless the first four are satisfied, so the check is not a convention someone
can forget.

When that day comes, this directory takes four files and nothing else:

| file | responsibility |
| --- | --- |
| `exchange_adapter.py` | one venue, one account, no abstraction layer over several |
| `order_router.py` | place, amend, cancel; idempotent by client order id |
| `reconciliation.py` | exchange state against journal state, every cycle |
| `kill_switch.py` | flat and stop, on any of the triggers below |

The first live run is symbolic in size, unleveraged, with a fixed maximum daily
loss, and it stops automatically after two consecutive losses, after any
reconciliation mismatch, and whenever data latency exceeds the mechanism's
budget.

Until then this directory stays empty, and that is the correct state.
