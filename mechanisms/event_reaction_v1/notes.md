# event_reaction_v1 — notes

RESEARCH_ONLY. Spec écrite avant toute jointure prix ; jamais exécutée ; aucun essai débité. Voir la tape correspondante et son readiness/catalogue.


## 2026-09-10 — first look preregistered (H1 of four)

The spec was rewritten to the sealed first-look definition: entry at the official publication timestamp + 60 s on the pre-existing Binance spot market, primary horizon 60 min, sensitivities 15 min / 6 h, benchmark BTCUSDT. The earlier draft (entry at trading start, direction = sign of announcement-day drift) was never run and is superseded. Three sibling mechanisms (`event_listing_reversal_v1`, `event_delisting_pressure_v1`, `event_cross_venue_lag_v1`) are sealed with it: family `news`, sub-family `official_event_reaction`, threshold_t(4) = 2.2414 one-sided. Harness: `first_look.py` (pinned in the FREEZE); synthetic positive control in `results/positive_control.json` (80 bps injected, 77.5–79.9 recovered, nulls rejected).
