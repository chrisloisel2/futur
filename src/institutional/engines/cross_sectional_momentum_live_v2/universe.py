"""
src/institutional/engines/cross_sectional_momentum_live_v2/universe.py
─────────────────────────────────────────────────────────────────────────────
PIT (point-in-time) candidate-universe resolution for
CROSS_SECTIONAL_MOMENTUM_LIVE_V2.

⚠ BUG FIX (2026-09-01, same-day discipline as the earlier symbol_resolver.py
MKR/PEPE/RNDR fix -- see configs/live_alpha_registry.yaml "BUG POLICY" note):
the ORIGINAL version of this module resolved candidates from Binance's LIVE
/fapi/v1/exchangeInfo ONCE per run and applied that CURRENT (as-of-today)
TRADING status retroactively to every historical weekly rebalance date back
to 2020. Two real integrity problems followed from that:

  1. SURVIVORSHIP: a symbol delisted before today's run never appeared
     ANYWHERE in the backfilled history, even for the (real, historical)
     weeks it was genuinely trading and liquid -- the ranking pool for OLD
     rebalances was silently restricted to "today's survivors" only.
  2. NO EXPLICIT LISTING-AGE GATE: whether a freshly-listed symbol could
     appear in the ranking was purely an INCIDENTAL side effect of
     signal.py's LIQUIDITY_WINDOW_DAYS=30 rolling-median window (a symbol's
     quote_volume column is only non-NaN once real trading data exists, so a
     symbol coincidentally became "eligible" ~30 real days after its actual
     Binance listing -- but this was never an intentional, onboard_ts-based,
     independently-auditable gate; it would have silently broken if
     LIQUIDITY_WINDOW_DAYS ever changed, and gave no audit trail of WHY a
     young symbol was excluded).

Neither of these let a not-yet-listed symbol's OWN price data leak
backward in time (Binance's klines endpoint genuinely has no data before a
symbol's real listing date, so `trailing_return`/`trailing_liquidity_usd` in
signal.py were never fed literal future-return values) -- but the UNIVERSE
COMPOSITION itself (which symbols even get a column in the panel, and from
when they start counting as "old enough") was wrong for the reasons above.

Fix, in two parts:

  (A) Survivorship: `historical_reinclusion_candidates()` adds back known
      DELISTED symbols from the existing, already-established
      data/listings_backfill/binance/listings_calendar.parquet (built by
      scripts/backfill_binance_perp_listings.py for ListingAgeGate,
      src/institutional/portfolio/listing_age_gate.py -- same convention,
      reused here, not reinvented) -- these get fetched via the SAME
      read-only klines_source.py REST client as any other symbol (Binance's
      public klines endpoint still serves historical bars for delisted
      symbols up to their last trading day) and are genuinely eligible for
      the historical rebalance dates when they actually traded. Symbols with
      NO reconstructable data anywhere in this worktree (status
      DELISTED_NO_DATA in the calendar) are logged as an honest, disclosed
      gap -- never silently dropped, never guessed at.

  (B) Explicit PIT eligibility gate: `build_pit_eligibility_log()` /
      `mask_pre_eligibility()` replace the incidental ~30-day protection
      with an EXPLICIT onboard_ts-based gate (`MIN_LISTING_AGE_DAYS`, same
      convention/default as ListingAgeGate) plus explicit "does real price
      history exist back this far" and "is trailing liquidity known/
      sufficient" checks, each with its own logged rejection reason, per
      (rebalance_date, symbol) -- written to a queryable parquet log
      (pit_universe_log.parquet) and a compact JSON summary
      (pit_universe_summary.json) by the runner script. `eligible_universe_size`
      now genuinely varies over time (few dozen symbols eligible in 2020-21,
      hundreds by 2026) instead of being the current ~519-symbol count
      copy-pasted backward across all of history.

candidate_symbols_from_exchange_info() / resolve_dynamic_liquid_universe()
below are UNCHANGED from the original build -- they still correctly answer
"what is tradeable RIGHT NOW" (needed to know what to fetch fresh going
forward, and this alpha's mission is explicitly a forward-looking dynamic
universe). What changed is that their output is no longer, by itself, used
to gate PAST rebalances -- see build_pit_eligibility_log()/
mask_pre_eligibility() below, which the runner applies BEFORE handing the
panel to signal.py's (untouched, frozen) build_weekly_decisions().

Honest residual limitation (documented, not hidden -- see the mission's own
framing): Binance does not expose a historical *exchangeInfo* snapshot feed,
so "correct exchange mapping"/instrument identity still relies on
listings_calendar.parquet's onboard_ts (the real, PIT-known listing
timestamp, exactly the same convention ListingAgeGate already uses) rather
than a full historical exchangeInfo replay. Two of this run's 519 live
candidates (DOSUSDT, GRVTUSDT) are absent from that calendar (listed after
its last regeneration) -- for those, onboard_ts is approximated as the first
date real price data appears in this alpha's own downloaded klines cache
(source="first_price_data_fallback", logged explicitly per symbol, never
silently assumed to be the exact true listing date). This is the exact
"first date with real data" proxy the mission pre-approved as defensible and
causal when true listing metadata isn't available.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Defensive sanity bound -- if exchangeInfo ever returned something wildly
# larger than this, the filter above is almost certainly broken (wrong field
# name, API contract change) rather than the universe genuinely growing
# 4-10x overnight. Fail loud instead of silently ingesting garbage.
MAX_SANE_CANDIDATE_COUNT = 2000

# ── PIT eligibility gate constants ──────────────────────────────────────────

# Same convention/default as src/institutional/portfolio/listing_age_gate.py
# (ListingAgeGate.__init__'s min_age_days=30, also
# multileg_backtester.py's listing_min_age_days: int = 30) -- reused
# deliberately rather than inventing a new threshold for V2. That gate exists
# because of a measured, published finding (reports/LISTING_EVENT_STUDY.md):
# negative net-of-cost drift in the days immediately after a perp listing.
# Decoupled here from signal.py's LIQUIDITY_WINDOW_DAYS (also 30, but for a
# completely different reason -- the trailing liquidity rolling window) so
# the two no longer silently coincide only by accident.
MIN_LISTING_AGE_DAYS = 30

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LISTING_CALENDAR = (
    ROOT / "data" / "listings_backfill" / "binance" / "listings_calendar.parquet")

# Calendar `status` values usable for historical survivorship reinclusion
# (real onboard_ts known, real price data fetchable). DELISTED_NO_DATA rows
# have onboard_ts=NaT and no reconstructable price series anywhere in this
# worktree -- logged as a disclosed gap, never fetched, never guessed.
_REINCLUDABLE_STATUS = "DELISTED"
_NO_DATA_STATUS = "DELISTED_NO_DATA"


def candidate_symbols_from_exchange_info(exchange_info: dict) -> List[dict]:
    """Pure filter (no I/O): returns the raw exchangeInfo symbol dicts
    passing the PERPETUAL / USDT / TRADING / COIN criteria above, in the
    order the API returned them. A malformed/incomplete entry (missing an
    expected key) simply fails the filter -- excluded, never a crash on one
    bad row. Empty/malformed `exchange_info` input -> empty list.

    Candidate filter (deterministic, no guessing/heuristic matching):
      - contractType == "PERPETUAL"    excludes CURRENT_QUARTER / NEXT_QUARTER
                                        calendar-dated contracts and the exotic
                                        "TRADIFI_PERPETUAL" contract type also
                                        observed on this endpoint at build time
                                        (2026-09-01).
      - quoteAsset == "USDT"           USDT-margined only, per the mission's own
                                        framing; excludes BTC/USDC/U/USD1
                                        margined pairs also present.
      - status == "TRADING"            real listing eligibility, never
                                        PENDING_TRADING/SETTLING -- what "TODAY"
                                        means, used only to know what to fetch
                                        fresh, see module docstring.
      - underlyingType == "COIN"       excludes tokenized EQUITY/CN_EQUITY/
                                        HK_EQUITY/KR_EQUITY/COMMODITY/PREMARKET
                                        underlyings and basket/INDEX products
                                        (e.g. BTCDOMUSDT) on the same endpoint.

    A fifth filter, `symbol.isascii()`, excludes a handful of non-ASCII (CJK)
    vanity ticker names that crash the shared (V1, frozen) klines REST
    client's URL construction -- see the original build's accounting in
    freeze_spec.json, unchanged here."""
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
    'BTCUSDT', '1000PEPEUSDT') passing the candidate filter above -- "what is
    tradeable RIGHT NOW". See module docstring: this is used to know what to
    fetch fresh going forward, and NOT (on its own, since the 2026-09-01 PIT
    fix) used to gate which symbols were eligible at a PAST rebalance date --
    see build_pit_eligibility_log()/mask_pre_eligibility() for that.

    Raises RuntimeError if the resolved count exceeds MAX_SANE_CANDIDATE_COUNT
    -- fail loud on a likely API/field-name regression rather than silently
    processing a wrong universe."""
    symbols = sorted({s["symbol"] for s in candidate_symbols_from_exchange_info(exchange_info)})
    if len(symbols) > MAX_SANE_CANDIDATE_COUNT:
        raise RuntimeError(
            f"resolve_dynamic_liquid_universe: {len(symbols)} candidats > "
            f"MAX_SANE_CANDIDATE_COUNT={MAX_SANE_CANDIDATE_COUNT} -- probable "
            "régression du filtre (champ renommé côté exchangeInfo ?), refus "
            "de continuer silencieusement."
        )
    return symbols


