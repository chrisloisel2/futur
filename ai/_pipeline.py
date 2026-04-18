"""
ai/_pipeline — Orchestration bout-en-bout du pipeline ML trading
================================================================

Ce module expose une API de haut niveau pour entraîner et utiliser
le pipeline complet sur les 7 niveaux.

Usage
-----
    from ai import pipeline

    # Entraîner tout
    result = pipeline.train(
        data_path="data/BTCUSD_1h_features.csv",
        run_dir="runs/my_run",
        mode="combined",           # "long" | "short" | "combined"
    )

    # Prédire sur une nouvelle barre
    signal = pipeline.predict(
        df_bar=bar,
        artifacts=result.artifacts,
    )

Fonctions
---------
    train()     — pipeline complet : filtre → régime → edge → risk
    predict()   — inférence sur une barre (retourne Signal)
    load()      — charger des artefacts sauvegardés depuis run_dir
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ── Types de retour ──────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Signal de trading produit par le pipeline."""
    side: str                    # "long" | "short" | "flat"
    p_filter: float              # proba P(tradeable)
    p_edge: float                # proba P(direction)
    regime: str                  # SHORTABLE | NEUTRAL | NO_SHORT
    p_bear_regime: float         # proba bear regime
    qty: float = 0.0             # taille de position [0, 1]
    stop_price: float = 0.0
    take_profit: float = 0.0
    reason: str = ""             # pourquoi flat (si side == "flat")
    market_context: str = ""     # contexte Level 3 (TREND_LONG, HIGH_VOL, …)
    specialist_used: bool = False  # True si un expert L3 a modifié p_edge


@dataclass
class TrainResult:
    """Résultat d'un entraînement complet."""
    run_dir: Path
    artifacts: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)


# ── API principale ───────────────────────────────────────────────────────────

