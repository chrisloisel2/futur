#!/usr/bin/env python3
# =============================================================================
# train_event_classifier_v2.py
# =============================================================================
# Script d'entraînement de l'EventClassifier avec le nouveau pipeline.
#
# Usage :
#   python train_event_classifier_v2.py \
#       --csv futur/data/BTCUSD_1h_Binance.csv \
#       --out runs/event_classifier_v2
#
# Ordre d'exécution :
#   1. Parsing args
#   2. Construction des datasets (make_tf_datasets)
#   3. Sanity checks
#   4. Construction du modèle (EventClassifier + wrapper)
#   5. Entraînement
#   6. Évaluation sur test
#   7. Calibration confidence
#   8. Sauvegarde des artefacts
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf

# ── Chemin vers le projet ─────────────────────────────────────────────────────
# Ce fichier est dans futur/ai/models/training/ → remonter 4 niveaux donne futur/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ai.models.level_1.Event_Classifier import EventClassifier, EventClassifierConfig
from ai.models.training.common.regime_pipeline_v2 import (
    PipelineConfig,
    make_tf_datasets,
    build_trainable_model,
    build_callbacks,
    build_val_detail_callback,
    overfit_test,
    sanity_checks,
    FEATURE_COLS,
    N_FEATURES,
    N_REGIMES,
    REGIME_NAMES,
    RobustFeatureScaler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# ARGS
# =============================================================================

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Train EventClassifier with causal feature pipeline"
    )
    # Data
    ap.add_argument("--csv", required=True, help="Path to OHLCV CSV (Binance 1h)")
    ap.add_argument("--out", default="runs/event_classifier_v2", help="Output dir")

    # Pipeline
    ap.add_argument("--horizon",  type=int,   default=12,   help="Forward label horizon (bars)")
    ap.add_argument("--seq_len",  type=int,   default=64,   help="Sequence length L")
    ap.add_argument("--batch",    type=int,   default=256,  help="Batch size")

    # Model
    ap.add_argument("--d_model",  type=int,   default=64,   help="TCN hidden dim")
    ap.add_argument("--n_layers", type=int,   default=3,    help="TCN layers")
    ap.add_argument("--dropout",  type=float, default=0.2,  help="TCN dropout")

    # Training
    ap.add_argument("--epochs",   type=int,   default=60,   help="Max epochs")
    ap.add_argument("--lr",       type=float, default=3e-4, help="Adam learning rate")
    ap.add_argument("--patience", type=int,   default=10,   help="EarlyStopping patience")

    # Loss weights
    ap.add_argument("--trade_w", type=float, default=0.30, help="Weight for tradeability BCE loss")
    ap.add_argument("--reg_w",   type=float, default=0.15, help="Weight for fwd_ret MSE loss")

    # Validation callback
    ap.add_argument(
        "--val_log_every", type=int, default=1,
        help="Log val detail every N epochs (1 = each epoch, 5 = every 5 epochs)",
    )

    return ap.parse_args()


# =============================================================================
# RAPPORT FINAL DÉTAILLÉ (test set)
# =============================================================================

