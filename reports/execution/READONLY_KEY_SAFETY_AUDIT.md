# READ-ONLY KEY SAFETY AUDIT (2026-09-12T19:28:23 UTC)

What `binance_account_readonly.py` can and cannot do, verified by tests on its own source, not by promise.

| property | how it is enforced | test |
|---|---|---|
| no order, cancel or transfer code path | only GET is implemented; the source contains no POST / DELETE / PUT and no order endpoint (checked on the AST with docstrings removed) | `test_the_module_contains_no_order_path` |
| whitelist | 9 endpoints; anything else raises `ReadOnlyViolation` before a URL is built | `test_any_endpoint_outside_the_whitelist_raises_before_a_request` |
| the seven prescribed account endpoints | `GET /fapi/v1/commissionRate`, `GET /fapi/v1/leverageBracket`, `GET /fapi/v2/account`, `GET /api/v3/account`, `GET /fapi/v1/income`, `GET /fapi/v1/userTrades`, `GET /sapi/v1/margin/allPairs` | `test_whitelist_is_exactly_the_prescribed_set_plus_two_reads` |
| two extra reads, justified | `GET /fapi/v1/exchangeInfo` is public and unsigned (symbol constraints); `GET /sapi/v1/account/apiRestrictions` is the **safety probe** that tells us what the key may do — without it a trading key could not be refused | same test |
| trading / withdrawal / transfer key refused | `check_permissions` reads the probe first; any of enableSpotAndMarginTrading, enableFutures, enableMargin, enableWithdrawals, enableInternalTransfer, permitsUniversalTransfer, enableVanillaOptions, enablePortfolioMarginTrading set → refused, and every later signed call returns `refused` | `test_a_key_that_can_trade_is_refused`, `test_withdrawal_and_transfer_keys_are_refused` |
| account flags are informational | `canWithdraw` / `canTrade` are account capabilities, true on any normal account whatever the key: recorded, never a refusal | `test_account_flags_are_cross_checked` |
| deny-by-default permissions | any `enable*` / `permits*` flag that is true and is not a read permission refuses the key (enableFixApiTrade, future flags) | `test_unknown_permission_flags_refuse_by_default` |
| structural gate | an account endpoint cannot be called before a successful apiRestrictions probe on the same client | `test_account_endpoint_before_probe_is_refused` |
| no redirect | the opener refuses every 3xx: the API-key header is never re-sent to another host | `test_redirects_are_not_followed` |
| sanitised errors | Binance error text loses any IP address before it reaches a report (`-2015` carries the caller IP) | `test_error_messages_lose_ip_addresses` |
| half-set credentials | `BINANCE_READONLY_API_KEY` without its secret (or the reverse) is an error, never a fallback to the legacy pair | `test_half_set_readonly_pair_never_falls_back` |
| no credentials → inert | signed endpoints return `no_credentials` without any request | `test_signed_endpoints_are_inert_without_credentials` |
| secrets never logged, written or returned | credentials live only in the client instance; reports carry a redacted form (`abc…yz (n chars)`) | `test_credentials_are_never_exposed` |
| raw account data | written under `data/account_execution/` which is gitignored; nothing account-specific is versioned | `.gitignore` `data/*` |

Credentials at audit time: absent (<absent>). Mode: **no_credentials**.

