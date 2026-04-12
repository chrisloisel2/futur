from __future__ import annotations
import numpy as np

def safe_mean(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.mean(x)) if x.size else 0.0

def safe_std(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.std(x)) if x.size else 0.0

def pearson_corr(a, b) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 5:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    den = (np.sqrt((a*a).sum()) * np.sqrt((b*b).sum()))
    if den <= 1e-12:
        return 0.0
    return float((a*b).sum() / den)

def sign_acc(pred, tgt) -> float:
    p = np.sign(np.asarray(pred))
    t = np.sign(np.asarray(tgt))
    if p.size == 0:
        return 0.0
    return float(np.mean(p == t))

def roi_proxy(edge_pred, edge_true, w=None) -> float:
    p = np.asarray(edge_pred, dtype=np.float64)
    t = np.asarray(edge_true, dtype=np.float64)
    s = np.sign(p)
    if w is None:
        return float(np.mean(s * t)) if p.size else 0.0
    w = np.asarray(w, dtype=np.float64)
    w = w / (w.mean() + 1e-12)
    return float(np.mean(s * t * w)) if p.size else 0.0