def _print_final_eval(
    model: tf.keras.Model,
    ds: tf.data.Dataset,
    meta_arrays: dict,
    keras_results: dict,
    split_name: str = "TEST",
) -> None:
    """
    Rapport complet sur un split (test) après entraînement :
      - Per-class recall/precision/F1/support
      - Confusion matrix normalisée
      - Tradeability metrics + exploitation joint
      - fwd_ret_pred correlation
    """
    from ai.models.training.common.regime_pipeline_v2 import (
        REGIME_NAMES, N_REGIMES, ValDetailCallback,
    )

    # Collecte des prédictions
    yr_true_l, yr_pred_l, probs_l = [], [], []
    yt_true_l, trade_l = [], []
    fwd_true_l, fwd_pred_l = [], []
    ent_l = []

    for batch in ds:
        x      = batch[0]
        labels = batch[1]
        out    = model(x, training=False)
        yr_true_l.append(labels["regime"].numpy().flatten())
        probs = out["regime_probs"].numpy()
        probs_l.append(probs)
        yr_pred_l.append(np.argmax(probs, axis=-1))
        yt_true_l.append(labels["tradeable"].numpy().flatten())
        trade_l.append(out["tradeability"].numpy().flatten())
        fwd_true_l.append(labels["fwd_ret_norm"].numpy().flatten())
        fwd_pred_l.append(out["fwd_ret_pred"].numpy().flatten())
        ent_l.append(out["entropy"].numpy().flatten())

    yr_true     = np.concatenate(yr_true_l).astype(np.int32)
    yr_pred     = np.concatenate(yr_pred_l).astype(np.int32)
    regime_probs = np.concatenate(probs_l, axis=0).astype(np.float32)
    yt_true     = np.concatenate(yt_true_l).astype(np.int32)
    trade_pred  = np.concatenate(trade_l).astype(np.float32)
    fwd_true    = np.concatenate(fwd_true_l).astype(np.float32)
    fwd_pred    = np.concatenate(fwd_pred_l).astype(np.float32)
    entropy     = np.concatenate(ent_l).astype(np.float32)

    n = N_REGIMES
    per_class   = ValDetailCallback._per_class_metrics(yr_true, yr_pred, n)
    cm          = ValDetailCallback._confusion_matrix(yr_true, yr_pred, n)
    macro_f1    = float(np.mean([m["f1"] for m in per_class]))
    overall_acc = float((yr_true == yr_pred).mean())

    majority_class = int(np.bincount(yr_true).argmax())
    baseline_acc   = float((yr_true == majority_class).mean())

    sep = "═" * 72
    print(f"\n{sep}")
    print(
        f"  {split_name} FINAL  n={len(yr_true):,}  "
        f"acc={overall_acc:.4f}  macro-F1={macro_f1:.4f}  "
        f"baseline={baseline_acc:.4f}"
    )
    print(sep)

    # Tableau per-class
    print(
        f"  {'REGIME':<10s}  {'Recall':>7}  {'Precision':>9}  "
        f"{'F1':>6}  {'Support':>8}"
    )
    print(f"  {'─'*10}  {'─'*7}  {'─'*9}  {'─'*6}  {'─'*8}")
    for m in per_class:
        flag = " ←" if m["recall"] < 0.25 else ""
        print(
            f"  {m['name']:<10s}  {m['recall']:>7.1%}  {m['precision']:>9.1%}  "
            f"{m['f1']:>6.3f}  {m['support']:>8,}{flag}"
        )
    print(f"  {'─'*10}  {'─'*7}  {'─'*9}  {'─'*6}  {'─'*8}")
    print(
        f"  {'MACRO':10s}  {'':>7}  {'':>9}  {macro_f1:>6.3f}  "
        f"  gain={overall_acc - baseline_acc:+.4f} vs majority"
    )

    # Confusion matrix
    cm_norm = cm.astype(float) / (cm.sum(axis=1, keepdims=True) + 1e-9)
    short = [REGIME_NAMES[c][:4].upper() for c in range(n)]
    print(f"\n  Confusion (normalisée lignes) :")
    print("        " + "".join(f"{s:>7s}" for s in short))
    for i in range(n):
        row = "  ".join(f"{cm_norm[i,j]:5.1%}" for j in range(n))
        print(f"  {short[i]:>6s} | {row}")

    # Tradeability
    print(f"\n  Tradeability (label positif = {float(yt_true.mean()):.1%}) :")
    for thr_t in (0.50, 0.55, 0.60):
        t_bin  = (trade_pred >= thr_t).astype(np.int32)
        t_acc  = float((t_bin == yt_true).mean())
        tp_t   = int(((t_bin == 1) & (yt_true == 1)).sum())
        fp_t   = int(((t_bin == 1) & (yt_true == 0)).sum())
        prec_t = tp_t / (tp_t + fp_t + 1e-9)
        cov_t  = float(t_bin.mean())
        print(f"    thr={thr_t:.2f}  acc={t_acc:.3f}  prec={prec_t:.3f}  cov={cov_t:.1%}")

    # Exploitation thresholds
    UP_IDX, DOWN_IDX = 1, 2
    p_up   = regime_probs[:, UP_IDX]
    p_down = regime_probs[:, DOWN_IDX]
    print(f"\n  Exploitation thresholds (P>θ AND trade>0.55) :")
    print(f"  {'θ':>5}  {'cov_UP':>8}  {'prec_UP':>8}  "
          f"{'cov_DW':>8}  {'prec_DW':>8}  {'total_cov':>10}")
    trade_mask = (trade_pred >= 0.55).astype(bool)
    for theta in (0.50, 0.55, 0.60, 0.65, 0.70):
        su = (p_up   > theta) & trade_mask
        sd = (p_down > theta) & trade_mask
        cov_u  = float(su.mean())
        cov_d  = float(sd.mean())
        prec_u = float((yr_true[su] == UP_IDX).mean())   if su.sum() > 0 else float("nan")
        prec_d = float((yr_true[sd] == DOWN_IDX).mean()) if sd.sum() > 0 else float("nan")
        tot_cov = float((su | sd).mean())
        print(f"  {theta:.2f}  {cov_u:>8.1%}  {prec_u:>8.3f}  "
              f"{cov_d:>8.1%}  {prec_d:>8.3f}  {tot_cov:>10.1%}")

    # fwd_ret_pred correlation
    corr = float(np.corrcoef(fwd_true, fwd_pred)[0, 1])
    print(f"\n  fwd_ret_pred  corr={corr:.3f}  "
          f"pred_std={float(fwd_pred.std()):.3f}  true_std={float(fwd_true.std()):.3f}")

    print(
        f"\n  Entropy  mean={float(entropy.mean()):.3f}  "
        f"max_theoretical={float(np.log(n)):.3f}"
    )
    print(sep)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    args = parse_args()
    out_dir = args.out
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # ── GPU check ──────────────────────────────────────────────────────────────
    gpus = tf.config.list_physical_devices("GPU")
    logger.info("GPUs available: %d", len(gpus))
    for g in gpus:
        tf.config.experimental.set_memory_growth(g, True)

    # =========================================================================
    # ÉTAPE 1 — PIPELINE & DATASETS
    # =========================================================================
    logger.info("Building datasets from %s …", args.csv)

    cfg = PipelineConfig(
        horizon=args.horizon,
        seq_len=args.seq_len,
    )

    ds_train, ds_val, ds_test, meta = make_tf_datasets(
        csv_path=args.csv,
        cfg=cfg,
        batch_size=args.batch,
        shuffle_train=True,
        seed=42,
    )

    # Sauvegarder le scaler et les métadonnées (sans les arrays numpy)
    meta_serializable = {
        k: v for k, v in meta.items() if k != "_arrays"
    }
    with open(os.path.join(out_dir, "pipeline_meta.json"), "w") as f:
        json.dump(meta_serializable, f, indent=2, default=str)

    RobustFeatureScaler.from_dict(meta["scaler"]).save(
        os.path.join(out_dir, "scaler.json")
    )

    # =========================================================================
    # ÉTAPE 2 — SANITY CHECKS
    # =========================================================================
    sanity_checks(meta, thresholds=meta["thresholds"])

    # Distribution des labels par split
    print("\nLabel statistics :")
    for split, stats in meta["label_stats"].items():
        print(f"\n  [{split.upper()}]  n_valid={stats['n_valid']:,}")
        for rname, pct in stats.get("regime_pct", {}).items():
            print(f"    {rname:10s} {pct:.1%}")

    print(f"Séquences : {meta['n_sequences']}")

    # =========================================================================
    # ÉTAPE 3 — MODÈLE
    # =========================================================================
    model_cfg = EventClassifierConfig(
        d_model=args.d_model,
        n_layers=args.n_layers,
        n_regimes=N_REGIMES,
        dropout=args.dropout,
        head_dropout=0.1,
    )
    classifier = EventClassifier(model_cfg)

    model = build_trainable_model(
        event_classifier=classifier,
        class_weights=meta["class_weights"],
        trade_loss_weight=args.trade_w,
        reg_loss_weight=args.reg_w,
        learning_rate=args.lr,
    )

    # Build (warm-up pour initialiser les poids)
    for batch in ds_train.take(1):
        _ = model(batch[0], training=False)
        break

    logger.info(
        "Model built — parameters=%d  features=%d  seq_len=%d  n_regimes=%d",
        model.count_params(), N_FEATURES, cfg.seq_len, N_REGIMES,
    )

    # Vérification des shapes
    x_probe = tf.zeros([1, cfg.seq_len, N_FEATURES])
    out_probe = classifier(x_probe, training=False)
    assert out_probe["regime_logits"].shape == (1, N_REGIMES), \
        f"Unexpected regime_logits shape: {out_probe['regime_logits'].shape}"
    assert out_probe["tradeability"].shape == (1, 1), \
        f"Unexpected tradeability shape: {out_probe['tradeability'].shape}"
    assert out_probe["fwd_ret_pred"].shape == (1, 1), \
        f"Unexpected fwd_ret_pred shape: {out_probe['fwd_ret_pred'].shape}"
    logger.info(
        "Output shapes OK — regime_logits=%s  tradeability=%s  fwd_ret_pred=%s",
        out_probe["regime_logits"].shape,
        out_probe["tradeability"].shape,
        out_probe["fwd_ret_pred"].shape,
    )

    # =========================================================================
    # ÉTAPE 4 — ENTRAÎNEMENT
    # =========================================================================

    # Classe majoritaire du train (pour la baseline affichée dans les logs val)
    arrays = meta["_arrays"]
    majority_class = int(np.bincount(arrays["yr_tr"]).argmax())

    # Callback de validation détaillée : s'exécute après chaque epoch
    # sur le val set complet et affiche recall/precision/F1 par classe,
    # confusion matrix, métriques confidence, entropie.
    val_detail_cb = build_val_detail_callback(
        ds_val=ds_val,
        out_dir=out_dir,
        majority_class=majority_class,
        log_every=args.val_log_every,
    )

    # ── Overfit test (vérifie que la loss est saine avant d'entraîner) ────────
    # Doit afficher PASS et loss_init ≈ log(3) = 1.099.
    # Si FAIL → ne pas lancer l'entraînement complet.
    ot = overfit_test(
        classifier,
        seq_len=cfg.seq_len,
        n_features=N_FEATURES,
    )
    if ot["verdict"] == "FAIL":
        raise RuntimeError(
            "Overfit test FAILED — loss buggée ou gradient bloqué. "
            "Corriger avant de lancer l'entraînement complet."
        )

    # L'ordre compte : val_detail_cb doit être AVANT EarlyStopping
    # pour que les logs apparaissent avant le message "Epoch X: early stopping"
    callbacks = [val_detail_cb] + build_callbacks(out_dir, patience=args.patience)

    logger.info(
        "Starting training (max %d epochs, val detail every %d epoch(s)) …",
        args.epochs, args.val_log_every,
    )

    # verbose=2 : une ligne par epoch (Keras) + les blocs val détaillés du callback
    # verbose=1 : barre de progression + blocs val — plus bavard mais utile en debug
    history = model.fit(
        ds_train,
        validation_data=ds_val,
        epochs=args.epochs,
        callbacks=callbacks,
        verbose=2,
    )

    # Sauvegarder l'historique Keras (métriques agrégées)
    history_path = os.path.join(out_dir, "history.json")
    with open(history_path, "w") as f:
        json.dump(
            {k: [float(v) for v in vals] for k, vals in history.history.items()},
            f, indent=2,
        )
    logger.info("History saved → %s", history_path)
    logger.info(
        "Val detail log → %s",
        str(Path(out_dir) / "val_detail_log.jsonl"),
    )

    # =========================================================================
    # ÉTAPE 5 — ÉVALUATION SUR TEST (rapport final complet)
    # =========================================================================
    logger.info("Evaluating on test set …")
    test_results = model.evaluate(ds_test, verbose=0, return_dict=True)

    # Rapport détaillé sur le test set (même format que val_detail_cb)
    _print_final_eval(model, ds_test, arrays, test_results, "TEST")

    with open(os.path.join(out_dir, "test_results.json"), "w") as f:
        json.dump(
            {k: float(v) for k, v in test_results.items()},
            f, indent=2,
        )

    # =========================================================================
    # ÉTAPE 6 — SAUVEGARDE DES POIDS FINAUX
    # =========================================================================
    final_weights_path = os.path.join(out_dir, "final_weights.weights.h5")
    model.save_weights(final_weights_path)
    logger.info("Final weights saved → %s", final_weights_path)

    # Sauvegarder la config modèle
    model_cfg_path = os.path.join(out_dir, "model_config.json")
    with open(model_cfg_path, "w") as f:
        json.dump(
            {
                "d_model": args.d_model,
                "n_layers": args.n_layers,
                "n_regimes": N_REGIMES,
                "dropout": args.dropout,
                "seq_len": cfg.seq_len,
                "n_features": N_FEATURES,
                "feature_cols": FEATURE_COLS,
                "horizon": cfg.horizon,
            },
            f, indent=2,
        )

    logger.info("All artifacts saved to %s", out_dir)
    print(f"\nArtefacts :")
    for fname in sorted(Path(out_dir).iterdir()):
        print(f"  {fname.name}")


