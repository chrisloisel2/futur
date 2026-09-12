# forced-flow first look — verdicts (un seul regard)

ledger seq 9 (branch-local 8, re-chained at merge) · threshold_t(2) = 1.9600 · tape b78d1b509b9ceddf

| H | mechanism | n | clusters | gross | median | t | cost wall | verdict | reasons |
|---|---|---|---|---|---|---|---|---|---|
| H1 | forced_liquidation_reaction_v1 | 368 | 51 | -7.0 | -0.8 | -1.45 | 44 | **REJECTED_NO_GROSS** | gross -7.0 < 10.0 |
| H2 | forced_liquidation_exhaustion_v1 | 62 | 15 | 13.8 | 10.2 | 1.18 | 43 | **REJECTED_COST_WALL** | gross 13.8 < 3.0 x cost 14.4 |
