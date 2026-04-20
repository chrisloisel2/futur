"""
level_2/long.py — ENTRAÎNEMENT DU MODÈLE LONG
==============================================

Entraîne un modèle binaire sur le label y_long.

Procédure en 3 niveaux :
  1. Baseline A : Logistic Regression (simple, interprétable)
  2. Baseline B : XGBoost / HistGBT (non-linéaire, robuste)
  3. Niveau C   : TCN (séquentiel, uniquement si baseline AUC > 0.63)

Règle de sélection : le modèle le plus complexe ne s'active que s'il bat
le plus simple de +tcn_min_improvement en macro_F1 OOS.

Séparation stricte : ce module ne connaît PAS le short.
"""
from __future__ import annotations

import pickle
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    precision_recall_fscore_support, confusion_matrix,
)
from sklearn.preprocessing import StandardScaler

from ai.level_0.features import FEATURES_LONG, validate_features
from ai.level_0.preprocessing import get_X, fit_scaler
from ai.level_2.long_config import LongModelConfig


def train_long_model(
    df,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    cfg: Optional[LongModelConfig] = None,
    train_tcn: bool = True,
) -> Dict:
    """
    Entraîne le modèle LONG complet.

    Retourne un dict avec :
      side, lr, xgb, best_model, best_model_name,
      scaler, best_tabular, best_tabular_f1, all_metrics
    """
    cfg = cfg or LongModelConfig()
    side = "long"
    label_col = "y_long"

    print("\n" + "=" * 70)
    print(f"STAGE 2 — EDGE MODEL LONG  (label=y_long, features={len(FEATURES_LONG)})")
    print("=" * 70)

    validate_features(df, FEATURES_LONG, context="long/train")

    # Exclure les gray zones (-1) du training
    train_idx  = np.where(train_mask)[0]
    y_train_raw = df.loc[train_mask, label_col].values.astype(np.int32)
    valid_train = y_train_raw >= 0
    train_idx_clean = train_idx[valid_train]
    train_mask_clean = np.zeros(len(df), dtype=bool)
    train_mask_clean[train_idx_clean] = True

    X_train = get_X(df, train_mask_clean, FEATURES_LONG)
    y_train = df.loc[train_mask_clean, label_col].values.astype(np.int32)
    X_val   = get_X(df, val_mask,        FEATURES_LONG)
    y_val   = df.loc[val_mask,           label_col].values.astype(np.int32)
    val_valid = y_val >= 0
    X_val  = X_val[val_valid]
    y_val  = y_val[val_valid]

    n_tr  = len(X_train)
    n_v   = len(X_val)
    pos_tr = int((y_train == 1).sum())
    pos_v  = int((y_val == 1).sum())
    spw    = float((y_train == 0).sum()) / max(pos_tr, 1)

    print(f"   Train  : {n_tr:,}  (LONG=1: {pos_tr:,} = {pos_tr/max(n_tr,1):.1%})")
    print(f"   Val    : {n_v:,}  (LONG=1: {pos_v:,} = {pos_v/max(n_v,1):.1%})")
    print(f"   scale_pos_weight = {spw:.2f}")

    if pos_tr < 200:
        raise RuntimeError(
            f"Trop peu d'exemples LONG ({pos_tr}) en train. "
            f"Réduire tradeable_quantile ou vérifier les données."
        )

    scaler      = fit_scaler(X_train)
    all_metrics: List[Dict] = []

    # ── Baseline A : Logistic Regression ─────────────────────────────────────
    lr = LogisticRegression(
        C=cfg.lr_C,
        class_weight="balanced",
        max_iter=cfg.lr_max_iter,
        solver=cfg.lr_solver,
        random_state=cfg.seed,
    )
    lr.fit(scaler.transform(X_train), y_train)
    m_lr = _eval_model(lr, scaler, X_val, y_val, "Logistic", side)
    all_metrics.append(m_lr)

    # ── Baseline B : XGBoost / HistGBT ───────────────────────────────────────
    xgb, xgb_name = _build_xgb(cfg, spw)
    xgb.fit(scaler.transform(X_train), y_train)
    m_xgb = _eval_model(xgb, scaler, X_val, y_val, xgb_name, side)
    all_metrics.append(m_xgb)

    best_tab = max(all_metrics, key=lambda m: m["macro_f1"])
    best_tab_model = lr if best_tab["model"] == "Logistic" else xgb
    print(f"\n   Meilleur tabulaire LONG : {best_tab['model']}  "
          f"macro_F1={best_tab['macro_f1']:.4f}  AUC={best_tab['auc']:.4f}")

    if best_tab["auc"] < cfg.min_auc:
        print(f"   ⚠  AUC={best_tab['auc']:.4f} < {cfg.min_auc} — signal long faible")
    if best_tab["macro_f1"] < cfg.min_macro_f1:
        print(f"   ⚠  macro_F1={best_tab['macro_f1']:.4f} < {cfg.min_macro_f1} — modèle marginal")

    # ── Niveau C : TCN ────────────────────────────────────────────────────────
    tcn_metrics = None
    if train_tcn and best_tab["auc"] >= cfg.min_auc:
        try:
            tcn_metrics = _train_tcn(df, train_mask, val_mask, scaler, cfg, out_dir)
            if tcn_metrics:
                all_metrics.append(tcn_metrics)
        except Exception as e:
            print(f"   TCN ignoré : {e}")
    elif train_tcn:
        print(f"   TCN skipped : AUC tabulaire trop faible ({best_tab['auc']:.4f})")

    # ── Sélection finale (inclut TCN si disponible) ───────────────────────────
    best_overall = max(all_metrics, key=lambda m: m["macro_f1"])
    artifact_model = best_tab_model
    if best_overall["model"] == "TCN" and tcn_metrics:
        print(f"\n   TCN sélectionné : macro_F1={best_overall['macro_f1']:.4f}")
        best_final_model = None
        best_final_name  = "TCN"
    else:
        best_final_model = best_tab_model
        best_final_name  = best_tab["model"]
        print(f"\n   Modèle final LONG : {best_final_name}")

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "logistic.pkl", "wb") as f: pickle.dump(lr, f)
    with open(out_dir / "xgb.pkl",      "wb") as f: pickle.dump(xgb, f)
    with open(out_dir / "scaler.pkl",   "wb") as f: pickle.dump(scaler, f)
    with open(out_dir / "best_model.pkl", "wb") as f: pickle.dump(artifact_model, f)
    with open(out_dir / "model.pkl",      "wb") as f: pickle.dump(artifact_model, f)

    summary = {
        "side":             side,
        "models":           all_metrics,
        "best_tabular":     best_tab["model"],
        "best_final":       best_final_name,
        "beats_threshold":  best_tab["auc"] >= cfg.min_auc,
    }
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(summary, f, indent=2)

    return {
        "side":              side,
        "lr":                lr,
        "xgb":               xgb,
        "best_model":        best_final_model or best_tab_model,
        "best_model_name":   best_final_name,
        "scaler":            scaler,
        "features":          list(FEATURES_LONG),
        "best_tabular":      best_tab["model"],
        "best_tabular_f1":   best_tab["macro_f1"],
        "all_metrics":       all_metrics,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _eval_model(clf, scaler, X_val, y_val, label, side) -> Dict:
    """Évalue un modèle binaire sur la validation."""
    X_sc    = scaler.transform(X_val)
    y_pred  = clf.predict(X_sc)
    y_proba = clf.predict_proba(X_sc)[:, 1] if hasattr(clf, "predict_proba") else y_pred.astype(float)

    acc   = accuracy_score(y_val, y_pred)
    mf1   = f1_score(y_val, y_pred, average="macro", zero_division=0)
    prec, recall, _, _ = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1], zero_division=0
    )
    try:
        auc = float(roc_auc_score(y_val, y_proba))
    except Exception:
        auc = float("nan")
    cm = confusion_matrix(y_val, y_pred, labels=[0, 1]).tolist()

    print(f"   [{label:>12}]  acc={acc:.4f}  macro_F1={mf1:.4f}  AUC={auc:.4f}  "
          f"prec_LONG={prec[1]:.3f}  recall_LONG={recall[1]:.3f}")

    return {
        "model":         label,
        "side":          side,
        "acc":           round(acc, 4),
        "macro_f1":      round(mf1, 4),
        "auc":           round(auc, 4),
        "precision_long": round(float(prec[1]), 4),
        "recall_long":    round(float(recall[1]), 4),
        "confusion_matrix": cm,
    }


