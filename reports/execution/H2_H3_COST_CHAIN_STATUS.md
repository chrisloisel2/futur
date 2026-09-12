# H2 / H3 COST CHAIN STATUS (2026-09-12T14:45:56 UTC)

A cost chain has three links — fee, spread, slippage — and is worth its weakest. `confirmed` and `contradicted` require every link measured (`account_actual`) or officially published; a chain that rests on a declared number is `unknown`. `unknown` is an honest state, not a defect.

| hypothesis | spec declared round trip | chain round trip | fee | spread | slippage | weakest | status |
|---|---|---|---|---|---|---|---|
| H2 | 24.0 bps | 24.0 bps | `official_published` | `declared` | `declared` | `declared` | **unknown** |
| H3 | 18.0 bps | 18.0 bps | `official_published` | `declared` | `declared` | `declared` | **unknown** |

## What would move each chain

- fee → `account_actual`: a read-only key and one `GET /fapi/v1/commissionRate` per symbol.
- spread, slippage → measured: P11 `depth_capacity_features` derives an effective-spread proxy and slippage bounds from the Vision archives; they enter the chain as `official_published`-grade links once a preregistration names which window it uses.
- The official fee may serve to **reject** a hypothesis whose gross is below 3 × the published cost. It may never serve to promote one.

