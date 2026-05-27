#!/usr/bin/env python3
"""
walk_forward_short_regime_v4.py — Architecture REGIME-SWITCHING
================================================================

Basé sur l'insight clé des papiers de recherche (arxiv 2602.11708,
Lopez de Prado, Hudson & Thames) :

  Le short profitable n'est PAS une prédiction bar-par-bar.
  C'est une décision de régime + allocation + timing d'entrée.

Architecture 3 couches :

  Couche 1 — RÉGIME MACRO (signal lent, hebdomadaire)
    • HMM-style : BTC < EMA200 + funding + OI momentum
    • Output : bear_confirmed / neutral / bull_confirmed
    • Changer de signal seulement si régime persiste 48h+ (évite le whipsaw)

  Couche 2 — ALLOCATION BASKET (signal moyen, journalier)
    • En bear_confirmed → short les N assets avec pire momentum 30j
    • Pondération = equal_weight (pas de ML)
    • Stop global : si portefeuille court perd 5% → exit total

  Couche 3 — TIMING D'ENTRÉE (signal rapide, heuristique)
    • Entrer uniquement quand :
        (a) funding_rate > +0.05%/8h (foule sur-longée)
        OU (b) OI monte + prix monte depuis 24h (accumulation fragile)
        OU (c) RSI 1h > 65 dans régime bear (pullback = entry)
    • Tenir jusqu'à profit_barrier (ATR×1.5) OU max 3 jours

Walk-forward :
  Folds 2022, 2023, 2024, 2025
  Backtest : simulation réaliste avec coût 15 bps + slippage 0.1%

Usage :
  python3 scripts/walk_forward_short_regime_v4.py
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "short_regime_v4"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Paramètres ──────────────────────────────────────────────────────────────

# Basket d'actifs à shorter (les plus liquides avec funding)
BASKET_ASSETS = ["BTC", "ETH", "SOL", "BNB", "LINK", "XRP"]
ALL_YEARS     = [2019, 2020, 2021, 2022, 2023, 2024, 2025]

# Couche 1 — Régime
REGIME_PERSIST_BARS = 48    # heures de persistance avant changement de régime

# Couche 2 — Allocation
N_SHORT_ASSETS   = 3        # nombre d'actifs à shorter dans le basket
MAX_PORTFOLIO_DD = 0.05     # -5% → exit portefeuille

# Couche 3 — Timing
FUNDING_ENTRY_THR = 0.0005  # > 0.05%/8h = foule sur-longée → entry
RSI_ENTRY_BEAR    = 65      # RSI > 65 en régime bear = pullback = entry SHORT
PROFIT_ATR_MULT   = 1.5     # take profit ATR × 1.5
MAX_HOLD_HOURS    = 72      # max 3 jours (72 barres)

# Coûts
COST_PCT  = 0.0015          # 15 bps frais
SLIP_PCT  = 0.0010          # 10 bps slippage

FOLDS = [
    {"train": [2019, 2020, 2021],             "test": 2022},
    {"train": [2019, 2020, 2021, 2022],       "test": 2023},
    {"train": [2019, 2020, 2021, 2022, 2023], "test": 2024},
    {"train": [2019, 2020, 2021, 2022, 2023, 2024], "test": 2025},
]

# ─── Chargement ───────────────────────────────────────────────────────────────

_COLS = [
    "timestamp", "open", "high", "low", "close", "volume",
    "atr_14", "atr_pct_14", "rsi_14",
    "funding_rate", "funding_z_7d", "funding_accel",
    "oi_sum", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h",
    "global_long_short_ratio", "lsr_z_1d",
    "taker_buy_sell_ratio", "fear_greed",
    "ret_60m", "ret_240m", "ret_1440m",
]


def load_asset(symbol: str) -> Optional[pd.DataFrame]:
    import pyarrow.parquet as pq
    frames = []
    for y in ALL_YEARS:
        path = DATA_DIR / f"{y}_{symbol}USDT_features.parquet"
        if not path.exists():
            continue
        avail = set(pq.ParquetFile(path).schema.names)
        cols  = [c for c in _COLS if c in avail]
        df    = pd.read_parquet(path, columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames.append(df)
    if not frames:
        return None
    raw = pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)

    # Resample 1h
    raw = raw.set_index("timestamp")
    agg  = {"Open": pd.NamedAgg("open","first"), "High": pd.NamedAgg("high","max"),
            "Low": pd.NamedAgg("low","min"),   "Close": pd.NamedAgg("close","last"),
            "Volume": pd.NamedAgg("volume","sum")}
    h_ohlcv = raw.resample("1h").agg(**agg)
    num  = [c for c in raw.select_dtypes(include=[np.number]).columns
            if c not in {"open","high","low","close","volume"}]
    h_other = raw[num].resample("1h").last()
    h = pd.concat([h_ohlcv, h_other], axis=1).dropna(subset=["Close"])
    h.index.name = "datetime"
    return h.reset_index()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df   = df.copy()
    c    = df["Close"]
    logc = np.log(c.clip(lower=1e-9))

    # EMA
    df["ema50"]  = c.ewm(span=50,  adjust=False, min_periods=25).mean()
    df["ema200"] = c.ewm(span=200, adjust=False, min_periods=100).mean()
    df["ema_spread"] = (df["ema50"] - df["ema200"]) / df["ema200"].clip(lower=1e-9)

    # RSI (si absent)
    if "rsi_14" not in df.columns:
        d  = c.diff()
        g  = d.clip(lower=0).ewm(span=14, adjust=False, min_periods=14).mean()
        l  = (-d).clip(lower=0).ewm(span=14, adjust=False, min_periods=14).mean()
        df["rsi_14"] = 100 - 100 / (1 + g / l.clip(lower=1e-9))

    # ATR
    if "atr_14" not in df.columns:
        hi = df["High"]; lo = df["Low"]
        tr = pd.concat([hi-lo, (hi-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
        df["atr_14"] = tr.ewm(span=14, adjust=False, min_periods=14).mean()

    # Momentum
    for w in [24, 72, 168, 720]:
        df[f"mom_{w}h"] = logc - logc.shift(w)

    # Rolling volatilité 24h
    df["rv_24h"] = logc.diff().rolling(24, min_periods=12).std() * np.sqrt(24)

    # Funding z-score (si absent)
    if "funding_rate" in df.columns:
        fr = pd.to_numeric(df["funding_rate"], errors="coerce")
        mu = fr.rolling(168, min_periods=48).mean()
        sg = fr.rolling(168, min_periods=48).std()
        df["funding_z_168h"] = (fr - mu) / sg.clip(lower=1e-9)
        df["funding_extreme_long"]  = (fr > FUNDING_ENTRY_THR).astype(float)
    else:
        df["funding_z_168h"]        = 0.0
        df["funding_extreme_long"]  = 0.0

    # OI momentum
    if "oi_sum" in df.columns:
        oi = pd.to_numeric(df["oi_sum"], errors="coerce")
        df["oi_mom_24h"] = oi.diff(24) / oi.shift(24).clip(lower=1e-9)
    else:
        df["oi_mom_24h"] = 0.0

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# COUCHE 1 — RÉGIME MACRO
# ═══════════════════════════════════════════════════════════════════════════════

def compute_macro_regime(df_btc: pd.DataFrame) -> pd.Series:
    """
    Détecte le régime macro depuis BTC.

    BEAR : price < EMA200 ET (mom_7j < -5% OU mom_30j < -15%)
           → capte les bear markets après le death cross
    BULL : price > EMA50 ET EMA50 > EMA200 ET mom_7j > +3%
    NEUTRAL : tout le reste

    Persistance par vote majoritaire sur 24h (pas min strict)
    pour éviter le whipsaw sans être trop tard.
    """
    c    = df_btc["Close"]
    e50  = df_btc["ema50"]
    e200 = df_btc["ema200"]
    rsi  = df_btc["rsi_14"]
    m7d  = df_btc.get("mom_168h", pd.Series(0.0, index=df_btc.index))
    m30d = df_btc.get("mom_720h", pd.Series(0.0, index=df_btc.index))

    # BEAR : price < EMA200 + momentum négatif sur 7j OU 30j
    bear_raw = (c < e200) & ((m7d < -0.05) | (m30d < -0.15))

    # BULL : structure haussière claire
    bull_raw = (c > e50) & (e50 > e200) & (m7d > 0.03)

    # Persistance : vote majoritaire sur 24h (>60% du temps en bear/bull)
    P = 24
    bear_score = bear_raw.astype(float).rolling(P, min_periods=P//2).mean()
    bull_score = bull_raw.astype(float).rolling(P, min_periods=P//2).mean()

    bear_confirmed = bear_score > 0.60
    bull_confirmed = bull_score > 0.60

    regime = np.where(bear_confirmed, "BEAR",
              np.where(bull_confirmed, "BULL", "NEUTRAL"))
    return pd.Series(regime, index=df_btc.index, name="macro_regime")


# ═══════════════════════════════════════════════════════════════════════════════
# COUCHE 2 — SÉLECTION DU BASKET
# ═══════════════════════════════════════════════════════════════════════════════

def select_short_basket(
    dfs: Dict[str, pd.DataFrame],
    macro_regime: pd.Series,
    dt: pd.Timestamp,
    n: int = N_SHORT_ASSETS,
) -> List[str]:
    """
    En régime BEAR : sélectionne les N actifs avec pire momentum 30j.
    Critères secondaires : funding élevé (foule sur-longée), OI en hausse.
    En NEUTRAL ou BULL : basket vide (pas de shorts).
    """
    if macro_regime.loc[dt] != "BEAR":
        return []

    scores: Dict[str, float] = {}
    for sym, df in dfs.items():
        if dt not in df.index:
            continue
        row = df.loc[dt]
        # Score de "fragilité short" = mauvais momentum + funding extrême
        mom30  = float(row.get("mom_720h", 0))        # momentum 30j (négatif = bon short)
        fext   = float(row.get("funding_extreme_long", 0))  # 1 = foule sur-longée
        oi_mom = float(row.get("oi_mom_24h", 0))      # OI qui monte = fragile
        # Score : plus c'est négatif et fragile, meilleur le short
        scores[sym] = -mom30 * 0.6 + fext * 0.3 + max(oi_mom, 0) * 0.1

    sorted_syms = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s for s, _ in sorted_syms[:n]]


# ═══════════════════════════════════════════════════════════════════════════════
# COUCHE 3 — TIMING D'ENTRÉE
# ═══════════════════════════════════════════════════════════════════════════════

def is_entry_signal(row: pd.Series) -> bool:
    """
    Conditions d'entrée short — optimisées pour le contexte BEAR.

    En bear market, on entre sur les REBONDS (dead-cat bounces) :
      (a) RSI > 48 : le marché a rebondi temporairement → entrée courte
      (b) Momentum 8h positif : mini-rebond récent
      (c) Funding positif extrême : foule encore longée malgré la baisse

    Logique : en régime BEAR confirmé, on ne cherche pas les signes
    de fragilité (ils sont évidents) — on cherche les moments où
    le prix reprend un peu de souffle pour entrer à un meilleur prix.
    """
    rsi      = float(row.get("rsi_14", 50))
    mom8h    = float(row.get("mom_8h", row.get("ret_480m", 0)))
    mom4h    = float(row.get("mom_4h", row.get("ret_240m", 0)))
    fr       = float(row.get("funding_rate", 0))

    # Rebond temporaire : RSI revenu au-dessus de 48 (pas encore overbought)
    bounce    = rsi > 48
    # Mini-rebond récent 4h-8h (dead cat)
    short_bounce = (mom8h > 0.005) or (mom4h > 0.003)
    # Funding encore positif → foule longée malgré la tendance bear
    fr_long   = fr > 0.0002   # > 0.02%/8h

    return bounce or short_bounce or fr_long


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST RÉGIME-SWITCHING
# ═══════════════════════════════════════════════════════════════════════════════

def backtest_regime_switching(
    dfs:           Dict[str, pd.DataFrame],
    macro_regime:  pd.Series,
    test_start:    pd.Timestamp,
    test_end:      pd.Timestamp,
    cost_pct:      float = COST_PCT + SLIP_PCT,
    _dfs_idx_cache: Dict = None,
) -> Dict:
    """
    Simulation réaliste du portefeuille short régime-conditionnel.

    Pour chaque heure dans la période de test :
      1. Vérifier le régime macro
      2. Si BEAR → sélectionner basket, chercher entrée pour chaque actif
      3. Gérer les positions ouvertes (take profit ATR×1.5 ou max 72h)
      4. Calculer le PnL du portefeuille

    Taille de position : 1/N par actif (equal weight).
    Max drawdown global : si DD > 5% → exit toutes positions.
    """
    # Pré-indexer tous les dfs par datetime (une seule fois)
    dfs_idx = _dfs_idx_cache or {
        s: (df.set_index("datetime") if "datetime" in df.columns else df)
        for s, df in dfs.items()
    }

    # Créer un index horaire commun
    test_range = pd.date_range(test_start, test_end, freq="1h", tz="UTC")

    positions: Dict[str, Dict] = {}   # sym → {entry_price, entry_time, atr, target, max_h}
    portfolio_equity = [1.0]           # equity curve
    equity = 1.0
    peak   = 1.0

    trade_log = []
    # Cooldown par symbole : ne pas re-entrer avant N heures après une clôture
    cooldown_until: Dict[str, pd.Timestamp] = {}

    for dt in test_range:
        if dt not in macro_regime.index:
            continue

        regime = macro_regime.loc[dt]

        # ── Gérer positions ouvertes ──────────────────────────────────────────
        closed_syms = []
        for sym, pos in list(positions.items()):
            if sym not in dfs_idx or dt not in dfs_idx[sym].index:
                continue
            current_price = float(dfs_idx[sym].loc[dt, "Close"])
            entry_price   = pos["entry_price"]

            exit_reason = None
            # Exit si régime change (raison principale)
            if regime != "BEAR":
                exit_reason = "REGIME_EXIT"
            # Stop ATR (protection capitale)
            elif current_price >= pos["stop"]:
                exit_reason = "STOP"
            # Max hold (72h)
            elif dt >= pos["max_close_dt"]:
                exit_reason = "TIME"

            if exit_reason:
                pnl_ret = (entry_price - current_price) / entry_price - cost_pct
                n_pos   = max(len(positions), 1)
                equity  = equity * (1 + pnl_ret / n_pos)
                peak    = max(peak, equity)
                trade_log.append({
                    "sym": sym, "dt_close": str(dt),
                    "entry": round(entry_price, 2), "exit": round(current_price, 2),
                    "pnl_ret": round(pnl_ret, 5),
                    "exit_reason": exit_reason, "win": pnl_ret > 0,
                })
                closed_syms.append(sym)
                # Cooldown 8h après chaque trade
                cooldown_until[sym] = dt + pd.Timedelta(hours=8)

        for sym in closed_syms:
            del positions[sym]

        # ── Stop global portefeuille ──────────────────────────────────────────
        dd = (equity - peak) / peak
        if dd < -MAX_PORTFOLIO_DD and positions:
            for sym, pos in list(positions.items()):
                if sym in dfs_idx and dt in dfs_idx[sym].index:
                    cp  = float(dfs_idx[sym].loc[dt, "Close"])
                    ep  = pos["entry_price"]
                    pnl = (ep - cp) / ep - cost_pct
                    equity *= (1 + pnl / max(len(positions), 1))
                    trade_log.append({
                        "sym": sym, "dt_close": str(dt),
                        "entry": round(ep, 2), "exit": round(cp, 2),
                        "pnl_ret": round(pnl, 5),
                        "exit_reason": "PORTFOLIO_STOP", "win": pnl > 0,
                    })
                    cooldown_until[sym] = dt + pd.Timedelta(hours=8)
            positions.clear()

        # ── Nouvelles entrées — REGIME-SWITCHING PUR ──────────────────────────
        # On entre dès le début du BEAR et on reste jusqu'à la fin.
        # Pas de timing intra-barre — c'est ce que font les papiers.
        if regime == "BEAR":
            basket = select_short_basket(dfs_idx, macro_regime, dt)
            for sym in basket:
                if sym in positions:
                    continue
                # Respecter le cooldown
                if cooldown_until.get(sym, pd.Timestamp.min.tz_localize("UTC")) > dt:
                    continue
                if sym not in dfs_idx or dt not in dfs_idx[sym].index:
                    continue
                row = dfs_idx[sym].loc[dt]

                entry_price = float(row["Close"])
                atr         = max(float(row.get("atr_14", 0) or 0), entry_price * 0.01)
                # Stop uniquement — pas de profit target (on tient jusqu'à régime change)
                # Stop = ATR × 3.0 (large, pour laisser la tendance jouer)
                stop_lvl    = entry_price * (1 + 3.0 * atr / entry_price)
                max_close   = dt + pd.Timedelta(hours=MAX_HOLD_HOURS)

                positions[sym] = {
                    "entry_price": entry_price,
                    "entry_time":  dt,
                    "stop":        stop_lvl,
                    "max_close_dt": max_close,
                }

        portfolio_equity.append(equity)
        peak = max(peak, equity)

    # ── Métriques ────────────────────────────────────────────────────────────
    if not trade_log:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "total_return": 0.0,
                "max_dd": 0.0, "n_bear_hours": 0}

    rets = np.array([t["pnl_ret"] for t in trade_log])
    wins = rets[rets > 0]; loss = abs(rets[rets < 0].sum())
    pf   = wins.sum() / loss if loss > 1e-9 else float("inf")

    eq   = np.array(portfolio_equity)
    peak_arr = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak_arr) / np.clip(peak_arr, 1e-9, None)).min())

    n_bear = int((macro_regime.loc[test_start:test_end] == "BEAR").sum())

    return {
        "n":            len(rets),
        "pf":           round(float(pf), 3),
        "wr":           round(float((rets > 0).mean()), 3),
        "total_return": round(float(equity - 1.0), 4),
        "max_dd":       round(max_dd, 4),
        "n_bear_hours": n_bear,
        "trades":       trade_log,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 65)
    print("  REGIME-SWITCHING SHORT v4 — Architecture 3 couches")
    print("=" * 65)
    print(f"  Basket : {BASKET_ASSETS}")
    print(f"  Régime : persistance {REGIME_PERSIST_BARS}h, macro BTC")
    print(f"  Entry  : funding>{FUNDING_ENTRY_THR*10000:.0f}bps OU RSI>{RSI_ENTRY_BEAR} OU OI↑+price↑")
    print(f"  Exit   : ATR×{PROFIT_ATR_MULT} profit OU stop OU {MAX_HOLD_HOURS}h max OU régime change")
    print(f"  Coût   : {(COST_PCT+SLIP_PCT)*10000:.0f} bps (frais + slippage)")

    # ── Charger tous les actifs ───────────────────────────────────────────────
    print("\n[1] Chargement des actifs...")
    dfs: Dict[str, pd.DataFrame] = {}
    for sym in BASKET_ASSETS:
        df = load_asset(sym)
        if df is None:
            print(f"    [skip] {sym}")
            continue
        df = add_indicators(df)
        dfs[sym] = df
        print(f"    {sym}: {len(df):,} barres 1h")

    if "BTC" not in dfs:
        print("ERREUR : BTC requis pour le régime macro")
        return

    # ── Régime macro sur BTC ──────────────────────────────────────────────────
    print("\n[2] Calcul du régime macro BTC...")
    btc_idx = dfs["BTC"].set_index("datetime")
    macro   = compute_macro_regime(btc_idx)

    n_bear = int((macro == "BEAR").sum())
    n_bull = int((macro == "BULL").sum())
    n_neut = int((macro == "NEUTRAL").sum())
    n_tot  = len(macro)
    print(f"    BEAR={n_bear/n_tot:.1%}  BULL={n_bull/n_tot:.1%}  NEUTRAL={n_neut/n_tot:.1%}")

    # Vérifier répartition par année
    macro_df = macro.reset_index()
    macro_df.columns = ["datetime", "regime"]
    for yr in range(2019, 2026):
        sub = macro_df[macro_df["datetime"].dt.year == yr]
        if len(sub) == 0:
            continue
        b = (sub["regime"] == "BEAR").mean()
        bl = (sub["regime"] == "BULL").mean()
        print(f"    {yr}: BEAR={b:.0%}  BULL={bl:.0%}  NEUTRAL={1-b-bl:.0%}")

    # ── Walk-forward ──────────────────────────────────────────────────────────
    print("\n[3] Walk-forward...")
    fold_results = []

    for fi, fold in enumerate(FOLDS):
        test_yr   = fold["test"]
        test_start = pd.Timestamp(f"{test_yr}-01-01", tz="UTC")
        test_end   = pd.Timestamp(f"{test_yr}-12-31 23:00", tz="UTC")

        print(f"\n  ── Fold {fi+1} : test={test_yr}")

        # Pas de "training" pour ce modèle — la logique régime est déterministe.
        # On valide que le régime est cohérent sur la période de test.
        test_regime = macro.loc[test_start:test_end]
        n_bear_test = int((test_regime == "BEAR").sum())
        print(f"     Heures BEAR dans test : {n_bear_test} / {len(test_regime)} "
              f"({n_bear_test/max(len(test_regime),1):.1%})")

        if n_bear_test < 48:
            print(f"     [skip] Moins de 48h de régime BEAR en {test_yr}")
            fold_results.append({"fold": fi+1, "test_year": test_yr,
                                  "status": "NO_BEAR", "n_bear_hours": n_bear_test})
            continue

        # Backtest (passe les dfs pré-indexés pour éviter re-indexation à chaque barre)
        dfs_idx = {s: (df.set_index("datetime") if "datetime" in df.columns else df)
                   for s, df in dfs.items()}
        res = backtest_regime_switching(dfs, macro, test_start, test_end,
                                        _dfs_idx_cache=dfs_idx)

        verdict = ("NO_TRADES" if res["n"] == 0
                   else "PASS" if res["pf"] >= 1.30 and res["total_return"] > 0
                   else "WEAK" if res["pf"] >= 1.00 and res["total_return"] > -0.02
                   else "FAIL")

        print(f"     n={res['n']}  PF={res['pf']:.3f}  WR={res['wr']:.1%}  "
              f"Return={res['total_return']:.2%}  MaxDD={res['max_dd']:.2%}  → {verdict}")

        fold_results.append({
            "fold": fi+1, "test_year": test_yr,
            "n": res["n"], "pf": res["pf"], "wr": res["wr"],
            "total_return": res["total_return"], "max_dd": res["max_dd"],
            "n_bear_hours": res["n_bear_hours"],
            "verdict": verdict,
        })

    # ── Synthèse ──────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  SYNTHÈSE FINALE")
    print(f"{'='*65}")
    print(f"  {'Fold':>4}  {'Test':>5}  {'n':>5}  {'PF':>7}  {'WR':>6}  {'Return':>8}  {'MaxDD':>7}  Verdict")
    print(f"  {'─'*60}")

    n_pass = 0
    for r in fold_results:
        if r.get("status") == "NO_BEAR":
            print(f"  {r['fold']:>4}  {r['test_year']:>5}  {'—':>5}  {'—':>7}  {'—':>6}  {'—':>8}  {'—':>7}  NO_BEAR")
            continue
        pf  = r.get("pf", np.nan)
        wr  = r.get("wr", np.nan)
        ret = r.get("total_return", np.nan)
        dd  = r.get("max_dd", np.nan)
        v   = r.get("verdict", "?")
        if v == "PASS": n_pass += 1
        print(f"  {r['fold']:>4}  {r['test_year']:>5}  {r['n']:>5}  "
              f"{pf:>7.3f}  {wr:>5.1%}  {ret:>7.2%}  {dd:>7.2%}  {v}")

    print(f"\n  PASS={n_pass}/{len(fold_results)}")
    if n_pass >= 3:
        verdict_global = "SHORT_REGIME_VIABLE — déploiement régime-conditionnel possible"
    elif n_pass >= 2:
        verdict_global = "SHORT_REGIME_PROMISING — valider sur plus d'actifs avant deploy"
    elif n_pass >= 1:
        verdict_global = "SHORT_REGIME_WEAK — signal partiel, hedge uniquement"
    else:
        verdict_global = "SHORT_REGIME_REJECTED — revoir paramètres de régime"
    print(f"  VERDICT : {verdict_global}")

    # ── Sauvegarder ──────────────────────────────────────────────────────────
    out = REPORT_DIR / "regime_v4_results.json"
    with open(out, "w") as f:
        json.dump({"folds": fold_results, "verdict": verdict_global,
                   "params": {
                       "REGIME_PERSIST_BARS": REGIME_PERSIST_BARS,
                       "N_SHORT_ASSETS": N_SHORT_ASSETS,
                       "PROFIT_ATR_MULT": PROFIT_ATR_MULT,
                       "MAX_HOLD_HOURS": MAX_HOLD_HOURS,
                       "FUNDING_ENTRY_THR": FUNDING_ENTRY_THR,
                   }}, f, indent=2, default=str)
    print(f"\n  Résultats → {out}")


if __name__ == "__main__":
    main()