def _build_xgb(cfg: LongModelConfig, spw: float):
    """Construit XGBoost ou HistGBT avec compensation d'imbalance."""
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=cfg.xgb_n_estimators,
            max_depth=cfg.xgb_max_depth,
            learning_rate=cfg.xgb_learning_rate,
            subsample=cfg.xgb_subsample,
            colsample_bytree=cfg.xgb_colsample_bytree,
            scale_pos_weight=spw,
            reg_alpha=cfg.xgb_reg_alpha,
            reg_lambda=cfg.xgb_reg_lambda,
            min_child_weight=cfg.xgb_min_child_weight,
            use_label_encoder=False,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=cfg.seed,
        )
        return clf, "XGBoost"
    except ImportError:
        pass
    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(
        learning_rate=cfg.xgb_learning_rate,
        max_iter=cfg.xgb_n_estimators,
        max_depth=cfg.xgb_max_depth,
        min_samples_leaf=cfg.xgb_min_child_weight * 2,
        class_weight="balanced",
        random_state=cfg.seed,
    )
    return clf, "HistGBT"


def _train_tcn(df, train_mask, val_mask, snapshot_scaler, cfg: LongModelConfig,
               out_dir: Path) -> Optional[Dict]:
    """
    Entraîne le TCN sur les fenêtres tradeables pour le LONG.
    """
    try:
        import tensorflow as tf
        from ai.models.training.common.scaler import RobustScaler, ReservoirSampler
        from ai.models.level_1.Event_Classifier import EventClassifier, EventClassifierConfig
    except ImportError as e:
        print(f"   TCN skipped (import manquant : {e})")
        return None

    print("\n   --- TCN Edge Model (LONG) ---")

    LOOKBACK = cfg.tcn_lookback
    F = len(FEATURES_LONG)

    tradeable_arr = df["tradeable_net"].values
    y_long_arr    = df["y_long"].values

    stride = 2
    total  = max(0, len(df) - LOOKBACK)

    def get_windows(mask_idx):
        idxset = set(mask_idx)
        for i in range(0, total, stride):
            end = i + LOOKBACK
            if end - 1 not in idxset:
                continue
            if tradeable_arr[end - 1] != 1:
                continue
            lbl = int(y_long_arr[end - 1])
            if lbl < 0:
                continue
            yield i, lbl

    train_idx = np.where(train_mask)[0]
    val_idx   = np.where(val_mask)[0]
    train_win = list(get_windows(train_idx))
    val_win   = list(get_windows(val_idx))

    if len(train_win) < cfg.tcn_min_windows:
        print(f"   TCN skipped : seulement {len(train_win)} fenêtres train")
        return None

    print(f"   Fenêtres train={len(train_win):,}  val={len(val_win):,}")

    Xraw = df[FEATURES_LONG].values.astype(np.float32)
    sc = RobustScaler()
    sampler = ReservoirSampler(200_000, seed=cfg.seed)
    for i, _ in train_win[:5000]:
        sampler.add(Xraw[i:i + LOOKBACK])
    sc.fit(sampler.get())
    Xn = sc.transform(Xraw)

    def make_ds(windows, shuffle=False):
        sig = (tf.TensorSpec((LOOKBACK, F), tf.float32),
               tf.TensorSpec((), tf.int32))
        def gen():
            for i, y in windows:
                yield Xn[i:i + LOOKBACK].astype(np.float32), np.int32(y)
        ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
        if shuffle:
            ds = ds.shuffle(2048, seed=cfg.seed)
        return ds.batch(cfg.tcn_batch_size).prefetch(tf.data.AUTOTUNE)

    ds_train = make_ds(train_win, shuffle=True)
    ds_val   = make_ds(val_win)

    try:
        model_arch_cfg = EventClassifierConfig(
            d_model=cfg.tcn_d_model, n_layers=cfg.tcn_n_layers,
            n_regimes=2, dropout=cfg.tcn_dropout, confidence_dropout=0.1,
        )
    except TypeError:
        model_arch_cfg = EventClassifierConfig(
            d_model=cfg.tcn_d_model, n_layers=cfg.tcn_n_layers,
            n_regimes=2, dropout=cfg.tcn_dropout,
        )
    model = EventClassifier(model_arch_cfg)

    opt = tf.keras.optimizers.AdamW(
        learning_rate=cfg.tcn_learning_rate,
        weight_decay=1e-4,
        global_clipnorm=1.0,
    )

    best_f1, best_ep, bad = 0.0, -1, 0
    print(f"\n   {'Ep':>3}  {'tr_loss':>9}  {'v_loss':>9}  {'acc':>7}  {'F1':>8}  t(s)")
    print("   " + "─" * 52)

    from sklearn.metrics import f1_score as sk_f1

    for ep in range(cfg.tcn_epochs):
        ep_t0 = time.time()
        tr_loss = []
        for xb, yb in ds_train:
            with tf.GradientTape() as tape:
                out    = model(xb, training=True)
                logits = out["regime_logits"]
                probs  = tf.nn.softmax(logits, -1)
                p_t    = tf.reduce_sum(probs * tf.one_hot(tf.cast(yb, tf.int32), 2), -1)
                ce     = tf.keras.losses.sparse_categorical_crossentropy(
                    yb, logits, from_logits=True)
                loss   = tf.reduce_mean((1 - p_t) ** 2.0 * ce)
            grads = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grads, model.trainable_variables))
            tr_loss.append(float(loss.numpy()))

        v_loss, yhat_all, yt_all = [], [], []
        for xb, yb in ds_val:
            out    = model(xb, training=False)
            logits = out["regime_logits"]
            probs  = tf.nn.softmax(logits, -1)
            p_t    = tf.reduce_sum(probs * tf.one_hot(tf.cast(yb, tf.int32), 2), -1)
            ce     = tf.keras.losses.sparse_categorical_crossentropy(
                yb, logits, from_logits=True)
            v_loss.append(float(tf.reduce_mean((1 - p_t) ** 2.0 * ce).numpy()))
            yhat_all.extend(tf.argmax(probs, -1).numpy().tolist())
            yt_all.extend(yb.numpy().tolist())

        yh  = np.array(yhat_all, np.int32)
        yt  = np.array(yt_all,   np.int32)
        acc = float((yh == yt).mean()) if len(yt) else 0.0
        mf1 = float(sk_f1(yt, yh, average="macro", zero_division=0)) if len(yt) else 0.0
        ep_t = time.time() - ep_t0
        print(f"   {ep+1:>3}  {np.mean(tr_loss):>9.4f}  {np.mean(v_loss):>9.4f}  "
              f"{acc:>6.2%}  {mf1:>8.4f}  {ep_t:.0f}")

        if mf1 > best_f1 + 1e-4:
            best_f1, best_ep, bad = mf1, ep + 1, 0
            out_dir.mkdir(parents=True, exist_ok=True)
            model.save_weights(str(out_dir / "tcn_best.weights.h5"))
        else:
            bad += 1
            if bad >= cfg.tcn_patience:
                print(f"   Early stop à l'epoch {ep+1}")
                break

    print(f"\n   TCN  best macro_F1={best_f1:.4f}  (epoch {best_ep})")

    with open(out_dir / "tcn_scaler.pkl", "wb") as f: pickle.dump(sc, f)

    return {
        "model":        "TCN",
        "side":         "long",
        "macro_f1":     round(best_f1, 4),
        "best_epoch":   best_ep,
    }
