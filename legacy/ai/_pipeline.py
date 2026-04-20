"""
ai/_pipeline — Orchestration bout-en-bout du pipeline ML trading
================================================================

Usage
-----
    from ai import pipeline

    result = pipeline.train(
        data_path="data/BTCUSD_1h_features.csv",
        run_dir="runs/my_run",
        mode="combined",
    )

    signal = pipeline.predict(
        df_bar=bar,
        artifacts=result.artifacts,
        current_price=42000.0,
    )

Fonctions
---------
    train()   — pipeline complet : filtre → régime → edge → risk
    predict() — inférence sur une barre (retourne Signal)
    load()    — charger des artefacts sauvegardés depuis run_dir
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


# ── Types de retour ──────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Signal de trading produit par le pipeline."""
    side: str                     # "long" | "short" | "flat"
    p_filter: float               # P(tradeable)
    p_edge: float                 # P(direction)
    regime: str                   # SHORTABLE | NEUTRAL | NO_SHORT
    p_bear_regime: float          # P(bear regime)
    qty: float = 0.0
    stop_price: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    market_context: str = ""
    specialist_used: bool = False


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
    filter_thr_*    : seuil du filtre tradeable (None = auto-calibré sur val)
    direction_thr_* : seuil directionnel (None = auto-calibré sur val)
    enable_short    : désactiver le short même en mode combined
    verbose         : affichage des métriques intermédiaires
    """
    import time
    from ai.level_0 import (
        load_csv, build_labels,
        compute_regime_col, compute_long_regime_col,
        compute_short_reversal_col,
        chronological_split, get_X,
        train_filter_model, calibrate_filter_threshold,
        FEATURES_FILTER, FEATURES_LONG, FEATURES_SHORT, FEATURES_REGIME,
        FILTER_BETA_LONG, FILTER_BETA_SHORT,
    )
    from ai.level_1 import train_bear_regime_model
    from ai.level_2 import (
        train_long_model, train_short_model,
        calibrate_long_model, calibrate_short_model,
        check_short_stability,
        LongModelConfig, ShortModelConfig,
    )
    from ai.level_7 import make_long_risk_config, make_short_risk_config

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    artifacts: Dict[str, Any] = {}
    metrics: Dict[str, Any] = {}

    # ── Level 0 : chargement ──────────────────────────────────────────────────
    if verbose:
        print("── Level 0 : chargement & labels")

    all_features = list(dict.fromkeys(
        FEATURES_FILTER + FEATURES_LONG + FEATURES_SHORT + FEATURES_REGIME
    ))
    df = load_csv(data_path, all_features)
    df = compute_regime_col(df)
    df = compute_long_regime_col(df)
    df = compute_short_reversal_col(df)

    # Split chronologique — doit précéder build_labels (calibration sur train uniquement)
    train_mask, val_mask, test_mask = chronological_split(df)

    df, label_stats = build_labels(df, train_mask)
    metrics["labels"] = label_stats
    if verbose:
        print(f"   labels : {label_stats}")

    # ── Level 0 : filtre tradeable ────────────────────────────────────────────
    filter_dir = run_dir / "filter"
    filter_dir.mkdir(exist_ok=True)
    clf_f, scaler_f, metrics_f = train_filter_model(df, train_mask, val_mask, filter_dir)
    artifacts["filter"] = {
        "model":    clf_f,
        "scaler":   scaler_f,
        "features": FEATURES_FILTER,
        "metrics":  metrics_f,
    }
    metrics["filter"] = metrics_f

    # Calibration du seuil filtre sur val (jamais sur test)
    if filter_thr_long is None or filter_thr_short is None:
        X_val_f   = get_X(df, val_mask, FEATURES_FILTER)
        proba_f   = clf_f.predict_proba(scaler_f.transform(X_val_f))[:, 1]
        y_val_f   = df.loc[val_mask, "tradeable_net"].values.astype(np.int32)
        valid_f   = y_val_f >= 0
        if filter_thr_long is None:
            filter_thr_long  = calibrate_filter_threshold(
                proba_f[valid_f], y_val_f[valid_f], beta=FILTER_BETA_LONG
            )
        if filter_thr_short is None:
            filter_thr_short = calibrate_filter_threshold(
                proba_f[valid_f], y_val_f[valid_f], beta=FILTER_BETA_SHORT
            )
    artifacts["filter_thr_long"]  = filter_thr_long
    artifacts["filter_thr_short"] = filter_thr_short
    if verbose:
        print(f"   seuil filtre long={filter_thr_long:.2f}  short={filter_thr_short:.2f}")

    # ── Level 1 : régime bear ─────────────────────────────────────────────────
    if verbose:
        print("── Level 1 : régime bear")
    regime_dir = run_dir / "regime"
    regime_dir.mkdir(exist_ok=True)
    bear_result = train_bear_regime_model(df, train_mask, val_mask, regime_dir)
    artifacts["bear_regime"] = bear_result
    metrics["bear_regime"] = bear_result.get("metrics", {})

    # ── Level 2 : edge scoring ────────────────────────────────────────────────
    do_long  = mode in ("long",  "combined")
    do_short = mode in ("short", "combined") and enable_short

    if do_long:
        if verbose:
            print("── Level 2 : edge long")
        long_dir = run_dir / "edge_long"
        long_dir.mkdir(exist_ok=True)
        long_result = train_long_model(df, train_mask, val_mask, long_dir)
        artifacts["long"] = long_result
        metrics["long"] = long_result.get("all_metrics", {})

        if direction_thr_long is None:
            _, cal_long = calibrate_long_model(
                clf     = long_result["best_model"],
                scaler  = long_result["scaler"],
                df      = df,
                val_mask= val_mask,
                side    = "long",
                out_dir = long_dir,
            )
            direction_thr_long = cal_long.get("recommended_threshold", 0.52)
            artifacts["long"]["calibration"] = cal_long
        artifacts["direction_thr_long"] = direction_thr_long
        if verbose:
            print(f"   seuil direction long={direction_thr_long:.2f}")

    if do_short:
        if verbose:
            print("── Level 2 : edge short")
        short_dir = run_dir / "edge_short"
        short_dir.mkdir(exist_ok=True)
        short_result = train_short_model(df, train_mask, val_mask, short_dir)
        artifacts["short"] = short_result
        metrics["short"] = short_result.get("all_metrics", {})

        if direction_thr_short is None:
            _, cal_short = calibrate_short_model(
                clf     = short_result["best_model"],
                scaler  = short_result["scaler"],
                df      = df,
                val_mask= val_mask,
                side    = "short",
                out_dir = short_dir,
            )
            direction_thr_short = cal_short.get("recommended_threshold", 0.55)
            artifacts["short"]["calibration"] = cal_short
        artifacts["direction_thr_short"] = direction_thr_short
        if verbose:
            print(f"   seuil direction short={direction_thr_short:.2f}")

        # Validation inter-années obligatoire pour le short
        is_stable, stability = check_short_stability(
            clf       = short_result["best_model"],
            scaler    = short_result["scaler"],
            df        = df,
            val_mask  = val_mask,
            threshold = direction_thr_short,
        )
        artifacts["short_stability"] = {"is_stable": is_stable, **stability}
        metrics["short_stability"]   = {"is_stable": is_stable, "n_bad_years": stability.get("n_bad_years")}
        if not is_stable and verbose:
            print(f"   ⚠  Short instable — déploiement risqué")

    # ── Level 3 : experts par contexte ───────────────────────────────────────
    if do_long or do_short:
        specialist_dir = run_dir / "specialists"
        try:
            from ai.level_3 import train_specialists, SpecialistPredictor, SpecialistConfig, RouterConfig
            _router = train_specialists(
                df             = df,
                train_mask     = train_mask,
                val_mask       = val_mask,
                out_dir        = specialist_dir,
                specialist_cfg = SpecialistConfig(),
                router_cfg     = RouterConfig(),
            )
            artifacts["specialist_predictor"] = SpecialistPredictor.from_router(_router)
            artifacts["specialist_dir"]       = str(specialist_dir)
        except Exception as _e:
            if verbose:
                print(f"   ⚠  Level 3 ignoré : {_e}")

    # ── Level 7 : risk config ─────────────────────────────────────────────────
    artifacts["risk_config_long"]  = make_long_risk_config()
    artifacts["risk_config_short"] = make_short_risk_config()

    # ── Sauvegarde du résumé ──────────────────────────────────────────────────
    elapsed = time.time() - t0
    summary = {
        "mode":                mode,
        "run_dir":             str(run_dir),
        "elapsed_s":           round(elapsed, 1),
        "filter_thr_long":     filter_thr_long,
        "filter_thr_short":    filter_thr_short,
        "direction_thr_long":  direction_thr_long,
        "direction_thr_short": direction_thr_short,
        "metrics":             metrics,
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

    df_bar        : DataFrame contenant les features (une ou plusieurs lignes)
    artifacts     : dictionnaire retourné par train() ou load()
    current_price : prix courant (pour stop/TP)
    capital       : capital disponible (pour taille de position)
    """
    from ai.level_0 import get_X, FEATURES_FILTER, FEATURES_LONG, FEATURES_SHORT
    from ai.level_1 import REGIME_NO_SHORT, REGIME_SHORTABLE

    filter_art       = artifacts["filter"]
    filter_thr_long  = artifacts.get("filter_thr_long",  0.40)
    filter_thr_short = artifacts.get("filter_thr_short", 0.45)
    dir_thr_long     = artifacts.get("direction_thr_long",  0.52)
    dir_thr_short    = artifacts.get("direction_thr_short", 0.55)

    # Masque all-true pour inférence (pas de split chronologique nécessaire)
    inf_mask = np.ones(len(df_bar), dtype=bool)

    # ── Level 0 : filtre tradeable ────────────────────────────────────────────
    X_f = get_X(df_bar, inf_mask, filter_art["features"])
    X_f = filter_art["scaler"].transform(X_f)
    p_filter = float(filter_art["model"].predict_proba(X_f)[-1, 1])

    # ── Level 1 : régime ─────────────────────────────────────────────────────
    regime_col = "regime"
    regime = str(df_bar[regime_col].iloc[-1]) if regime_col in df_bar.columns else "NEUTRAL"
    p_bear = 0.0
    if "bear_regime" in artifacts:
        br = artifacts["bear_regime"]
        X_r = get_X(df_bar, inf_mask, br["features"])
        X_r = br["scaler"].transform(X_r)
        p_bear = float(br["model"].predict_proba(X_r)[-1, 1])

    # ── Level 2 LONG ─────────────────────────────────────────────────────────
    p_long = 0.0
    if "long" in artifacts and p_filter >= filter_thr_long:
        lr = artifacts["long"]
        features_l = lr.get("features", FEATURES_LONG)
        X_l = get_X(df_bar, inf_mask, features_l)
        X_l = lr["scaler"].transform(X_l)
        p_long = float(lr["best_model"].predict_proba(X_l)[-1, 1])

    # ── Level 2 SHORT ────────────────────────────────────────────────────────
    p_short = 0.0
    if ("short" in artifacts
            and regime != REGIME_NO_SHORT
            and p_filter >= filter_thr_short):
        sr = artifacts["short"]
        features_s = sr.get("features", FEATURES_SHORT)
        X_s = get_X(df_bar, inf_mask, features_s)
        X_s = sr["scaler"].transform(X_s)
        p_short = float(sr["best_model"].predict_proba(X_s)[-1, 1])

    # ── Level 3 : fusion specialists ─────────────────────────────────────────
    market_context  = ""
    specialist_used = False
    if "specialist_predictor" in artifacts:
        try:
            sp = artifacts["specialist_predictor"]
            _routing        = sp.predict_row(
                row     = df_bar.iloc[-1],
                p_long  = p_long,
                p_short = p_short,
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
        side  = "long"
        p_edge = p_long
        cfg    = artifacts["risk_config_long"]
    elif go_short:
        side  = "short"
        p_edge = p_short
        cfg    = artifacts["risk_config_short"]
    else:
        if p_filter < filter_thr_long:
            reason = "filtre"
        elif regime == REGIME_NO_SHORT:
            reason = "régime"
        else:
            reason = "edge"
        return Signal(
            side="flat", p_filter=p_filter, p_edge=max(p_long, p_short),
            regime=regime, p_bear_regime=p_bear, reason=reason,
            market_context=market_context, specialist_used=specialist_used,
        )

    # ── Level 7 : risk sizing ─────────────────────────────────────────────────
    stop_pct    = cfg.stop_loss_pct
    tp_pct      = cfg.stop_loss_pct * cfg.risk_reward_ratio
    kelly       = cfg.kelly_fraction * (p_edge - (1 - p_edge))
    kelly       = max(0.0, min(kelly, cfg.max_position_pct))

    if side == "long":
        stop_price  = current_price * (1 - stop_pct)
        take_profit = current_price * (1 + tp_pct)
    else:
        stop_price  = current_price * (1 + stop_pct)
        take_profit = current_price * (1 - tp_pct)

    qty = (capital * kelly) / current_price

    return Signal(
        side=side,
        p_filter=p_filter,
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

    Reconstruit la même structure de dict qu'après train(),
    compatible directement avec predict().
    """
    from ai.level_0 import FEATURES_FILTER, FEATURES_LONG, FEATURES_SHORT, FEATURES_REGIME
    from ai.level_7 import make_long_risk_config, make_short_risk_config

    run_dir   = Path(run_dir)
    artifacts: Dict[str, Any] = {}

    # Seuils depuis le résumé JSON
    summary_path = run_dir / "pipeline_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        for key in ("filter_thr_long", "filter_thr_short",
                    "direction_thr_long", "direction_thr_short"):
            if summary.get(key) is not None:
                artifacts[key] = summary[key]

    # ── Filtre (level 0) ──────────────────────────────────────────────────────
    filter_dir = run_dir / "filter"
    _f_model  = filter_dir / "filter_model.pkl"
    _f_scaler = filter_dir / "filter_scaler.pkl"
    if _f_model.exists() and _f_scaler.exists():
        artifacts["filter"] = {
            "model":    _load_pkl(_f_model),
            "scaler":   _load_pkl(_f_scaler),
            "features": FEATURES_FILTER,
            "metrics":  _load_json(filter_dir / "metrics.json"),
        }

    # ── Régime bear (level 1) ─────────────────────────────────────────────────
    regime_dir = run_dir / "regime"
    _r_model   = regime_dir / "bear_regime_model.pkl"
    _r_scaler  = regime_dir / "bear_regime_scaler.pkl"
    _r_metrics = _load_json(regime_dir / "bear_regime_metrics.json")
    if _r_model.exists() and _r_scaler.exists():
        artifacts["bear_regime"] = {
            "model":     _load_pkl(_r_model),
            "scaler":    _load_pkl(_r_scaler),
            "features":  _r_metrics.get("features", FEATURES_REGIME),
            "threshold": _r_metrics.get("activation_threshold", 0.70),
            "metrics":   _r_metrics,
        }

    # ── Edge long (level 2) ───────────────────────────────────────────────────
    long_dir  = run_dir / "edge_long"
    _l_model  = long_dir / "best_model.pkl"
    _l_scaler = long_dir / "scaler.pkl"
    _l_metrics = _load_json(long_dir / "metrics.json")
    if _l_model.exists() and _l_scaler.exists():
        artifacts["long"] = {
            "best_model": _load_pkl(_l_model),
            "scaler":     _load_pkl(_l_scaler),
            "features":   _l_metrics.get("features", FEATURES_LONG),
            "metrics":    _l_metrics,
        }

    # ── Edge short (level 2) ──────────────────────────────────────────────────
    short_dir  = run_dir / "edge_short"
    _s_model   = short_dir / "best_model.pkl"
    _s_scaler  = short_dir / "scaler.pkl"
    _s_metrics = _load_json(short_dir / "metrics.json")
    if _s_model.exists() and _s_scaler.exists():
        artifacts["short"] = {
            "best_model": _load_pkl(_s_model),
            "scaler":     _load_pkl(_s_scaler),
            "features":   _s_metrics.get("features", FEATURES_SHORT),
            "metrics":    _s_metrics,
        }

    # ── Level 3 specialists ───────────────────────────────────────────────────
    specialist_dir = run_dir / "specialists"
    if specialist_dir.exists():
        try:
            from ai.level_3 import SpecialistPredictor
            artifacts["specialist_predictor"] = SpecialistPredictor.load(specialist_dir)
            artifacts["specialist_dir"]       = str(specialist_dir)
        except Exception as _e:
            print(f"   ⚠  Level 3 non chargé : {_e}")

    # ── Level 7 : risk config ─────────────────────────────────────────────────
    artifacts["risk_config_long"]  = make_long_risk_config()
    artifacts["risk_config_short"] = make_short_risk_config()

    return artifacts


# ── Helpers privés ───────────────────────────────────────────────────────────

def _load_pkl(path: Path) -> Any:
    with open(path, "rb") as f:
        return pickle.load(f)


def _load_json(path: Path) -> Dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}
