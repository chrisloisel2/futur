#!/usr/bin/env python3
"""
scripts/monitor_sol_e.py — Monitoring paper TRM_E_SOL
=======================================================

Surveille les signaux et trades SOL en paper trading.
Compare au benchmark LGB_E (PF=1.334) et décide :
  CONTINUE_PAPER | REVERT_LGB_E | PAUSE_SIGNAL | INVESTIGATE

Usage :
  python scripts/monitor_sol_e.py              # rapport complet
  python scripts/monitor_sol_e.py --rolling    # résumé rolling 5 trades
  python scripts/monitor_sol_e.py --decision   # verdict simple
  python scripts/monitor_sol_e.py --drift      # contrôle dérive features
  python scripts/monitor_sol_e.py --watch      # boucle continue (60s)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT      = Path(__file__).resolve().parent.parent
FLEET_DIR = ROOT / "reports" / "paper_trading" / "SOLUSDT"
SIG_FILE  = FLEET_DIR / "signals.csv"
TRD_FILE  = FLEET_DIR / "trades.csv"
MON_DIR   = ROOT / "reports" / "monitoring"
MON_DIR.mkdir(parents=True, exist_ok=True)
REPORT    = MON_DIR / "sol_e_daily.txt"
JSON_OUT  = MON_DIR / "sol_e_status.json"

# ── Benchmark LGB_E (référence hostile audit) ─────────────────────────────────
BENCHMARK = {
    "pf":       1.334,
    "worst_yr": 1.222,
    "max_dd":   15.8,
    "wr":       0.461,
}

# ── Fenêtres de décision ──────────────────────────────────────────────────────
ROLLING_N        = 5     # rapport rolling tous les N trades
DECISION_N       = 30    # décision principale sur N trades
EARLY_STOP_N     = 20    # arrêt anticipé si PF très bas
NO_SIGNAL_DAYS   = 30    # tester lower threshold si 0 trade en N jours

# ── Seuils de décision ────────────────────────────────────────────────────────
THR_CONTINUE     = 1.20
THR_INCUBATE_LO  = 1.00
THR_HARD_STOP_N  = 1.00  # < 1.0 sur DECISION_N → REVERT
THR_EARLY_STOP   = 0.80  # < 0.80 sur EARLY_STOP_N → PAUSE
THR_AVG_LOSS_X   = 1.5   # avg_loss > avg_win × 1.5 → INVESTIGATE_EXITS

# ── Plages historiques (backtest 2022-2025 LGB_E) ────────────────────────────
HIST_RANGES = {
    "cvd_24h_z":        (-4.0, 4.0),
    "cvd_4h_z":         (-4.0, 4.0),
    "cvd_momentum":     (-2.0, 2.0),
    "basis_annualized": (-200.0, 200.0),
    "p_long":           (0.50, 1.00),
}
HIST_MEANS = {
    "cvd_24h_z":        -0.003,
    "cvd_4h_z":         -0.001,
    "basis_annualized": 6.455,
}
HIST_STDS = {
    "cvd_24h_z":        1.210,
    "cvd_4h_z":         1.037,
    "basis_annualized": 35.74,
}

# Fréquence signal attendue (LGB_E 425 trades / 4 ans / 52 semaines)
EXPECTED_SIGNAL_RATE_WEEK = 425 / (4 * 52)   # ≈ 2.0 signaux/semaine


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_signals() -> pd.DataFrame:
    if not SIG_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(SIG_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def _load_trades() -> pd.DataFrame:
    if not TRD_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(TRD_FILE).fillna("")
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    return df.sort_values("entry_time").reset_index(drop=True)


def _pf_metrics(df_closed: pd.DataFrame) -> dict:
    """Calcule PF, WR et métriques sur les trades fermés."""
    if df_closed.empty:
        return {"pf": 0.0, "wr": 0.0, "n": 0, "avg_win": 0.0, "avg_loss": 0.0,
                "ratio": float("nan"), "pnl": 0.0, "dd": 0.0}
    rets  = df_closed["future_ret_net"].astype(float) / 100.0
    wins  = rets[rets > 0]
    loss  = rets[rets < 0].abs()
    pf    = float(wins.sum() / max(loss.sum(), 1e-9))
    wr    = float((rets > 0).mean())
    ratio = float(wins.mean() / loss.mean()) if (len(wins) > 0 and len(loss) > 0) else float("nan")

    # Drawdown simulé
    equity = rets.cumsum()
    peak   = equity.cummax()
    dd     = float((peak - equity).max() * 100)

    return {
        "pf":       round(pf, 3),
        "wr":       round(wr, 3),
        "n":        len(rets),
        "avg_win":  round(float(wins.mean() * 100), 3) if len(wins) else 0.0,
        "avg_loss": round(float(loss.mean() * 100), 3) if len(loss) else 0.0,
        "ratio":    round(ratio, 3),
        "pnl":      round(float(rets.sum() * 100), 2),
        "dd":       round(dd, 2),
    }


def _rolling_report(df_trades: pd.DataFrame, window: int = ROLLING_N) -> List[dict]:
    """Rapport rolling par fenêtre de `window` trades."""
    if df_trades.empty or "outcome" not in df_trades.columns:
        return []
    closed = df_trades[df_trades["outcome"].str.upper().isin(["WIN", "LOSS"])]
    if len(closed) < window:
        return []
    reports = []
    for i in range(window, len(closed) + 1, window):
        chunk = closed.iloc[i - window : i]
        m = _pf_metrics(chunk)
        m["window_end"]    = i
        m["last_trade_dt"] = str(chunk["entry_time"].iloc[-1])[:16]
        reports.append(m)
    return reports


def _detect_drift(df_signals: pd.DataFrame) -> dict:
    """
    Détecte la dérive des features E par rapport au backtest (KS-approximation).
    Utilise un test de z-score : |mean_live - mean_hist| / std_hist > 2σ.
    """
    results = {}
    E_FEATS = ["cvd_24h_z", "cvd_4h_z", "basis_annualized"]

    long_sigs = df_signals[df_signals["action"] == "LONG"]
    if len(long_sigs) < 5:
        return {"status": "INSUFFICIENT_DATA", "n": len(long_sigs)}

    for feat in E_FEATS:
        if feat not in long_sigs.columns:
            results[feat] = {"status": "MISSING_COLUMN"}
            continue

        live_vals = long_sigs[feat].dropna()
        if len(live_vals) < 5:
            results[feat] = {"status": "INSUFFICIENT"}
            continue

        hist_mean = HIST_MEANS.get(feat, 0.0)
        hist_std  = HIST_STDS.get(feat, 1.0)
        live_mean = float(live_vals.mean())
        live_std  = float(live_vals.std())

        # Z-score de la moyenne live par rapport à la distribution historique
        z_mean = abs(live_mean - hist_mean) / max(hist_std, 1e-6)
        # Ratio std
        std_ratio = live_std / max(hist_std, 1e-6)

        out_of_range = live_vals[
            (live_vals < HIST_RANGES[feat][0]) | (live_vals > HIST_RANGES[feat][1])
        ]

        drift = z_mean > 2.0 or std_ratio > 2.0 or len(out_of_range) / len(live_vals) > 0.10
        results[feat] = {
            "status":         "DRIFT" if drift else "OK",
            "live_mean":      round(live_mean, 4),
            "hist_mean":      round(hist_mean, 4),
            "z_mean":         round(z_mean, 3),
            "live_std":       round(live_std, 4),
            "hist_std":       round(hist_std, 4),
            "std_ratio":      round(std_ratio, 3),
            "pct_out_range":  round(len(out_of_range) / len(live_vals) * 100, 1),
        }

    # Fréquence signal
    now_utc = datetime.now(timezone.utc)
    sigs_week = len(df_signals[
        (df_signals["action"] == "LONG") &
        (df_signals["timestamp"] >= pd.Timestamp(now_utc) - pd.Timedelta(weeks=1))
    ])
    expected  = EXPECTED_SIGNAL_RATE_WEEK
    freq_ok   = sigs_week >= expected * 0.30   # ≥30% de la fréquence attendue

    results["signal_frequency"] = {
        "last_7d":     sigs_week,
        "expected_7d": round(expected, 1),
        "ratio":       round(sigs_week / max(expected, 1e-3), 2),
        "status":      "OK" if freq_ok else "LOW_SIGNAL",
    }

    any_drift = any(
        v.get("status") == "DRIFT"
        for k, v in results.items()
        if isinstance(v, dict) and k != "signal_frequency"
    )
    results["overall"] = "DRIFT_DETECTED" if any_drift else "OK"
    return results


def _decision(df_trades: pd.DataFrame) -> Tuple[str, str]:
    """
    Retourne (verdict, raison) basé sur les trades fermés.
    """
    if df_trades.empty or "outcome" not in df_trades.columns:
        return "WAIT", "Aucun trade enregistré"
    closed = df_trades[df_trades["outcome"].str.upper().isin(["WIN", "LOSS"])]
    n = len(closed)

    if n == 0:
        now_utc   = datetime.now(timezone.utc)
        first_dt  = df_trades["entry_time"].min() if not df_trades.empty else None
        days_open = (now_utc - first_dt.to_pydatetime()).days if first_dt else 0
        if days_open >= NO_SIGNAL_DAYS:
            return "LOWER_THRESHOLD_TEST", f"0 trades fermés en {days_open}j → tester thr 0.55-0.60 en shadow"
        return "WAIT", f"n={n} — insuffisant (seuil décision: {DECISION_N})"

    m = _pf_metrics(closed)
    pf = m["pf"]

    # Arrêt anticipé
    if n >= EARLY_STOP_N and pf < THR_EARLY_STOP:
        return "PAUSE_SIGNAL", f"PF={pf:.3f} < {THR_EARLY_STOP} sur {n} trades (early stop)"

    # Hard stop
    if n >= DECISION_N and pf < THR_HARD_STOP_N:
        return "REVERT_LGB_E", f"PF={pf:.3f} < {THR_HARD_STOP_N} sur {n} trades (hard stop)"

    # Investigation exits
    if n >= 10 and m["avg_loss"] > 0 and m["avg_win"] > 0:
        if m["avg_loss"] > m["avg_win"] * THR_AVG_LOSS_X:
            return "INVESTIGATE_EXITS", (
                f"avg_loss={m['avg_loss']:.3f}% > avg_win={m['avg_win']:.3f}% × {THR_AVG_LOSS_X}"
            )

    # Décision principale
    if n >= DECISION_N:
        if pf >= THR_CONTINUE:
            return "CONTINUE_PAPER", f"PF={pf:.3f} ≥ {THR_CONTINUE} sur {n} trades"
        elif pf >= THR_INCUBATE_LO:
            return "INCUBATE", f"PF={pf:.3f} entre {THR_INCUBATE_LO} et {THR_CONTINUE} sur {n} trades"
        else:
            return "REVERT_LGB_E", f"PF={pf:.3f} < {THR_INCUBATE_LO} sur {n} trades"

    return "WAIT", f"n={n}/{DECISION_N} trades — attente"


def _pf_by_bucket(df_trades: pd.DataFrame, df_signals: pd.DataFrame,
                  col: str, n_buckets: int = 3) -> dict:
    """PF par bucket d'une feature (nécessite jointure signaux/trades)."""
    closed = df_trades[df_trades["outcome"].str.upper().isin(["WIN", "LOSS"])].copy()
    if col not in df_signals.columns or len(closed) < 10:
        return {}
    # Jointure approximative par timestamp
    merged = closed.copy()
    merged["_dt"] = merged["entry_time"]
    sig_sub = df_signals[df_signals["action"] == "LONG"][["timestamp", col]].copy()
    sig_sub["_dt"] = sig_sub["timestamp"]
    merged = pd.merge_asof(
        merged.sort_values("_dt"),
        sig_sub.sort_values("_dt"),
        on="_dt", direction="nearest", tolerance=pd.Timedelta("2h")
    )
    if col not in merged.columns or merged[col].isna().all():
        return {}
    try:
        merged["bucket"] = pd.qcut(merged[col], n_buckets, labels=False, duplicates="drop")
    except Exception:
        return {}
    result = {}
    for b, grp in merged.groupby("bucket"):
        m = _pf_metrics(grp)
        result[f"bucket_{b}"] = {"pf": m["pf"], "n": m["n"]}
    return result


