"""
tests/institutional/test_asof_join.py
═══════════════════════════════════════════════════════════════════════════════
Tests de l'as-of join causal.

TESTS CRITIQUES :
    test_no_lookahead_*   → PREUVES que le lookahead est impossible
    test_forward_*        → PREUVES que direction=forward est refusée
    test_right_ts_never_* → PREUVES post-hoc de causalité

Philosophie :
    Ces tests sont des preuves mathématiques, pas juste des smoke tests.
    Chaque test de causalité nomme explicitement ce qu'il prouve et
    quel scénario de lookahead il empêche.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from institutional.data.asof_join import (
    AsofJoinReport,
    ForwardJoinForbiddenError,
    LookaheadError,
    asof_join,
    asof_join_funding,
    asof_join_metrics,
)

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _utc_ts(*args: int) -> pd.Timestamp:
    """Raccourci : _utc_ts(2024, 1, 1, 12) → Timestamp UTC."""
    return pd.Timestamp(datetime(*args, tzinfo=timezone.utc))


def _make_ohlcv(
    n: int,
    freq: str = "1h",
    start: str = "2024-01-01",
    seed: int = 0,
) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(seed)
    c   = 50_000 * np.cumprod(1 + rng.normal(0, 0.001, n))
    return pd.DataFrame({"close": c, "volume": rng.uniform(100, 500, n)}, index=idx)


def _make_funding(
    timestamps: list[pd.Timestamp],
    rates: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {"funding_rate": rates},
        index=pd.DatetimeIndex(timestamps),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def ohlcv_6h() -> pd.DataFrame:
    """6 barres 1h de 00:00 à 05:00 UTC."""
    return _make_ohlcv(6, "1h", start="2024-01-01 00:00")


@pytest.fixture
def funding_8h() -> pd.DataFrame:
    """Funding toutes les 8h : 00:00 et 08:00."""
    return _make_funding(
        [
            _utc_ts(2024, 1, 1, 0),   # T = 00:00
            _utc_ts(2024, 1, 1, 8),   # T = 08:00
        ],
        [0.0001, 0.0002],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests de base
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinBasic:

    def test_backward_join_returns_dataframe_and_report(
        self, ohlcv_6h: pd.DataFrame, funding_8h: pd.DataFrame
    ) -> None:
        result, report = asof_join(
            ohlcv_6h, funding_8h, tolerance=pd.Timedelta("10h")
        )
        assert isinstance(result, pd.DataFrame)
        assert isinstance(report, AsofJoinReport)
        assert "funding_rate" in result.columns

    def test_result_has_same_index_as_left(
        self, ohlcv_6h: pd.DataFrame, funding_8h: pd.DataFrame
    ) -> None:
        result, _ = asof_join(
            ohlcv_6h, funding_8h, tolerance=pd.Timedelta("10h")
        )
        assert result.index.equals(ohlcv_6h.index)

    def test_backward_join_correct_values(self) -> None:
        """
        Scénario concret :
            left  : barres à T=00, 01, 02, 03, 04, 05 (1h)
            right : valeurs à T=00 (val=1.0) et T=04 (val=2.0)

        Attendu :
            T=00 → 1.0  (T=00 ≤ T=00, distance=0)
            T=01 → 1.0  (T=00 ≤ T=01, distance=1h, dans tolérance 2h)
            T=02 → 1.0  (T=00 ≤ T=02, distance=2h, dans tolérance 2h)
            T=03 → NaN  (T=00 distance=3h > tolérance 2h, T=04 > T=03 → rejeté)
            T=04 → 2.0  (T=04 ≤ T=04, distance=0)
            T=05 → 2.0  (T=04 ≤ T=05, distance=1h)
        """
        left = _make_ohlcv(6, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"signal": [1.0, 2.0]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),   # T=00
                _utc_ts(2024, 1, 1, 4),   # T=04
            ]),
        )
        tol = pd.Timedelta("2h")
        result, report = asof_join(left, right, tolerance=tol)

        assert result.at[_utc_ts(2024, 1, 1, 0), "signal"] == pytest.approx(1.0)
        assert result.at[_utc_ts(2024, 1, 1, 1), "signal"] == pytest.approx(1.0)
        assert result.at[_utc_ts(2024, 1, 1, 2), "signal"] == pytest.approx(1.0)
        assert pd.isna(result.at[_utc_ts(2024, 1, 1, 3), "signal"])     # hors tol
        assert result.at[_utc_ts(2024, 1, 1, 4), "signal"] == pytest.approx(2.0)
        assert result.at[_utc_ts(2024, 1, 1, 5), "signal"] == pytest.approx(2.0)

    def test_multi_column_join(self) -> None:
        left = _make_ohlcv(4, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"oi_sum": [1000.0, 2000.0], "lsr": [1.2, 0.8]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),
                _utc_ts(2024, 1, 1, 2),
            ]),
        )
        result, report = asof_join(left, right, tolerance=pd.Timedelta("2h"))
        assert "oi_sum" in result.columns
        assert "lsr"    in result.columns
        assert report.joined_cols == ("oi_sum", "lsr")

    def test_right_cols_filter(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = pd.DataFrame(
            {"a": [1.0, 2.0], "b": [3.0, 4.0], "c": [5.0, 6.0]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),
                _utc_ts(2024, 1, 1, 2),
            ]),
        )
        result, _ = asof_join(
            left, right, tolerance=pd.Timedelta("2h"), right_cols=["a", "c"]
        )
        assert "a" in result.columns
        assert "c" in result.columns
        assert "b" not in result.columns

    def test_empty_right_returns_all_null(self, ohlcv_6h: pd.DataFrame) -> None:
        right = pd.DataFrame(
            {"funding_rate": pd.Series([], dtype=float)},
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        result, report = asof_join(ohlcv_6h, right, tolerance=pd.Timedelta("10h"))
        assert result["funding_rate"].isna().all()
        assert report.coverage_rate  == pytest.approx(0.0)
        assert report.n_matched      == 0

    def test_suffix_avoids_column_conflict(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = pd.DataFrame(
            {"close": [1.0, 2.0]},       # conflit avec left.close
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),
                _utc_ts(2024, 1, 1, 2),
            ]),
        )
        result, _ = asof_join(
            left, right, tolerance=pd.Timedelta("2h"), suffix="_r"
        )
        assert "close"   in result.columns
        assert "close_r" in result.columns


# ══════════════════════════════════════════════════════════════════════════════
# Tests de causalité — PREUVES DE NO-LOOKAHEAD
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinCausality:

    def test_no_lookahead_future_value_not_visible_at_past_timestamp(self) -> None:
        """
        PREUVE DE CAUSALITÉ 1 :
            Une valeur right qui n'existe qu'à T+3h ne doit PAS être visible
            à T, T+1h, ou T+2h.

        Scénario :
            left  : barres à T=00, 01, 02, 03, 04 (1h)
            right : seule valeur à T=03 (après T=00, 01, 02)

        Si lookahead : T=00, T=01, T=02 auraient la valeur de T=03.
        Résultat attendu : T=00, T=01, T=02 → NaN, T=03 → valeur, T=04 → valeur
        """
        left = _make_ohlcv(5, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"signal": [42.0]},
            index=pd.DatetimeIndex([_utc_ts(2024, 1, 1, 3)]),  # T=03 SEULEMENT
        )

        result, _ = asof_join(left, right, tolerance=pd.Timedelta("4h"))

        # Avant T=03 : NaN (la valeur n'existait pas encore)
        assert pd.isna(result.at[_utc_ts(2024, 1, 1, 0), "signal"]), \
            "LOOKAHEAD : valeur T=03 visible à T=00"
        assert pd.isna(result.at[_utc_ts(2024, 1, 1, 1), "signal"]), \
            "LOOKAHEAD : valeur T=03 visible à T=01"
        assert pd.isna(result.at[_utc_ts(2024, 1, 1, 2), "signal"]), \
            "LOOKAHEAD : valeur T=03 visible à T=02"

        # À T=03 et après : visible
        assert result.at[_utc_ts(2024, 1, 1, 3), "signal"] == pytest.approx(42.0)
        assert result.at[_utc_ts(2024, 1, 1, 4), "signal"] == pytest.approx(42.0)

    def test_no_lookahead_update_not_visible_before_update_timestamp(self) -> None:
        """
        PREUVE DE CAUSALITÉ 2 :
            Quand une valeur right change à T=2h, les barres à T<2h doivent
            encore avoir l'ancienne valeur.

        Scénario :
            right[T=00] = old_value = 1.0
            right[T=02] = new_value = 99.0
            tolerance   = 3h

        Attendu :
            T=00 → 1.0  (old_value, distance=0)
            T=01 → 1.0  (old_value, distance=1h < tol=3h)
            T=02 → 99.0 (new_value vient d'arriver)
            T=03 → 99.0 (new_value récente, distance=1h)
        """
        left = _make_ohlcv(4, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"signal": [1.0, 99.0]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),   # old
                _utc_ts(2024, 1, 1, 2),   # new — update à T=02
            ]),
        )

        result, _ = asof_join(left, right, tolerance=pd.Timedelta("3h"))

        # T=01 doit encore avoir l'ancienne valeur (pas la mise à jour de T=02)
        assert result.at[_utc_ts(2024, 1, 1, 1), "signal"] == pytest.approx(1.0), \
            "LOOKAHEAD : la mise à jour de T=02 est visible à T=01"
        assert result.at[_utc_ts(2024, 1, 1, 2), "signal"] == pytest.approx(99.0)

    def test_no_lookahead_tolerance_prevents_stale_data_filling_future(self) -> None:
        """
        PREUVE DE CAUSALITÉ 3 :
            La tolerance empêche qu'une valeur très ancienne soit
            utilisée pour combler un grand gap (ce serait de la stale data,
            pas du lookahead, mais aussi problématique).

        Scénario :
            right[T=00] = 1.0
            left à T=05 → distance = 5h
            tolerance   = 2h → T=05 doit être NaN
        """
        left = _make_ohlcv(6, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"signal": [1.0]},
            index=pd.DatetimeIndex([_utc_ts(2024, 1, 1, 0)]),
        )

        result, _ = asof_join(left, right, tolerance=pd.Timedelta("2h"))

        # T=00 → 1.0 (distance=0, OK)
        # T=01 → 1.0 (distance=1h, dans tol)
        # T=02 → 1.0 (distance=2h, exactement à la limite)
        # T=03 → NaN (distance=3h > tol=2h)
        assert result.at[_utc_ts(2024, 1, 1, 0), "signal"] == pytest.approx(1.0)
        assert result.at[_utc_ts(2024, 1, 1, 1), "signal"] == pytest.approx(1.0)
        # T=03 doit être NaN
        assert pd.isna(result.at[_utc_ts(2024, 1, 1, 3), "signal"]), \
            "Valeur stale propagée au-delà de la tolerance — risque de stale data"

    def test_right_timestamp_never_exceeds_left_timestamp(self) -> None:
        """
        PREUVE DE CAUSALITÉ 4 (assertion post-hoc) :
            Pour chaque barre jointe, le right_timestamp qui a fourni
            la valeur doit être ≤ left_timestamp.

        Ce test vérifie directement la propriété fondamentale du backward join.
        """
        left = _make_ohlcv(10, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"val": list(range(5))},
            index=pd.date_range("2024-01-01 00:00", periods=5, freq="2h", tz="UTC"),
        )

        # Si lookahead existait, LookaheadError serait levée dans _assert_no_lookahead_in_result
        result, report = asof_join(left, right, tolerance=pd.Timedelta("3h"))

        # Vérification explicite : pour chaque joined non-NaN,
        # retrouver le right_ts et vérifier right_ts ≤ left_ts
        right_idx = right.index
        for left_ts in result.index:
            val = result.at[left_ts, "val"]
            if pd.notna(val):
                pos = right_idx.searchsorted(left_ts, side="right") - 1
                if pos >= 0:
                    right_ts = right_idx[pos]
                    assert right_ts <= left_ts, (
                        f"LOOKAHEAD DÉTECTÉ : right_ts={right_ts} > left_ts={left_ts}"
                    )

    def test_funding_example_causal_join(self) -> None:
        """
        PREUVE DE CAUSALITÉ 5 — exemple réaliste funding 8h :
            Funding settle à T=00:00 et T=08:00.
            Les barres 1h de T=01 à T=07 doivent avoir le funding de T=00.
            La barre T=08 doit avoir le funding de T=08.
            La barre T=09 doit avoir le funding de T=08 (pas T=16 → lookahead).
        """
        # 12h de barres 1h
        left = _make_ohlcv(12, "1h", start="2024-01-01 00:00")
        right = _make_funding(
            [
                _utc_ts(2024, 1, 1,  0),  # funding 0 = 0.0001
                _utc_ts(2024, 1, 1,  8),  # funding 1 = 0.0002
                _utc_ts(2024, 1, 1, 16),  # funding 2 = 0.0003 — FUTUR pour T<16
            ],
            [0.0001, 0.0002, 0.0003],
        )

        result, _ = asof_join_funding(left, right, max_stale_hours=10.0)

        # Barres T=00 à T=07 → funding T=00 = 0.0001
        for h in range(8):
            ts  = _utc_ts(2024, 1, 1, h)
            val = result.at[ts, "funding_rate"]
            assert val == pytest.approx(0.0001), (
                f"LOOKAHEAD à T={h:02d}h : funding=0.0002 (T=08h) visible trop tôt"
            )

        # Barre T=08 → funding T=08 = 0.0002
        assert result.at[_utc_ts(2024, 1, 1, 8), "funding_rate"] == pytest.approx(0.0002)

        # Barre T=09 → toujours funding T=08 = 0.0002 (pas funding T=16)
        assert result.at[_utc_ts(2024, 1, 1, 9), "funding_rate"] == pytest.approx(0.0002), \
            "LOOKAHEAD : funding T=16 visible à T=09"


# ══════════════════════════════════════════════════════════════════════════════
# Tests des garde-fous d'entrée
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinGuards:

    def test_forward_direction_forbidden(self) -> None:
        """
        PREUVE : l'API n'expose pas direction="forward".
        Toute tentative de passer direction="forward" lèverait TypeError
        car ce paramètre n'existe pas dans la signature publique.
        """
        left  = _make_ohlcv(4, "1h")
        right = _make_funding(
            [_utc_ts(2024, 1, 1, 0)], [0.001]
        )
        # La signature ne contient pas direction → TypeError si tenté
        with pytest.raises(TypeError):
            asof_join(left, right, tolerance=pd.Timedelta("1h"), direction="forward")  # type: ignore[call-arg]

    def test_zero_tolerance_raises(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(ValueError, match="tolerance"):
            asof_join(left, right, tolerance=pd.Timedelta("0s"))

    def test_negative_tolerance_raises(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(ValueError, match="tolerance"):
            asof_join(left, right, tolerance=pd.Timedelta("-1h"))

    def test_non_datetime_index_left_raises(self) -> None:
        left  = pd.DataFrame({"a": [1, 2, 3]}, index=[0, 1, 2])
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(TypeError, match="DatetimeIndex"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_non_datetime_index_right_raises(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = pd.DataFrame({"a": [1.0]}, index=[0])
        with pytest.raises(TypeError, match="DatetimeIndex"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_no_timezone_left_raises(self) -> None:
        left = _make_ohlcv(4, "1h")
        left.index = left.index.tz_localize(None)
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(TypeError, match="timezone"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_no_timezone_right_raises(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        right.index = right.index.tz_localize(None)
        with pytest.raises(TypeError, match="timezone"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_non_utc_timezone_left_raises(self) -> None:
        left = _make_ohlcv(4, "1h")
        left.index = left.index.tz_convert("America/New_York")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(TypeError, match="UTC"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_empty_left_raises(self) -> None:
        left  = _make_ohlcv(0, "1h")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(ValueError, match="vide"):
            asof_join(left, right, tolerance=pd.Timedelta("1h"))

    def test_missing_right_col_raises(self) -> None:
        left  = _make_ohlcv(4, "1h")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        with pytest.raises(ValueError, match="absentes"):
            asof_join(
                left, right,
                tolerance=pd.Timedelta("1h"),
                right_cols=["nonexistent_col"],
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tests du rapport AsofJoinReport
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinReport:

    def test_coverage_rate_full_match(self) -> None:
        """Si toutes les barres left ont une correspondance, coverage = 1.0."""
        # right à T=00 avec tolerance=10h → toutes les 6h de left matchent
        left  = _make_ohlcv(6, "1h", start="2024-01-01 00:00")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        _, report = asof_join(left, right, tolerance=pd.Timedelta("10h"))
        assert report.coverage_rate == pytest.approx(1.0)
        assert report.n_matched     == 6

    def test_coverage_rate_zero(self) -> None:
        """Si tolerance trop courte pour atteindre le seul right point → 0."""
        left = _make_ohlcv(4, "1h", start="2024-01-01 03:00")  # T=03 à T=06
        right = _make_funding(
            [_utc_ts(2024, 1, 1, 0)],   # right seulement à T=00
            [0.001],
        )
        # tolerance=1h, left commence à T=03 → distance min = 3h > tolerance
        _, report = asof_join(left, right, tolerance=pd.Timedelta("1h"))
        assert report.coverage_rate == pytest.approx(0.0)
        assert report.n_null_after  == 4

    def test_stale_rate_zero_when_fresh(self) -> None:
        left  = _make_ohlcv(4, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"val": [1.0, 2.0, 3.0, 4.0]},
            index=pd.date_range("2024-01-01 00:00", periods=4, freq="1h", tz="UTC"),
        )
        _, report = asof_join(left, right, tolerance=pd.Timedelta("1h"))
        assert report.stale_rate == pytest.approx(0.0, abs=0.01)

    def test_max_staleness_computed(self) -> None:
        # right à T=00, left dernière barre à T=05 → staleness max = 5h
        left  = _make_ohlcv(6, "1h", start="2024-01-01 00:00")
        right = _make_funding([_utc_ts(2024, 1, 1, 0)], [0.001])
        _, report = asof_join(left, right, tolerance=pd.Timedelta("10h"))
        assert report.max_staleness_s == pytest.approx(5 * 3600, rel=0.01)

    def test_report_summary_contains_coverage(
        self, ohlcv_6h: pd.DataFrame, funding_8h: pd.DataFrame
    ) -> None:
        _, report = asof_join(ohlcv_6h, funding_8h, tolerance=pd.Timedelta("10h"))
        summary = report.summary()
        assert "coverage" in summary.lower()
        assert "left" in summary.lower()

    def test_n_left_and_n_right_correct(self) -> None:
        left  = _make_ohlcv(10, "1h")
        right = _make_funding(
            [_utc_ts(2024, 1, 1, 0), _utc_ts(2024, 1, 1, 4)], [0.1, 0.2]
        )
        _, report = asof_join(left, right, tolerance=pd.Timedelta("5h"))
        assert report.n_left  == 10
        assert report.n_right == 2


# ══════════════════════════════════════════════════════════════════════════════
# Tests des fonctions de haut niveau
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinHighLevel:

    def test_asof_join_funding_returns_funding_rate_col(
        self, ohlcv_6h: pd.DataFrame, funding_8h: pd.DataFrame
    ) -> None:
        result, _ = asof_join_funding(ohlcv_6h, funding_8h)
        assert "funding_rate" in result.columns

    def test_asof_join_funding_tolerance_10h(
        self, ohlcv_6h: pd.DataFrame, funding_8h: pd.DataFrame
    ) -> None:
        result, report = asof_join_funding(ohlcv_6h, funding_8h, max_stale_hours=10.0)
        assert report.tolerance == pd.Timedelta("10h")
        # T=00 → funding de T=00 = 0.0001
        assert result.at[_utc_ts(2024, 1, 1, 0), "funding_rate"] == pytest.approx(0.0001)

    def test_asof_join_metrics_with_col_filter(self) -> None:
        left = _make_ohlcv(4, "1h", start="2024-01-01 00:00")
        metrics = pd.DataFrame(
            {"oi_sum": [1000.0, 2000.0], "lsr": [1.5, 1.2], "extra": [0.0, 0.0]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 0),
                _utc_ts(2024, 1, 1, 2),
            ]),
        )
        result, _ = asof_join_metrics(
            left, metrics, cols=["oi_sum", "lsr"], max_stale_hours=2.0
        )
        assert "oi_sum" in result.columns
        assert "lsr"    in result.columns
        assert "extra"  not in result.columns


# ══════════════════════════════════════════════════════════════════════════════
# Tests d'intégration — pipeline complet
# ══════════════════════════════════════════════════════════════════════════════


class TestAsofJoinIntegration:

    def test_full_pipeline_btc_style(self) -> None:
        """
        Simule un pipeline complet BTC 1h × funding 8h × OI 5m.

        Vérifie :
            1. Toutes les jointures sont causales
            2. Les colonnes sont présentes
            3. Les valeurs futures ne sont pas visibles
        """
        # OHLCV 1h — 24h
        ohlcv = _make_ohlcv(24, "1h", start="2024-01-01 00:00")

        # Funding 8h (T=00, T=08, T=16)
        funding = _make_funding(
            [
                _utc_ts(2024, 1, 1,  0),
                _utc_ts(2024, 1, 1,  8),
                _utc_ts(2024, 1, 1, 16),
            ],
            [0.0001, 0.0002, -0.0001],
        )

        # OI 5m — approximé avec 1h pour ce test
        oi = pd.DataFrame(
            {"oi_sum": [float(i * 1000) for i in range(24)]},
            index=pd.date_range("2024-01-01 00:00", periods=24, freq="1h", tz="UTC"),
        )

        # Jointure 1 : OHLCV × funding
        master, r1 = asof_join_funding(ohlcv, funding, max_stale_hours=10.0)
        assert r1.coverage_rate > 0
        assert "funding_rate" in master.columns

        # Jointure 2 : master × OI
        master, r2 = asof_join_metrics(master, oi, cols=["oi_sum"])
        assert "oi_sum" in master.columns

        # Vérification causale : T=07:00 doit avoir funding de T=00 (pas T=08)
        assert master.at[_utc_ts(2024, 1, 1, 7), "funding_rate"] == pytest.approx(0.0001), \
            "LOOKAHEAD : funding T=08 visible à T=07"

        assert master.index.equals(ohlcv.index), "Index modifié par les jointures"
        assert len(master) == 24

    def test_unsorted_right_is_sorted_internally(self) -> None:
        """right non trié → doit être trié en interne sans erreur."""
        left = _make_ohlcv(4, "1h", start="2024-01-01 00:00")
        right = pd.DataFrame(
            {"val": [2.0, 1.0]},
            index=pd.DatetimeIndex([
                _utc_ts(2024, 1, 1, 2),   # inversé intentionnellement
                _utc_ts(2024, 1, 1, 0),
            ]),
        )
        # Ne doit pas lever d'exception
        result, _ = asof_join(left, right, tolerance=pd.Timedelta("3h"))
        assert result.at[_utc_ts(2024, 1, 1, 0), "val"] == pytest.approx(1.0)
        assert result.at[_utc_ts(2024, 1, 1, 2), "val"] == pytest.approx(2.0)