# ── (A) Survivorship: historical delisted-symbol reinclusion ───────────────

def load_listing_calendar(path: Optional[Path] = None) -> pd.DataFrame:
    """Read-only load of the existing, already-established
    data/listings_backfill/binance/listings_calendar.parquet (built by
    scripts/backfill_binance_perp_listings.py, already relied on by
    ListingAgeGate -- see module docstring). Columns: symbol, onboard_ts
    (real exchange listing timestamp, tz-aware UTC, NaT if unknown), status
    (TRADING/DELISTED/DELISTED_NO_DATA/SETTLING/PENDING_TRADING), source.

    Never written to by this module -- read-only, same discipline as every
    other cross-alpha shared file this mission restricts. Missing file ->
    empty DataFrame with the right columns + a printed warning (never a
    crash): callers fall back to the first-price-data proxy for every
    symbol, which is honestly logged wherever it happens."""
    cols = ["symbol", "onboard_ts", "status", "source"]
    p = path if path is not None else DEFAULT_LISTING_CALENDAR
    if not p.exists():
        print(f"[universe] listings_calendar introuvable ({p}) -- onboard_ts "
              f"sera approximé via 'first_price_data_fallback' pour TOUS les "
              f"symboles (dégradation honnête, voir module docstring).")
        return pd.DataFrame(columns=cols)
    cal = pd.read_parquet(p)
    cal = cal[[c for c in cols if c in cal.columns]].copy()
    if "onboard_ts" in cal.columns:
        cal["onboard_ts"] = pd.to_datetime(cal["onboard_ts"], utc=True)
    return cal


