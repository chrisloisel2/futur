from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from sklearn.linear_model import SGDClassifier, LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    classification_report, f1_score, confusion_matrix, log_loss, brier_score_loss,
    average_precision_score
)
from sklearn.preprocessing import StandardScaler

DEFAULT_CLASSES = ["calm", "reversal"]  # BINARY REGIME


def create_focal_sample_weights(y: np.ndarray, alpha: float = 0.25, gamma: float = 2.0) -> np.ndarray:
    """
    Create focal-like sample weights to focus on hard examples.

    Args:
        y: Target labels
        alpha: Weight for class balance (0.25 = upweight minority 4x)
        gamma: Focusing parameter (2.0 standard)

    Returns:
        Sample weights
    """
    # Simple implementation: upweight minority class
    unique, counts = np.unique(y, return_counts=True)
    weights = np.ones(len(y))

    for cls, count in zip(unique, counts):
        # Inverse frequency with smoothing
        weight = len(y) / (len(unique) * count)
        weights[y == cls] = weight ** alpha

    return weights


def train_regime_classifier_variant(
    X_train: np.ndarray,
    y_train: np.ndarray,
    variant: str = "sgd_no_weight",
    random_state: int = 42,
) -> Any:
    """
    Train one of 3 model variants.

    Variants:
        - sgd_no_weight: SGDClassifier without class_weight
        - sgd_focal: SGDClassifier with focal-like sample weights
        - logreg: LogisticRegression with L2 (baseline)

    Returns:
        Trained base model (before calibration)
    """
    if variant == "sgd_no_weight":
        model = SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=1e-4,
            max_iter=2000,
            tol=1e-3,
            class_weight=None,  # NO class_weight
            random_state=random_state,
        )
        model.fit(X_train, y_train)

    elif variant == "sgd_focal":
        sample_weights = create_focal_sample_weights(y_train, alpha=0.5)

        # Log sample weight stats for verification
        print(f"    [sgd_focal] Sample weight stats:")
        print(f"      min={sample_weights.min():.4f}, max={sample_weights.max():.4f}, "
              f"mean={sample_weights.mean():.4f}, unique={len(np.unique(sample_weights))}")

        # Check weights per class
        for cls in np.unique(y_train):
            cls_weights = sample_weights[y_train == cls]
            print(f"      class {cls}: mean_weight={cls_weights.mean():.4f}, count={len(cls_weights)}")

        model = SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=1e-4,
            max_iter=2000,
            tol=1e-3,
            class_weight=None,
            random_state=random_state,
        )
        model.fit(X_train, y_train, sample_weight=sample_weights)

    elif variant == "logreg":
        model = LogisticRegression(
            penalty="l2",
            C=1.0,  # Inverse of alpha
            solver="saga",
            max_iter=2000,
            random_state=random_state,
        )
        model.fit(X_train, y_train)

    else:
        raise ValueError(f"Unknown variant: {variant}")

    return model


def calibrate_classifier(
    base_model: Any,
    X_train: np.ndarray,
    y_train: np.ndarray,
    method: str = "isotonic",
    cv: int = 3,
) -> CalibratedClassifierCV:
    """
    Calibrate classifier probabilities.

    Args:
        base_model: Trained base model
        X_train: Training features
        y_train: Training labels
        method: 'isotonic' or 'sigmoid'
        cv: Number of CV folds

    Returns:
        Calibrated classifier
    """
    # Note: CalibratedClassifierCV refits the base model with CV
    # We pass the base model as a template
    clf = CalibratedClassifierCV(
        base_model,
        method=method,
        cv=cv,
        n_jobs=-1
    )
    clf.fit(X_train, y_train)
    return clf


