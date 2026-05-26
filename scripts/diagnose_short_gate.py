#!/usr/bin/env python3
"""
diagnose_short_gate.py — Diagnostic rapide : impact des nouvelles gates SHORT sur BTC

Hypothèses testées :
  H1 — Momentum gate : ret_7d > +8% → les shorts sont mauvais (recovery, dead-cat)
  H2 — Late-entry gate : ret_24h < -8% → move déjà consommé → squeeze

Méthode :
  Analyse ORACLE — on utilise les vrais rendements futurs (y_short labels),
  pas un modèle ML. Cela isole l'impact pur des gates sur la qualité du signal.

  1. Charger BTC 2019-2024 (parquets 1-minute)
  2. Rééchantillonner en 1h (OHLCV + features)
  3. Calculer EMA50, EMA200, RSI14 sur les prix horaires
  4. Calculer les labels oracle y_short (ret_8h < -threshold, filtre non-retournement)
  5. Comparer PnL brut : gate actuelle vs gate actuelle + momentum + late-entry
  6. Reporter par année

Usage :
  python scripts/diagnose_short_gate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data_out" / "result"
SYMBOLS   = ["BTCUSDT"]
YEARS     = [2019, 2020, 2021, 2022, 2023, 2024]
HORIZON_H = 8        # position durée 8h
QUANTILE  = 0.84     # top 16% des mouvements (identique à TRADEABLE_QUANTILE_SHORT)
COST_BPS  = 15       # frais stress (long + funding short) en bps
COST_FRAC = COST_BPS / 10_000

# ─── Gates nouvelles ──────────────────────────────────────────────────────────
MOMENTUM_GATE_7D   = +0.08   # ret_7d > +8% → NO_SHORT (recovery)
MOMENTUM_GATE_3D   = +0.05   # ret_3d > +5% → NO_SHORT (accélération)
LATE_ENTRY_GATE    = -0.08   # ret_24h < -8% → move consommé → skip


# ─── Helpers techniques ───────────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span // 2).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(span=period, adjust=False, min_periods=period).mean()
    avg_l = loss.ewm(span=period, adjust=False, min_periods=period).mean()
    rs    = avg_g / avg_l.clip(lower=1e-9)
    return 100 - (100 / (1 + rs))


# ─── Chargement & resampling ──────────────────────────────────────────────────

def load_yearly(symbol: str, years: list[int]) -> pd.DataFrame:
    frames = []
    for y in years:
        path = DATA_DIR / f"{y}_{symbol}_features.parquet"
        if not path.exists():
            print(f"  [skip] {path.name} manquant")
            continue
        df = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames.append(df)
        print(f"  chargé {path.name} : {len(df):,} barres")
    if not frames:
        raise FileNotFoundError("Aucun parquet trouvé")
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    df = df.set_index("timestamp")
    h = df.resample("1h").agg(
        Open=("open", "first"),
        High=("high", "max"),
        Low=("low", "min"),
        Close=("close", "last"),
        Volume=("volume", "sum"),
    ).dropna(subset=["Close"])
    h.index.name = "datetime"
    return h.reset_index()


# ─── Feature engineering minimal ─────────────────────────────────────────────

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]

    df["ema50"]          = ema(c, 50)
    df["ema200"]         = ema(c, 200)
    df["ema_spread"]     = df["ema50"] - df["ema200"]        # >0 = bullish
    df["dist_ema50_pct"] = (c - df["ema50"]) / df["ema50"]  # >0 = above EMA50
    df["rsi14"]          = rsi(c, 14)

    logc = np.log(c)
    df["ret_1h"]  = logc.diff(1)
    df["ret_3d"]  = logc.diff(72)    # 72 barres × 1h = 3 jours
    df["ret_7d"]  = logc.diff(168)   # 168 barres × 1h = 7 jours
    df["ret_24h"] = logc.diff(24)    # 24 barres × 1h = 24 heures

    return df


# ─── Labels oracle ────────────────────────────────────────────────────────────

def add_labels(df: pd.DataFrame, train_years: list[int]) -> pd.DataFrame:
    c     = np.log(df["Close"].values.astype(np.float64))
    n     = len(df)

    # Forward log-return 8h
    fwd8 = np.full(n, np.nan)
    fwd8[:n - HORIZON_H] = c[HORIZON_H:] - c[:n - HORIZON_H]
    df["fwd_ret_8h"] = fwd8

    # Max 1h-ret dans les 16h suivant l'entrée (anti-reversal short)
    ret1h = np.diff(c, prepend=np.nan)  # ret1h[i] = c[i] - c[i-1]
    ret1h_safe = np.where(np.isnan(ret1h), 0.0, ret1h)
    win16_max = np.full(n, np.nan)
    W = 16
    for i in range(n - W):
        win16_max[i] = ret1h_safe[i + 1 : i + 1 + W].max()
    df["fwd_ret_h16_max"] = win16_max

    # Calibrer threshold sur train uniquement
    years  = df["datetime"].dt.year
    tmask  = years.isin(train_years).values
    fwd_tr = np.abs(fwd8[tmask & np.isfinite(fwd8)])
    thr_raw = float(np.quantile(fwd_tr, QUANTILE))
    thr     = thr_raw + COST_FRAC
    df.attrs["thr_short"] = thr
    df.attrs["thr_raw"]   = thr_raw

    # y_short oracle
    raw_short = fwd8 < -thr
    no_rev    = win16_max < thr_raw * 0.45   # filtre non-retournement
    df["y_short_oracle"] = np.where(raw_short & no_rev, 1,
                           np.where(raw_short & ~no_rev, -1, 0)).astype(np.int8)

    df.attrs["thr_short_used"] = thr
    return df


# ─── Gates ───────────────────────────────────────────────────────────────────

def add_gates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gate actuelle (EMA-based) et nouvelles gates (momentum + late-entry).
    Toutes sont des indicateurs PASSÉS — aucun leakage.
    """
    price_above_ema50  = df["dist_ema50_pct"] > 0
    ema50_above_ema200 = df["ema_spread"] > 0
    rsi_bullish        = df["rsi14"] > 55

    # Gate actuelle : EMA-based (lente)
    df["gate_ema"]  = (price_above_ema50 & ema50_above_ema200 & rsi_bullish)

    # Gate momentum rapide (nouvelle)
    df["gate_momentum"] = (df["ret_7d"] > MOMENTUM_GATE_7D) | (df["ret_3d"] > MOMENTUM_GATE_3D)

    # Gate late-entry (nouvelle)
    df["gate_late_entry"] = df["ret_24h"] < LATE_ENTRY_GATE

    # Gate combinée
    df["gate_combined"] = df["gate_ema"] | df["gate_momentum"] | df["gate_late_entry"]

    return df