def historical_reinclusion_candidates(
        listing_calendar: pd.DataFrame,
        exclude: Optional[set] = None) -> Tuple[List[str], List[str]]:
    """Splits the calendar's DELISTED rows into (fetchable, no_data):

      - fetchable: status==DELISTED, onboard_ts known, symbol ends in
        'USDT' and is ASCII (same identity conventions as the live
        candidate filter) -- Binance's public klines REST endpoint still
        serves historical bars for these up to their last trading day
        (verified live 2026-09-01), so they are genuinely reconstructable
        and get added to the fetch universe.
      - no_data: status==DELISTED_NO_DATA (onboard_ts unknown, no
        reconstructable price series anywhere -- e.g. BDXNUSDT, SXPUSDT at
        build time) -- NEVER fetched, NEVER silently dropped: logged as an
        explicit, disclosed gap by the caller.

    `exclude` (typically today's live TRADING candidate set) removes any
    symbol already present there -- defensive dedup; in practice a symbol
    cannot be simultaneously TRADING today and DELISTED in the calendar, but
    this makes that invariant explicit rather than assumed."""
    if listing_calendar is None or listing_calendar.empty:
        return [], []
    excl = exclude or set()
    cal = listing_calendar
    fetchable_mask = (
        (cal["status"] == _REINCLUDABLE_STATUS)
        & cal["onboard_ts"].notna()
        & cal["symbol"].astype(str).str.endswith("USDT")
        & cal["symbol"].astype(str).apply(lambda s: s.isascii())
        & ~cal["symbol"].isin(excl)
    )
    fetchable = sorted(cal.loc[fetchable_mask, "symbol"].unique().tolist())
    no_data = sorted(cal.loc[cal["status"] == _NO_DATA_STATUS, "symbol"].unique().tolist())
    return fetchable, no_data


