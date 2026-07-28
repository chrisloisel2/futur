"""Zone explicite legacy, séparée de la prod et de la recherche.

Archive-only: not importable by design. Historical artifacts stay on disk
for reference, but nothing in src/, scripts/, or research/ may depend on
this package's code. See docs/FOUNDATION_AUDIT.md.
"""
raise ImportError(
    "legacy/ is a non-importable archive (Phase 2 rebuild rule: "
    "'aucun import depuis legacy/'). If you need something here, copy the "
    "specific logic into src/futur/ with its own tests -- do not import "
    "this package or restore its importability."
)