# ─── Simulation PnL ──────────────────────────────────────────────────────────
#
# APPROCHE CORRECTE :
#   On simule une stratégie naïve "short à chaque barre SHORTABLE" qui n'a PAS
#   de modèle ML — elle représente le bruit de fond que le modèle doit filtrer.
#   On mesure l'impact des gates en termes de PnL brut.
#   PnL = -fwd_ret_8h - COST_FRAC  (short gagne si ret_8h < 0)

def simulate_naive_short(df: pd.DataFrame, gate_col: str) -> dict:
    """Short naïf sur toutes les barres SHORTABLE non filtrées par gate_col."""
    valid = (
        df["fwd_ret_8h"].notna() &
        (~df[gate_col])           # gate=True → NO_SHORT
    )
    trades = df.loc[valid, "fwd_ret_8h"].values
    if len(trades) == 0:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan}

    pnl  = -trades - COST_FRAC
    wins = pnl[pnl > 0]
    loss = pnl[pnl < 0]
    pf   = wins.sum() / max(abs(loss.sum()), 1e-9)
    wr   = len(wins) / len(pnl)
    exp  = float(np.mean(pnl))
    return {"n": len(pnl), "pf": round(pf, 3), "wr": round(wr, 3), "exp": round(exp, 4)}


def pnl_stats(fwd_rets: np.ndarray) -> dict:
    if len(fwd_rets) == 0:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan}
    pnl  = -fwd_rets - COST_FRAC
    wins = pnl[pnl > 0]
    loss = pnl[pnl < 0]
    pf   = wins.sum() / max(abs(loss.sum()), 1e-9)
    wr   = len(wins) / len(pnl)
    return {"n": len(pnl), "pf": round(pf, 3), "wr": round(wr, 3), "exp": round(float(np.mean(pnl)), 4)}


# ─── Rapport ─────────────────────────────────────────────────────────────────

