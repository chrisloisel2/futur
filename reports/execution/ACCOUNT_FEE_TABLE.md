# ACCOUNT FEE TABLE — P8 (2026-09-12T20:35:15 UTC)

Every fee carries its provenance. `account_actual` is what this account is charged; `official_published` is the venue's schedule; `declared` is a number a spec wrote down. Only the first two can settle a cost-wall question.

| market | maker (bps) | taker (bps) | provenance | source |
|---|---|---|---|---|
| USDS-M perp (VIP0) | 2.0 | 5.0 | `official_published` | published_fee_schedules_2026-09-10.json (official) |
| H2 spec assumption | — | 5.0 | `declared` | mechanisms/event_listing_perp_fade_v1/spec.json |
| H3 spec assumption | — | 5.0 | `declared` | mechanisms/event_delisting_pressure_v1/spec.json |

Account fee tier: **unknown** (needs a usable key).

Until a read-only key exists, the strongest available figure is the published VIP0 schedule, and every cost wall computed from it inherits that provenance: strong enough to reject, not strong enough to promote.