def train(
    data_path: str | Path,
    run_dir: str | Path = "runs/pipeline",
    mode: str = "combined",
    filter_thr_long: Optional[float] = None,
    filter_thr_short: Optional[float] = None,
    direction_thr_long: Optional[float] = None,
    direction_thr_short: Optional[float] = None,
    enable_short: bool = True,
    verbose: bool = True,
) -> TrainResult:
    """
    Entraîne le pipeline complet sur le CSV fourni.

    Paramètres
    ----------
    data_path       : chemin vers le CSV enrichi (features pré-calculées)
    run_dir         : répertoire de sortie (créé si absent)
    mode            : "long" | "short" | "combined"
    filter_thr_*    : seuil du filtre tradeable (None = auto-calibré)
    direction_thr_* : seuil du modèle directionnel (None = auto-calibré)
    enable_short    : désactiver le short même en mode combined
    verbose         : affichage des métriques intermédiaires

    Retourne
    --------
    TrainResult avec artefacts sauvegardés dans run_dir/
    """
    import time
    from ai.level_0 import (
        load_csv, build_labels, compute_regime_col, compute_long_regime_col,
        compute_short_reversal_col, chronological_split, get_X, fit_scaler,
        train_filter_model, calibrate_filter_threshold,
        FEATURES_FILTER, FEATURES_LONG, FEATURES_SHORT,
    )
    from ai.level_1 import train_bear_regime_model, apply_regime_filter
    from ai.level_2 import (
        train_long_model, train_short_model,
        calibrate_long_model, calibrate_short_model,
        check_short_stability,
        LongModelConfig, ShortModelConfig,
    )
    from ai.level_3 import train_specialists, SpecialistPredictor, SpecialistConfig, RouterConfig
    from ai.level_7 import (
        make_long_risk_config, make_short_risk_config,
        load_or_create_risk_controller,
    )

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    artifacts: Dict[str, Any] = {}
    metrics: Dict[str, Any] = {}

    # ── Level 0 : données ────────────────────────────────────────────────────
    if verbose:
        print("── Level 0 : chargement & labels")
    df = load_csv(data_path)
    df = compute_regime_col(df)
    df = compute_long_regime_col(df)
    df = compute_short_reversal_col(df)
    df, label_stats = build_labels(df)
    metrics["labels"] = label_stats

    # Filtre tradeable
    filter_dir = run_dir / "filter"
    filter_dir.mkdir(exist_ok=True)
    filter_result = train_filter_model(df, out_dir=filter_dir, verbose=verbose)
    artifacts["filter"] = filter_result

    if filter_thr_long is None:
        filter_thr_long = calibrate_filter_threshold(filter_result, side="long")
    if filter_thr_short is None:
        filter_thr_short = calibrate_filter_threshold(filter_result, side="short")
    artifacts["filter_thr_long"] = filter_thr_long
    artifacts["filter_thr_short"] = filter_thr_short

    # ── Level 1 : régimes ────────────────────────────────────────────────────
    if verbose:
        print("── Level 1 : régimes")
    regime_dir = run_dir / "regime"
    regime_dir.mkdir(exist_ok=True)
    bear_result = train_bear_regime_model(df, out_dir=regime_dir, verbose=verbose)
    artifacts["bear_regime"] = bear_result

    # ── Level 2 : edge scoring ────────────────────────────────────────────────
    do_long  = mode in ("long", "combined")
    do_short = mode in ("short", "combined") and enable_short

    if do_long:
        if verbose:
            print("── Level 2 : edge long")
        long_dir = run_dir / "edge_long"
        long_dir.mkdir(exist_ok=True)
        long_result = train_long_model(df, out_dir=long_dir, verbose=verbose)
        artifacts["long"] = long_result

        if direction_thr_long is None:
            cal_long = calibrate_long_model(long_result, df, side="long")
            direction_thr_long = cal_long["threshold"]
            artifacts["cal_long"] = cal_long
        artifacts["direction_thr_long"] = direction_thr_long

    if do_short:
        if verbose:
            print("── Level 2 : edge short")
        short_dir = run_dir / "edge_short"
        short_dir.mkdir(exist_ok=True)
        short_result = train_short_model(df, out_dir=short_dir, verbose=verbose)
        artifacts["short"] = short_result

        # Validation inter-années obligatoire
        stability = check_short_stability(short_result, df)
        artifacts["short_stability"] = stability
        metrics["short_stability"] = {
            "bad_years": stability.bad_years,
            "deploy": stability.should_deploy,
        }

        if direction_thr_short is None:
            cal_short = calibrate_short_model(short_result, df, side="short")
            direction_thr_short = cal_short["threshold"]
            artifacts["cal_short"] = cal_short
        artifacts["direction_thr_short"] = direction_thr_short

    # ── Level 3 : experts par contexte ───────────────────────────────────────
    specialist_dir = run_dir / "specialists"
    if do_long or do_short:
        try:
            from ai.level_0.preprocessing import chronological_split
            _splits = chronological_split(df)
            _tm = _splits["train_mask"]
            _vm = _splits["val_mask"]
            _router = train_specialists(
                df=df,
                train_mask=_tm,
                val_mask=_vm,
                out_dir=specialist_dir,
                specialist_cfg=SpecialistConfig(),
                router_cfg=RouterConfig(),
            )
            artifacts["specialist_predictor"] = SpecialistPredictor.from_router(_router)
            artifacts["specialist_dir"] = str(specialist_dir)
        except Exception as _e:
            print(f"   ⚠  Level 3 ignoré : {_e}")

    # ── Level 7 : risk config ─────────────────────────────────────────────────
    artifacts["risk_config_long"]  = make_long_risk_config()
    artifacts["risk_config_short"] = make_short_risk_config()

    # ── Sauvegarde du résumé ──────────────────────────────────────────────────
    elapsed = time.time() - t0
    summary = {
        "mode": mode,
        "run_dir": str(run_dir),
        "elapsed_s": round(elapsed, 1),
        "filter_thr_long": filter_thr_long,
        "filter_thr_short": filter_thr_short,
        "direction_thr_long": direction_thr_long,
        "direction_thr_short": direction_thr_short,
        "metrics": metrics,
    }
    (run_dir / "pipeline_summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    if verbose:
        print(f"\nPipeline terminé en {elapsed:.1f}s → {run_dir}")

    return TrainResult(run_dir=run_dir, artifacts=artifacts, metrics=metrics, summary=summary)


def predict(
    df_bar: pd.DataFrame,
    artifacts: Dict[str, Any],
    current_price: float,
    capital: float = 10_000.0,
) -> Signal:
    """
    Inférence sur une (ou plusieurs) barres.

    Paramètres
    ----------
    df_bar        : DataFrame contenant les features (une ou plusieurs lignes)
    artifacts     : dictionnaire retourné par train() ou load()
    current_price : prix courant (pour stop/TP)
    capital       : capital disponible (pour taille de position)

    Retourne
    --------
    Signal (side, p_filter, p_edge, regime, qty, stop_price, take_profit)
    """
    from ai.level_0 import get_X, FEATURES_FILTER, FEATURES_LONG, FEATURES_SHORT
    from ai.level_1 import REGIME_NO_SHORT, REGIME_SHORTABLE

    filter_result    = artifacts["filter"]
    filter_thr_long  = artifacts.get("filter_thr_long", 0.40)
    filter_thr_short = artifacts.get("filter_thr_short", 0.45)
    dir_thr_long     = artifacts.get("direction_thr_long", 0.52)
    dir_thr_short    = artifacts.get("direction_thr_short", 0.55)

    # ── Level 0 : filtre tradeable ────────────────────────────────────────────
    X_filter = get_X(df_bar, FEATURES_FILTER, scaler=filter_result.scaler)
    p_filter_long  = float(filter_result.model.predict_proba(X_filter)[-1, 1])
    p_filter_short = p_filter_long  # modèle partagé

    # ── Level 1 : régime ─────────────────────────────────────────────────────
    regime = str(df_bar[artifacts.get("regime_col", "regime")].iloc[-1])
    p_bear = 0.0
    if "bear_regime" in artifacts:
        br = artifacts["bear_regime"]
        from ai.level_0 import FEATURES_REGIME
        X_regime = get_X(df_bar, FEATURES_REGIME, scaler=br.scaler)
        p_bear = float(br.model.predict_proba(X_regime)[-1, 1])

    # ── Level 2 LONG ─────────────────────────────────────────────────────────
    p_long = 0.0
    if "long" in artifacts and p_filter_long >= filter_thr_long:
        lr = artifacts["long"]
        X_long = get_X(df_bar, FEATURES_LONG, scaler=lr.scaler)
        p_long = float(lr.best_model.predict_proba(X_long)[-1, 1])

    # ── Level 2 SHORT ────────────────────────────────────────────────────────
    p_short = 0.0
    if ("short" in artifacts
            and regime != REGIME_NO_SHORT
            and p_filter_short >= filter_thr_short):
        sr = artifacts["short"]
        X_short = get_X(df_bar, FEATURES_SHORT, scaler=sr.scaler)
        p_short = float(sr.best_model.predict_proba(X_short)[-1, 1])

    # ── Level 3 : fusion specialists ─────────────────────────────────────────
    market_context  = ""
    specialist_used = False
    if "specialist_predictor" in artifacts:
        try:
            sp = artifacts["specialist_predictor"]
            _routing = sp.predict_row(
                row    = df_bar.iloc[-1],
                p_long = p_long,
                p_short= p_short,
            )
            p_long          = _routing.p_long_final
            p_short         = _routing.p_short_final
            market_context  = _routing.context
            specialist_used = _routing.expert_used
        except Exception:
            pass

    # ── Décision ─────────────────────────────────────────────────────────────
    go_long  = p_long  >= dir_thr_long
    go_short = p_short >= dir_thr_short and regime == REGIME_SHORTABLE

    if go_long:
        side = "long"
        p_edge = p_long
        cfg = artifacts["risk_config_long"]
    elif go_short:
        side = "short"
        p_edge = p_short
        cfg = artifacts["risk_config_short"]
    else:
        reason = (
            "filtre" if p_filter_long < filter_thr_long
            else "régime" if regime == REGIME_NO_SHORT
            else "edge"
        )
        return Signal(
            side="flat", p_filter=p_filter_long, p_edge=max(p_long, p_short),
            regime=regime, p_bear_regime=p_bear, reason=reason,
            market_context=market_context, specialist_used=specialist_used,
        )

    # ── Level 7 : risk sizing ─────────────────────────────────────────────────
    stop_pct = cfg.stop_loss_pct
    tp_pct   = cfg.stop_loss_pct * cfg.risk_reward_ratio
    kelly    = cfg.kelly_fraction * (p_edge - (1 - p_edge))
    kelly    = max(0.0, min(kelly, cfg.max_position_pct))

    if side == "long":
        stop_price  = current_price * (1 - stop_pct)
        take_profit = current_price * (1 + tp_pct)
    else:
        stop_price  = current_price * (1 + stop_pct)
        take_profit = current_price * (1 - tp_pct)

    qty = (capital * kelly) / current_price

    return Signal(
        side=side,
        p_filter=p_filter_long,
        p_edge=p_edge,
        regime=regime,
        p_bear_regime=p_bear,
        qty=round(qty, 6),
        stop_price=round(stop_price, 2),
        take_profit=round(take_profit, 2),
        market_context=market_context,
        specialist_used=specialist_used,
    )


def load(run_dir: str | Path) -> Dict[str, Any]:
    """
    Charge les artefacts d'un run précédent depuis run_dir/.

    Retourne
    --------
    dict artifacts compatible avec predict()
    """
    run_dir = Path(run_dir)
    artifacts: Dict[str, Any] = {}

    summary_path = run_dir / "pipeline_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        artifacts["filter_thr_long"]      = summary.get("filter_thr_long")
        artifacts["filter_thr_short"]     = summary.get("filter_thr_short")
        artifacts["direction_thr_long"]   = summary.get("direction_thr_long")
        artifacts["direction_thr_short"]  = summary.get("direction_thr_short")

    for name, subdir in [("filter", "filter"), ("long", "edge_long"),
                          ("short", "edge_short"), ("bear_regime", "regime")]:
        pkl = run_dir / subdir / "model.pkl"
        if pkl.exists():
            with open(pkl, "rb") as f:
                artifacts[name] = pickle.load(f)

    # Level 3 specialists
    specialist_dir = run_dir / "specialists"
    if specialist_dir.exists():
        try:
            from ai.level_3 import SpecialistPredictor
            artifacts["specialist_predictor"] = SpecialistPredictor.load(specialist_dir)
            artifacts["specialist_dir"] = str(specialist_dir)
        except Exception as _e:
            print(f"   ⚠  Level 3 non chargé : {_e}")

    from ai.level_7 import make_long_risk_config, make_short_risk_config
    artifacts["risk_config_long"]  = make_long_risk_config()
    artifacts["risk_config_short"] = make_short_risk_config()

    return artifacts