# ── (B) Explicit PIT eligibility gate ───────────────────────────────────────

def onboard_ts_map(listing_calendar: pd.DataFrame) -> Dict[str, pd.Timestamp]:
    """symbol -> onboard_ts for every calendar row with a known onboard_ts.
    Empty/missing calendar -> empty dict (all symbols fall back to
    first-price-data, see resolve_onboard_dates)."""
    if listing_calendar is None or listing_calendar.empty:
        return {}
    ok = listing_calendar["onboard_ts"].notna()
    return dict(zip(listing_calendar.loc[ok, "symbol"], listing_calendar.loc[ok, "onboard_ts"]))


def first_price_date_per_symbol(panel_close: pd.DataFrame) -> Dict[str, pd.Timestamp]:
    """Per symbol column, the first date with a real (non-NaN) close price
    in the already-downloaded panel -- the fallback PIT-safe proxy for
    onboard_ts when a symbol has no listings_calendar entry (see module
    docstring). This is causal and non-guessed: Binance's klines endpoint
    genuinely has no data before a symbol's real listing, so "first row with
    data" cannot be later than the truth and is very rarely earlier than it
    (at most a data-collection artifact, never a lookahead one)."""
    out: Dict[str, pd.Timestamp] = {}
    if panel_close is None or panel_close.empty:
        return out
    for sym in panel_close.columns:
        s = panel_close[sym]
        valid = s[s.notna()]
        if len(valid) > 0:
            out[sym] = pd.Timestamp(valid.index.min())
    return out


def resolve_onboard_dates(symbols: List[str], listing_calendar: pd.DataFrame,
                           panel_close: pd.DataFrame) -> pd.DataFrame:
    """For every symbol in `symbols`, resolve a PIT-known onboard_ts:
      1. listings_calendar.parquet (source='listings_calendar') if present.
      2. else the first date with real price data in `panel_close`
         (source='first_price_data_fallback') -- honestly logged, not hidden.
      3. else NaT (source='unknown') -- symbol has neither a calendar entry
         nor any downloaded price data; excluded from every rebalance (can't
         be judged eligible on data that doesn't exist), logged as such.

    Returns columns [symbol, onboard_ts, onboard_source]."""
    cal_map = onboard_ts_map(listing_calendar)
    price_map = first_price_date_per_symbol(panel_close)
    rows = []
    for sym in symbols:
        if sym in cal_map:
            rows.append((sym, cal_map[sym], "listings_calendar"))
        elif sym in price_map:
            rows.append((sym, price_map[sym], "first_price_data_fallback"))
        else:
            rows.append((sym, pd.NaT, "unknown"))
    return pd.DataFrame(rows, columns=["symbol", "onboard_ts", "onboard_source"])


