# event first look — verdicts (un seul regard)

ledger seq 7 · threshold_t(4) = 2.2414 · snapshot 6e957851e452395f

| H | mechanism | n | gross bps | t | cost wall | verdict | reasons |
|---|---|---|---|---|---|---|---|
| H1 | event_reaction_v1 | 142 | -2.5 | -0.04 | 84 | **REJECTED_NO_GROSS** | gross -2.5 < 30.0 |
| H2 | event_listing_reversal_v1 | 141 | 202.4 | 3.25 | 84 | **INDECIDABLE** | tradable_share 0.00 < 0.8 |
| H3 | event_delisting_pressure_v1 | 56 | 630.8 | 2.44 | 59 | **INDECIDABLE** | n_eff 21.6 < 30; top1_share 0.13 > 0.1 |
| H4 | event_cross_venue_lag_v1 | 272 | -0.3 | -0.03 | 59 | **REJECTED_NO_GROSS** | gross -0.3 < 30.0 |
