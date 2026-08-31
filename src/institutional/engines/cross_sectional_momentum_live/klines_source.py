"""
src/institutional/engines/cross_sectional_momentum_live/klines_source.py
─────────────────────────────────────────────────────────────────────────────
Live daily OHLCV+volume loader for CROSS_SECTIONAL_MOMENTUM_LIVE_V1.

Data source choice (see freeze_spec.json `data_investigation` for the full
accounting): the source mechanism (W1 report, M1) needs only price, returns,
and $-volume at a DAILY 7d->7d granularity -- it does not need 1h bars, L2,
or any derivatives field. Three candidates were investigated and rejected
before this one:

  - data/enriched/*_1h_enriched.parquet: only 10/50 files have a fresh mtime
    (the live paper-trading fleet symbols); the other 40/50 are stale since
    2026-06-29 (>2 months at the time this module was written) with no active
    updater found. A cross-sectional rank needs the WHOLE universe's recent
    returns -- ranking 40/50 symbols on 2-month-old closes would be silently
    wrong, not just incomplete. Rejected.
  - data_v2/normalized/instrument_master.parquet (312-symbol PIT panel): the
    ORIGINAL spec's data (see CROSS_SECTIONAL_MOMENTUM_PIT_V1, DATA_BLOCKED)
    -- lives only in the separate futur-data-v2 worktree, no continuous live
    update confirmed. Not used here (that's the whole reason this is a
    different, RECONSTRUCTED alpha_id).
  - data/derivatives_raw/ (mark_price via futur-derivatives.service, ~5min
    refresh): has live price for the frozen-50 universe but NOT real kline
    trading volume (OI/funding/mark-price only) -- cannot support the
    liquidity filter this mechanism requires. Not used here.

Chosen instead: Binance USDM futures public REST /fapi/v1/klines
(interval=1d), no API key -- the SAME endpoint already used elsewhere in this
repo (scripts/backfill_enriched_from_binance.py,
src/institutional/data/derivatives_collector/symbol_resolver.py uses the
sibling exchangeInfo endpoint). Daily bars only: a multi-year history is a
handful of API calls per symbol (limit=1500 days/call), and the resulting
local cache (date, close, quote_volume -- 3 columns) is a few thousand rows
per symbol, nowhere near "gros stockage". Polled ON DEMAND inside the runner
script -- this module starts no background process and is not wired to any
systemd unit; see the mission's explicit instruction to avoid new live
infrastructure.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

import pandas as pd

BINANCE_FAPI = "https://fapi.binance.com"
KLINES_ENDPOINT = f"{BINANCE_FAPI}/fapi/v1/klines"
INTERVAL = "1d"
MAX_LIMIT = 1500
DEFAULT_START = "2020-01-01T00:00:00Z"   # matches this repo's usual training_window start; Binance
                                          # simply returns from each symbol's real listing date onward.

CACHE_COLUMNS = ["date", "close", "quote_volume"]


def _get(url: str, tries: int = 4, timeout: float = 20.0) -> Optional[list]:
    """Same retry/backoff shape as scripts/backfill_enriched_from_binance.py's
    `_get` -- HTTP 400 means the symbol/params are invalid (never retried,
    caller must treat as 'no data'), anything else retries with linear
    backoff up to `tries` attempts."""
    for k in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "futur-cross-sectional-momentum-live"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def fetch_daily_klines(exchange_symbol: str, start_ms: int) -> List[list]:
    """Paginate /fapi/v1/klines interval=1d from start_ms to now (raw Binance
    kline rows, unparsed). Returns [] if nothing new / symbol invalid --
    never raises for a routine empty page."""
    rows: List[list] = []
    cur = start_ms
    now_ms = int(time.time() * 1000)
    while cur < now_ms:
        url = f"{KLINES_ENDPOINT}?symbol={exchange_symbol}&interval={INTERVAL}&startTime={cur}&limit={MAX_LIMIT}"
        data = _get(url)
        if not data:
            break
        rows.extend(data)
        last_open = data[-1][0]
        if last_open <= cur:
            break   # defensive: avoid an infinite loop if the API ever returns a non-advancing page
        cur = last_open + 86_400_000
        if len(data) < MAX_LIMIT:
            break   # short page = caught up to "now"
        time.sleep(0.15)
    return rows


def rows_to_frame(rows: List[list]) -> pd.DataFrame:
    """Pure transform (no I/O): raw Binance kline rows -> (date, close,
    quote_volume), UTC daily dates, deduped/sorted. Kline field layout:
    [openTime, open, high, low, close, volume, closeTime, quoteAssetVolume,
    numTrades, takerBuyBase, takerBuyQuote, ignore] -- close=index 4,
    quoteAssetVolume=index 7 (REAL traded $-volume, unlike the
    data/enriched taker_buy_* placeholder columns flagged in the mission
    brief -- not used here at all, this module never touches taker_buy_*)."""
    if not rows:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["date"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True).dt.floor("D")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df = df[CACHE_COLUMNS].drop_duplicates(subset="date", keep="last").sort_values("date")
    return df.reset_index(drop=True)


def load_cache(cache_path: Path) -> pd.DataFrame:
    if cache_path.exists():
        df = pd.read_parquet(cache_path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df
    return pd.DataFrame(columns=CACHE_COLUMNS)


def refresh_symbol_cache(exchange_symbol: str, cache_path: Path,
                          default_start: str = DEFAULT_START) -> pd.DataFrame:
    """Incrementally top up `cache_path`'s local daily-bar cache for
    `exchange_symbol` (only fetches from the last cached date forward --
    never re-downloads full history on every run), writes it back, and
    returns the full cached panel (date/close/quote_volume, UTC, sorted,
    deduped). Never raises on a routine empty response -- returns whatever
    was already cached (possibly empty) rather than crash the whole run over
    one symbol."""
    existing = load_cache(cache_path)
    if existing.empty:
        start_ms = int(pd.Timestamp(default_start).timestamp() * 1000)
    else:
        last_date = pd.Timestamp(existing["date"].max())
        start_ms = int((last_date + pd.Timedelta(days=1)).timestamp() * 1000)

    now_ms = int(time.time() * 1000)
    if start_ms >= now_ms:
        return existing   # already up to date, nothing to fetch

    try:
        new_rows = fetch_daily_klines(exchange_symbol, start_ms)
    except Exception as e:
        print(f"[klines_source] {exchange_symbol}: fetch failed ({e!r}) -- "
              f"using existing cache only ({len(existing)} rows).")
        return existing

    new_df = rows_to_frame(new_rows)
    if new_df.empty:
        return existing

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(cache_path, index=False)
    return combined
