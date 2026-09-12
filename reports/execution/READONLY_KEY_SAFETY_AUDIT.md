# READ-ONLY KEY SAFETY AUDIT (2026-09-12T14:45:56 UTC)

What `binance_account_readonly.py` can and cannot do, verified by tests on its own source, not by promise.

| property | how it is enforced | test |
|---|---|---|
| no order, cancel or transfer code path | only GET is implemented; the source contains no POST / DELETE / PUT and no order endpoint (checked on the AST with docstrings removed) | `test_the_module_contains_no_order_path` |
| whitelist | 9 endpoints; anything else raises `ReadOnlyViolation` before a URL is built | `test_any_endpoint_outside_the_whitelist_raises_before_a_request` |
| the seven prescribed account endpoints | `GET /fapi/v1/commissionRate`, `GET /fapi/v1/leverageBracket`, `GET /fapi/v2/account`, `GET /api/v3/account`, `GET /fapi/v1/income`, `GET /fapi/v1/userTrades`, `GET /sapi/v1/margin/allPairs` | `test_whitelist_is_exactly_the_prescribed_set_plus_two_reads` |
| two extra reads, justified | `GET /fapi/v1/exchangeInfo` is public and unsigned (symbol constraints); `GET /sapi/v1/account/apiRestrictions` is the **safety probe** that tells us what the key may do — without it a trading key could not be refused | same test |
| trading / withdrawal / transfer key refused | `check_permissions` reads the probe first; any of enableSpotAndMarginTrading, enableFutures, enableMargin, enableWithdrawals, enableInternalTransfer, permitsUniversalTransfer, enableVanillaOptions, enablePortfolioMarginTrading set → refused, and every later signed call returns `refused` | `test_a_key_that_can_trade_is_refused`, `test_withdrawal_and_transfer_keys_are_refused` |
| second barrier on account flags | `canWithdraw` on the account payloads → refuse even if the probe was silent | `test_account_flags_are_cross_checked` |
| no credentials → inert | signed endpoints return `no_credentials` without any request | `test_signed_endpoints_are_inert_without_credentials` |
| secrets never logged, written or returned | credentials live only in the client instance; reports carry a redacted form (`abc…yz (n chars)`) | `test_credentials_are_never_exposed` |
| raw account data | written under `data/account_execution/` which is gitignored; nothing account-specific is versioned | `.gitignore` `data/*` |

Credentials at audit time: absent (<absent>). Mode: **no_credentials**.

