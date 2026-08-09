"""
data_v2/features/basis.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 9: perp/spot basis series at 5m, built by causally joining
data_v2/normalized/perp_ohlcv and data_v2/normalized/spot_ohlcv (both built
by build_perp_5m.py / build_spot_5m.py). No forward-filling across missing
bars -- an inner join on timestamp, so a basis value only exists where both
legs genuinely have a bar.

Columns:
  perp_spot_basis   = perp_close / spot_close - 1
  basis_z_1d/_7d     = perp_spot_basis rolling z-score (288 / 2016 bars)
  basis_change_5m/15m/1h = perp_spot_basis.diff(1/3/12)
  premium_index      = real Binance Vision premium-index series
                        (data/derivatives_backfill/binance_vision_premium),
                        49/50 legacy-enriched symbols covered per prior
                        audit -- NOT derived from perp_spot_basis, it is
                        Binance's own published premium metric, joined in
                        as-is when the file exists and is readable (one
                        known-corrupt file found in that store during this
                        pass: BTCUSDT_premium_5m.parquet, magic bytes
                        missing, pre-dates this session -- skipped with a
                        warning, not fixed here, out of scope for Data V2).
  mark_spot_basis    = mark_price / spot_close - 1, only at the sparse (8h,
                        since 2023-10-31) settlement points where
                        data/derivatives_backfill/binance/funding/*.parquet
                        actually carries a real mark_price -- per
                        DATA_INVENTORY.yaml no absolute mark price exists at
                        5m anywhere, so this column is intentionally mostly
                        NaN, not forward-filled into a fabricated level.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PERP_DIR = ROOT / "data_v2/normalized/perp_ohlcv/venue=binance"
SPOT_DIR = ROOT / "data_v2/normalized/spot_ohlcv/venue=binance"
PREMIUM_DIR = ROOT / "data/derivatives_backfill/binance_vision_premium"
FUNDING_DIR = ROOT / "data/derivatives_backfill/binance/funding"
OUT_DIR = ROOT / "data_v2/normalized/basis/venue=binance"

ROLL_1D = 288
ROLL_7D = 288 * 7


def _load_5m_dir(symbol_dir: Path, value_cols: list[str]) -> pd.DataFrame | None:
    if not symbol_dir.exists():
        return None
    parts = sorted(symbol_dir.glob("year=*/*.parquet"))
    if not parts:
        return None
    frames = [pd.read_parquet(p, columns=["timestamp"] + value_cols) for p in parts]
    df = pd.concat(frames).drop_duplicates(subset="timestamp").sort_values("timestamp")
    return df.set_index("timestamp")


def _load_premium(symbol: str) -> pd.DataFrame | None:
    path = PREMIUM_DIR / f"{symbol}_premium_5m.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["ts", "premium"])
    except Exception as e:
        warnings.warn(f"premium_index unreadable for {symbol}: {type(e).__name__}: {e} -- skipped")
        return None
    df = df.rename(columns={"ts": "timestamp", "premium": "premium_index"})
    return df.set_index("timestamp")


def _load_mark_price(symbol: str) -> pd.DataFrame | None:
    path = FUNDING_DIR / f"{symbol}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["timestamp", "mark_price"])
    df = df.dropna(subset=["mark_price"])
    if df.empty:
        return None
    return df.set_index("timestamp").sort_index()


def build_basis_symbol(symbol: str) -> pd.DataFrame | None:
    perp = _load_5m_dir(PERP_DIR / f"symbol={symbol}", ["close"])
    spot = _load_5m_dir(SPOT_DIR / f"symbol={symbol}", ["spot_close"])
    if perp is None or spot is None:
        return None
    perp = perp.rename(columns={"close": "perp_close"})

    joined = perp.join(spot, how="inner")
    if joined.empty:
        return None

    joined["perp_spot_basis"] = joined["perp_close"] / joined["spot_close"] - 1.0
    basis = joined["perp_spot_basis"]
    joined["basis_z_1d"] = (basis - basis.rolling(ROLL_1D, min_periods=ROLL_1D // 3).mean()) / basis.rolling(
        ROLL_1D, min_periods=ROLL_1D // 3
    ).std()
    joined["basis_z_7d"] = (basis - basis.rolling(ROLL_7D, min_periods=ROLL_7D // 3).mean()) / basis.rolling(
        ROLL_7D, min_periods=ROLL_7D // 3
    ).std()
    joined["basis_change_5m"] = basis.diff(1)
    joined["basis_change_15m"] = basis.diff(3)
    joined["basis_change_1h"] = basis.diff(12)

    premium = _load_premium(symbol)
    if premium is not None:
        joined = joined.join(premium, how="left")
    else:
        joined["premium_index"] = pd.NA

    mark = _load_mark_price(symbol)
    if mark is not None:
        # causal: attach the mark_price only at its own real settlement,
        # bucketed to the spot bar covering that instant via FLOOR (never
        # round up into a future bar -- real settlement timestamps carry a
        # few ms of positive jitter after the canonical 00:00/08:00/16:00
        # UTC mark, e.g. "16:00:00.003", never negative in observed data;
        # flooring always lands on the bar that had already started, an
        # exact match after that, not a nearest-neighbour search). An
        # earlier version used merge_asof(direction="nearest", tolerance=
        # 5min): "nearest" has no causality guarantee -- it can match a
        # spot bar that starts AFTER the mark timestamp if that bar happens
        # to be numerically closer, which would leak a not-yet-existing
        # price into mark_spot_basis. floor+exact removes that risk
        # entirely (deterministic, and mark_spot_basis on real BTCUSDT data
        # confirmed unchanged at 183/183 matched after this fix). No
        # forward-fill between settlements -- this stays sparse by design.
        mark_df = mark.reset_index()[["timestamp", "mark_price"]].copy()
        mark_df["spot_timestamp"] = mark_df["timestamp"].dt.floor("5min")
        spot_keyed = spot.reset_index()[["timestamp", "spot_close"]].rename(columns={"timestamp": "spot_timestamp"})
        matched = mark_df.merge(spot_keyed, on="spot_timestamp", how="inner")
        mark_basis = (
            (matched["mark_price"] / matched["spot_close"] - 1.0)
            .rename("mark_spot_basis")
            .set_axis(matched["spot_timestamp"])
        )
        mark_basis = mark_basis[~mark_basis.index.duplicated(keep="last")]
        joined = joined.join(mark_basis, how="left")
    else:
        joined["mark_spot_basis"] = pd.NA

    return joined.reset_index()


def write_basis_symbol(symbol: str) -> int:
    df = build_basis_symbol(symbol)
    if df is None or df.empty:
        return 0
    total = 0
    for y, chunk in df.groupby(df["timestamp"].dt.year):
        year_dir = OUT_DIR / f"symbol={symbol}" / f"year={y}"
        year_dir.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(year_dir / "basis_5m.parquet", index=False)
        total += len(chunk)
    return total


def main() -> None:
    im = pd.read_parquet(ROOT / "data_v2/instruments/instrument_master.parquet")
    symbols = sorted(im.loc[im["symbol"].str.endswith("USDT"), "symbol"])
    built, skipped = 0, 0
    for symbol in symbols:
        n = write_basis_symbol(symbol)
        if n:
            built += 1
            print(f"  {symbol:14} rows={n}", flush=True)
        else:
            skipped += 1
    print(f"\nbasis built for {built} symbols, {skipped} skipped (no perp+spot overlap yet) -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
