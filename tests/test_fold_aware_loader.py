"""
tests/test_fold_aware_loader.py
─────────────────────────────────────────────────────────────────────────────
Tests unitaires pour FoldAwareModelLoader et BacktestFoldPlan.
Ces tests valident l'intégrité walk-forward sans charger de vrais modèles.

Run : pytest tests/test_fold_aware_loader.py -v
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ajouter la racine du projet au path
sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.run_backtest_engine import (
    BacktestFoldPlan,
    FoldAwareModelLoader,
    FoldMissingError,
    annotate_trades_with_fold,
    build_fold_model_usage,
)
from src.institutional.backtest.event_backtester import (
    BacktestConfig, BacktestResult, Trade,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_model_tree(tmp_path: Path) -> Path:
    """Crée une arborescence de faux modèles fold pour engine/asset."""
    engine = "btc_eth_trend"
    asset  = "BTCUSDT"
    for year in [2022, 2023, 2024, 2025]:
        fold_dir = tmp_path / "artifacts/institutional/backtests" / engine / asset / "v1.0" / str(year)
        fold_dir.mkdir(parents=True)
        # Faux .pkl — juste besoin que le fichier existe
        (fold_dir / f"model_{year}.pkl").write_bytes(b"fake_model")
    return tmp_path


@pytest.fixture
def loader() -> FoldAwareModelLoader:
    return FoldAwareModelLoader()


# ─── Tests BacktestFoldPlan ────────────────────────────────────────────────────

class TestBacktestFoldPlan:
    def test_fold_plan_fields(self):
        plan = BacktestFoldPlan(
            fold_year         = 2023,
            model_path        = Path("/some/path/model_2023.pkl"),
            test_start        = "2023-01-01",
            test_end          = "2023-12-31",
            train_period      = "2021-01-01 → 2022-09-30",
            validation_period = "2022-10-01 → 2022-12-31",
        )
        assert plan.fold_year == 2023
        assert plan.model_version == "v1.0"
        assert "2023" in str(plan.model_path)

    def test_model_year_matches_fold_year(self):
        """Le nom du fichier modèle doit correspondre à l'année du fold."""
        for year in [2022, 2023, 2024, 2025]:
            plan = BacktestFoldPlan(
                fold_year         = year,
                model_path        = Path(f"model_{year}.pkl"),
                test_start        = f"{year}-01-01",
                test_end          = f"{year}-12-31",
                train_period      = "...",
                validation_period = "...",
            )
            assert str(year) in plan.model_path.name, (
                f"model_path doit contenir l'année {year}, got: {plan.model_path}"
            )


# ─── Tests FoldAwareModelLoader.build_plan ────────────────────────────────────

class TestBuildPlan:
    def test_fold_plan_covers_correct_years(self, loader, fake_model_tree, monkeypatch):
        """Seuls les folds dans [start, end] sont inclus."""
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2023-01-01", "2024-12-31")
        years = [p.fold_year for p in plans]
        assert years == [2023, 2024], f"Attendu [2023, 2024], obtenu {years}"

    def test_fold_plan_full_range(self, loader, fake_model_tree, monkeypatch):
        """Plage complète 2022-2025 → 4 folds."""
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2022-01-01", "2025-12-31")
        assert len(plans) == 4

    def test_fold_model_path_correct(self, loader, fake_model_tree, monkeypatch):
        """model_2023.pkl est chargé pour l'année 2023, pas model_2022.pkl."""
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2022-01-01", "2025-12-31")
        for plan in plans:
            assert f"model_{plan.fold_year}.pkl" == plan.model_path.name, (
                f"Fold {plan.fold_year}: modèle attendu model_{plan.fold_year}.pkl, "
                f"obtenu {plan.model_path.name}"
            )

    def test_missing_fold_raises(self, loader, fake_model_tree, monkeypatch):
        """Supprimer un .pkl → FoldMissingError, pas de fallback silencieux."""
        monkeypatch.chdir(fake_model_tree)
        missing = fake_model_tree / "artifacts/institutional/backtests/btc_eth_trend/BTCUSDT/v1.0/2023/model_2023.pkl"
        missing.unlink()
        with pytest.raises(FoldMissingError) as exc_info:
            loader.build_plan("btc_eth_trend", "BTCUSDT", "2022-01-01", "2025-12-31")
        assert "2023" in str(exc_info.value)

    def test_no_leakage_fold_year_uses_own_model(self, loader, fake_model_tree, monkeypatch):
        """
        Garantie anti-leakage : aucun fold ne peut utiliser un modèle d'année future.
        model_2023.pkl ne doit jamais être utilisé pour fold_year=2022.
        """
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2022-01-01", "2025-12-31")
        for plan in plans:
            model_year = int(plan.model_path.stem.replace("model_", ""))
            assert model_year == plan.fold_year, (
                f"LEAKAGE: fold {plan.fold_year} utilise model_{model_year}.pkl !"
            )
            assert model_year <= plan.fold_year, (
                f"LEAKAGE FUTURE: fold {plan.fold_year} utilise un modèle de {model_year} "
                f"(entraîné après la période de test)"
            )

    def test_ordered_chronologically(self, loader, fake_model_tree, monkeypatch):
        """Les folds sont retournés dans l'ordre chronologique."""
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2022-01-01", "2025-12-31")
        years = [p.fold_year for p in plans]
        assert years == sorted(years), "Les folds doivent être ordonnés chronologiquement"

    def test_single_year_returns_one_fold(self, loader, fake_model_tree, monkeypatch):
        """start et end dans la même année → exactement 1 fold."""
        monkeypatch.chdir(fake_model_tree)
        plans = loader.build_plan("btc_eth_trend", "BTCUSDT", "2024-03-01", "2024-09-30")
        assert len(plans) == 1
        assert plans[0].fold_year == 2024


