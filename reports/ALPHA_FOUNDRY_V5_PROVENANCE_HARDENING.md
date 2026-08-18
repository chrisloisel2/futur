# Alpha Foundry V5 — Provenance and readiness hardening

The first multimodal 6h readiness produced A1-A8 as data-ready, with 648003 rows, 1322 columns, zero future-clock violations and zero duplicate keys. That run also exposed two control-plane weaknesses that are now fail-closed.

## 1. Feature provenance is distinct from clock sanity

Auditing a set of `*_available_ts_ns` columns only proves that the clocks which exist are not from the future. It does not prove that every research feature has a declared causal origin.

V5 therefore adds `FEATURE_PROVENANCE.json`:

- every base-state feature is declared as inherited from the separately validated causal Market Physics tape;
- event/trade features are declared as receive-time replay products;
- derivative features are declared as receive-time replay products;
- cross-plane features declare both book and derivative dependencies;
- unknown derived columns fail provenance sealing;
- discovery refuses to run without a clean provenance manifest;
- the provenance manifest digest is written into experiment summaries and artifact seals.

Readiness proof levels are now:

- `FAILED`
- `STRUCTURAL_ONLY`
- `CLOCKS_AUDITED_NO_FEATURE_PROVENANCE`
- `FEATURE_PROVENANCE_FAILED`
- `FULL_FEATURE_PROVENANCE`

## 2. Readiness pattern false positives are blocked

The original A14 option pattern `*iv_*` could match `deriv__...` columns. A14 now accepts only the explicit `option__` namespace, and its feature materializer is isolated to that namespace.

Additional hard prerequisites:

- A3 requires actual trade activity in addition to book depletion observables;
- A7 requires depth/capacity in addition to liquidation and OI;
- A8 requires fair-value price in addition to OI and funding/basis state.

## Boundary

`DATA READY` remains only a statement that the causal prerequisites for a mechanism are present. It is not statistical evidence and it is not an alpha verdict. No A1-A8 discovery run should begin until the existing tensor is provenance-sealed and the new readiness audit reports `FULL_FEATURE_PROVENANCE`.
