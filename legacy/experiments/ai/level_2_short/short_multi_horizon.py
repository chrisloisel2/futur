# -*- coding: utf-8 -*-
"""
ai/level_2/short_multi_horizon.py — MODELE SHORT MULTI-HORIZON
===============================================================

Architecture :
  Input (152 features gamechanger)
       |
  +----|----+----+----+----+----+----+
  H1   H2   H3   H4   H6   H8   H12
  |    |    |    |    |    |    |
  p1   p2   p3   p4   p6   p8   p12
       |___________________________|
                    |
              META-LEARNER
                    |
             ShortSignal
              p_final
              best_horizon
              agreement_score   (0..1)
              per_horizon_probs {h: prob}
              horizon_signal    {h: bool}

Logique :
  - Chaque modele H_h predit P(baisse > seuil_h dans les h prochaines barres).
  - Les seuils seuil_h sont calibres independamment sur le train (quantile).
  - Le meta-learner (LogReg) combine [p1, p2, p3, p4, p6, p8, p12] -> p_final.
  - L'agreement_score mesure combien d'horizons sont en accord.

Interpretation :
  - agreement 7/7 : signal massif, toutes les fenetres confirment
  - agreement 5/7 (h1-h6) : short court/moyen terme confirme
  - agreement 2/7 (h8-h12 uniquement) : short long terme, pas urgent
  - best_horizon = h ou P(baisse) est maximal -> horizon ideal d'entree
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_recall_fscore_support, f1_score
from sklearn.preprocessing import StandardScaler

from ai.level_0.features import FEATURES_SHORT
from ai.level_0.preprocessing import get_X
from ai.level_0.constants import (
    SHORT_TRADEABLE_QUANTILE, SHORT_MIN_ABS_RETURN, SHORT_COST_PCT,
    SHORT_SQUEEZE_LIMIT, SHORT_GRAY_MULT, CLOSE_COL,
)

# ─── Horizons disponibles ─────────────────────────────────────────────────────
MH_HORIZONS: List[int] = [1, 2, 3, 4, 6, 8, 12]   # en barres 1h

# Ponderation par horizon pour le score de consensus
# Les horizons 2-6h ont plus de poids (plus fiables sur crypto)
MH_WEIGHTS: Dict[int, float] = {1: 0.08, 2: 0.15, 3: 0.17, 4: 0.18,
                                  6: 0.17, 8: 0.14, 12: 0.11}


# ─── Types de retour ──────────────────────────────────────────────────────────

@dataclass
class ShortSignal:
    """Signal short multi-horizon produit par le meta-learner."""
    p_final:          float             # probabilite finale (meta)
    best_horizon:     int               # horizon ou P(baisse) est max
    agreement:        float             # fraction d horizons en accord [0,1]
    per_horizon:      Dict[int, float]  # {h: p_h} pour chaque horizon
    horizon_active:   Dict[int, bool]   # {h: True si p_h >= seuil_h}
    weighted_score:   float             # somme ponderee des signaux actifs

    def to_dict(self) -> dict:
        d = asdict(self)
        # JSON-serialisable
        d["per_horizon"]    = {str(k): v for k, v in d["per_horizon"].items()}
        d["horizon_active"] = {str(k): v for k, v in d["horizon_active"].items()}
        return d

    def summary(self) -> str:
        bars = ""
        for h in MH_HORIZONS:
            p = self.per_horizon.get(h, 0.0)
            active = self.horizon_active.get(h, False)
            mark = "+" if active else "."
            bars += f"  H{h:2d}: {p:.2f} {mark}\n"
        return (
            f"ShortSignal p={self.p_final:.3f}  best=H{self.best_horizon}h  "
            f"agreement={self.agreement:.0%}  score={self.weighted_score:.3f}\n"
            + bars
        )


@dataclass
class MultiHorizonShortModel:
    """
    Modele short multi-horizon entraine.

    Usage :
        signal = model.predict_one(X_scaled)   # une barre
        signals = model.predict_batch(X_scaled) # batch
    """
    horizons:    List[int]
    models:      Dict[int, Any]              # {h: XGBClassifier}
    scalers:     Dict[int, StandardScaler]   # {h: scaler}
    thresholds:  Dict[int, float]            # {h: seuil_calibre}
    meta_model:  Any                         # LogisticRegression
    meta_scaler: StandardScaler
    features:    List[str]
    horizon_auc: Dict[int, float] = field(default_factory=dict)
    meta_auc:    float = 0.0

    # ── Prediction batch ────────────────────────────────────────────────────

    def predict_horizons_batch(self, X_scaled: np.ndarray) -> Dict[int, np.ndarray]:
        """Retourne un dict {h: proba_array} pour chaque horizon."""
        return {
            h: self.models[h].predict_proba(
                self.scalers[h].transform(X_scaled)
            )[:, 1]
            for h in self.horizons
        }

    def predict_meta(self, per_h: Dict[int, np.ndarray]) -> np.ndarray:
        """Meta-prediction a partir des probas par horizon."""
        meta_X = np.column_stack([per_h[h] for h in self.horizons])
        return self.meta_model.predict_proba(
            self.meta_scaler.transform(meta_X)
        )[:, 1]

    def predict_full_batch(self, X: np.ndarray) -> List[ShortSignal]:
        """
        Prediction complete batch. Retourne une liste de ShortSignal.

        X      : matrice (n, n_features) AVANT scaling (scaling par horizon interne)
        """
        per_h = self.predict_horizons_batch(X)
        p_final = self.predict_meta(per_h)
        n = len(p_final)

        signals = []
        for i in range(n):
            per_h_i = {h: float(per_h[h][i]) for h in self.horizons}
            active_i = {h: per_h_i[h] >= self.thresholds.get(h, 0.55)
                        for h in self.horizons}

            n_active = sum(active_i.values())
            agreement = n_active / len(self.horizons)
            best_h = max(per_h_i, key=per_h_i.get)

            weighted = sum(
                MH_WEIGHTS.get(h, 0.1) * per_h_i[h]
                for h in self.horizons
            )

            signals.append(ShortSignal(
                p_final=float(p_final[i]),
                best_horizon=best_h,
                agreement=agreement,
                per_horizon=per_h_i,
                horizon_active=active_i,
                weighted_score=float(weighted),
            ))
        return signals

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Interface compatible sklearn : retourne p_final pour chaque barre."""
        per_h = self.predict_horizons_batch(X)
        return self.predict_meta(per_h)

    def get_agreement_array(self, X: np.ndarray) -> np.ndarray:
        """Retourne le tableau d'agreement [0..1] pour chaque barre."""
        per_h = self.predict_horizons_batch(X)
        active = np.stack([
            (per_h[h] >= self.thresholds.get(h, 0.55)).astype(float)
            for h in self.horizons
        ], axis=1)  # (n, 7)
        return active.mean(axis=1)

    def report(self) -> str:
        lines = ["MultiHorizonShortModel"]
        lines.append(f"  Horizons : {self.horizons}")
        lines.append(f"  Meta AUC : {self.meta_auc:.4f}")
        for h in self.horizons:
            lines.append(f"  H{h:2d} : AUC={self.horizon_auc.get(h, 0):.4f}  "
                         f"thr={self.thresholds.get(h, 0):.3f}")
        return "\n".join(lines)

    # ── Serialisation ────────────────────────────────────────────────────────

    def save(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for h in self.horizons:
            with open(out_dir / f"model_h{h}.pkl", "wb") as f:
                pickle.dump(self.models[h], f)
            with open(out_dir / f"scaler_h{h}.pkl", "wb") as f:
                pickle.dump(self.scalers[h], f)
        with open(out_dir / "meta_model.pkl", "wb") as f:
            pickle.dump(self.meta_model, f)
        with open(out_dir / "meta_scaler.pkl", "wb") as f:
            pickle.dump(self.meta_scaler, f)
        meta = {
            "horizons": self.horizons,
            "thresholds": self.thresholds,
            "horizon_auc": self.horizon_auc,
            "meta_auc": self.meta_auc,
            "features": self.features,
        }
        with open(out_dir / "mh_meta.json", "w") as f:
            json.dump(meta, f, indent=2)

    @classmethod
    def load(cls, out_dir: Path) -> "MultiHorizonShortModel":
        out_dir = Path(out_dir)
        with open(out_dir / "mh_meta.json") as f:
            meta = json.load(f)
        horizons = meta["horizons"]
        models, scalers = {}, {}
        for h in horizons:
            with open(out_dir / f"model_h{h}.pkl", "rb") as f:
                models[h] = pickle.load(f)
            with open(out_dir / f"scaler_h{h}.pkl", "rb") as f:
                scalers[h] = pickle.load(f)
        with open(out_dir / "meta_model.pkl", "rb") as f:
            meta_model = pickle.load(f)
        with open(out_dir / "meta_scaler.pkl", "rb") as f:
            meta_scaler = pickle.load(f)
        return cls(
            horizons=horizons,
            models=models, scalers=scalers,
            thresholds={int(k): v for k, v in meta["thresholds"].items()},
            meta_model=meta_model, meta_scaler=meta_scaler,
            features=meta["features"],
            horizon_auc={int(k): v for k, v in meta["horizon_auc"].items()},
            meta_auc=meta.get("meta_auc", 0.0),
        )


# ─── Construction des labels multi-horizon ───────────────────────────────────

def _forward_ret_short(log_close: np.ndarray, h: int) -> np.ndarray:
    """
    Retour forward SHORT a horizon h : -(log(C[t+h]) - log(C[t])).
    Positif = prix a baisse = short profitable.
    NaN pour les h dernieres barres.
    """
    n = len(log_close)
    out = np.full(n, np.nan)
    out[:n - h] = log_close[:n - h] - log_close[h:]  # signe court
    return out


def _mfe_short(log_close: np.ndarray, h: int) -> np.ndarray:
    """MFE court : profit max sur [t+1, t+h] pour un short."""
    n = len(log_close)
    mfe = np.full(n, np.nan)
    if n <= h:
        return mfe
    future = log_close[1:]
    if len(future) < h:
        return mfe
    wins = sliding_window_view(future, window_shape=h)
    valid = wins.shape[0]
    mfe[:valid] = log_close[:valid] - wins.min(axis=1)
    return np.clip(mfe, 0.0, None)


def _mae_short(log_close: np.ndarray, h: int) -> np.ndarray:
    """MAE court : perte adverse max sur [t+1, t+h] pour un short."""
    n = len(log_close)
    mae = np.full(n, np.nan)
    if n <= h:
        return mae
    future = log_close[1:]
    if len(future) < h:
        return mae
    wins = sliding_window_view(future, window_shape=h)
    valid = wins.shape[0]
    mae[:valid] = wins.max(axis=1) - log_close[:valid]
    return np.clip(mae, 0.0, None)


def _build_mh_label(
    ret: np.ndarray, mfe: np.ndarray, mae: np.ndarray,
    threshold: float, gray_mult: float = SHORT_GRAY_MULT,
) -> np.ndarray:
    """
    Construit le label 0/1/-1 pour un horizon.
    1 = short profitable a cet horizon
    0 = short non profitable
    -1 = gray zone (exclu de l'entrainement)
    """
    min_ret = max(SHORT_MIN_ABS_RETURN * 0.5, threshold * 0.3)
    thr_gray = threshold * gray_mult

    positive = (
        (ret >= threshold)
        & (ret >= min_ret)
        & (mfe >= SHORT_COST_PCT * 1.5)
        & (mae <= SHORT_SQUEEZE_LIMIT * 1.2)
        & np.isfinite(ret)
    )

    gray = (
        ~positive & np.isfinite(ret)
        & ((ret >= threshold * 0.5) & (ret < thr_gray))
    )

    y = np.zeros(len(ret), dtype=np.int8)
    y[positive] = 1
    y[gray]     = -1
    y[~np.isfinite(ret)] = -1
    return y


def compute_mh_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    horizons: List[int] = MH_HORIZONS,
) -> Tuple[pd.DataFrame, Dict[int, float]]:
    """
    Calcule les labels short pour chaque horizon.

    Retourne :
        df enrichi des colonnes y_short_h{h}
        thresholds : {h: seuil calibre sur train}
    """
    close_col = CLOSE_COL if CLOSE_COL in df.columns else (
                "close" if "close" in df.columns else None)
    if close_col is None:
        raise RuntimeError("Colonne Close/close introuvable.")

    close = pd.to_numeric(df[close_col], errors="coerce").values
    log_c = np.log(np.clip(close, 1e-9, None))

    df = df.copy()
    thresholds: Dict[int, float] = {}

    for h in horizons:
        ret = _forward_ret_short(log_c, h)
        mfe = _mfe_short(log_c, h)
        mae = _mae_short(log_c, h)

        # Seuil calibre sur train uniquement
        train_ret = ret[train_mask & np.isfinite(ret)]
        if len(train_ret) == 0:
            raise RuntimeError(f"Aucune donnee train pour horizon {h}.")

        # Quantile sur les valeurs absolues des mouvements
        thr = float(np.quantile(np.abs(train_ret), SHORT_TRADEABLE_QUANTILE))
        thr = max(thr, SHORT_MIN_ABS_RETURN * max(0.5, h / 4.0))
        thresholds[h] = thr

        y = _build_mh_label(ret, mfe, mae, thr)
        df[f"y_short_h{h}"] = y
        df[f"future_ret_short_h{h}"] = ret  # pour diagnostics

    return df, thresholds