def mask_pre_eligibility(panel_close: pd.DataFrame, panel_quote_volume: pd.DataFrame,
                          onboard_df: pd.DataFrame,
                          min_listing_age_days: int = MIN_LISTING_AGE_DAYS
                          ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (masked_close, masked_quote_volume): copies of the input
    panels with every cell (date, symbol) set to NaN wherever
    `date < onboard_ts[symbol] + min_listing_age_days`. This is what
    actually GATES signal.py's (untouched, frozen) trailing_return /
    trailing_liquidity_usd -- an EXPLICIT, onboard_ts-based, independently
    auditable listing-age gate, replacing the original build's incidental
    reliance on LIQUIDITY_WINDOW_DAYS's rolling-window size (see module
    docstring).

    Minor documented rounding side effect: because trailing_return uses a
    LOOKBACK_DAYS=7 shift, a symbol only becomes fully "un-masked" for its
    own return computation `lookback` days after the age gate itself opens
    (its tret_7d window needs 7 real days immediately before the gate, which
    are themselves masked) -- i.e. first possible selection is
    ~min_listing_age_days + lookback real days after onboard_ts, not exactly
    min_listing_age_days. This is a conservative (MORE cautious, never a
    lookahead) rounding artifact of masking the raw input panel rather than
    injecting a separate eligibility mask into signal.py itself -- a
    deliberate choice to leave signal.py's frozen spec completely untouched
    (see freeze_spec.json's frozen_spec, unchanged by this fix).

    A symbol with onboard_source='unknown' (no calendar entry AND no price
    data at all) is masked out entirely (NaN at every date) -- correctly
    never eligible, since there is no PIT-safe basis to judge it."""
    close = panel_close.copy()
    vol = panel_quote_volume.copy()
    cutoff = dict(zip(
        onboard_df["symbol"],
        onboard_df["onboard_ts"] + pd.Timedelta(days=min_listing_age_days),
    ))
    for sym in close.columns:
        co = cutoff.get(sym)
        if co is None or pd.isna(co):
            close[sym] = float("nan")
            if sym in vol.columns:
                vol[sym] = float("nan")
            continue
        pre_mask = close.index < co
        close.loc[pre_mask, sym] = float("nan")
        if sym in vol.columns:
            vol.loc[pre_mask, sym] = float("nan")
    return close, vol


def build_pit_eligibility_log(
        rebalance_dates: List[pd.Timestamp],
        symbols: List[str],
        onboard_df: pd.DataFrame,
        panel_close: pd.DataFrame,
        panel_quote_volume: pd.DataFrame,
        min_listing_age_days: int = MIN_LISTING_AGE_DAYS,
        min_liquidity_usd: float = 2_000_000.0,
        liquidity_window: int = 30,
        lookback: int = 7,
) -> pd.DataFrame:
    """Full per-(rebalance_date, symbol) PIT eligibility audit log -- the
    genuinely inspectable trail the mission asked for (not just a summary
    count). Evaluated on the UNMASKED panels (real downloaded data) so the
    log can distinguish WHY a symbol is rejected, in this precedence order
    (first failing gate wins, exactly one reason per row):

      not_yet_listed                     rebalance_date < onboard_ts
      insufficient_listing_age_{N}d       0 <= age_days < min_listing_age_days
      no_price_history_before_rebalance_date   age satisfied, but tret_7d or
                                          liquidity_usd_30d can't be computed
                                          (real data gap in the downloaded
                                          klines cache -- e.g. a fetch
                                          failure, or a delisted symbol whose
                                          data has already run out)
      insufficient_liquidity_${M}m_{W}d   computable but below the trailing
                                          liquidity floor
      (eligible)                          reason=None -- passes every gate;
                                          this IS the pool signal.py's
                                          top-quintile ranking then runs over

    Reuses signal.py's OWN trailing_return/trailing_liquidity_usd formulas
    (imported, not duplicated) purely for this audit computation -- the
    actual gating fed to build_weekly_decisions still goes through
    mask_pre_eligibility() + signal.py unchanged; this log is a read-only
    diagnostic view, never itself part of the selection path.

    Columns: [rebalance_date, symbol, onboard_ts, onboard_source, age_days,
    tret_7d, liquidity_usd_30d, eligible, reason]."""
    from src.institutional.engines.cross_sectional_momentum_live_v2.signal import (
        trailing_liquidity_usd, trailing_return)

    cols = ["rebalance_date", "symbol", "onboard_ts", "onboard_source", "age_days",
            "tret_7d", "liquidity_usd_30d", "eligible", "reason"]
    if not rebalance_dates or not symbols or panel_close is None or panel_close.empty:
        return pd.DataFrame(columns=cols)

    onboard_map = dict(zip(onboard_df["symbol"], onboard_df["onboard_ts"]))
    source_map = dict(zip(onboard_df["symbol"], onboard_df["onboard_source"]))

    ret_panel = panel_close.reindex(columns=symbols).apply(lambda s: trailing_return(s, lookback))
    liq_panel = panel_quote_volume.reindex(columns=symbols).apply(
        lambda s: trailing_liquidity_usd(s, liquidity_window))

    liq_reason = f"insufficient_liquidity_${min_liquidity_usd / 1e6:.0f}m_{liquidity_window}d"
    age_reason = f"insufficient_listing_age_{min_listing_age_days}d"

    rows = []
    for d in rebalance_dates:
        d = pd.Timestamp(d)
        for sym in symbols:
            onboard = onboard_map.get(sym, pd.NaT)
            source = source_map.get(sym, "unknown")
            age_days = (d - onboard).days if pd.notna(onboard) else None

            tret = ret_panel.loc[d, sym] if d in ret_panel.index and sym in ret_panel.columns else float("nan")
            liq = liq_panel.loc[d, sym] if d in liq_panel.index and sym in liq_panel.columns else float("nan")

            if age_days is None:
                reason = "not_yet_listed"          # no PIT-safe onboard date at all -> never eligible
            elif age_days < 0:
                reason = "not_yet_listed"
            elif age_days < min_listing_age_days:
                reason = age_reason
            elif pd.isna(tret) or pd.isna(liq):
                reason = "no_price_history_before_rebalance_date"
            elif liq < min_liquidity_usd:
                reason = liq_reason
            else:
                reason = None

            rows.append((d, sym, onboard, source, age_days, tret, liq, reason is None, reason))

    return pd.DataFrame(rows, columns=cols)


def write_pit_universe_log(log_df: pd.DataFrame, out_path: Path) -> None:
    """Writes the full per-(rebalance_date, symbol) audit log as parquet --
    genuinely queryable (groupby rebalance_date, filter by reason, etc.),
    not just a summary count. Overwrites the previous run's log (this is an
    audit artifact regenerated fresh each run, unlike decisions.parquet
    which is append-only/idempotent)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_df.to_parquet(out_path, index=False)