# ─── Tests proba_df concat ────────────────────────────────────────────────────

class TestProbaConcat:
    def _make_proba_fold(self, year: int, n: int = 100) -> pd.DataFrame:
        idx = pd.date_range(f"{year}-01-01", periods=n, freq="1h", tz="UTC")
        return pd.DataFrame({
            "p_up":       np.random.rand(n),
            "p_down":     np.random.rand(n) * 0.3,
            "p_flat":     np.random.rand(n) * 0.3,
            "fold_year":  year,
            "model_path": f"model_{year}.pkl",
            "model_type": "LightGBMClassifier",
        }, index=idx)

    def test_proba_concat_contiguous(self):
        """Le proba_df concaténé est trié et sans chevauchement entre folds."""
        parts = [self._make_proba_fold(y) for y in [2022, 2023, 2024, 2025]]
        proba_df = pd.concat(parts).sort_index()

        # Trié
        assert proba_df.index.is_monotonic_increasing

        # Pas de chevauchement : chaque barre appartient à un seul fold
        for year in [2022, 2023, 2024, 2025]:
            mask = proba_df["fold_year"] == year
            ts   = proba_df.index[mask]
            assert (ts.year == year).all(), f"Des barres du fold {year} sont hors période"

    def test_fold_year_column_present(self):
        """fold_year est présent dans chaque ligne du proba_df."""
        parts = [self._make_proba_fold(y) for y in [2022, 2023]]
        proba_df = pd.concat(parts)
        assert "fold_year" in proba_df.columns
        assert proba_df["fold_year"].notna().all()

    def test_model_path_column_present(self):
        """model_path est présent dans chaque ligne du proba_df."""
        parts = [self._make_proba_fold(y) for y in [2022, 2023]]
        proba_df = pd.concat(parts)
        assert "model_path" in proba_df.columns
        assert proba_df["model_path"].notna().all()


# ─── Tests annotate_trades_with_fold ──────────────────────────────────────────

class TestAnnotateTrades:
    def _make_trade(self, trade_id: int, ts: pd.Timestamp) -> Trade:
        t = Trade(
            trade_id    = trade_id,
            asset       = "BTCUSDT",
            direction   = 1,
            entry_ts    = ts,
            entry_price = 30000.0,
            size_usd    = 2500.0,
            size_units  = 2500.0 / 30000.0,
            entry_fee   = 1.25,
        )
        t.exit_ts    = ts + pd.Timedelta(hours=10)
        t.exit_price = 30500.0
        t.exit_fee   = 1.25
        t.exit_reason = "max_holding"
        t.pnl_gross  = (30500.0 - 30000.0) * t.size_units
        t.pnl_net    = t.pnl_gross - t.entry_fee - t.exit_fee
        return t

    def test_annotate_sets_fold_year(self):
        ts       = pd.Timestamp("2023-06-15 12:00", tz="UTC")
        proba_df = pd.DataFrame({
            "p_up":       [0.70],
            "fold_year":  [2023],
            "model_path": ["artifacts/.../2023/model_2023.pkl"],
            "model_type": ["LightGBMClassifier"],
        }, index=[ts])

        trade  = self._make_trade(1, ts)
        result = BacktestResult(config=BacktestConfig())
        result.trades.append(trade)

        annotate_trades_with_fold(result, proba_df, threshold=0.60)

        assert trade.fold_year  == 2023
        assert "2023" in str(trade.model_path)
        assert trade.model_type == "LightGBMClassifier"
        assert trade.threshold  == 0.60
        assert trade.prediction == pytest.approx(0.70)

    def test_annotate_no_match_keeps_none(self):
        """Un trade dont l'entry_ts n'est pas dans proba_df garde fold_year=None."""
        ts       = pd.Timestamp("2023-06-15 12:00", tz="UTC")
        ts_other = pd.Timestamp("2024-01-01 00:00", tz="UTC")
        proba_df = pd.DataFrame({
            "p_up":       [0.70],
            "fold_year":  [2023],
            "model_path": ["model_2023.pkl"],
            "model_type": ["LightGBMClassifier"],
        }, index=[ts])

        trade  = self._make_trade(1, ts_other)
        result = BacktestResult(config=BacktestConfig())
        result.trades.append(trade)

        annotate_trades_with_fold(result, proba_df, threshold=0.60)
        assert trade.fold_year is None