# ─── Rapport principal ────────────────────────────────────────────────────────

def build_report(verbose: bool = True) -> dict:
    sigs   = _load_signals()
    trades = _load_trades()
    now    = datetime.now(timezone.utc)

    # Filtrer SOL seulement
    if "symbol" in sigs.columns:
        sigs = sigs[sigs["symbol"] == "SOLUSDT"]
    if "symbol" in trades.columns:
        trades = trades[trades["symbol"] == "SOLUSDT"]

    if trades.empty or "outcome" not in trades.columns:
        closed = pd.DataFrame()
        open_p = pd.DataFrame()
    else:
        closed = trades[trades["outcome"].str.upper().isin(["WIN", "LOSS"])]
        open_p = trades[trades["outcome"].str.upper() == "OPEN"]
    n_closed = len(closed)
    n_open   = len(open_p)

    metrics  = _pf_metrics(closed)
    verdict, reason = _decision(trades)
    drift    = _detect_drift(sigs)
    rolling  = _rolling_report(trades)

    # Feature E snapshot (dernières barres signalées)
    e_snapshot: dict = {}
    long_sigs = sigs[sigs["action"] == "LONG"]
    if len(long_sigs):
        last = long_sigs.iloc[-1]
        for f in ["cvd_4h_z","cvd_24h_z","cvd_momentum","basis_annualized"]:
            if f in long_sigs.columns:
                e_snapshot[f] = round(float(last[f]), 4) if pd.notna(last[f]) else None

    report = {
        "timestamp":        now.isoformat(),
        "asset":            "SOLUSDT",
        "model":            "TRM_E_SOL",
        "signal":           "E_cvd_basis_real",
        "benchmark":        BENCHMARK,
        "n_closed":         n_closed,
        "n_open":           n_open,
        "n_signals_long":   len(long_sigs),
        "metrics":          metrics,
        "verdict":          verdict,
        "reason":           reason,
        "drift":            drift,
        "rolling_5":        rolling[-1] if rolling else {},
        "e_snapshot_last":  e_snapshot,
    }

    if verbose:
        _print_report(report, rolling, sigs, trades)

    # Sauvegarder JSON
    JSON_OUT.write_text(json.dumps(report, indent=2, default=str))
    return report