# =============================================================================
# INFÉRENCE (exemple production)
# =============================================================================

def load_model_for_inference(
    out_dir: str,
) -> tuple:
    """
    Recharge le modèle et le scaler pour l'inférence en production.

    Returns :
      (classifier, scaler, model_cfg_dict)

    Exemple d'utilisation :
      classifier, scaler, cfg = load_model_for_inference("runs/event_classifier_v2")
      # Préparer une fenêtre de 64 barres, 28 features
      x_raw = ...  # [64, 28] numpy array, features non scalées
      x_scaled = scaler.transform(x_raw[None])  # [1, 64, 28] après scaling
      x_clipped = np.clip(x_scaled, -10, 10)
      out = classifier(tf.constant(x_clipped, dtype=tf.float32), training=False)
      regime       = int(tf.argmax(out["regime_probs"], axis=-1).numpy()[0])
      tradeability = float(out["tradeability"].numpy()[0, 0])
      fwd_ret_pred = float(out["fwd_ret_pred"].numpy()[0, 0])
      # Signal = (out["regime_probs"][0,1]>0.60 or out["regime_probs"][0,2]>0.60) and tradeability>0.55
    """
    cfg_path = os.path.join(out_dir, "model_config.json")
    with open(cfg_path) as f:
        model_cfg_dict = json.load(f)

    model_cfg = EventClassifierConfig(
        d_model=model_cfg_dict["d_model"],
        n_layers=model_cfg_dict["n_layers"],
        n_regimes=model_cfg_dict["n_regimes"],
        dropout=model_cfg_dict["dropout"],
    )
    classifier = EventClassifier(model_cfg)

    # Build avant de charger les poids
    L = model_cfg_dict["seq_len"]
    F = model_cfg_dict["n_features"]
    _ = classifier(tf.zeros([1, L, F]), training=False)
    classifier.load_weights(os.path.join(out_dir, "best_weights.weights.h5"))

    scaler = RobustFeatureScaler.load(os.path.join(out_dir, "scaler.json"))

    return classifier, scaler, model_cfg_dict


if __name__ == "__main__":
    main()
