# Risk Controller (Stress + Ruin-Aware)

Pipeline: exposures -> correlation -> VaR/CVaR -> fractional Kelly -> scenario engine -> killswitch -> orders plan builder. Applies portfolio caps without mixing book budgets.

Outputs: RiskState (var/cvar, cluster exposure, book risk snapshots, scenario results, caps/actions) and OrdersPlan (order intents + stops/time-stops with risk tags). Stored to Mongo caches and optionally S3 parquet under artifacts/risk/.

Causality: uses current TargetPositions, PortfolioState, BooksState, and States only; no future info. Scenarios apply configured shocks. Kill switch triggers flatten/freeze logic.