def summarize_pit_log(log_df: pd.DataFrame) -> List[dict]:
    """Per-rebalance-date compact summary: {rebalance_date,
    eligible_universe_size, selected_universe, rejected_symbols}. Written
    alongside the parquet log for quick human inspection (mirrors this
    repo's usual JSON-summary + parquet-detail pairing, e.g. decisions.parquet
    + run_state.json). `selected_universe` here means every symbol passing
    ALL PIT gates that date (existed, tradable/not-yet-delisted-per-data,
    listing age satisfied, real history present, liquidity threshold met) --
    i.e. exactly the pool signal.py's top-quintile ranking then runs over,
    NOT only the symbols actually picked LONG that week (see decisions.parquet
    for the actual picks)."""
    if log_df is None or log_df.empty:
        return []
    out = []
    for d, grp in log_df.groupby("rebalance_date"):
        elig = grp[grp["eligible"]]
        rej = grp[~grp["eligible"]]
        out.append({
            "rebalance_date": pd.Timestamp(d).isoformat(),
            "eligible_universe_size": int(len(elig)),
            "selected_universe": sorted(elig["symbol"].tolist()),
            "rejected_symbols": dict(zip(rej["symbol"], rej["reason"])),
        })
    return sorted(out, key=lambda r: r["rebalance_date"])