# ─── Entrainement ─────────────────────────────────────────────────────────────

def _build_xgb_short_h(spw: float, h: int):
    """XGBoost adapte a chaque horizon (hyperparams progressifs)."""
    depth   = 3 if h <= 2 else (4 if h <= 6 else 4)
    n_est   = 400 if h <= 2 else (500 if h <= 6 else 600)
    lr      = 0.03 if h <= 2 else 0.025
    colsamp = 0.45 if h <= 2 else 0.50
    try:
        from xgboost import XGBClassifier
        return XGBClassifier(
            n_estimators=n_est,
            max_depth=depth,
            learning_rate=lr,
            subsample=0.75,
            colsample_bytree=colsamp,
            scale_pos_weight=min(spw, 80.0),
            reg_alpha=0.20, reg_lambda=1.50,
            min_child_weight=10,
            use_label_encoder=False,
            eval_metric="logloss",
            n_jobs=-1, random_state=42,
        ), "XGBoost"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(
            learning_rate=lr, max_iter=n_est, max_depth=depth,
            min_samples_leaf=20, class_weight="balanced", random_state=42,
        ), "HistGBT"


def train_multi_horizon_short(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    horizons: List[int] = MH_HORIZONS,
    features: Optional[List[str]] = None,
    verbose: bool = True,
) -> MultiHorizonShortModel:
    """
    Entraine le modele short multi-horizon.

    Etapes :
      1. Calcul labels pour chaque horizon (sur df entier, seuils sur train)
      2. Entrainement d'un XGBoost par horizon (sur train)
      3. Evaluation AUC par horizon (sur val)
      4. Entrainement du meta-learner (LogReg sur probas val)
      5. Calibration du seuil par horizon (sur TRAIN 2022 si dispo, sinon val)
      6. Rapport et sauvegarde

    Retourne MultiHorizonShortModel.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    feat = features or FEATURES_SHORT

    # ── 0. Validation features ───────────────────────────────────────────────
    missing = [f for f in feat if f not in df.columns]
    if missing:
        print(f"   [MH] Features manquantes ({len(missing)}) -> fill 0: "
              f"{missing[:5]}{'...' if len(missing)>5 else ''}")
        for f in missing:
            df = df.copy()
            df[f] = 0.0

    if verbose:
        print(f"\n   [MH-SHORT] Horizons : {horizons}  Features : {len(feat)}")

    # ── 1. Labels multi-horizon ─────────────────────────────────────────────
    df, label_thresholds = compute_mh_labels(df, train_mask, horizons)

    if verbose:
        for h in horizons:
            col = f"y_short_h{h}"
            n1 = int((df.loc[train_mask, col] == 1).sum())
            n0 = int((df.loc[train_mask, col] == 0).sum())
            pct = n1 / max(n1 + n0, 1)
            print(f"     H{h:2d}: thr={label_thresholds[h]:.4f}  "
                  f"positifs_train={n1} ({pct:.1%})  "
                  f"negatifs_train={n0}")

    # ── 2. Entrainement par horizon ──────────────────────────────────────────
    models: Dict[int, Any] = {}
    scalers: Dict[int, StandardScaler] = {}
    horizon_auc: Dict[int, float] = {}
    per_h_val_probs: Dict[int, np.ndarray] = {}
    per_h_train_probs: Dict[int, np.ndarray] = {}

    # Masque commun pour le meta-learner : barres val ou y_short_h4 est valide
    # Sert a aligner les probas de tous les horizons sur la meme population.
    vm_h4_clean = val_mask & (df["y_short_h4"].values >= 0)

    for h in horizons:
        label = f"y_short_h{h}"
        # Train : exclure gray zone de cet horizon
        tm = train_mask & (df[label].values >= 0)
        # Val pour AUC de cet horizon (peut etre different de vm_h4_clean)
        vm_h = val_mask & (df[label].values >= 0)

        X_tr = get_X(df, tm, feat)
        y_tr = df.loc[tm, label].values.astype(np.int32)
        X_va_h = get_X(df, vm_h, feat)
        y_va_h = df.loc[vm_h, label].values.astype(np.int32)

        # Pour le META-LEARNER : evaluer sur vm_h4_clean (meme population pour tous)
        X_va_common = get_X(df, vm_h4_clean, feat)

        pos = int((y_tr == 1).sum())
        neg = int((y_tr == 0).sum())
        if pos < 50:
            print(f"   [MH] H{h}: trop peu de positifs ({pos}) — horizon desactive")
            from sklearn.dummy import DummyClassifier
            dummy = DummyClassifier(strategy="constant", constant=0)
            if len(X_tr) > 0:
                dummy.fit(X_tr, y_tr)
            sc = StandardScaler()
            if len(X_tr) > 0:
                sc.fit(X_tr)
            models[h] = dummy; scalers[h] = sc
            horizon_auc[h] = 0.5
            per_h_val_probs[h]   = np.full(vm_h4_clean.sum(), 0.5)
            per_h_train_probs[h] = np.full(tm.sum(), 0.5)
            continue

        spw = neg / max(pos, 1)
        sc = StandardScaler(); sc.fit(X_tr)
        scalers[h] = sc

        clf, cname = _build_xgb_short_h(spw, h)
        clf.fit(sc.transform(X_tr), y_tr)
        models[h] = clf

        # Probas sur val pour AUC de cet horizon
        p_va_h = clf.predict_proba(sc.transform(X_va_h))[:, 1]
        # Probas sur population commune (pour meta-learner)
        p_va_common = clf.predict_proba(sc.transform(X_va_common))[:, 1]
        # Probas sur train (pour calibration des seuils)
        p_tr = clf.predict_proba(sc.transform(X_tr))[:, 1]

        per_h_val_probs[h]   = p_va_common   # aligne sur vm_h4_clean
        per_h_train_probs[h] = p_tr

        try:
            auc = float(roc_auc_score(y_va_h, p_va_h))
        except Exception:
            auc = 0.5
        horizon_auc[h] = auc

        if verbose:
            n1_val = int((y_va_h == 1).sum())
            print(f"     H{h:2d} [{cname}]: AUC={auc:.4f}  "
                  f"spw={spw:.1f}  pos_train={pos}  pos_val={n1_val}")

    # ── 3. Meta-learner (entraine sur val, population vm_h4_clean) ──────────
    # Meta-label : y_short_h4 (horizon de reference)
    # Les per_h_val_probs[h] sont tous alignes sur vm_h4_clean (population commune).
    y_meta = df.loc[vm_h4_clean, "y_short_h4"].values.astype(np.int32)

    n_meta = int(vm_h4_clean.sum())
    meta_probs = []
    for h in horizons:
        p = per_h_val_probs.get(h)
        if p is None or len(p) != n_meta:
            p = np.full(n_meta, 0.5)
        meta_probs.append(p)

    meta_X = np.column_stack(meta_probs)  # (n_val_clean, n_horizons)

    meta_sc = StandardScaler()
    meta_clf = LogisticRegression(C=0.5, class_weight="balanced",
                                   max_iter=1000, random_state=42)
    meta_clf.fit(meta_sc.fit_transform(meta_X), y_meta)

    p_meta_val = meta_clf.predict_proba(meta_sc.transform(meta_X))[:, 1]
    try:
        meta_auc = float(roc_auc_score(y_meta, p_meta_val))
    except Exception:
        meta_auc = 0.5

    if verbose:
        print(f"\n   META-LEARNER : AUC={meta_auc:.4f}  "
              f"n_val={len(y_meta)}  positifs={int(y_meta.sum())}")

        # Coefficients meta : quels horizons comptent le plus ?
        if hasattr(meta_clf, "coef_"):
            coefs = meta_clf.coef_[0]
            print("   Poids horizons (meta) :")
            for i, h in enumerate(horizons):
                bar = "+" * int(abs(coefs[i]) * 20) if coefs[i] > 0 else \
                      "-" * int(abs(coefs[i]) * 20)
                print(f"     H{h:2d}: {coefs[i]:+.3f}  {bar}")

    # ── 4. Calibration du seuil par horizon sur TRAIN 2022 (bear year) ──────
    # On utilise les predictions sur train (2022 = annee bear la plus fiable).
    # Seuil = percentile 85 des probas positives sur train (haut precision).
    calib_thresholds: Dict[int, float] = {}
    for h in horizons:
        label = f"y_short_h{h}"
        tm = train_mask & (df[label].values == 1)  # vrais positifs train
        p_tr = per_h_train_probs.get(h)
        if p_tr is None or tm.sum() < 5:
            calib_thresholds[h] = 0.60
            continue
        p_pos = p_tr  # deja filtres sur train
        # Seuil = percentile 70 des probas sur les positifs
        # (etre dans le top 30% des predits positifs sur train)
        try:
            calib_thresholds[h] = float(np.percentile(p_pos, 70))
        except Exception:
            calib_thresholds[h] = 0.60
        # Borner entre [0.45, 0.80]
        calib_thresholds[h] = float(np.clip(calib_thresholds[h], 0.45, 0.80))

    if verbose:
        print("\n   Seuils par horizon :")
        for h in horizons:
            print(f"     H{h:2d}: {calib_thresholds[h]:.3f}")

    # ── 5. Rapport de correlation inter-horizons ─────────────────────────────
    if verbose:
        _print_horizon_correlation(per_h_val_probs, horizons, val_mask.sum())

    # ── 6. Construction et sauvegarde ───────────────────────────────────────
    model = MultiHorizonShortModel(
        horizons=horizons,
        models=models,
        scalers=scalers,
        thresholds=calib_thresholds,
        meta_model=meta_clf,
        meta_scaler=meta_sc,
        features=list(feat),
        horizon_auc=horizon_auc,
        meta_auc=meta_auc,
    )
    model.save(out_dir)

    # Rapport JSON
    report = {
        "horizons": horizons,
        "meta_auc": round(meta_auc, 4),
        "horizon_auc": {h: round(v, 4) for h, v in horizon_auc.items()},
        "thresholds": {h: round(v, 4) for h, v in calib_thresholds.items()},
        "label_thresholds": {h: round(v, 5) for h, v in label_thresholds.items()},
    }
    with open(out_dir / "mh_report.json", "w") as f:
        json.dump(report, f, indent=2)

    if verbose:
        print(f"\n   [MH] Sauvegarde -> {out_dir}")
        print(model.report())

    return model


def _print_horizon_correlation(
    per_h_probs: Dict[int, np.ndarray],
    horizons: List[int],
    n_val: int,
) -> None:
    """Affiche la matrice de correlation entre les probas des differents horizons."""
    try:
        valid_h = [h for h in horizons if h in per_h_probs
                   and len(per_h_probs[h]) > 10]
        if len(valid_h) < 2:
            return
        mat = np.column_stack([per_h_probs[h] for h in valid_h])
        corr = np.corrcoef(mat.T)
        print(f"\n   Correlations inter-horizons (val n={n_val}) :")
        header = "       " + "  ".join(f"H{h:2d}" for h in valid_h)
        print(header)
        for i, h in enumerate(valid_h):
            row = f"  H{h:2d}  " + "  ".join(
                f"{corr[i,j]:.2f}" if i != j else " 1.00"
                for j in range(len(valid_h))
            )
            print(row)
    except Exception:
        pass


# ─── Integration backtest : p_short avec signal de consensus ─────────────────

def mh_compute_batch(
    model: MultiHorizonShortModel,
    X: np.ndarray,
    min_agreement: float = 3 / 7,
) -> dict:
    """
    Calcul batch efficace de tous les signaux MH.

    Retourne un dict avec :
      p_final        : (n,) probabilite meta finale (0 si agreement insuffisant)
      agreement      : (n,) fraction d horizons en accord [0,1]
      best_horizon   : (n,) horizon optimal par barre (entier)
      per_h_probs    : dict {h: (n,)} probas brutes par horizon
      per_h_active   : (n, 7) bool — horizon[j] actif pour la barre i
    """
    per_h = model.predict_horizons_batch(X)
    p_meta = model.predict_meta(per_h)

    # Matrice de probas et flags actifs
    prob_mat = np.column_stack([per_h[h] for h in model.horizons])  # (n, 7)
    thr_arr  = np.array([model.thresholds.get(h, 0.55) for h in model.horizons])
    active_mat = (prob_mat >= thr_arr).astype(float)  # (n, 7)

    agreement_arr = active_mat.mean(axis=1)  # (n,)

    # Best horizon : horizon avec la plus haute proba pour chaque barre
    best_h_idx = prob_mat.argmax(axis=1)  # (n,)
    best_h_arr = np.array(model.horizons)[best_h_idx]  # (n,)

    # Gate agreement
    p_gated = np.where(agreement_arr >= min_agreement, p_meta, 0.0)

    return {
        "p_final":      p_gated,
        "agreement":    agreement_arr,
        "best_horizon": best_h_arr,
        "per_h_probs":  per_h,
        "per_h_active": active_mat,
    }


def compute_mh_forward_returns(
    close_values: np.ndarray,
    horizons: List[int],
) -> Dict[int, np.ndarray]:
    """
    Calcule les retours forward SHORT pour chaque horizon a partir des prix close.

    Retourne {h: ret_short_h} ou ret_short_h[t] = -(log(C[t+h]) - log(C[t]))
    Positif = prix a baisse = short profitable.
    NaN pour les h dernieres barres.
    """
    log_c = np.log(np.clip(close_values, 1e-9, None))
    result = {}
    for h in horizons:
        n = len(log_c)
        ret = np.full(n, np.nan)
        if n > h:
            ret[:n - h] = log_c[:n - h] - log_c[h:]  # signe short
        result[h] = ret
    return result


def mh_short_proba_with_gate(
    model: MultiHorizonShortModel,
    X: np.ndarray,
    min_agreement: float = 0.40,
) -> np.ndarray:
    """
    Retourne la probabilite short finale avec gate de consensus.

    Si agreement < min_agreement : p retourne 0 (pas de trade).
    Cela supprime les signaux isoles (seul un horizon predit).

    min_agreement = 2/7 = 0.286 -> au moins 2 horizons en accord
    min_agreement = 3/7 = 0.429 -> au moins 3 horizons en accord (defaut)
    min_agreement = 4/7 = 0.571 -> au moins 4 horizons en accord (strict)
    """
    per_h = model.predict_horizons_batch(X)
    p_final = model.predict_meta(per_h)
    agreement = model.get_agreement_array(X)
    # Mettre 0 si consensus insuffisant
    p_final = np.where(agreement >= min_agreement, p_final, 0.0)
    return p_final
