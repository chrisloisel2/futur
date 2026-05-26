# SHORT v2 Rebuild

Verdict policy:

- `live_allowed` is always `false` from this validation script.
- `paper_allowed` can only become `true` for `SHORT_V2_HEDGE_PAPER_CANDIDATE`.
- Standalone short is disabled by default; v2 is hedge-only.

Strict validation command:

```bash
.venv/bin/python scripts/walk_forward_short_v2.py --max-assets 50 --folds 2022 2023 2024 2025 2026
```

Materialize real liquidation columns first when MongoDB has
`trader.liquidation_events`:

```bash
.venv/bin/python scripts/materialize_short_v2_liquidations.py \
  --data-dir data/enriched \
  --out-dir data/enriched_short_v2

.venv/bin/python scripts/walk_forward_short_v2.py \
  --data-dir data/enriched_short_v2 \
  --max-assets 50 \
  --folds 2022 2023 2024 2025 2026
```

Research-only dry run when real liquidation columns are missing:

```bash
.venv/bin/python scripts/walk_forward_short_v2.py \
  --allow-liquidation-proxy \
  --no-require-liquidations
```

The proxy mode is not deployment-grade. It exists only to test the model,
labels and threshold plumbing until real liquidation flow is materialized.
