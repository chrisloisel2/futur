"""
src/institutional/engines/cross_sectional_momentum_live_v2/universe.py
─────────────────────────────────────────────────────────────────────────────
Dynamic PIT (point-in-time) candidate-universe resolution for
CROSS_SECTIONAL_MOMENTUM_LIVE_V2.

Unlike V1 (configs/portfolio_v1_1_parallel_50.yaml, a FIXED frozen-50 list),
this alpha's whole point is a broader, dynamically-eligible liquid-altcoin
universe: at each run, the CANDIDATE universe is derived directly from
Binance USDM futures' LIVE /fapi/v1/exchangeInfo -- every symbol currently
status=TRADING is a real, tradeable-today instrument, not a lookahead-biased
fixed list picked by a human ahead of time. This is exactly the "univers PIT
dynamique" the mission asked for: eligibility as of the query time, not a
snapshot frozen in the past.

Candidate filter (pure, deterministic, no guessing/heuristic matching):
  - contractType == "PERPETUAL"    excludes CURRENT_QUARTER / NEXT_QUARTER
                                    calendar-dated contracts and the exotic
                                    "TRADIFI_PERPETUAL" contract type also
                                    observed on this endpoint at build time
                                    (2026-09-01) -- not economically the same
                                    instrument as a standard crypto perp.
  - quoteAsset == "USDT"           USDT-margined only, per the mission's own
                                    framing ("USDT-margined perpetual
                                    symbols"); excludes BTC/USDC/U/USD1
                                    margined pairs also present on this
                                    endpoint.
  - status == "TRADING"            real listing eligibility, never
                                    PENDING_TRADING/SETTLING (128 SETTLING
                                    symbols observed at build time -- these
                                    are exactly the kind of "silently wrong"
                                    inclusion the mission warned about;
                                    excluded, same discipline as
                                    symbol_resolver.py).
  - underlyingType == "COIN"       Binance's USDM futures exchangeInfo at
                                    build time ALSO lists tokenized
                                    EQUITY / CN_EQUITY / HK_EQUITY / KR_EQUITY
                                    / COMMODITY / PREMARKET underlyings, and
                                    basket/INDEX products (e.g. BTCDOMUSDT,
                                    a BTC-dominance index, not a single
                                    coin) on the exact same endpoint. This
                                    alpha is a crypto cross-sectional
                                    momentum mechanism -- non-COIN
                                    underlyings are excluded, not silently
                                    swept in by an unqualified "all USDT
                                    perpetuals" filter.

A fifth, narrower filter applies on top: `symbol.isascii()`. Verified live at
build time (2026-09-01): 4 of the 523 candidates otherwise passing the four
criteria above carry non-ASCII (CJK) vanity/novelty ticker names (e.g. a
literal Chinese-character symbol string). Binance's own USDM klines REST
endpoint accepts them, but this alpha reuses V1's klines_source.py
READ-ONLY (see package docstring) -- a generic, frozen, un-modifiable-here
Binance REST client that builds request URLs without percent-encoding the
symbol, so Python's http.client raises UnicodeEncodeError on these four
names (caught and logged by that shared client's own existing error
handling -- it fails soft, this run still completes for every other
symbol). Rather than leave this as an opaque caught exception per run,
`.isascii()` excludes these upfront: an explicit, deterministic, documented
filter instead of a silent runtime failure path. This is a V2-only
decision, made without touching V1's file (off-limits per the mission),
and it costs nothing economically -- these are novelty listings, not part
of any reasonable "liquid altcoin" universe, and would almost certainly
have failed the liquidity filter in signal.py regardless.

This produces a CANDIDATE universe only (~500 symbols at build time, verified
2026-09-01: 523 pass the first four criteria, 519 pass all five once the
ASCII-ticker filter is included) -- NOT yet the "liquid alt" universe.
The causal trailing-30d liquidity filter in signal.py (own threshold, see
that module's docstring) does the actual "liquid altcoin, not junk"
narrowing -- mirroring the source report's own methodology (a base liquidity
filter applied over its full candidate universe, THEN cross-sectional
ranking within the liquid cohort), not a hand-picked symbol list.

No historical reconstruction of past exchangeInfo snapshots is attempted --
only TODAY's live eligibility is known (Binance does not expose a historical
listing-status feed). This means a symbol delisted before today never
appears even in the historical replay window, and the resolved candidate set
mildly survivorship-biases toward names that are still trading today. This
is the same limitation V1 already carries (see its freeze_spec.json) and not
a new one introduced here -- documented in
reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V2/freeze_spec.json.
"""
from __future__ import annotations

from typing import Dict, List

# Defensive sanity bound -- if exchangeInfo ever returned something wildly
# larger than this, the filter above is almost certainly broken (wrong field
# name, API contract change) rather than the universe genuinely growing
# 4-10x overnight. Fail loud instead of silently ingesting garbage.
MAX_SANE_CANDIDATE_COUNT = 2000


def candidate_symbols_from_exchange_info(exchange_info: dict) -> List[dict]:
    """Pure filter (no I/O): returns the raw exchangeInfo symbol dicts
    passing the PERPETUAL / USDT / TRADING / COIN criteria above, in the
    order the API returned them. A malformed/incomplete entry (missing an
    expected key) simply fails the filter -- excluded, never a crash on one
    bad row. Empty/malformed `exchange_info` input -> empty list."""
    out: List[dict] = []
    for s in (exchange_info or {}).get("symbols", []) or []:
        if not isinstance(s, dict):
            continue
        symbol = s.get("symbol")
        if (s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
                and s.get("underlyingType") == "COIN"
                and isinstance(symbol, str) and symbol.isascii()):
            out.append(s)
    return out


def resolve_dynamic_liquid_universe(exchange_info: dict) -> List[str]:
    """Sorted, deduplicated list of exchange symbol strings (e.g.
    'BTCUSDT', '1000PEPEUSDT') passing the candidate filter above.

    This is the CANDIDATE universe for this run -- the causal liquidity
    filter in signal.py still needs to run on top of this (inside
    build_weekly_decisions) before any symbol is actually
    tradeable-eligible for a given rebalance date.

    Raises RuntimeError if the resolved count exceeds MAX_SANE_CANDIDATE_COUNT
    (see module docstring) -- fail loud on a likely API/field-name
    regression rather than silently processing a wrong universe."""
    symbols = sorted({s["symbol"] for s in candidate_symbols_from_exchange_info(exchange_info)})
    if len(symbols) > MAX_SANE_CANDIDATE_COUNT:
        raise RuntimeError(
            f"resolve_dynamic_liquid_universe: {len(symbols)} candidats > "
            f"MAX_SANE_CANDIDATE_COUNT={MAX_SANE_CANDIDATE_COUNT} -- probable "
            "régression du filtre (champ renommé côté exchangeInfo ?), refus "
            "de continuer silencieusement."
        )
    return symbols
