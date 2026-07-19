#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/train_exit_model.py — Entraînement du modèle de sortie de position
===========================================================================

Workflow :
  1. Pour chaque symbol dans TOP_10 :
     a. Lire le parquet enrichi
     b. Calculer y_long via compute_label_columns + build_labels
     c. Générer les samples de sortie (train et val)
  2. Pooler tous les symbols
  3. Entraîner ExitFleetV1 sur la pool
  4. Afficher les stats
  5. Sauvegarder le modèle

Usage :
  python scripts/train_exit_model.py
  python scripts/train_exit_model.py --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_0.labels import compute_label_columns, build_labels, compute_long_regime_col
from ai.level_0.exit_labels import generate_exit_samples, EXIT_ALL_FEATURES
from ai.level_2.exit_model_v1 import ExitFleetV1

# ─── Configuration ────────────────────────────────────────────────────────────

ENRICHED_DIR = ROOT / "data" / "enriched"
MODELS_DIR   = ROOT / "reports" / "paper_trading" / ".models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PATH  = MODELS_DIR / "exit_model_v1.pkl"

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

from datetime import datetime as _dt
TRAIN_END = _dt.now().year - 1    # 2025 — cohérent avec paper_multi_signal
VAL_YEAR  = _dt.now().year        # 2026


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_enriched(symbol: str) -> pd.DataFrame | None:
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        print(f"  [{symbol}] parquet manquant : {path}")
        return None
    try:
        df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"  [{symbol}] erreur lecture : {e}")
        return None


def _print_phase_stats(df: pd.DataFrame, tag: str = "") -> None:
    """Affiche les taux d'exit par phase et état PnL."""
    if "bars_held" not in df.columns or "y_exit" not in df.columns:
        return

    bh = df["bars_held"].values
    ye = df["y_exit"].values

    print(f"\n  Stats {tag} (n={len(df):,}, y_exit={ye.mean():.2%}) :")

    for phase, lo, hi in [("early", 0.5, 2.5), ("mid", 2.5, 5.5), ("late", 5.5, 9)]:
        mask  = (bh >= lo) & (bh < hi)
        n_ph  = mask.sum()
        rate  = ye[mask].mean() if n_ph > 0 else 0.0
        print(f"    {phase:<8}: n={n_ph:>6,}  exit_rate={rate:.2%}")

    if "unrealized_ret" in df.columns:
        from ai.level_0.constants import COST_PCT
        ret = df["unrealized_ret"].values
        margin = COST_PCT + 0.005
        for pnl_state, mask_fn in [
            ("winning",   (ret > margin)),
            ("breakeven", (ret >= -margin) & (ret <= margin)),
            ("losing",    (ret < -margin)),
        ]:
            n_s  = mask_fn.sum()
            rate = ye[mask_fn].mean() if n_s > 0 else 0.0
            print(f"    {pnl_state:<10}: n={n_s:>6,}  exit_rate={rate:.2%}")


