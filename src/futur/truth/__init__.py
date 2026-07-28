"""futur.truth -- canonical accounting/event engine (Phase 4).

Scope (see docs/TRUTH_ACCOUNTING.md): domain models, append-only ledger,
spot/perp accounting, margin/liquidation, invariants, deterministic replay,
reconciliation. Explicitly excludes strategies, signals, features, ML,
exchange connectivity, and any real historical data.

Must never import from src.alpha20, src.institutional, legacy,
frontend_pipeline, or the second, divergent runtime copy under the repo's
hyphenated trading·system directory -- enforced by
tests/architecture/test_no_forbidden_imports_from_src.py.
"""
