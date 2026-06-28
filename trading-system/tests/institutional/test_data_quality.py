"""
tests/institutional/test_data_quality.py
═══════════════════════════════════════════════════════════════════════════════
Tests du DataQualityChecker et du DataLoader.

Philosophie :
    Chaque test vérifie UN seul défaut de données.
    Les tests "cas OK" vérifient que des données propres passent sans issue.
    Les tests de détection vérifient que le défaut EST détecté.
    Les tests de blocage vérifient que PipelineBlockedError est levée.

Fixtures :
    clean_ohlcv_1h    : 200 barres 1h BTC propres
    clean_ohlcv_1d    : 200 barres 1d BTC propres
    checker_1h        : DataQualityChecker configuré pour 1h
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from institutional.data.checker import (
    CheckerConfig,
    DataQualityChecker,
    PipelineBlockedError,
)
from institutional.data.loaders import DataLoader, LoadConfig
from institutional.data.schemas import QualityLevel


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de construction de fixtures
# ══════════════════════════════════════════════════════════════════════════════


def _make_ohlcv(
    n: int = 200,
    freq: str = "1h",
    start: str = "2024-01-01",
    base_price: float = 50_000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Génère un DataFrame OHLCV propre avec DatetimeIndex UTC."""
    rng   = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n, freq=freq, tz="UTC")

    # Prix log-normal autour de base_price
    returns  = rng.normal(0, 0.002, size=n)
    closes   = base_price * np.cumprod(1 + returns)
    opens    = np.roll(closes, 1)
    opens[0] = base_price

    hl_range = closes * rng.uniform(0.001, 0.005, size=n)
    highs    = np.maximum(opens, closes) + hl_range
    lows     = np.minimum(opens, closes) - hl_range
    volumes  = rng.uniform(100, 1000, size=n)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def clean_ohlcv_1h() -> pd.DataFrame:
    return _make_ohlcv(200, "1h")


@pytest.fixture
def clean_ohlcv_1d() -> pd.DataFrame:
    return _make_ohlcv(200, "1D")


@pytest.fixture
def checker_1h() -> DataQualityChecker:
    return DataQualityChecker(CheckerConfig(expected_freq="1h"))


@pytest.fixture
def checker_1d() -> DataQualityChecker:
    return DataQualityChecker(CheckerConfig(expected_freq="1D"))


# ══════════════════════════════════════════════════════════════════════════════
# TestCheckerConfig
# ══════════════════════════════════════════════════════════════════════════════


class TestCheckerConfig:

    def test_default_config_valid(self) -> None:
        config = CheckerConfig()
        assert config.expected_freq  == "1h"
        assert config.nan_rate_warning  < config.nan_rate_critical

    def test_nan_rate_inversion_raises(self) -> None:
        with pytest.raises(ValueError, match="nan_rate_warning"):
            CheckerConfig(nan_rate_warning=0.10, nan_rate_critical=0.05)

    def test_outlier_zscore_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="outlier_zscore"):
            CheckerConfig(outlier_zscore=0.0)

    def test_gap_multiplier_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="gap_multiplier"):
            CheckerConfig(gap_multiplier=0.5)

    def test_max_stale_bars_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_stale_bars"):
            CheckerConfig(max_stale_bars=0)


# ══════════════════════════════════════════════════════════════════════════════
# TestDataQualityChecker — cas OK
# ══════════════════════════════════════════════════════════════════════════════