def train_calibrated_regime_classifier(
    X_train: np.ndarray,
    y_train: np.ndarray,
    class_names: List[str] = None,
    variant: str = "sgd_no_weight",
    calibration_method: str = "isotonic",
) -> CalibratedClassifierCV:
    """
    Train calibrated regime classifier.

    Args:
        X_train: Training features (already scaled)
        y_train: Training labels
        class_names: Class names (for logging)
        variant: Model variant
        calibration_method: Calibration method

    Returns:
        Calibrated classifier
    """
    if class_names is None:
        class_names = DEFAULT_CLASSES

    # Train base model
    base_model = train_regime_classifier_variant(X_train, y_train, variant=variant)

    # Calibrate
    clf = calibrate_classifier(
        base_model, X_train, y_train,
        method=calibration_method,
        cv=3
    )

    return clf


def find_optimal_threshold(
    y_true: np.ndarray,
    y_proba_pos: np.ndarray,
    min_recall_per_class: float = 0.50,
    metric: str = "balanced_accuracy",
) -> Tuple[float, Dict[str, float]]:
    """
    Find optimal classification threshold via grid search.

    Args:
        y_true: True labels (0 or 1)
        y_proba_pos: Probability of positive class (reversal)
        min_recall_per_class: Minimum recall required for both classes
        metric: Optimization metric ('balanced_accuracy' or 'macro_f1')

    Returns:
        (best_threshold, best_metrics)
    """
    thresholds = np.arange(0.05, 0.96, 0.05)
    best_threshold = 0.5
    best_score = -np.inf
    best_metrics = {}

    for thresh in thresholds:
        y_pred = (y_proba_pos >= thresh).astype(int)

        # Compute recalls
        calm_mask = (y_true == 0)
        reversal_mask = (y_true == 1)

        calm_recall = (y_pred[calm_mask] == 0).mean() if calm_mask.sum() > 0 else 0
        reversal_recall = (y_pred[reversal_mask] == 1).mean() if reversal_mask.sum() > 0 else 0

        # Check minimum recall constraint
        if calm_recall < min_recall_per_class or reversal_recall < min_recall_per_class:
            continue

        # Compute optimization metric
        if metric == "balanced_accuracy":
            score = (calm_recall + reversal_recall) / 2.0
        elif metric == "macro_f1":
            # Compute F1 per class
            calm_pred_mask = (y_pred == 0)
            reversal_pred_mask = (y_pred == 1)

            calm_precision = (y_true[calm_pred_mask] == 0).mean() if calm_pred_mask.sum() > 0 else 0
            reversal_precision = (y_true[reversal_pred_mask] == 1).mean() if reversal_pred_mask.sum() > 0 else 0

            calm_f1 = 2 * calm_precision * calm_recall / (calm_precision + calm_recall + 1e-12)
            reversal_f1 = 2 * reversal_precision * reversal_recall / (reversal_precision + reversal_recall + 1e-12)

            score = (calm_f1 + reversal_f1) / 2.0
        else:
            raise ValueError(f"Unknown metric: {metric}")

        if score > best_score:
            best_score = score
            best_threshold = thresh
            best_metrics = {
                'threshold': thresh,
                'calm_recall': calm_recall,
                'reversal_recall': reversal_recall,
                'balanced_accuracy': (calm_recall + reversal_recall) / 2.0,
                'score': score,
            }

    if not best_metrics:
        # No threshold satisfies constraints, return default
        return 0.5, {
            'threshold': 0.5,
            'calm_recall': 0.0,
            'reversal_recall': 0.0,
            'balanced_accuracy': 0.0,
            'score': 0.0,
            'warning': 'No threshold satisfies recall constraints',
        }

    return best_threshold, best_metrics


def compute_ece_multiclass(y_true: np.ndarray, y_proba: np.ndarray, n_bins: int = 10) -> float:
    """Compute Expected Calibration Error for multiclass."""
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


