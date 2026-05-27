#!/usr/bin/env python3
"""
paper_short_signal.py — Signal papier SHORT (bear-regime conditionnel)
======================================================================

Génère des signaux SHORT papier pour les actifs en régime bear confirmé.

Conditions d'activation :
  - BTC < EMA 200 jours ET mom_30j < -5%  (régime macro bear)
  - Actif < EMA 200 jours ET mom_30j < -10% (régime bear individuel)
  - p_short >= seuil (LightGBM DART entraîné sur données bear récentes)

PAPER TRADING UNIQUEMENT — SHORT live = DÉSACTIVÉ
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─── Imports projet ───────────────────────────────────────────────────────────
try:
    from ai.level_0.short_features import compute_all_short_features, FEATURES_SHORT_GAMECHANGER
    _HAS_SHORT_FEAT = True
except ImportError:
    _HAS_SHORT_FEAT = False
    FEATURES_SHORT_GAMECHANGER: List[str] = []

try:
    from ai.level_0.short_proxy_features import compute_all_proxy_features, FEATURES_SHORT_PROXY
    _HAS_PROXY = True
except ImportError:
    _HAS_PROXY = False
    FEATURES_SHORT_PROXY: List[str] = []

try:
    from ai.level_0.short_labels import compute_short_label_columns, build_short_labels
    _HAS_LABELS = True
except ImportError:
    _HAS_LABELS = False

try:
    from ai.level_0.features import FEATURES_SHORT
    _HAS_FEATURES = True
except ImportError:
    _HAS_FEATURES = False
    FEATURES_SHORT: List[str] = []

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

from sklearn.ensemble import HistGradientBoostingClassifier

# ─── Constantes ───────────────────────────────────────────────────────────────
DATA_DIR   = ROOT / "data"
REPORT_DIR = ROOT / "reports" / "paper_trading"
SIGNAL_CSV = REPORT_DIR / "paper_short_signals.csv"
POSITION_CSV = REPORT_DIR / "paper_positions.csv"

EMA_SPAN_200D   = 4800   # 200 jours × 24h = 4800 barres 1h
MOM_WINDOW      = 720    # 30 jours = 720 barres 1h
BTC_MOM_THRESH  = -0.05  # BTC doit baisser > 5% sur 30j
ASSET_MOM_THRESH = -0.10 # Asset doit baisser > 10% sur 30j
N_MONTHS_HISTORY = 24    # mois d'historique pour entraîner le modèle
HOLD_BARS        = 4     # durée de position (4h)

PRIORITY_ASSETS = ["BTCUSD", "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]


# ─── Bear regime gate ─────────────────────────────────────────────────────────

def _compute_ema200d(close: np.ndarray) -> np.ndarray:
    alpha = 2.0 / (EMA_SPAN_200D + 1)
    ema = np.full(len(close), np.nan)
    if len(close) == 0:
        return ema
    ema[0] = close[0]
    for i in range(1, len(close)):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
    ema[:min(720, len(ema))] = np.nan
    return ema


def compute_bear_gate(df: pd.DataFrame, mom_threshold: float = ASSET_MOM_THRESH) -> pd.Series:
    close = df["Close"].values.astype(float)
    ema   = _compute_ema200d(close)
    mom   = np.full(len(close), np.nan)
    if len(close) > MOM_WINDOW:
        mom[MOM_WINDOW:] = np.log(close[MOM_WINDOW:] / close[:-MOM_WINDOW])
    bear = (close < ema) & (mom < mom_threshold)
    return pd.Series(bear.astype(float), index=df.index, name="macro_bear")


# ─── Chargement données ───────────────────────────────────────────────────────

def load_assets(data_dir: Path, max_assets: int = 50, months: int = N_MONTHS_HISTORY) -> Dict[str, pd.DataFrame]:
    files = sorted(data_dir.glob("*_features.csv"))
    if not files:
        print(f"  WARN : aucun fichier *_features.csv dans {data_dir}")
        return {}

    # Priorité aux actifs liquides
    def priority_key(p: Path) -> int:
        sym = p.stem.split("_")[0]
        return PRIORITY_ASSETS.index(sym) if sym in PRIORITY_ASSETS else 99

    files = sorted(files, key=priority_key)[:max_assets]

    cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=months)
    assets: Dict[str, pd.DataFrame] = {}

    for f in files:
        sym = f.stem.replace("_1h_features", "").replace("_features", "")
        try:
            df = pd.read_csv(f, parse_dates=["datetime"])
            if df["datetime"].dt.tz is None:
                df["datetime"] = df["datetime"].dt.tz_localize("UTC")
            df = df.sort_values("datetime").reset_index(drop=True)
            df = df[df["datetime"] >= cutoff].copy()
            if len(df) < MOM_WINDOW + 100:
                continue
            assets[sym] = df
        except Exception:
            pass

    return assets


# ─── Feature engineering ──────────────────────────────────────────────────────

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if _HAS_SHORT_FEAT:
        try:
            df = compute_all_short_features(df)
        except Exception:
            pass
    if _HAS_PROXY:
        try:
            df = compute_all_proxy_features(df)
        except Exception:
            pass
    df["macro_bear"] = compute_bear_gate(df)
    return df


# ─── Modèle rapide ───────────────────────────────────────────────────────────

def _get_features(df: pd.DataFrame) -> List[str]:
    candidates = list(dict.fromkeys(
        FEATURES_SHORT_GAMECHANGER + FEATURES_SHORT + FEATURES_SHORT_PROXY
    ))
    avail = [f for f in candidates if f in df.columns
             and pd.api.types.is_numeric_dtype(df[f])]
    if len(avail) < 5:
        avail = [c for c in df.select_dtypes(include=[np.number]).columns
                 if c not in ("Close", "Open", "High", "Low", "Volume",
                              "macro_bear", "y_short_clean", "y_short_4h")]
    return avail[:120]


def train_model(df_all: pd.DataFrame, feats: List[str]) -> object:
    label_col = "y_short_clean"

    if _HAS_LABELS:
        try:
            df_all = compute_short_label_columns(df_all)
            years  = df_all["datetime"].dt.year.values if "datetime" in df_all.columns \
                     else np.ones(len(df_all), dtype=int) * 2023
            train_mask = years <= (years.max() - 1)
            df_all = build_short_labels(df_all, train_mask)
        except Exception:
            label_col = None

    if label_col is None or label_col not in df_all.columns:
        # fallback : label simple
        if "future_ret_short_4h" in df_all.columns:
            q = df_all["future_ret_short_4h"].quantile(0.88)
            df_all["_label"] = (df_all["future_ret_short_4h"] > q).astype(int)
            label_col = "_label"
        else:
            return None

    # Entraîner uniquement sur les barres bear
    bear_mask = df_all["macro_bear"].values > 0
    df_train  = df_all[bear_mask & (df_all[label_col] >= 0)].copy()

    if len(df_train) < 50:
        return None

    X = df_train[feats].fillna(0).values.astype(np.float32)
    y = df_train[label_col].values.astype(int)

    n_pos = max(int((y == 1).sum()), 1)
    n_neg = max(int((y == 0).sum()), 1)
    spw   = n_neg / n_pos

    if _HAS_LGB:
        model = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            max_depth=5,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=float(np.clip(spw, 1, 50)),
            verbose=-1,
            random_state=42,
        )
    else:
        model = HistGradientBoostingClassifier(
            max_iter=200, max_depth=4, learning_rate=0.05,
            min_samples_leaf=20, class_weight="balanced", random_state=42,
        )

    try:
        model.fit(X, y)
        return model
    except Exception as e:
        print(f"  WARN model fit : {e}")
        return None


def predict(model, df: pd.DataFrame, feats: List[str]) -> float:
    if model is None:
        return 0.5
    try:
        x = df[feats].fillna(0).values[-1:].astype(np.float32)
        p = model.predict_proba(x)[0, 1]
        return float(p)
    except Exception:
        return 0.5


# ─── Paper portfolio ──────────────────────────────────────────────────────────

def load_positions() -> pd.DataFrame:
    if POSITION_CSV.exists():
        return pd.read_csv(POSITION_CSV, parse_dates=["entry_time"])
    return pd.DataFrame(columns=["entry_time", "symbol", "entry_price",
                                  "p_short_at_entry", "hold_bars", "status", "pnl_pct"])


def update_positions(positions: pd.DataFrame, assets: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    if positions.empty:
        return positions
    now = pd.Timestamp.now()
    for i, row in positions.iterrows():
        if row["status"] != "OPEN":
            continue
        sym = row["symbol"]
        if sym not in assets:
            continue
        df   = assets[sym]
        age_h = (now - row["entry_time"]).total_seconds() / 3600
        if age_h >= HOLD_BARS:
            exit_price = float(df["Close"].iloc[-1])
            entry_price = float(row["entry_price"])
            pnl = (entry_price - exit_price) / entry_price * 100
            positions.at[i, "pnl_pct"] = round(pnl, 4)
            positions.at[i, "status"]  = "CLOSED"
    return positions


def open_position(positions: pd.DataFrame, sym: str, price: float, p_short: float) -> pd.DataFrame:
    already_open = not positions.empty and \
        ((positions["symbol"] == sym) & (positions["status"] == "OPEN")).any()
    if already_open:
        return positions
    new_row = {
        "entry_time": pd.Timestamp.now(),
        "symbol": sym,
        "entry_price": price,
        "p_short_at_entry": round(p_short, 4),
        "hold_bars": HOLD_BARS,
        "status": "OPEN",
        "pnl_pct": 0.0,
    }
    return pd.concat([positions, pd.DataFrame([new_row])], ignore_index=True)


def save_positions(positions: pd.DataFrame) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    positions.to_csv(POSITION_CSV, index=False)


# ─── Affichage ────────────────────────────────────────────────────────────────

def _btc_regime(assets: Dict[str, pd.DataFrame]) -> Tuple[str, str]:
    for sym in ["BTCUSD", "BTCUSDT"]:
        if sym not in assets:
            continue
        df    = assets[sym]
        close = df["Close"].values.astype(float)
        ema   = _compute_ema200d(close)
        mom   = np.full(len(close), np.nan)
        if len(close) > MOM_WINDOW:
            mom[MOM_WINDOW:] = np.log(close[MOM_WINDOW:] / close[:-MOM_WINDOW])
        last_close = close[-1]
        last_ema   = ema[-1]
        last_mom   = mom[-1]
        if np.isnan(last_ema) or np.isnan(last_mom):
            return "INCONNU", f"Price={last_close:,.0f}"
        if last_close < last_ema and last_mom < BTC_MOM_THRESH:
            status = "BEAR"
        elif last_close < last_ema:
            status = "BEAR_WEAK"
        else:
            status = "BULL"
        detail = (f"EMA200d={last_ema:,.0f} | Price={last_close:,.0f} | "
                  f"mom30d={last_mom*100:+.1f}%")
        return status, detail
    return "INCONNU", "BTC non disponible"


def print_report(
    btc_regime: str,
    btc_detail: str,
    signals: List[dict],
    positions: pd.DataFrame,
    threshold: float,
) -> None:
    now_str = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M UTC")
    print("\n" + "=" * 60)
    print(f"  PAPER SHORT SIGNAL — {now_str}")
    print("=" * 60)
    print(f"  Régime BTC : {btc_regime}  ({btc_detail})")

    bear_count = sum(1 for s in signals if s["bear_gate"])
    print(f"  Actifs en régime bear : {bear_count}/{len(signals)}")

    candidates = [s for s in signals if s["bear_gate"] and s["p_short"] >= threshold * 0.85]
    candidates.sort(key=lambda x: x["p_short"], reverse=True)

    # Actifs en bear mais sous le seuil (monitoring)
    bear_watch = [s for s in signals if s["bear_gate"] and s["p_short"] < threshold * 0.85]
    bear_watch.sort(key=lambda x: x["p_short"], reverse=True)

    # Actifs proches du bear (< 5% de l'EMA200d)
    near_bear = [s for s in signals if not s["bear_gate"] and s.get("pct_from_ema", 0) > -5]
    near_bear.sort(key=lambda x: x.get("pct_from_ema", -99))

    print(f"\n  TOP SHORT CANDIDATS (p_short >= {threshold*0.85:.2f}) :")
    if not candidates:
        print("    — aucun signal au-dessus du seuil —")
    else:
        print(f"  {'#':>3}  {'Symbol':<14} {'p_short':>8} {'mom30d':>8} {'ATR%':>6}  Action")
        print("  " + "-" * 52)
        for i, s in enumerate(candidates[:10], 1):
            action = s["action"]
            flag   = "◀ ENTER" if action == "ENTER_PAPER" else ""
            print(f"  {i:>3}  {s['symbol']:<14} {s['p_short']:>8.3f} "
                  f"{s['mom30d']*100:>+7.1f}%  {s['atr_pct']*100:>5.1f}%  "
                  f"{action}  {flag}")

    if bear_watch:
        print(f"\n  ACTIFS EN BEAR — signal faible (p < {threshold*0.85:.2f}) :")
        print(f"  {'Symbol':<14} {'p_short':>8} {'mom30d':>8} {'%EMA200':>8}")
        print("  " + "-" * 44)
        for s in bear_watch[:8]:
            pct_ema = s.get("pct_from_ema", 0)
            print(f"  {s['symbol']:<14} {s['p_short']:>8.3f} "
                  f"{s['mom30d']*100:>+7.1f}%  {pct_ema:>+7.1f}%")

    if near_bear:
        print(f"\n  ACTIFS PROCHES DU BEAR (< 5% sous EMA200d) :")
        print(f"  {'Symbol':<14} {'%EMA200':>8} {'mom30d':>8}  Statut")
        print("  " + "-" * 44)
        for s in near_bear[:5]:
            pct_ema = s.get("pct_from_ema", 0)
            print(f"  {s['symbol']:<14} {pct_ema:>+7.1f}%  "
                  f"{s['mom30d']*100:>+7.1f}%  near-bear")

    # Paper portfolio
    open_pos = positions[positions["status"] == "OPEN"] if not positions.empty else pd.DataFrame()
    closed   = positions[positions["status"] == "CLOSED"] if not positions.empty else pd.DataFrame()
    total_pnl = float(closed["pnl_pct"].sum()) if not closed.empty else 0.0
    n_open    = len(open_pos)

    print(f"\n  PAPER PORTFOLIO :")
    print(f"    Positions ouvertes : {n_open}")
    if not open_pos.empty:
        for _, r in open_pos.iterrows():
            print(f"      {r['symbol']:<14} entry={r['entry_price']:>10,.2f}  "
                  f"p@entry={r['p_short_at_entry']:.3f}")
    print(f"    P&L cumulé (trades fermés) : {total_pnl:+.2f}%  "
          f"({len(closed)} trades)")

    print("\n" + "=" * 60)
    print("  !! PAPER TRADING UNIQUEMENT — SHORT LIVE = DÉSACTIVÉ !!")
    print("=" * 60 + "\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper SHORT signal generator")
    parser.add_argument("--data-dir",       default=str(DATA_DIR))
    parser.add_argument("--min-bear-assets",type=int,   default=3)
    parser.add_argument("--threshold",      type=float, default=0.60)
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--update-positions", action="store_true")
    parser.add_argument("--max-assets",     type=int,   default=50)
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1. Charger les données
    print(f"[1/5] Chargement des données ({args.max_assets} actifs, {N_MONTHS_HISTORY}m)…")
    assets = load_assets(Path(args.data_dir), args.max_assets, N_MONTHS_HISTORY)
    if not assets:
        print("Aucune donnée trouvée. Vérifier --data-dir.")
        sys.exit(1)
    print(f"  {len(assets)} actifs chargés.")

    # 2. Enrichissement + gate bear
    print(f"[2/5] Feature engineering…")
    for sym in list(assets):
        try:
            assets[sym] = enrich(assets[sym])
        except Exception as e:
            print(f"  WARN {sym} : {e}")
            del assets[sym]

    # 3. Régime BTC
    btc_regime, btc_detail = _btc_regime(assets)
    bear_assets = {sym: df for sym, df in assets.items()
                   if df["macro_bear"].iloc[-1] > 0}

    if len(bear_assets) < args.min_bear_assets and btc_regime == "BULL":
        print(f"\n  MARCHÉ EN BULL — SHORT INACTIF ({len(bear_assets)} actifs bear < {args.min_bear_assets} requis)")
        print(f"  BTC : {btc_detail}")
        print("  Aucun signal généré.\n")
        return

    # 4. Entraîner le modèle sur les données bear combinées
    print(f"[3/5] Entraînement modèle (barres bear uniquement)…")
    df_all = pd.concat(list(assets.values()), ignore_index=True)
    feats  = _get_features(df_all)
    print(f"  {len(feats)} features | {int(df_all['macro_bear'].sum()):,} barres bear")

    model = train_model(df_all, feats)
    if model is None:
        print("  WARN : modèle non entraîné (pas assez de données bear). Signal heuristique.")

    # 5. Générer signaux par actif
    print(f"[4/5] Génération des signaux…")
    positions = load_positions()
    if args.update_positions:
        positions = update_positions(positions, assets)

    signals = []
    signal_rows = []
    now = pd.Timestamp.now()

    open_syms = set(positions[positions["status"] == "OPEN"]["symbol"].tolist()) \
                if not positions.empty else set()

    for sym, df in assets.items():
        if len(df) < 5:
            continue

        last      = df.iloc[-1]
        bear_gate = float(last.get("macro_bear", 0)) > 0

        # Pour les actifs en bear ET near-bear, on génère la prédiction
        close_arr = df["Close"].values.astype(float)
        ema200d   = _compute_ema200d(close_arr)
        last_ema  = ema200d[-1] if not np.isnan(ema200d[-1]) else close_arr[-1]
        close     = float(close_arr[-1])
        pct_from_ema = (close - last_ema) / last_ema * 100  # négatif = sous EMA

        # Prédire aussi pour les near-bear (surveillance)
        near_bear_flag = (not bear_gate) and pct_from_ema > -8  # dans les 8% de l'EMA
        p_short = predict(model, df, feats) if (model and (bear_gate or near_bear_flag)) else 0.0

        mom30    = 0.0
        if len(close_arr) > MOM_WINDOW:
            mom30 = float(np.log(close_arr[-1] / close_arr[-MOM_WINDOW - 1]))
        atr_pct  = float(last.get("atr_pct_14", 0.02))

        if sym in open_syms:
            action = "HOLD"
        elif bear_gate and p_short >= args.threshold:
            action = "ENTER_PAPER"
        elif bear_gate and p_short >= args.threshold * 0.85:
            action = "WATCH"
        else:
            action = "NO_SIGNAL"

        signals.append({"symbol": sym, "p_short": p_short, "bear_gate": bear_gate,
                         "mom30d": mom30, "atr_pct": atr_pct, "action": action,
                         "close": close, "pct_from_ema": pct_from_ema,
                         "ema200d": round(last_ema, 2)})

        signal_rows.append({
            "timestamp":  now.isoformat(),
            "symbol":     sym,
            "p_short":    round(p_short, 4),
            "bear_gate":  int(bear_gate),
            "close":      close,
            "mom_30d":    round(mom30 * 100, 2),
            "atr_pct":    round(atr_pct * 100, 2),
            "action":     action,
        })

        if action == "ENTER_PAPER" and not args.dry_run:
            positions = open_position(positions, sym, close, p_short)

    # 6. Sauvegarder
    if not args.dry_run:
        save_positions(positions)
        sig_df = pd.DataFrame(signal_rows)
        if SIGNAL_CSV.exists():
            sig_df = pd.concat([pd.read_csv(SIGNAL_CSV), sig_df], ignore_index=True)
        sig_df.to_csv(SIGNAL_CSV, index=False)
        print(f"[5/5] Signaux sauvegardés → {SIGNAL_CSV}")

    # 7. Afficher rapport
    print_report(btc_regime, btc_detail, signals, positions, args.threshold)

    enters = [s for s in signals if s["action"] == "ENTER_PAPER"]
    if enters:
        print(f"  {len(enters)} nouveau(x) signal(s) ENTER_PAPER enregistré(s)")
    print(f"  Durée : {time.time()-t0:.1f}s\n")


if __name__ == "__main__":
    main()
