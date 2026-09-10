# event_listing_perp_fade_v1

RESEARCH_ONLY. H2 made executable: short the new Binance USDS-M perpetual from launch + 15 min to
launch + 6 h, on listings where the perpetual is the first Binance market actually tradable.

- Origin: regard seq 7 found the listing fade on spot (+202 bps, t 3.25) but 0 % executable (short
  on spot). This mechanism tests the same economic idea on **disjoint events** (145 assets priced in
  H1/H2 excluded) and a **different market** (the perpetual), with a real VIP0 short path.
- Universe frozen before any price: `reports/first_look/event_listing_perp_fade_v1_UNIVERSE.json`
  (metadata only: exchangeInfo.onboardDate, Vision file existence via HEAD, first-bar timestamps).
- Launch time = first 1-min bar of the perpetual on Binance Vision (evidence of trading), cross-checked
  with `onboardDate` (170/174 agree within 5 min; 4 older listings have a date-only onboardDate).
- Prereg: `reports/first_look/event_listing_perp_fade_v1_PREREG.md`; freeze: `..._FREEZE.json`.
- One look, then the verdict is written once to `results/verdict.json`.