def report_year(df: pd.DataFrame, year: int) -> None:
    mask = df["datetime"].dt.year == year
    sub  = df.loc[mask].copy()
    if len(sub) == 0:
        return

    # Région SHORTABLE selon gate EMA (actuelle)
    shortable = ~sub["gate_ema"] & sub["fwd_ret_8h"].notna()
    n_shortable = int(shortable.sum())

    # Barres supplémentaires bloquées par chaque nouvelle gate
    new_mom_blocks  = sub["gate_momentum"] & shortable
    new_late_blocks = sub["gate_late_entry"] & shortable & ~sub["gate_momentum"]
    n_additional = int((sub["gate_momentum"] | sub["gate_late_entry"]).values[shortable.values].sum())

    res_ema      = pnl_stats(sub.loc[shortable, "fwd_ret_8h"].values)
    res_combined = pnl_stats(sub.loc[shortable & ~sub["gate_momentum"] & ~sub["gate_late_entry"],
                                      "fwd_ret_8h"].values)
    res_blocked  = pnl_stats(sub.loc[shortable & (sub["gate_momentum"] | sub["gate_late_entry"]),
                                      "fwd_ret_8h"].values)

    print(f"\n{'─'*65}")
    print(f"  {year} BTC  ({len(sub):,} barres)  SHORTABLE={n_shortable}  nouvelles_bloquées={n_additional}")
    print(f"{'─'*65}")
    print(f"  Stratégie naïve (short à chaque barre SHORTABLE, coût {COST_BPS} bps):")
    print(f"    Gate EMA seule  : n={res_ema['n']:4d}  PF={res_ema['pf']:.3f}  WR={res_ema['wr']:.1%}  E={res_ema['exp']:.4f}")
    print(f"    + Momentum+Late : n={res_combined['n']:4d}  PF={res_combined['pf']:.3f}  WR={res_combined['wr']:.1%}  E={res_combined['exp']:.4f}")
    if res_blocked['n'] > 0:
        print(f"    Trades bloqués  : n={res_blocked['n']:4d}  PF={res_blocked['pf']:.3f}  WR={res_blocked['wr']:.1%}  E={res_blocked['exp']:.4f}")
    if res_combined['n'] > 0 and res_ema['n'] > 0:
        dpf = res_combined['pf'] - res_ema['pf']
        sign = "+" if dpf >= 0 else ""
        verdict = "BON FILTRE" if dpf > 0.05 else ("NEUTRE" if abs(dpf) < 0.05 else "FILTRE NOCIF")
        print(f"    ΔPF = {sign}{dpf:.3f}  ({verdict})")


def report_breakdown(df: pd.DataFrame) -> None:
    """Décompose le PnL par type de contexte sur toutes les barres SHORTABLE."""
    shortable = ~df["gate_ema"] & df["fwd_ret_8h"].notna()

    scenarios = {
        "Normal (ni recovery ni flush)"   : (~df["gate_momentum"]) & (~df["gate_late_entry"]),
        "Recovery 7j  (ret7d > +8%)"      : df["ret_7d"] > MOMENTUM_GATE_7D,
        "Accél 3j     (ret3d > +5%)"      : (df["ret_3d"] > MOMENTUM_GATE_3D) & (df["ret_7d"] <= MOMENTUM_GATE_7D),
        "Late-entry   (ret24h < -8%)"     : df["gate_late_entry"] & (~df["gate_momentum"]),
    }

    print(f"\n{'='*65}")
    print("  DÉCOMPOSITION — PnL par contexte (barres SHORTABLE, toutes années)")
    print(f"{'='*65}")
    print(f"  {'Contexte':<38} {'N':>5}  {'PF':>6}  {'WR':>6}  {'E':>8}")
    print(f"  {'─'*38}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*8}")

    for label, mask in scenarios.items():
        sub = df.loc[shortable & mask, "fwd_ret_8h"].values
        r   = pnl_stats(sub)
        if r['n'] == 0:
            print(f"  {label:<38} {'0':>5}")
            continue
        pf_str = f"{r['pf']:6.3f}" if not np.isnan(r['pf']) else "   inf"
        print(f"  {label:<38} {r['n']:>5}  {pf_str}  {r['wr']:>5.1%}  {r['exp']:>8.4f}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  DIAGNOSE SHORT GATE — BTC oracle analysis")
    print("=" * 60)

    print("\n[1] Chargement des données 1-minute...")
    raw = load_yearly("BTCUSDT", YEARS)
    print(f"  Total : {len(raw):,} barres 1-minute")

    print("\n[2] Resampling → 1h...")
    df = resample_1h(raw)
    print(f"  Total : {len(df):,} barres horaires")

    print("\n[3] Feature engineering minimal...")
    df = add_features(df)

    print("\n[4] Forward returns (pas de labels oracle — stratégie naïve)...")
    # Calcul du fwd_ret_8h directement (sans filtrage par labels)
    c     = np.log(df["Close"].values.astype(np.float64))
    n     = len(df)
    fwd8  = np.full(n, np.nan)
    fwd8[:n - HORIZON_H] = c[HORIZON_H:] - c[:n - HORIZON_H]
    df["fwd_ret_8h"] = fwd8
    print(f"  {int(np.isfinite(fwd8).sum()):,} barres avec fwd_ret_8h valide")

    print("\n[5] Calcul des gates...")
    df = add_gates(df)

    # Rapport par année
    print("\n[6] Résultats par année (stratégie naïve : short à chaque barre non gated)")
    for year in YEARS:
        report_year(df, year)

    # Décomposition globale
    report_breakdown(df)

    print(f"\n{'='*65}")
    print("  LECTURE DES RÉSULTATS")
    print(f"{'='*65}")
    print("  - PF 'Trades bloqués' < 1.0  → les nouvelles gates éliminent de VRAIES pertes")
    print("  - PF '+ Momentum+Late' > PF 'Gate EMA seule' → amélioration réelle")
    print("  - Scénarios Recovery 7j / Late-entry avec PF < 1.0 → cas nocifs confirmés")
    print()


if __name__ == "__main__":
    main()