# ─── Tests fold_model_usage.json ──────────────────────────────────────────────

class TestFoldModelUsageJson:
    REQUIRED_KEYS = {
        "fold_year", "model_path", "model_type",
        "test_start", "test_end", "train_period", "validation_period",
        "model_version", "n_bars", "n_signals", "n_trades", "pnl_net",
    }

    def _make_trade_with_fold(self, fold_year: int, pnl: float) -> Trade:
        ts = pd.Timestamp(f"{fold_year}-06-01 12:00", tz="UTC")
        t  = Trade(
            trade_id    = fold_year,
            asset       = "BTCUSDT",
            direction   = 1,
            entry_ts    = ts,
            entry_price = 30000.0,
            size_usd    = 2500.0,
            size_units  = 0.083,
            entry_fee   = 1.25,
        )
        t.exit_ts    = ts + pd.Timedelta(hours=10)
        t.exit_price = 30000.0 + pnl / 0.083
        t.exit_fee   = 1.25
        t.exit_reason = "max_holding"
        t.pnl_gross  = pnl + 2.5
        t.pnl_net    = pnl
        t.fold_year  = fold_year
        t.model_path = f"artifacts/.../v1.0/{fold_year}/model_{fold_year}.pkl"
        return t

    def test_fold_model_usage_json_schema(self, tmp_path):
        """Toutes les clés obligatoires sont présentes dans chaque fold."""
        plans = [
            BacktestFoldPlan(
                fold_year=yr, model_path=Path(f"model_{yr}.pkl"),
                test_start=f"{yr}-01-01", test_end=f"{yr}-12-31",
                train_period="...", validation_period="...",
            )
            for yr in [2022, 2023]
        ]
        trades = [self._make_trade_with_fold(yr, pnl) for yr, pnl in [(2022, 50.0), (2023, -20.0)]]
        result = BacktestResult(config=BacktestConfig())
        result.trades.extend(trades)

        n = 200
        idx = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")
        proba_df = pd.DataFrame({
            "p_up":       np.random.rand(n),
            "fold_year":  [2022] * 100 + [2023] * 100,
            "model_path": [f"model_2022.pkl"] * 100 + [f"model_2023.pkl"] * 100,
        }, index=idx)

        build_fold_model_usage(plans, result, proba_df, "BTCUSDT", "TEST_PORTFOLIO",
                               threshold=0.60, out_dir=tmp_path)

        path = tmp_path / "fold_model_usage.json"
        assert path.exists(), "fold_model_usage.json absent"

        data = json.loads(path.read_text())
        assert "folds" in data
        assert "portfolio" in data
        assert "asset" in data
        assert "threshold" in data

        for fold_entry in data["folds"]:
            missing = self.REQUIRED_KEYS - set(fold_entry.keys())
            assert not missing, f"Clés manquantes dans le fold: {missing}"

    def test_fold_pnl_correct(self, tmp_path):
        """Le PnL par fold doit correspondre aux trades annotés."""
        plans = [
            BacktestFoldPlan(
                fold_year=2022, model_path=Path("model_2022.pkl"),
                test_start="2022-01-01", test_end="2022-12-31",
                train_period="...", validation_period="...",
            )
        ]
        trades = [self._make_trade_with_fold(2022, 42.0)]
        result = BacktestResult(config=BacktestConfig())
        result.trades.extend(trades)

        n = 100
        idx = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")
        proba_df = pd.DataFrame({
            "p_up":       np.random.rand(n),
            "fold_year":  [2022] * n,
            "model_path": ["model_2022.pkl"] * n,
        }, index=idx)

        build_fold_model_usage(plans, result, proba_df, "BTCUSDT", "TEST",
                               threshold=0.60, out_dir=tmp_path)

        data = json.loads((tmp_path / "fold_model_usage.json").read_text())
        fold_2022 = next(f for f in data["folds"] if f["fold_year"] == 2022)
        assert fold_2022["n_trades"] == 1
        assert fold_2022["pnl_net"]  == pytest.approx(42.0, abs=0.01)