def compute_reliability_curve(
    y_true: np.ndarray,
    y_proba_pos: np.ndarray,
    n_bins: int = 10,
) -> Dict[str, np.ndarray]:
    """
    Compute reliability curve for binary classification.

    Returns dict with:
        - bin_centers: Mean predicted probability per bin
        - bin_accuracy: Actual accuracy per bin
        - bin_counts: Number of samples per bin
    """
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.digitize(y_proba_pos, bins[1:-1])

    bin_centers = []
    bin_accuracy = []
    bin_counts = []

    for b in range(n_bins):
        m = idx == b
        if not np.any(m):
            continue

        bin_centers.append(y_proba_pos[m].mean())
        bin_accuracy.append((y_true[m] == 1).mean())  # fraction of positives
        bin_counts.append(m.sum())

    return {
        'bin_centers': np.array(bin_centers),
        'bin_accuracy': np.array(bin_accuracy),
        'bin_counts': np.array(bin_counts),
    }


def evaluate_regime_classifier(
    clf,
    X_val: np.ndarray,
    y_val: np.ndarray,
    class_names: List[str] = None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluate regime classifier on validation set.

    Args:
        clf: Trained classifier
        X_val: Validation features (already scaled)
        y_val: Validation labels
        class_names: Class names
        threshold: Optional custom threshold (default 0.5)

    Returns:
        Metrics dictionary
    """
    if class_names is None:
        class_names = DEFAULT_CLASSES

    n_classes = len(class_names)
    labels = list(range(n_classes))

    y_proba = clf.predict_proba(X_val)

    # Apply threshold if provided
    if threshold is not None:
        y_pred = (y_proba[:, 1] >= threshold).astype(int)
    else:
        y_pred = clf.predict(X_val)

    accuracy = float((y_pred == y_val).mean())
    macro_f1 = float(f1_score(y_val, y_pred, average="macro", zero_division=0))

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

    # FIXED: Binary Brier score using probability of positive class
    brier = float(brier_score_loss(y_val, y_proba[:, 1]))

    # ECE
    ece = compute_ece_multiclass(y_val, y_proba, n_bins=10)

    # Reliability curve
    reliability = compute_reliability_curve(y_val, y_proba[:, 1], n_bins=10)

    # Other metrics
    entropy = float(-np.sum(y_proba * np.log(y_proba + 1e-12), axis=1).mean())
    ll = float(log_loss(y_val, y_proba, labels=labels))

    cm = confusion_matrix(y_val, y_pred, labels=labels)

    pred_dist = {}
    for i, c in enumerate(class_names):
        pred_dist[c] = float((y_pred == i).mean())

    balanced_accuracy = float(np.mean([recall_per_class[c] for c in class_names]))

    # EXCELLENCE METRICS: PR-AUC for reversal (rare class)
    pr_auc_reversal = float(average_precision_score(y_val, y_proba[:, 1]))

    # EXCELLENCE METRICS: Base-rate consistency
    true_rate_reversal = float((y_val == 1).mean())
    pred_rate_reversal = float((y_pred == 1).mean())
    rate_ratio = pred_rate_reversal / max(true_rate_reversal, 1e-9)

    return {
        "accuracy": accuracy,
        "balanced_accuracy": balanced_accuracy,
        "macro_f1": macro_f1,
        "recall_per_class": recall_per_class,
        "precision_per_class": precision_per_class,
        "f1_per_class": f1_per_class,
        "brier": brier,  # FIXED: Now uses correct binary Brier
        "log_loss": ll,
        "ece": float(ece),
        "entropy": entropy,
        "confusion_matrix": cm.tolist(),
        "pred_distribution": pred_dist,
        "classification_report": report,
        "reliability_curve": {
            'bin_centers': reliability['bin_centers'].tolist(),
            'bin_accuracy': reliability['bin_accuracy'].tolist(),
            'bin_counts': reliability['bin_counts'].tolist(),
        },
        "threshold": threshold if threshold is not None else 0.5,
        # EXCELLENCE METRICS
        "pr_auc_reversal": pr_auc_reversal,
        "true_rate_reversal": true_rate_reversal,
        "pred_rate_reversal": pred_rate_reversal,
        "rate_ratio": rate_ratio,
    }


def sanity_check_metrics(metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Perform sanity checks on metrics.

    Returns:
        (is_sane, warnings)
    """
    warnings = []

    # Check 1: Calm recall too low
    calm_recall = metrics['recall_per_class'].get('calm', 0)
    if calm_recall < 0.40:
        warnings.append(f"Calm recall very low ({calm_recall:.2%}) - model may be unstable")

    # Check 2: Extreme threshold (WARNING only, not fail)
    threshold = metrics.get('threshold', 0.5)
    pred_rate = metrics.get('pred_rate_reversal', 0)
    true_rate = metrics.get('true_rate_reversal', 0.05)
    precision_reversal = metrics.get('precision_per_class', {}).get('reversal', 0)

    if threshold < 0.15 or threshold > 0.85:
        # Only warn if precision is also low or rate_ratio is extreme
        rate_ratio = pred_rate / max(true_rate, 1e-9)
        if precision_reversal < 0.25 or rate_ratio < 0.5 or rate_ratio > 2.0:
            warnings.append(f"Extreme threshold ({threshold:.2f}) with low quality (precision={precision_reversal:.2f}, ratio={rate_ratio:.2f})")

    # Check 3: ECE vs Brier inconsistency
    ece = metrics.get('ece', 0)
    brier = metrics.get('brier', 0)
    if ece < 0.05 and brier > 0.25:
        warnings.append(f"ECE very low ({ece:.3f}) but Brier high ({brier:.3f}) - investigate")

    # Check 4: Class collapse (FIXED - use pred_rate vs true_rate)
    # For binary with rare reversal class (true_rate ~4%), pred_rate should be similar
    if pred_rate < 0.005:
        warnings.append(f"Degenerate negative - never predicts reversal (pred_rate={pred_rate:.3%})")
    elif pred_rate > 0.40:
        warnings.append(f"Degenerate positive - over-predicts reversal (pred_rate={pred_rate:.1%} vs true_rate={true_rate:.1%})")

    is_sane = len(warnings) == 0
    return is_sane, warnings


if __name__ == "__main__":
    # Test with synthetic data
    print("Testing Binary Regime Classifier V2")
    print("=" * 60)

    np.random.seed(42)
    n_samples = 1000
    n_features = 20

    X = np.random.randn(n_samples, n_features)
    y = (X[:, 0] + X[:, 1] > 0).astype(int)  # Simple decision boundary

    # Split
    split = int(0.8 * n_samples)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # Train all variants
    variants = ["sgd_no_weight", "sgd_focal", "logreg"]

    for variant in variants:
        print(f"\n--- Variant: {variant} ---")
        clf = train_calibrated_regime_classifier(X_train, y_train, variant=variant)

        # Find optimal threshold
        y_proba = clf.predict_proba(X_val)
        best_threshold, threshold_metrics = find_optimal_threshold(
            y_val, y_proba[:, 1], min_recall_per_class=0.50
        )

        print(f"Best threshold: {best_threshold:.2f}")
        print(f"  Calm recall:     {threshold_metrics['calm_recall']:.3f}")
        print(f"  Reversal recall: {threshold_metrics['reversal_recall']:.3f}")
        print(f"  Balanced acc:    {threshold_metrics['balanced_accuracy']:.3f}")

        # Evaluate
        metrics = evaluate_regime_classifier(clf, X_val, y_val, threshold=best_threshold)

        print(f"\nMetrics:")
        print(f"  Accuracy:        {metrics['accuracy']:.3f}")
        print(f"  Balanced Acc:    {metrics['balanced_accuracy']:.3f}")
        print(f"  Macro F1:        {metrics['macro_f1']:.3f}")
        print(f"  Brier:           {metrics['brier']:.4f}")
        print(f"  ECE:             {metrics['ece']:.4f}")

        # Sanity checks
        is_sane, warnings = sanity_check_metrics(metrics)
        if not is_sane:
            print(f"\n⚠️  Sanity check warnings:")
            for w in warnings:
                print(f"  - {w}")
