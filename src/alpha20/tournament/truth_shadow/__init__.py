"""src/alpha20/tournament/truth_shadow/ -- Phase 4C read-only shadow of
CarryBasisAdapter onto src.futur.truth's TruthEngine.

This package lives OUTSIDE src/futur/truth on purpose: the truth domain
must never import src.alpha20 or src.institutional back (enforced by
tests/architecture/test_truth_domain_has_no_alpha20_dependency.py). Only
this package, and code above it, may depend on both.

Nothing here is authoritative. The legacy runtime (CarryBasisAdapter /
MultiLegBacktester) remains the sole source of truth for decisions,
sizing, and results -- see docs/PHASE4C_CARRY_SHADOW.md.
"""