def _print_report(report: dict, rolling: list, sigs: pd.DataFrame, trades: pd.DataFrame) -> None:
    now = report["timestamp"][:16]
    m   = report["metrics"]
    v   = report["verdict"]
    r   = report["reason"]

    lines = [
        "=" * 72,
        f"  MONITORING TRM_E_SOL — SOLUSDT                         {now} UTC",
        "=" * 72,
        "",
        f"  VERDICT   : {v}",
        f"  Raison    : {r}",
        "",
        "  ── MÉTRIQUES GLOBALES ──────────────────────────────────────────────",
        f"  n_trades  : {m['n']} fermés  {report['n_open']} ouverts  {report['n_signals_long']} signaux LONG",
        f"  PF        : {m['pf']:.3f}  (benchmark LGB_E = {BENCHMARK['pf']:.3f})",
        f"  WR        : {m['wr']:.1%}  (benchmark {BENCHMARK['wr']:.1%})",
        f"  avg_win   : +{m['avg_win']:.3f}%    avg_loss : -{m['avg_loss']:.3f}%",
        f"  win/loss  : {m['ratio']:.3f}×   breakeven WR : {m['avg_loss']/(m['avg_win']+m['avg_loss'])*100:.1f}%" if m['avg_win']+m['avg_loss'] > 0 else "  win/loss  : —",
        f"  net PnL   : {m['pnl']:>+.2f}%  max DD : {m['dd']:.1f}%  (benchmark {BENCHMARK['max_dd']:.1f}%)",
        "",
        "  ── ROLLING 5 DERNIERS TRADES ───────────────────────────────────────",
    ]

    if rolling:
        last = rolling[-1]
        lines += [
            f"  PF      : {last['pf']:.3f}   WR  : {last['wr']:.1%}   n : {last['n']}",
            f"  avg_win : +{last['avg_win']:.3f}%   avg_loss : -{last['avg_loss']:.3f}%",
            f"  net PnL : {last['pnl']:>+.2f}%",
        ]
    else:
        lines.append(f"  Insuffisant ({m['n']} < {ROLLING_N} requis)")

    lines += ["", "  ── DÉRIVE FEATURES E ───────────────────────────────────────────────"]
    drift = report["drift"]
    if drift.get("overall"):
        lines.append(f"  Statut global : {drift['overall']}")
    for feat in ["cvd_24h_z","cvd_4h_z","basis_annualized"]:
        fd = drift.get(feat, {})
        if fd:
            st = fd.get("status","?")
            lm = fd.get("live_mean","?")
            hm = fd.get("hist_mean","?")
            zm = fd.get("z_mean","?")
            lines.append(f"  {feat:<22} {st:<12} live_mean={lm:>8}  hist={hm:>8}  z={zm:>5}")

    freq = drift.get("signal_frequency", {})
    if freq:
        lines.append(f"  Signal 7j : {freq.get('last_7d',0)}/{freq.get('expected_7d',0):.1f} attendus → {freq.get('status','?')}")

    lines += [
        "",
        "  ── SNAPSHOT FEATURES E (dernier signal LONG) ───────────────────────",
    ]
    snap = report.get("e_snapshot_last", {})
    if snap:
        for f, v_ in snap.items():
            lines.append(f"  {f:<22} {v_}")
    else:
        lines.append("  (aucun signal LONG encore)")

    lines += [
        "",
        "  ── RÈGLES ACTIVES ──────────────────────────────────────────────────",
        f"  CONTINUE_PAPER     si PF ≥ {THR_CONTINUE} sur {DECISION_N} trades",
        f"  INCUBATE           si PF [{THR_INCUBATE_LO}-{THR_CONTINUE}[ sur {DECISION_N} trades",
        f"  REVERT_LGB_E       si PF < {THR_HARD_STOP_N} sur {DECISION_N} trades",
        f"  PAUSE_SIGNAL       si PF < {THR_EARLY_STOP} sur {EARLY_STOP_N} trades",
        f"  INVESTIGATE_EXITS  si avg_loss > avg_win × {THR_AVG_LOSS_X}",
        "",
        "=" * 72,
    ]

    output = "\n".join(lines)
    print(output)
    REPORT.write_text(output)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rolling",   action="store_true", help="Résumé rolling uniquement")
    parser.add_argument("--decision",  action="store_true", help="Verdict seul")
    parser.add_argument("--drift",     action="store_true", help="Dérive features uniquement")
    parser.add_argument("--watch",     action="store_true", help="Boucle continue 60s")
    args = parser.parse_args()

    if args.watch:
        print("Mode watch — Ctrl+C pour arrêter")
        while True:
            report = build_report(verbose=True)
            v = report["verdict"]
            if v in ("REVERT_LGB_E", "PAUSE_SIGNAL"):
                print(f"\n  ⚠ ACTION REQUISE : {v}")
                print(f"  Raison : {report['reason']}")
            time.sleep(60)
        return

    if args.decision:
        trades = _load_trades()
        if "symbol" in trades.columns:
            trades = trades[trades["symbol"] == "SOLUSDT"]
        v, r = _decision(trades)
        print(f"  VERDICT : {v}\n  Raison  : {r}")
        return

    if args.drift:
        sigs = _load_signals()
        if "symbol" in sigs.columns:
            sigs = sigs[sigs["symbol"] == "SOLUSDT"]
        d = _detect_drift(sigs)
        print(json.dumps(d, indent=2))
        return

    if args.rolling:
        trades = _load_trades()
        if "symbol" in trades.columns:
            trades = trades[trades["symbol"] == "SOLUSDT"]
        r = _rolling_report(trades)
        for row in r:
            print(json.dumps(row, indent=2))
        return

    build_report(verbose=True)


if __name__ == "__main__":
    main()
