# ACCOUNT EXECUTION REALITY — P8 (2026-09-12T20:45:45 UTC)

What execution actually costs on this account, as opposed to what a spec declared. Read-only: the module implements GET and nothing else, calls only a whitelisted set of endpoints, and refuses a key that carries trading permission. No order, no signal, no verdict, no budget. Credentials come from the environment and are never written, logged or returned.

**Credentials: present** (0jJ…tp (64 chars)).


## The six questions

| # | question | answer |
|---|---|---|
| 1 | actual futures taker fee | **unknown** — no usable key |
| 2 | actual futures maker fee | **unknown** — no usable key |
| 3 | funding payments available | error |
| 4 | leverage brackets available | error |
| 5 | H2 / H3 cost assumptions | {'H2': 'confirmed', 'H3': 'unknown'} |
| 6 | what remains theoretical | see below |

### 6. What remains theoretical

- actual futures fee (account_actual): Binance serves every signed /fapi endpoint, reads included, only to a key with 'Enable Futures' -- a trading permission. There is no futures-read-only key: a read-only key gets -2015 on /fapi and the futures fee cannot be read as account_actual without accepting a trading-capable key (IP-restricted, --allow-trading-key, journaled).
- the key has NO IP restriction (ipRestrict=false): restrict it to this machine before any further use
- spread and slippage in both cost chains are the values declared in the specs; P11 measured them per window (see 9_cost_chain_by_window): the chain leaves 'unknown' only once a preregistration names the window and notional
- borrow availability and borrow cost for spot delisting shorts (sapi margin endpoints, key required)

## Cost chains

A cost chain is worth its weakest link. `confirmed` and `contradicted` are only possible when every link is measured or officially published; otherwise the answer is `unknown`, which is not a failure — it is the correct statement about a number nobody measured.

| hypothesis | spec declared round trip | chain round trip | weakest provenance | status |
|---|---|---|---|---|
| H2 | 24.0 bps | 23.7 bps | `official_published` | **confirmed** |
| H3 | 18.0 bps | 18.0 bps | `declared` | **unknown** |

Fee link: 5.00 bps per side, provenance `official_published` (published VIP0, official, as of 2026-09-10).

## Endpoints

| name | endpoint | status | what it would give |
|---|---|---|---|
| commission_rate | `GET /fapi/v1/commissionRate` | error | actual maker/taker fee for one futures symbol |
| leverage_brackets | `GET /fapi/v1/leverageBracket` | error | leverage brackets and maintenance margin |
| futures_account | `GET /fapi/v2/account` | error | futures account metadata: fee tier, canTrade / canWithdraw flags |
| spot_account | `GET /api/v3/account` | ok | spot commission rates, canTrade / canWithdraw flags |
| funding_income | `GET /fapi/v1/income` | error | own income rows (funding fees, commissions) |
| own_fills | `GET /fapi/v1/userTrades` | error | own fills |
| margin_pairs | `GET /sapi/v1/margin/allPairs` | ok | margin pairs (is the asset borrowable?) |

## Safety properties of this module

- Only GET exists in `binance_account_readonly.py`; there is no order, cancel or transfer code path to disable.
- Every call goes through a whitelist of ten read endpoints; anything else raises before a request is built.
- A key granting any of enableSpotAndMarginTrading, enableFutures, enableMargin, enableWithdrawals, enableInternalTransfer, permitsUniversalTransfer, enableVanillaOptions, enablePortfolioMarginTrading is refused unless the caller passes `--allow-trading-key`, and the refusal is reported.
- Raw account responses are written under `data/account_execution/` (gitignored). Nothing account-specific is versioned.

