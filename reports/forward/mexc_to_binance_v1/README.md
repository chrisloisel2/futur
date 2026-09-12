# MEXC_TO_BINANCE_V1 — forward collection store

Written by `mechanisms/mexc_to_binance_migration_v1/forward_collect.py` (timer `futur-mexc-forward-collect.timer`, every 6 h) for
Binance USDⓈ-M perpetual launches **after the seal** only. Files: `REGISTRY.json` (per-launch step statuses), `UNIVERSE.json`,
`MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json`, `CAUSAL_MATRIX.json`, `CAPACITY_FEATURES.json`, `WASH.json` (the harness's forward inputs),
`forward_collect.log.jsonl` (append-only), `collector.log`. Regenerated, therefore gitignored; the harness records each file's sha256
in the LOOK_LEDGER entry at the look. No return is read here. `first_look.py --status` reports `SEALED_NOT_TESTED` and the eligible count.