def _simulate_vs_baseline(fleet: ExitFleetV1, df_val: pd.DataFrame) -> None:
    """Simulation : exit model vs baseline (hold MAX_HOLD)."""
    if df_val is None or len(df_val) == 0:
        return
    if "t0" not in df_val.columns or "unrealized_ret" not in df_val.columns:
        return

    from ai.level_0.constants import COST_PCT

    p_all   = fleet.predict(df_val)
    thr     = fleet.threshold_

    # Regrouper par position
    has_sym = "symbol" in df_val.columns
    if has_sym:
        groups = df_val.groupby(["symbol", "t0"])
    else:
        groups = df_val.groupby("t0")

    baseline_pnls = []
    model_pnls    = []
    n_early_exits = 0
    n_positions   = 0

    df_val_reset = df_val.reset_index(drop=True)
    p_all_reset  = fleet.predict(df_val_reset)

    if has_sym:
        groups2 = df_val_reset.groupby(["symbol", "t0"])
    else:
        groups2 = df_val_reset.groupby("t0")

    for grp_key, grp_df in groups2:
        if "k" in grp_df.columns:
            grp_df = grp_df.sort_values("k")

        rets  = grp_df["unrealized_ret"].values
        p_row = p_all_reset[grp_df.index.values]

        # Baseline : dernière barre
        baseline_pnl = rets[-1] - COST_PCT
        baseline_pnls.append(baseline_pnl)

        # Exit model : première barre où p >= thr
        exit_k = None
        for j, p in enumerate(p_row):
            if p >= thr:
                exit_k = j
                break
        if exit_k is None:
            exit_k = len(rets) - 1

        model_pnl = rets[exit_k] - COST_PCT
        model_pnls.append(model_pnl)

        if exit_k < len(rets) - 1:
            n_early_exits += 1
        n_positions += 1

    bl_sum = sum(baseline_pnls)
    mo_sum = sum(model_pnls)
    bl_pos = sum(1 for p in baseline_pnls if p > 0)
    mo_pos = sum(1 for p in model_pnls if p > 0)

    print(f"\n  Simulation val (threshold={thr:.2f}) :")
    print(f"    Positions       : {n_positions:,}")
    print(f"    Sorties anticipées (exit model) : {n_early_exits:,}  ({n_early_exits/max(n_positions,1):.1%})")
    print(f"    Baseline net PnL : {bl_sum:+.4f}  WR={bl_pos/max(n_positions,1):.1%}")
    print(f"    Exit model PnL  : {mo_sum:+.4f}  WR={mo_pos/max(n_positions,1):.1%}")
    print(f"    Amélioration    : {mo_sum - bl_sum:+.4f}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Entraînement du exit model")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Sous-ensemble de symbols (défaut : TOP_10)")
    args = parser.parse_args()

    t_start = time.time()
    now     = datetime.now(timezone.utc)

    print("=" * 68)
    print("  TRAIN EXIT MODEL v1 — modèle de sortie de position")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Train <= {TRAIN_END}  Val = {VAL_YEAR}")
    print(f"  Output : {OUTPUT_PATH.relative_to(ROOT)}")
    print("=" * 68)

    targets = args.symbols if args.symbols else TOP_10
    available = [s for s in targets if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    missing   = [s for s in targets if s not in available]

    if missing:
        print(f"\n  Assets manquants : {missing}")
    print(f"\n  Assets : {', '.join(available)}  ({len(available)}/{len(targets)})")

    if not available:
        print("  Aucun asset disponible. Abandon.")
        sys.exit(1)

    # ── Phase 1 : Génération des samples ─────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  PHASE 1 — Génération des samples d'exit")
    print(f"{'─'*68}")

    all_train: list = []
    all_val:   list = []

    for sym in available:
        t_sym = time.time()
        print(f"\n  [{sym}]")

        df = _load_enriched(sym)
        if df is None:
            continue

        # Labels
        try:
            df = compute_label_columns(df)
            df = compute_long_regime_col(df)
        except Exception as e:
            print(f"    WARN compute_label_columns: {e}")
            continue

        years    = df["datetime"].dt.year.values
        tr_mask  = years <= TRAIN_END
        val_mask = years == VAL_YEAR

        if tr_mask.sum() < 200:
            print(f"    SKIP : trop peu de barres train ({tr_mask.sum()})")
            continue

        try:
            df, stats = build_labels(df, tr_mask)
        except Exception as e:
            print(f"    WARN build_labels: {e}")
            continue

        n_long_train = int((df["y_long"].values[tr_mask] == 1).sum())
        n_long_val   = int((df["y_long"].values[val_mask] == 1).sum()) if val_mask.sum() > 0 else 0
        print(f"    y_long==1 : train={n_long_train:,}  val={n_long_val:,}")

        if n_long_train < 10:
            print(f"    SKIP : pas assez de signaux long en train ({n_long_train})")
            continue

        # Générer samples train
        try:
            df_tr_samples = generate_exit_samples(df, tr_mask, symbol=sym)
            all_train.append(df_tr_samples)
            print(f"    Samples train : {len(df_tr_samples):,}  "
                  f"y_exit={df_tr_samples['y_exit'].mean():.2%}  "
                  f"({time.time()-t_sym:.1f}s)")
        except Exception as e:
            print(f"    WARN generate_exit_samples train: {e}")
            import traceback; traceback.print_exc()

        # Générer samples val
        if val_mask.sum() >= 50 and n_long_val >= 5:
            try:
                df_val_samples = generate_exit_samples(df, val_mask, symbol=sym)
                all_val.append(df_val_samples)
                print(f"    Samples val   : {len(df_val_samples):,}  "
                      f"y_exit={df_val_samples['y_exit'].mean():.2%}")
            except Exception as e:
                print(f"    WARN generate_exit_samples val: {e}")

    if not all_train:
        print("\n  ERREUR : aucun sample généré. Vérifier les données.")
        sys.exit(1)

    df_train = pd.concat(all_train, ignore_index=True)
    df_val   = pd.concat(all_val,   ignore_index=True) if all_val else None

    print(f"\n  Pool total :")
    print(f"    Train : {len(df_train):,} samples  y_exit={df_train['y_exit'].mean():.2%}")
    if df_val is not None:
        print(f"    Val   : {len(df_val):,} samples  y_exit={df_val['y_exit'].mean():.2%}")

    _print_phase_stats(df_train, tag="Train")
    if df_val is not None:
        _print_phase_stats(df_val, tag="Val")

    # ── Phase 2 : Entraînement ────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  PHASE 2 — Entraînement ExitFleetV1")
    print(f"{'─'*68}")

    t_fit = time.time()
    fleet = ExitFleetV1()
    fleet.fit(df_train, df_val)

    print(f"\n  Entraînement terminé en {time.time()-t_fit:.0f}s")
    print(f"\n  AUC par spécialiste :")
    print(f"    {'Spécialiste':<38} {'AUC':>8} {'n_train':>10} {'n_pos':>8}")
    print(f"    {'─'*38} {'─'*8} {'─'*10} {'─'*8}")
    for ctx_name, spec in sorted(fleet.specialists.items(), key=lambda x: -x[1].val_auc_):
        auc_str  = f"{spec.val_auc_:.4f}" if spec.val_auc_ > 0 else "  N/A"
        n_tr_str = str(spec.n_train_) if spec.n_train_ > 0 else "  –"
        n_po_str = str(spec.n_pos_)   if spec.n_pos_   > 0 else "  –"
        print(f"    {ctx_name:<38} {auc_str:>8} {n_tr_str:>10} {n_po_str:>8}")

    # ── Phase 3 : Simulation ──────────────────────────────────────────────────
    if df_val is not None:
        print(f"\n{'─'*68}")
        print(f"  PHASE 3 — Simulation val (exit model vs baseline)")
        print(f"{'─'*68}")
        _simulate_vs_baseline(fleet, df_val)

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Sauvegarde → {OUTPUT_PATH}")
    joblib.dump(fleet, OUTPUT_PATH)
    size_mb = OUTPUT_PATH.stat().st_size / 1024 / 1024
    print(f"  Taille : {size_mb:.1f} MB")
    print(f"  Threshold calibré : {fleet.threshold_:.2f}")
    print(f"  Features actives  : {len(fleet.features_)}/{len(EXIT_ALL_FEATURES)}")

    elapsed = time.time() - t_start
    print(f"\n{'═'*68}")
    print(f"  DONE — {elapsed:.0f}s total")
    print(f"  Modèle : {OUTPUT_PATH.relative_to(ROOT)}")
    print(f"{'═'*68}")


if __name__ == "__main__":
    main()
