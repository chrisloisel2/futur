"""
Production-Ready Regime Classifier Training with Impulse Recall Fix
===================================================================

CRITICAL FIXES:
1. SGDClassifier + class_weight='balanced' (not LogisticRegression)
2. CalibratedClassifierCV for Brier score fix
3. Hard gate: impulse recall >= 0.35
4. 5 discriminant features for impulse detection
5. Comprehensive per-class metrics
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
import numpy as np


@dataclass
class RegimeClassifierMetrics:
    """Complete metrics for regime classifier evaluation."""
    # Overall
    accuracy: float
    macro_f1: float
    weighted_f1: float

    # Per-class
    per_class_recall: Dict[str, float]
    per_class_precision: Dict[str, float]
    per_class_f1: Dict[str, float]

    # Calibration
    brier_score: float
    ece: float
    avg_entropy: float

    # Confusion
    confusion_matrix: list
    confusion_matrix_normalized: list
    pred_distribution: Dict[str, float]

    # Gate
    impulse_recall_gate_passed: bool
    min_impulse_recall: float = 0.35

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def add_impulse_discriminant_features(df) -> None:
    """
    Add 5 critical features IN-PLACE for impulse discrimination.

    These capture VIOLENCE of movement, not just direction.
    """
    # 1. Instant velocity
    df['abs_ret_1m'] = df['ret'].abs() if 'ret' in df.columns else df['log_ret'].abs()

    # 2. Cumulative momentum (5-bar)
    ret_col = 'ret' if 'ret' in df.columns else 'log_ret'
    df['abs_ret_5m'] = df[ret_col].rolling(5).sum().abs().fillna(0)

    # 3. Range normalized
    if 'High' in df.columns and 'Low' in df.columns and 'Close' in df.columns:
        df['range_1m'] = (df['High'] - df['Low']) / (df['Close'] + 1e-8)

    # 4. Volume anomaly (z-score)
    if 'Volume' in df.columns:
        vol_mean = df['Volume'].rolling(60).mean()
        vol_std = df['Volume'].rolling(60).std()
        df['vol_z_60m'] = (df['Volume'] - vol_mean) / (vol_std + 1e-8)
        df['vol_z_60m'] = df['vol_z_60m'].fillna(0)

    # 5. RV ratio (short/long)
    if ret_col in df.columns:
        rv_5 = df[ret_col].rolling(5).std()
        rv_60 = df[ret_col].rolling(60).std()
        df['rv_ratio_5_60'] = rv_5 / (rv_60 + 1e-9)
        df['rv_ratio_5_60'] = df['rv_ratio_5_60'].fillna(0)


def train_production_regime_classifier(X_train, y_train, classes: list, min_impulse_recall: float = 0.35):
    """
    Train regime classifier with ALL production fixes.

    Returns:
        (calibrated_model, training_metrics)

    Raises:
        ValueError: If impulse recall < threshold (CLASS COLLAPSE)
    """
    from sklearn.linear_model import SGDClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support,
        confusion_matrix as sklearn_cm
    )

    # Base classifier with balanced class weights
    base_clf = SGDClassifier(
        loss='log_loss',
        penalty='l2',
        alpha=1e-5,
        max_iter=20,
        tol=1e-3,
        class_weight='balanced',  # ✅ FIXES CLASS IMBALANCE
        random_state=42,
        n_jobs=-1
    )

    # Calibrate probabilities
    clf = CalibratedClassifierCV(
        base_clf,
        method='isotonic',  # ✅ FIXES BRIER
        cv=3,
        n_jobs=-1
    )

    # Train
    clf.fit(X_train, y_train)

    # Evaluate on training data
    y_pred = clf.predict(X_train)
    y_proba = clf.predict_proba(X_train)

    # Compute metrics
    accuracy = float(accuracy_score(y_train, y_pred))
    macro_f1 = float(f1_score(y_train, y_pred, average='macro'))
    weighted_f1 = float(f1_score(y_train, y_pred, average='weighted'))

    # Per-class
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_train, y_pred, labels=classes, zero_division=0
    )

    per_class_recall = {cls: float(r) for cls, r in zip(classes, recall)}
    per_class_precision = {cls: float(p) for cls, p in zip(classes, precision)}
    per_class_f1 = {cls: float(f) for cls, f in zip(classes, f1)}

    # Brier score (multiclass)
    n_classes = len(classes)
    y_onehot = np.zeros((len(y_train), n_classes))
    for i, cls in enumerate(classes):
        y_onehot[y_train == cls, i] = 1
    brier = float(np.mean((y_proba - y_onehot) ** 2))

    # ECE
    ece = compute_ece_multiclass(y_train, y_proba, classes)

    # Entropy
    avg_entropy = float(-(y_proba * np.log(y_proba + 1e-9)).sum(axis=1).mean())

    # Confusion matrix
    cm = sklearn_cm(y_train, y_pred, labels=classes)
    cm_norm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]

    # Prediction distribution
    unique, counts = np.unique(y_pred, return_counts=True)
    pred_dist = {
        cls: float(counts[np.where(unique == cls)[0][0]] / len(y_pred))
        if cls in unique else 0.0
        for cls in classes
    }

    # Gate check
    impulse_recall = per_class_recall.get('impulse', 0.0)
    gate_passed = impulse_recall >= min_impulse_recall

    metrics = RegimeClassifierMetrics(
        accuracy=accuracy,
        macro_f1=macro_f1,
        weighted_f1=weighted_f1,
        per_class_recall=per_class_recall,
        per_class_precision=per_class_precision,
        per_class_f1=per_class_f1,
        brier_score=brier,
        ece=ece,
        avg_entropy=avg_entropy,
        confusion_matrix=cm.tolist(),
        confusion_matrix_normalized=cm_norm.tolist(),
        pred_distribution=pred_dist,
        impulse_recall_gate_passed=gate_passed,
        min_impulse_recall=min_impulse_recall
    )

    # GATE CHECK
    if not gate_passed:
        raise ValueError(
            f"❌ IMPULSE RECALL GATE FAILED: {impulse_recall:.3f} < {min_impulse_recall} "
            f"- CLASS COLLAPSE DETECTED. Model REJECTED."
        )

    return clf, metrics


def compute_ece_multiclass(y_true, y_proba, classes, n_bins=10):
    """Expected Calibration Error for multiclass."""
    y_pred_idx = np.argmax(y_proba, axis=1)
    confidences = np.max(y_proba, axis=1)

    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.digitize(confidences, bins[1:-1])

    ece = 0.0
    for i in range(n_bins):
        mask = bin_indices == i
        if mask.sum() == 0:
            continue

        y_true_idx = np.array([classes.index(val) if val in classes else 0 for val in y_true[mask]])
        bin_accuracy = float((y_pred_idx[mask] == y_true_idx).mean())
        bin_confidence = float(confidences[mask].mean())
        bin_weight = mask.sum() / len(y_true)

        ece += bin_weight * abs(bin_accuracy - bin_confidence)

    return float(ece)


def print_regime_metrics_report(metrics: RegimeClassifierMetrics, classes: list):
    """Print human-readable metrics report."""
    print("\n" + "="*80)
    print("REGIME CLASSIFIER EVALUATION")
    print("="*80)

    print(f"\n📊 Overall Metrics:")
    print(f"  Accuracy:    {metrics.accuracy:.4f}")
    print(f"  Macro F1:    {metrics.macro_f1:.4f}")
    print(f"  Weighted F1: {metrics.weighted_f1:.4f}")
    print(f"  Brier Score: {metrics.brier_score:.4f}")
    print(f"  ECE:         {metrics.ece:.4f}")
    print(f"  Avg Entropy: {metrics.avg_entropy:.4f}")

    print(f"\n🎯 Per-Class Recall (CRITICAL):")
    for cls, recall in metrics.per_class_recall.items():
        threshold = metrics.min_impulse_recall if cls == 'impulse' else 0.30
        status = "✅" if recall >= threshold else "❌"
        print(f"  {status} {cls:10s}: {recall:.4f}")

    if not metrics.impulse_recall_gate_passed:
        print(f"\n🚨 GATE FAILURE: Impulse recall below {metrics.min_impulse_recall}")

    print(f"\n📈 Per-Class F1:")
    for cls, f1 in metrics.per_class_f1.items():
        print(f"  {cls:10s}: {f1:.4f}")

    print(f"\n📊 Prediction Distribution:")
    for cls, frac in metrics.pred_distribution.items():
        print(f"  {cls:10s}: {frac:.2%}")

    print(f"\n🔍 Confusion Matrix:")
    cm = np.array(metrics.confusion_matrix)
    print("           ", " ".join(f"{cls:8s}" for cls in classes))
    for i, cls in enumerate(classes):
        print(f"{cls:10s}", " ".join(f"{cm[i,j]:8d}" for j in range(len(classes))))

    # Confusion analysis
    print(f"\n📉 Major Confusions (>30%):")
    cm_norm = np.array(metrics.confusion_matrix_normalized)
    for i, true_cls in enumerate(classes):
        for j, pred_cls in enumerate(classes):
            if i != j:
                confusion_rate = cm_norm[i,j]
                if confusion_rate > 0.3:
                    print(f"  ⚠️  {true_cls} → {pred_cls}: {cm[i,j]:,} ({confusion_rate:.1%})")

    print("\n" + "="*80 + "\n")
