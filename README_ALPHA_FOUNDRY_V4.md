# Alpha Foundry V4

This branch introduces the architecture foundation for a multi-mechanism alpha research factory.

Quick checks:

```bash
python3 -m pytest tests/unit/test_alpha_foundry_v4.py -v
python3 scripts/alpha_foundry_v4_manifest.py
```

The full research contract is documented in `reports/ALPHA_FOUNDRY_V4_PROTOCOL.md`.

The currently running Phase 5.2 confirmation remains isolated and must not be reused for V4 discovery before its locked verdict is sealed.
