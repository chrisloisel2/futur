"""
src/institutional/backtest/walk_forward.py
─────────────────────────────────────────────────────────────────────────────
Walk-forward strict — expanding et rolling window.

Garanties :
  - Aucune donnée de test dans le train
  - Embargo entre train et test
  - Threshold calibré sur val, jamais sur test
  - Feature selection sur train uniquement
  - Scaler fit sur train uniquement
  - Résultats agrégés par fold

Modes :
  - expanding : train s'agrandit d'un fold à l'autre
  - rolling   : fenêtre d'entraînement fixe
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "backtests"


@dataclass
class WalkForwardConfig:
    train_start: str              # "2021-01-01"
    test_periods: List[str]       # ["2022", "2023", "2024", "2025"]
    validation_months: int = 3    # mois de validation (final portion of train)
    embargo_bars: int = 24 * 7    # 7 jours d'embargo entre train et test
    min_train_bars: int = 24 * 90 # 90 jours min
    mode: str = "expanding"       # "expanding" | "rolling"
    rolling_train_months: int = 18  # si mode=rolling
    retrain_freq: str = "Y"       # fréquence de retrain ("Y", "Q", "M")


@dataclass
class FoldResult:
    fold_id: str
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str
    n_train: int
    n_val: int
    n_test: int
    train_metrics: Dict[str, float]
    val_metrics: Dict[str, float]
    test_metrics: Dict[str, float]
    feature_importance: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def degradation(self, metric: str = "auc_ovr") -> float:
        """Dégradation train → test (signal de overfitting)."""
        train_val = self.train_metrics.get(metric, 0)
        test_val = self.test_metrics.get(metric, 0)
        if train_val == 0:
            return 0.0
        return (train_val - test_val) / train_val


@dataclass
class WalkForwardReport:
    folds: List[FoldResult]
    config: WalkForwardConfig
    aggregated_metrics: Dict[str, Any] = field(default_factory=dict)

    def compute_aggregate(self) -> None:
        """Calcule les métriques agrégées sur tous les folds."""
        for metric in ["pf", "sharpe", "auc_ovr", "hit_rate", "cagr"]:
            vals = [f.test_metrics.get(metric, np.nan) for f in self.folds]
            vals = [v for v in vals if not np.isnan(v)]
            if vals:
                self.aggregated_metrics[f"{metric}_mean"] = float(np.mean(vals))
                self.aggregated_metrics[f"{metric}_median"] = float(np.median(vals))
                self.aggregated_metrics[f"{metric}_min"] = float(np.min(vals))
                self.aggregated_metrics[f"{metric}_std"] = float(np.std(vals))

        # Pass rate
        n_pass = sum(1 for f in self.folds if f.test_metrics.get("pf", 0) > 1.10)
        self.aggregated_metrics["pass_rate"] = n_pass / max(len(self.folds), 1)
        self.aggregated_metrics["n_folds"] = len(self.folds)

    def summary(self) -> str:
        lines = [f"Walk-Forward ({self.config.mode}) — {len(self.folds)} folds"]
        lines.append("-" * 60)
        for f in self.folds:
            pf = f.test_metrics.get("pf", 0)
            sharpe = f.test_metrics.get("sharpe", 0)
            lines.append(
                f"  {f.fold_id}: test={f.test_start}:{f.test_end}"
                f"  PF={pf:.2f}  Sharpe={sharpe:.2f}"
                f"  n_train={f.n_train}  n_test={f.n_test}"
            )
        lines.append("-" * 60)
        for k, v in self.aggregated_metrics.items():
            if isinstance(v, float):
                lines.append(f"  {k}: {v:.4f}")
        return "\n".join(lines)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "config": {
                "train_start": self.config.train_start,
                "test_periods": self.config.test_periods,
                "validation_months": self.config.validation_months,
                "embargo_bars": self.config.embargo_bars,
                "mode": self.config.mode,
            },
            "aggregated_metrics": self.aggregated_metrics,
            "folds": [
                {
                    "fold_id": f.fold_id,
                    "train_start": f.train_start,
                    "train_end": f.train_end,
                    "val_start": f.val_start,
                    "val_end": f.val_end,
                    "test_start": f.test_start,
                    "test_end": f.test_end,
                    "n_train": f.n_train,
                    "n_val": f.n_val,
                    "n_test": f.n_test,
                    "train_metrics": f.train_metrics,
                    "val_metrics": f.val_metrics,
                    "test_metrics": f.test_metrics,
                    "degradation_auc": f.degradation("auc_ovr"),
                    "degradation_pf": f.degradation("pf"),
                }
                for f in self.folds
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))


def generate_folds(
    df: pd.DataFrame,
    config: WalkForwardConfig,
) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]]:
    """
    Génère les splits (train, val, test) en respectant la chronologie.

    Yields
    ------
    (df_train, df_val, df_test, fold_id)
    """
    test_years = [int(y) for y in config.test_periods]

    for year in test_years:
        test_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{year}-12-31 23:00:00", tz="UTC")

        # Embargo : exclure les barres juste avant test
        train_cutoff = test_start - pd.Timedelta(bars=config.embargo_bars, freq="H")

        if config.mode == "expanding":
            actual_train_start = pd.Timestamp(config.train_start, tz="UTC")
        else:
            # Rolling : train des N derniers mois avant cutoff
            actual_train_start = train_cutoff - pd.DateOffset(months=config.rolling_train_months)

        # Validation = derniers `validation_months` mois du train
        val_cutoff = train_cutoff - pd.DateOffset(months=config.validation_months)

        df_train_full = df.loc[actual_train_start:train_cutoff]
        df_val = df.loc[val_cutoff:train_cutoff]
        df_train = df.loc[actual_train_start:val_cutoff]
        df_test = df.loc[test_start:test_end]

        if len(df_train) < config.min_train_bars:
            logger.warning(
                f"Fold {year}: train trop court ({len(df_train)} barres < "
                f"{config.min_train_bars}) — sauté"
            )
            continue

        if len(df_test) == 0:
            logger.warning(f"Fold {year}: test vide — sauté")
            continue

        fold_id = str(year)
        logger.info(
            f"Fold {fold_id}: "
            f"train={actual_train_start.date()}:{val_cutoff.date()} ({len(df_train)}bars), "
            f"val={val_cutoff.date()}:{train_cutoff.date()} ({len(df_val)}bars), "
            f"test={test_start.date()}:{test_end.date()} ({len(df_test)}bars)"
        )

        yield df_train, df_val, df_test, fold_id


def run_walk_forward(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    target_col: str,
    model_factory: Callable,       # Callable() → InstitutionalModel
    config: WalkForwardConfig,
    backtest_fn: Optional[Callable] = None,
    save_dir: Optional[Path] = None,
) -> WalkForwardReport:
    """
    Exécute le walk-forward complet.

    Paramètres
    ----------
    features      : DataFrame features (index DatetimeIndex)
    labels        : DataFrame labels (index DatetimeIndex)
    target_col    : colonne cible dans labels
    model_factory : callable() → nouveau modèle vierge
    config        : configuration walk-forward
    backtest_fn   : callable(model, X_test, y_test) → dict_metrics
    save_dir      : répertoire de sauvegarde des artifacts

    Retourne
    --------
    WalkForwardReport avec tous les folds
    """
    # Assembler features + labels (NE PAS le faire avant split)
    label_col = labels[[target_col]].rename(columns={target_col: "_target_"})
    combined = features.join(label_col, how="inner").dropna(subset=["_target_"])

    folds: List[FoldResult] = []

    for df_train, df_val, df_test, fold_id in generate_folds(combined, config):
        X_train = df_train.drop(columns=["_target_"], errors="ignore")
        y_train = df_train["_target_"]
        X_val = df_val.drop(columns=["_target_"], errors="ignore")
        y_val = df_val["_target_"]
        X_test = df_test.drop(columns=["_target_"], errors="ignore")
        y_test = df_test["_target_"]

        # Supprimer les colonnes meta
        meta_cols = ["asset", "feature_version", "label_version", "config_hash"]
        X_train = X_train.drop(columns=meta_cols, errors="ignore")
        X_val = X_val.drop(columns=meta_cols, errors="ignore")
        X_test = X_test.drop(columns=meta_cols, errors="ignore")

        # Entraînement (scaler fit UNIQUEMENT sur X_train dans le modèle)
        model = model_factory()
        model.fit(X_train, y_train, X_val=X_val, y_val=y_val)

        # Métriques
        train_metrics = model.card.train_metrics if model.card else {}
        val_metrics = model.card.validation_metrics if model.card else {}

        # Test metrics
        test_metrics = {}
        if backtest_fn is not None:
            test_metrics = backtest_fn(model, X_test, y_test)

        fold_result = FoldResult(
            fold_id=fold_id,
            train_start=str(X_train.index.min().date()),
            train_end=str(X_train.index.max().date()),
            val_start=str(X_val.index.min().date()),
            val_end=str(X_val.index.max().date()),
            test_start=str(X_test.index.min().date()),
            test_end=str(X_test.index.max().date()),
            n_train=len(X_train),
            n_val=len(X_val),
            n_test=len(X_test),
            train_metrics=train_metrics,
            val_metrics=val_metrics,
            test_metrics=test_metrics,
            feature_importance=model.feature_importance(),
        )
        folds.append(fold_result)

        # Sauvegarde modèle
        if save_dir is not None:
            model.save(save_dir / fold_id / f"model_{fold_id}.pkl")

        logger.info(f"  Fold {fold_id} terminé: {test_metrics}")

    report = WalkForwardReport(folds=folds, config=config)
    report.compute_aggregate()

    if save_dir:
        report.save(save_dir / "walk_forward_report.json")

    logger.info("\n" + report.summary())
    return report
