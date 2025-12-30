from __future__ import annotations
from typing import Dict, Any, List
import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import classification_report, f1_score, confusion_matrix, log_loss

DEFAULT_CLASSES = ["calm", "reversal"]  # BINARY REGIME: impulse removed (now an event, not a regime)


def train_calibrated_regime_classifier(
    X_train: np.ndarray,
    y_train: np.ndarray,
    class_names: List[str] = None,
) -> CalibratedClassifierCV:
    if class_names is None:
        class_names = DEFAULT_CLASSES

    base = SGDClassifier(
        loss="log_loss",
        penalty="l2",
        alpha=1e-4,
        max_iter=2000,
        tol=1e-3,
        class_weight="balanced",
        random_state=42,
    )

    # isotonic is heavy; keep cv small
    clf = CalibratedClassifierCV(base, method="isotonic", cv=3, n_jobs=-1)
    clf.fit(X_train, y_train)
    return clf


def compute_ece_multiclass(y_true: np.ndarray, y_proba: np.ndarray, n_bins: int = 10) -> float:
    y_pred = np.argmax(y_proba, axis=1)
    conf = np.max(y_proba, axis=1)

    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.digitize(conf, bins[1:-1])

    ece = 0.0
    n = len(y_true)
    for b in range(n_bins):
        m = idx == b
        if not np.any(m):
            continue
        acc = (y_pred[m] == y_true[m]).mean()
        c = conf[m].mean()
        ece += (m.sum() / n) * abs(acc - c)
    return float(ece)


def evaluate_regime_classifier(
    clf,
    X_val: np.ndarray,
    y_val: np.ndarray,
    class_names: List[str] = None,
) -> Dict[str, Any]:
    if class_names is None:
        class_names = DEFAULT_CLASSES

    n_classes = len(class_names)
    labels = list(range(n_classes))

    y_pred = clf.predict(X_val)
    y_proba = clf.predict_proba(X_val)

    accuracy = float((y_pred == y_val).mean())
    macro_f1 = float(f1_score(y_val, y_pred, average="macro"))

    report = classification_report(
        y_val, y_pred,
        labels=labels,
        target_names=class_names,
        output_dict=True,
        zero_division=0
    )

    recall_per_class = {c: float(report[c]["recall"]) for c in class_names}
    precision_per_class = {c: float(report[c]["precision"]) for c in class_names}
    f1_per_class = {c: float(report[c]["f1-score"]) for c in class_names}

    # multiclass brier (for logging, not gating alone)
    y_onehot = np.zeros((len(y_val), n_classes), dtype=np.float32)
    y_onehot[np.arange(len(y_val)), y_val] = 1.0
    multiclass_brier = float(np.mean((y_proba - y_onehot) ** 2))

    ece = compute_ece_multiclass(y_val, y_proba, n_bins=10)
    entropy = float(-np.sum(y_proba * np.log(y_proba + 1e-12), axis=1).mean())
    ll = float(log_loss(y_val, y_proba, labels=labels))

    cm = confusion_matrix(y_val, y_pred, labels=labels)

    pred_dist = {}
    for i, c in enumerate(class_names):
        pred_dist[c] = float((y_pred == i).mean())

    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "recall_per_class": recall_per_class,
        "precision_per_class": precision_per_class,
        "f1_per_class": f1_per_class,
        "multiclass_brier": multiclass_brier,
        "log_loss": ll,
        "ece": float(ece),
        "entropy": entropy,
        "confusion_matrix": cm.tolist(),
        "pred_distribution": pred_dist,
        "classification_report": report,
    }


def production_gates(metrics: Dict[str, Any]) -> tuple[bool, str]:
    """
    Production gates for BINARY regime classifier.
    Removed impulse-specific gate (impulse is now an event, not a regime).
    """
    # Gate 1: Minimum accuracy for binary classification
    if metrics["accuracy"] < 0.60:
        return False, f"ACCURACY {metrics['accuracy']:.3f} < 0.60 (binary threshold)"

    # Gate 2: Calibration quality (ECE)
    if metrics["ece"] > 0.10:
        return False, f"ECE {metrics['ece']:.3f} > 0.10"

    # Gate 3: Minimum recall per class (avoid collapse to one class)
    calm_recall = metrics["recall_per_class"].get("calm", 0.0)
    reversal_recall = metrics["recall_per_class"].get("reversal", 0.0)

    if calm_recall < 0.50:
        return False, f"CALM RECALL {calm_recall:.3f} < 0.50"

    if reversal_recall < 0.50:
        return False, f"REVERSAL RECALL {reversal_recall:.3f} < 0.50"

    # Gate 4: Collapse guard (no class should dominate >75% for binary)
    if max(metrics["pred_distribution"].values()) > 0.75:
        return False, "PREDICTION COLLAPSE (one class dominates >75%)"

    return True, ""