class TestDataQualityCheckerOK:

    def test_clean_data_is_valid(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        report = checker_1h.check(clean_ohlcv_1h, "BTCUSDT", "futures")
        assert report.is_valid(), f"Données propres invalides : {report.issues}"

    def test_clean_data_no_critical_issues(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        report = checker_1h.check(clean_ohlcv_1h, "BTCUSDT", "futures")
        critical = [i for i in report.issues if i.level == QualityLevel.CRITICAL]
        assert critical == []

    def test_check_or_raise_on_clean_data(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        report = checker_1h.check_or_raise(clean_ohlcv_1h, "BTCUSDT", "futures")
        assert report.rows == 200

    def test_report_fields_populated(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        report = checker_1h.check(clean_ohlcv_1h, "BTCUSDT", "futures")
        assert report.rows         == 200
        assert report.asset        == "BTCUSDT"
        assert report.source       == "futures"
        assert report.timeframe    == "1h"
        assert report.missing_rate == pytest.approx(0.0, abs=1e-6)
        assert report.first_timestamp is not None
        assert report.last_timestamp  is not None

    def test_daily_data_passes(
        self, checker_1d: DataQualityChecker, clean_ohlcv_1d: pd.DataFrame
    ) -> None:
        report = checker_1d.check(clean_ohlcv_1d, "BTCUSDT", "daily")
        assert report.is_valid()


# ══════════════════════════════════════════════════════════════════════════════
# TestDataQualityChecker — détection des défauts
# ══════════════════════════════════════════════════════════════════════════════


class TestDataQualityCheckerDetection:

    # ── Index ─────────────────────────────────────────────────────────────────

    def test_unsorted_timestamps_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        # Inverser quelques lignes pour créer un désordre
        df = clean_ohlcv_1h.copy()
        new_idx    = df.index.tolist()
        new_idx[5], new_idx[6] = new_idx[6], new_idx[5]
        df.index   = pd.DatetimeIndex(new_idx)

        report = checker_1h.check(df, "BTCUSDT", "futures")
        issue_fields = [i.field for i in report.issues]
        assert "index" in issue_fields
        assert not report.is_valid()

    def test_duplicate_timestamps_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df  = clean_ohlcv_1h.copy()
        dup = df.iloc[[10]]              # copie de la ligne 10
        df  = pd.concat([df, dup]).sort_index()

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.duplicate_count >= 1
        assert not report.is_valid()
        critical = [i for i in report.issues if i.level == QualityLevel.CRITICAL]
        assert any("dupliqué" in i.message for i in critical)

    def test_timezone_absent_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.index = df.index.tz_localize(None)     # supprimer la timezone

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        tz_issues = [i for i in report.issues if "tz" in i.field.lower() or "timezone" in i.message.lower()]
        assert len(tz_issues) > 0

    def test_timezone_non_utc_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.index = df.index.tz_convert("America/New_York")

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        assert any("UTC" in i.message for i in report.issues)

    def test_gap_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        # Créer un trou de 5h (> 1.5 × 1h)
        df = clean_ohlcv_1h.drop(clean_ohlcv_1h.index[50:54])

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.max_gap_minutes > 60.0   # gap > 1h
        gap_issues = [i for i in report.issues if "gap" in i.field.lower()]
        assert len(gap_issues) > 0

    def test_missing_required_column_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.drop(columns=["volume"])

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        col_issues = [i for i in report.issues if i.field == "columns"]
        assert any("volume" in i.message for i in col_issues)

    def test_nan_values_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[10:15, df.columns.get_loc("close")] = np.nan   # 5 NaN sur close

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.missing_rate > 0
        nan_issues = [i for i in report.issues if i.field == "nan"]
        assert len(nan_issues) > 0

    def test_inf_values_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[5, df.columns.get_loc("close")] = np.inf

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        inf_issues = [i for i in report.issues if i.field == "inf"]
        assert len(inf_issues) > 0, f"Issues : {report.issues}"

    # ── Prix ──────────────────────────────────────────────────────────────────

    def test_negative_price_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[3, df.columns.get_loc("close")] = -100.0

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        assert any(i.field == "close" for i in report.issues)

    def test_zero_price_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[3, df.columns.get_loc("open")] = 0.0

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()

    # ── OHLC cohérence ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("violation,setup", [
        ("high < low",   lambda df: df.__setitem__(("high", df.index[5]), df.loc[df.index[5], "low"] - 100)),
        ("high < open",  lambda df: df.__setitem__(("high", df.index[5]), df.loc[df.index[5], "open"] - 100)),
        ("high < close", lambda df: df.__setitem__(("high", df.index[5]), df.loc[df.index[5], "close"] - 100)),
        ("low > open",   lambda df: df.__setitem__(("low", df.index[5]), df.loc[df.index[5], "open"] + 100)),
        ("low > close",  lambda df: df.__setitem__(("low", df.index[5]), df.loc[df.index[5], "close"] + 100)),
    ])
    def test_ohlc_inconsistency_detected(
        self,
        checker_1h: DataQualityChecker,
        clean_ohlcv_1h: pd.DataFrame,
        violation: str,
        setup: object,
    ) -> None:
        df = clean_ohlcv_1h.copy()
        # Appliquer la violation directement
        idx = df.index[5]
        col = violation.split(" ")[0]   # "high" ou "low"
        comparand_col = violation.split(" ")[2]
        comparand_val = df.at[idx, comparand_col]
        offset = -100.0 if "<" in violation else 100.0
        df.at[idx, col] = comparand_val + offset

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid(), f"Violation {violation!r} non détectée"
        ohlc_issues = [i for i in report.issues if i.field in ("high", "low")]
        assert len(ohlc_issues) > 0, f"Issue OHLC manquante pour {violation}"

    # ── Volume ────────────────────────────────────────────────────────────────

    def test_negative_volume_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[7, df.columns.get_loc("volume")] = -1.0

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert not report.is_valid()
        vol_issues = [i for i in report.issues if i.field == "volume"]
        assert any("< 0" in i.message for i in vol_issues)

    def test_nan_volume_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[7, df.columns.get_loc("volume")] = np.nan

        report = checker_1h.check(df, "BTCUSDT", "futures")
        vol_issues = [i for i in report.issues if i.field == "volume"]
        assert any("NaN" in i.message or "nan" in i.message.lower() for i in vol_issues)

    # ── Stale data ────────────────────────────────────────────────────────────

    def test_stale_data_detected(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        checker = DataQualityChecker(CheckerConfig(expected_freq="1h", max_stale_bars=3))
        df = clean_ohlcv_1h.copy()
        # Créer 10 barres consécutives avec le même close (> max_stale_bars=3)
        stale_val = df.iloc[20]["close"]
        df.iloc[20:30, df.columns.get_loc("close")] = stale_val

        report = checker.check(df, "BTCUSDT", "futures")
        stale_issues = [i for i in report.issues if "stale" in i.message.lower() or "identique" in i.message.lower()]
        assert len(stale_issues) > 0, f"Stale data non détectée. Issues : {[i.message for i in report.issues]}"
        assert report.stale_intervals > 0

    # ── Outliers ──────────────────────────────────────────────────────────────

    def test_extreme_outlier_detected(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        # Créer un saut de 80% sur une barre
        df.iloc[50, df.columns.get_loc("close")] *= 1.8

        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.outlier_count > 0
        outlier_issues = [i for i in report.issues if "outlier" in i.message.lower()]
        assert len(outlier_issues) > 0

    # ── PipelineBlockedError ──────────────────────────────────────────────────

    def test_check_or_raise_blocks_on_duplicate(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        # Ajouter un duplicat
        dup = df.iloc[[5]]
        df  = pd.concat([df, dup]).sort_index()

        with pytest.raises(PipelineBlockedError) as exc_info:
            checker_1h.check_or_raise(df, "BTCUSDT", "futures")

        assert exc_info.value.report.duplicate_count >= 1

    def test_pipeline_blocked_error_contains_report(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[3, df.columns.get_loc("close")] = -1.0

        with pytest.raises(PipelineBlockedError) as exc_info:
            checker_1h.check_or_raise(df, "BTCUSDT", "futures")

        report = exc_info.value.report
        assert report is not None
        assert report.asset == "BTCUSDT"

    # ── Cas limites ───────────────────────────────────────────────────────────

    def test_empty_dataframe_handled(
        self, checker_1h: DataQualityChecker
    ) -> None:
        df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.rows == 0

    def test_single_row_dataframe_handled(
        self, checker_1h: DataQualityChecker, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.iloc[:1]
        report = checker_1h.check(df, "BTCUSDT", "futures")
        assert report.rows == 1

    def test_non_datetime_index_flagged(
        self, checker_1h: DataQualityChecker
    ) -> None:
        df = pd.DataFrame(
            {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100.0]},
            index=[0],
        )
        report = checker_1h.check(df, "TEST", "raw")
        assert any("DatetimeIndex" in i.message for i in report.issues)


# ══════════════════════════════════════════════════════════════════════════════
# TestDataLoader
# ══════════════════════════════════════════════════════════════════════════════


class TestDataLoader:

    # ── Parquet ───────────────────────────────────────────────────────────────

    def test_load_parquet_clean(self, clean_ohlcv_1h: pd.DataFrame) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "btc.parquet"
            # Sauvegarder avec timestamp comme colonne
            df_with_col = clean_ohlcv_1h.reset_index().rename(
                columns={"index": "timestamp"}
            )
            df_with_col.to_parquet(path, index=False)

            loader = DataLoader(
                load_config=LoadConfig(on_duplicate="raise"),
                check_config=CheckerConfig(expected_freq="1h"),
            )
            result, report = loader.load_parquet(path, "BTCUSDT", "futures")

            assert isinstance(result.index, pd.DatetimeIndex)
            assert result.index.tz is not None
            assert report.is_valid()
            assert len(result) == 200

    def test_load_parquet_nonexistent_raises(self) -> None:
        loader = DataLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_parquet("/tmp/nonexistent_file.parquet", "X", "Y")

    def test_load_parquet_wrong_extension_raises(self) -> None:
        loader = DataLoader()
        with pytest.raises(ValueError, match="Extension"):
            loader.load_parquet("/tmp/file.csv", "X", "Y")

    # ── CSV ───────────────────────────────────────────────────────────────────

    def test_load_csv_clean(self, clean_ohlcv_1h: pd.DataFrame) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "btc.csv"
            df_with_col = clean_ohlcv_1h.reset_index().rename(
                columns={"index": "timestamp"}
            )
            df_with_col.to_csv(path, index=False)

            loader = DataLoader(
                load_config=LoadConfig(on_duplicate="keep_first"),
                check_config=CheckerConfig(expected_freq="1h"),
            )
            result, report = loader.load_csv(path, "BTCUSDT", "futures")

            assert len(result) == 200
            assert report.is_valid()

    def test_load_csv_nonexistent_raises(self) -> None:
        loader = DataLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_csv("/tmp/nonexistent.csv", "X", "Y")

    # ── Duplicats ─────────────────────────────────────────────────────────────

    def test_on_duplicate_raise_raises(self, clean_ohlcv_1h: pd.DataFrame) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "btc.parquet"
            df_dup = pd.concat([clean_ohlcv_1h, clean_ohlcv_1h.iloc[[5]]])
            df_dup = df_dup.reset_index().rename(columns={"index": "timestamp"})
            df_dup.to_parquet(path, index=False)

            loader = DataLoader(
                load_config=LoadConfig(on_duplicate="raise"),
                check_config=CheckerConfig(expected_freq="1h"),
            )
            with pytest.raises(ValueError, match="dupliqué"):
                loader.load_parquet(path, "BTCUSDT", "futures")

    def test_on_duplicate_keep_first_silently_drops(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "btc.parquet"
            df_dup = pd.concat([clean_ohlcv_1h, clean_ohlcv_1h.iloc[[5]]])
            df_dup = df_dup.reset_index().rename(columns={"index": "timestamp"})
            df_dup.to_parquet(path, index=False)

            loader = DataLoader(
                load_config=LoadConfig(on_duplicate="keep_first", raise_on_invalid=False),
                check_config=CheckerConfig(expected_freq="1h"),
            )
            result, report = loader.load_parquet(path, "BTCUSDT", "futures")
            assert len(result) == 200   # duplicat supprimé

    # ── FFill ─────────────────────────────────────────────────────────────────

    def test_no_ffill_without_limit(self, clean_ohlcv_1h: pd.DataFrame) -> None:
        """Vérifier que LoadConfig(ffill_limit=None) ne fait jamais de ffill."""
        df = clean_ohlcv_1h.copy()
        df.iloc[10, df.columns.get_loc("close")] = np.nan

        loader = DataLoader(
            load_config=LoadConfig(ffill_limit=None, raise_on_invalid=False),
        )
        result, _ = loader.from_dataframe(df, "BTCUSDT", "futures")
        # NaN doit subsister
        assert pd.isna(result.iloc[10]["close"])

    def test_ffill_with_limit_fills_up_to_limit(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        # Créer 3 NaN consécutifs
        df.iloc[10:13, df.columns.get_loc("close")] = np.nan

        loader = DataLoader(
            load_config=LoadConfig(ffill_limit=2, raise_on_invalid=False),
        )
        result, _ = loader.from_dataframe(df, "BTCUSDT", "futures")

        # Les 2 premiers NaN sont comblés, le 3e reste NaN
        assert not pd.isna(result.iloc[10]["close"])   # comblé
        assert not pd.isna(result.iloc[11]["close"])   # comblé
        assert pd.isna(result.iloc[12]["close"])       # > limit → NaN

    # ── Retour de from_dataframe ──────────────────────────────────────────────

    def test_from_dataframe_returns_report(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        loader = DataLoader()
        result, report = loader.from_dataframe(clean_ohlcv_1h, "BTCUSDT", "futures")
        assert report.asset == "BTCUSDT"
        assert report.rows  == len(result)

    def test_raise_on_invalid_true_blocks_bad_data(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[5, df.columns.get_loc("close")] = -999.0

        loader = DataLoader(
            load_config=LoadConfig(raise_on_invalid=True),
        )
        with pytest.raises(PipelineBlockedError):
            loader.from_dataframe(df, "BTCUSDT", "futures")

    def test_raise_on_invalid_false_returns_report(
        self, clean_ohlcv_1h: pd.DataFrame
    ) -> None:
        df = clean_ohlcv_1h.copy()
        df.iloc[5, df.columns.get_loc("close")] = -999.0

        loader = DataLoader(
            load_config=LoadConfig(raise_on_invalid=False),
        )
        result, report = loader.from_dataframe(df, "BTCUSDT", "futures")
        assert not report.is_valid()    # invalide mais pas d'exception

    # ── LoadConfig validation ─────────────────────────────────────────────────

    def test_invalid_on_duplicate_raises(self) -> None:
        with pytest.raises(ValueError, match="on_duplicate"):
            LoadConfig(on_duplicate="INVALID")

    def test_ffill_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="ffill_limit"):
            LoadConfig(ffill_limit=0)
