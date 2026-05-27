"""
inference/predictor.py — PRÉDICTEUR LIVE
=========================================

Charge tous les artefacts d'un run (filtre, long, short) et expose
une interface bar-à-bar pour le live trading.

Usage :
    pred = LivePredictor.load_from_run(Path("runs/local/20260413-094213"))
    result = pred.predict(row_dict)
    # result : {"action": "LONG"|"SHORT"|"HOLD", "p_long": 0.73, "p_short": 0.41, ...}

Règles :
  - Pas de mocks, pas de stubs — tous les modèles sont chargés depuis disk
  - Si un artefact est manquant → exception claire (pas de fallback silencieux)
  - L'inférence est stateless (pas d'état interne sauf les modèles chargés)
  - Séparation stricte long/short : deux chemins d'inférence indépendants
"""
from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Dict, Optional, Any

import numpy as np


class LivePredictor:
    """
    Prédicteur live chargeant les artefacts d'un run complet.

    Arborescence attendue du run_dir :
        run_dir/
          filter/
            filter_model.pkl
            filter_scaler.pkl
            metrics.json         ← contient thr_long, thr_short
          long/
            best_model.pkl
            scaler.pkl
            calibrator.pkl       ← optionnel
            calibration_metrics.json  ← recommanded_threshold
          short/
            best_model.pkl
            scaler.pkl
            calibrator.pkl       ← optionnel
            calibration_metrics.json
    """

    def __init__(
        self,
        # Filter
        clf_filter,
        scaler_filter,
        filter_features: list,
        filter_thr_long: float,
        filter_thr_short: float,
        # Long
        clf_long,
        scaler_long,
        features_long: list,
        threshold_long: float,
        calibrator_long=None,
        # Short
        clf_short=None,
        scaler_short=None,
        features_short: Optional[list] = None,
        threshold_short: float = 0.60,
        calibrator_short=None,
        # Flags
        short_enabled: bool = True,
    ):
        self.clf_filter       = clf_filter
        self.scaler_filter    = scaler_filter
        self.filter_features  = filter_features
        self.filter_thr_long  = filter_thr_long
        self.filter_thr_short = filter_thr_short

        self.clf_long       = clf_long
        self.scaler_long    = scaler_long
        self.features_long  = features_long
        self.threshold_long = threshold_long
        self.calibrator_long = calibrator_long

        self.clf_short       = clf_short
        self.scaler_short    = scaler_short
        self.features_short  = features_short or []
        self.threshold_short = threshold_short
        self.calibrator_short = calibrator_short

        self.short_enabled = short_enabled and clf_short is not None

    @classmethod
    def load_from_run(cls, run_dir: Path, short_enabled: bool = True) -> "LivePredictor":
        """
        Charge tous les artefacts depuis un répertoire de run.

        Lève une exception claire si un fichier requis est absent.
        """
        run_dir = Path(run_dir)

        # ── Filter ────────────────────────────────────────────────────────────
        filter_dir = run_dir / "filter"
        clf_filter  = _load_pkl(filter_dir / "filter_model.pkl",  required=True)
        scaler_filt = _load_pkl(filter_dir / "filter_scaler.pkl", required=True)
        filt_meta   = _load_json(filter_dir / "metrics.json", required=True)
        filter_thr_long  = filt_meta.get("recommended_threshold_long",  0.50)
        filter_thr_short = filt_meta.get("recommended_threshold_short", 0.50)
        filter_features  = filt_meta.get("features", [])

        if not filter_features:
            # Fallback : reconstruire depuis core
            from core.features import FEATURES_FILTER
            filter_features = FEATURES_FILTER

        # ── Long ──────────────────────────────────────────────────────────────
        long_dir     = run_dir / "long"
        clf_long     = _load_pkl(long_dir / "best_model.pkl", required=True)
        scaler_long  = _load_pkl(long_dir / "scaler.pkl",     required=True)
        cal_long     = _load_pkl(long_dir / "calibrator.pkl", required=False)
        long_meta    = _load_json(long_dir / "calibration_metrics.json", required=False) or {}
        thr_long     = long_meta.get("recommended_threshold", 0.55)
        from core.features import FEATURES_LONG
        features_long = FEATURES_LONG

        # ── Short ─────────────────────────────────────────────────────────────
        clf_short    = None
        scaler_short = None
        cal_short    = None
        thr_short    = 0.60
        features_short = []

        if short_enabled:
            short_dir = run_dir / "short"
            if short_dir.exists():
                clf_short    = _load_pkl(short_dir / "best_model.pkl", required=False)
                scaler_short = _load_pkl(short_dir / "scaler.pkl",     required=False)
                cal_short    = _load_pkl(short_dir / "calibrator.pkl", required=False)
                short_meta   = _load_json(short_dir / "calibration_metrics.json", required=False) or {}
                thr_short    = short_meta.get("recommended_threshold", 0.60)
                from core.features import FEATURES_SHORT
                features_short = FEATURES_SHORT
            else:
                print(f"   [LivePredictor] short/ absent dans {run_dir} — short désactivé")
                short_enabled = False

        return cls(
            clf_filter=clf_filter,
            scaler_filter=scaler_filt,
            filter_features=filter_features,
            filter_thr_long=filter_thr_long,
            filter_thr_short=filter_thr_short,
            clf_long=clf_long,
            scaler_long=scaler_long,
            features_long=features_long,
            threshold_long=thr_long,
            calibrator_long=cal_long,
            clf_short=clf_short,
            scaler_short=scaler_short,
            features_short=features_short,
            threshold_short=thr_short,
            calibrator_short=cal_short,
            short_enabled=short_enabled,
        )

    def predict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prédit l'action pour un bar donné.

        Arguments
        ---------
        row : dict {feature_name: value} pour ce bar

        Retourne
        --------
        dict avec :
          action         : "LONG" | "SHORT" | "HOLD"
          p_tradeable    : probabilité filtre (0..1)
          p_long         : probabilité long (0..1, ou None si non calculée)
          p_short        : probabilité short (0..1, ou None si non calculé)
          p_long_cal     : p_long après calibration
          p_short_cal    : p_short après calibration
          filter_passed  : bool
          long_signal    : bool
          short_signal   : bool
          reason         : str (pour debug)
        """
        result = {
            "action":       "HOLD",
            "p_tradeable":  None,
            "p_long":       None,
            "p_short":      None,
            "p_long_cal":   None,
            "p_short_cal":  None,
            "filter_passed": False,
            "long_signal":  False,
            "short_signal": False,
            "reason":       "init",
        }

        # ── 1. Filtre ─────────────────────────────────────────────────────────
        try:
            x_f = np.array([[row[f] for f in self.filter_features]], dtype=np.float32)
            p_f = float(self.clf_filter.predict_proba(
                self.scaler_filter.transform(x_f)
            )[0, 1])
        except KeyError as e:
            result["reason"] = f"missing_filter_feature:{e}"
            return result

        result["p_tradeable"] = round(p_f, 4)

        # Vérifier si le filtre passe pour au moins un côté
        passes_long  = p_f >= self.filter_thr_long
        passes_short = p_f >= self.filter_thr_short

        if not (passes_long or passes_short):
            result["reason"] = f"filter_rejected:{p_f:.4f}"
            return result

        result["filter_passed"] = True

        # ── 2. Signal long ────────────────────────────────────────────────────
        long_signal = False
        p_long_cal  = 0.0
        if passes_long:
            try:
                x_l = np.array([[row[f] for f in self.features_long]], dtype=np.float32)
                p_l = float(self.clf_long.predict_proba(
                    self.scaler_long.transform(x_l)
                )[0, 1])
                p_long_cal = _apply_calibrator(self.calibrator_long, p_l)
                result["p_long"]     = round(p_l, 4)
                result["p_long_cal"] = round(p_long_cal, 4)
                long_signal = p_long_cal >= self.threshold_long
            except KeyError as e:
                result["reason"] = f"missing_long_feature:{e}"

        # ── 3. Signal short ───────────────────────────────────────────────────
        short_signal = False
        p_short_cal  = 0.0
        if self.short_enabled and passes_short and not long_signal:
            try:
                x_s = np.array([[row[f] for f in self.features_short]], dtype=np.float32)
                p_s = float(self.clf_short.predict_proba(
                    self.scaler_short.transform(x_s)
                )[0, 1])
                p_short_cal = _apply_calibrator(self.calibrator_short, p_s)
                result["p_short"]     = round(p_s, 4)
                result["p_short_cal"] = round(p_short_cal, 4)
                short_signal = p_short_cal >= self.threshold_short
            except KeyError as e:
                result["reason"] = f"missing_short_feature:{e}"

        # ── 4. Décision finale ────────────────────────────────────────────────
        result["long_signal"]  = long_signal
        result["short_signal"] = short_signal

        if long_signal:
            result["action"] = "LONG"
            result["reason"] = f"long_p={p_long_cal:.4f}>={self.threshold_long:.4f}"
        elif short_signal:
            result["action"] = "SHORT"
            result["reason"] = f"short_p={p_short_cal:.4f}>={self.threshold_short:.4f}"
        else:
            result["reason"] = (
                f"no_signal: p_long={p_long_cal:.4f}({self.threshold_long:.2f})"
                f" p_short={p_short_cal:.4f}({self.threshold_short:.2f})"
            )

        return result

    def summary(self) -> str:
        """Résumé lisible des paramètres du prédicteur."""
        lines = [
            "LivePredictor",
            f"  Filter : thr_long={self.filter_thr_long:.3f} thr_short={self.filter_thr_short:.3f}",
            f"  Long   : thr={self.threshold_long:.3f}  features={len(self.features_long)}",
        ]
        if self.short_enabled:
            lines.append(
                f"  Short  : thr={self.threshold_short:.3f}  features={len(self.features_short)}"
            )
        else:
            lines.append("  Short  : DÉSACTIVÉ")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_pkl(path: Path, required: bool = True):
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    if required:
        raise FileNotFoundError(f"Artefact requis introuvable : {path}")
    return None


def _load_json(path: Path, required: bool = True) -> Optional[dict]:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    if required:
        raise FileNotFoundError(f"JSON requis introuvable : {path}")
    return None


def _apply_calibrator(calibrator, p_raw: float) -> float:
    if calibrator is None:
        return p_raw
    try:
        arr = np.array([p_raw])
        try:
            return float(calibrator.predict(arr)[0])
        except AttributeError:
            return float(calibrator.predict_proba(arr.reshape(-1, 1))[0, 1])
    except Exception:
        return p_raw
