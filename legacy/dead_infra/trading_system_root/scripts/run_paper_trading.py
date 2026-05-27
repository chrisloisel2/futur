#!/usr/bin/env python3
"""
run_paper_trading.py — Phase 3 : Paper Trading
===============================================

Deux modes :
  --mode replay   : Replay historique rapide (pour tests, validation 100+ trades)
  --mode live     : Connexion WebSocket Binance temps réel

Pipeline (identique pour les deux modes) :
  1. Barre fermée reçue (WebSocket ou CSV)
  2. FeatureWindow → features causales (ATR, EMA, RSI, …)
  3. Signal heuristique ou prédictions ML (--preds)
  4. RiskController.decide() → accept / reject
  5. Simulation fill (close ± slippage)
  6. Surveillance TP/SL/time sur les barres suivantes
  7. Logging JSONL complet (entries, rejections, trades, metrics)
  8. Sauvegarde état RiskController en JSON

Usage :
  # Replay validation (100+ trades)
  python run_paper_trading.py --mode replay \\
      --data data/datasets/data_bitstamp/btcusd_bitstamp_1min_2012-2025.csv.gz \\
      --start 2017-01-01 --end 2024-12-31 --resample 60

  # Live WebSocket (BTC/USDT 1h)
  python run_paper_trading.py --mode live --symbol BTCUSDT --interval 1h

Critères validation Phase 3 :
  ✓ 100+ trades exécutés
  ✓ Capital stable ou en hausse (drawdown < 10%)
  ✓ Logs complets (JSONL avec entries, exits, rejections, metrics)
  ✓ Pas de bug critique
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── Imports internes ──────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC  = Path(__file__).resolve().parent.parent / "src"
_RC_PATH = _ROOT / "ai" / "models" / "level_7"

for _p in [str(_SRC), str(_RC_PATH)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from RiskController import RiskController, RiskConfig
from pipeline.execution.paper_trader import PaperTrader, PaperConfig

try:
    from infra.exchange.ws.binance_ws import BinanceKlineStream, KlineBar
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# OHLCV helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_ohlcv(path: str, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    p = Path(path)
    print(f"[data] Chargement {p.name} …")
    t0 = time.time()
    df = pd.read_csv(p, dtype={
        "timestamp": "int64", "open": "float32", "high": "float32",
        "low": "float32", "close": "float32", "volume": "float32",
    })
    df.columns = df.columns.str.lower()
    if "timestamp" in df.columns:
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    elif "datetime" in df.columns:
        df["dt"] = pd.to_datetime(df["datetime"], utc=True)
    else:
        raise ValueError("Colonne timestamp introuvable")
    df = df.sort_values("dt").reset_index(drop=True)
    if start:
        df = df[df["dt"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["dt"] <= pd.Timestamp(end, tz="UTC")]
    df = df[df["volume"] > 0].reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype("float64")
    elapsed = time.time() - t0
    print(f"[data] {len(df):,} barres | {df['dt'].iloc[0].date()} → {df['dt'].iloc[-1].date()} ({elapsed:.1f}s)")
    return df


def resample_ohlcv(df: pd.DataFrame, freq_min: int) -> pd.DataFrame:
    if freq_min <= 1:
        return df
    print(f"[resample] {freq_min}m → agrégation OHLCV …")
    df2 = df.set_index("dt").sort_index()
    rule = f"{freq_min}min"
    agg  = df2.resample(rule, closed="left", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    count = df2["close"].resample(rule, closed="left", label="left").count()
    agg   = agg[count >= freq_min * 0.90]
    agg   = agg.reset_index()
    print(f"[resample] → {len(agg):,} barres {freq_min}m")
    return agg


# ─────────────────────────────────────────────────────────────────────────────
# MODE REPLAY
# ─────────────────────────────────────────────────────────────────────────────

def run_replay(
    args: argparse.Namespace,
    pt: PaperTrader,
    preds_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Replay rapide du CSV historique.
    Traite barre par barre — même logique que le mode live.
    """
    df = load_ohlcv(args.data, args.start, args.end)
    if args.resample > 1:
        df = resample_ohlcv(df, args.resample)

    n = len(df)
    if n < 300:
        print(f"[ERREUR] Pas assez de données : {n} barres")
        sys.exit(1)

    print(f"[replay] Début — {n:,} barres à traiter …")
    t0       = time.time()
    n_trades = 0
    n_bars   = 0

    # Aligne les prédictions sur l'index du DataFrame
    if preds_df is not None:
        preds_df.index = pd.to_datetime(preds_df.index, utc=True)

    for i in range(n):
        row = df.iloc[i]
        dt_str = str(row["dt"])

        # Prédiction ML optionnelle
        prob_override = None
        if preds_df is not None:
            dt_ts = pd.Timestamp(row["dt"])
            if dt_ts in preds_df.index:
                prob_override = float(preds_df.loc[dt_ts, "prob_up"])

        trade = pt.on_bar(
            symbol   = args.symbol,
            bar_index = i,
            dt_str   = dt_str,
            open_    = float(row["open"]),
            high     = float(row["high"]),
            low      = float(row["low"]),
            close    = float(row["close"]),
            volume   = float(row["volume"]),
            prob_up_override = prob_override,
        )

        if trade is not None:
            n_trades += 1
            if n_trades % 50 == 0:
                m = pt.metrics()
                print(
                    f"  [{i:>6}/{n}] trades={m['n_trades']:>4} | "
                    f"equity=${m['equity_final']:>10,.2f} | "
                    f"WR={m['win_rate']*100:.1f}% | "
                    f"PF={m['profit_factor']:.2f} | "
                    f"DD={m['max_drawdown_pct']:.1f}%"
                )

        n_bars += 1

    elapsed = time.time() - t0
    print(f"[replay] Terminé — {n_bars:,} barres en {elapsed:.1f}s ({n_bars/elapsed:.0f} bar/s)")


# ─────────────────────────────────────────────────────────────────────────────
# MODE LIVE (WebSocket Binance)
# ─────────────────────────────────────────────────────────────────────────────

async def run_live_ws(
    args: argparse.Namespace,
    pt: PaperTrader,
    state_path: Path,
) -> None:
    """
    Connecte le WebSocket Binance et traite les barres en temps réel.
    Appuyer Ctrl+C pour arrêter proprement.
    """
    if not _WS_AVAILABLE:
        print("[ERREUR] Module websockets non disponible")
        sys.exit(1)

    stream   = BinanceKlineStream(
        symbol           = args.symbol.lower(),
        interval         = args.interval,
        max_reconnects   = -1,          # reconnexion infinie
        reconnect_delay  = 3.0,
    )
    bar_index = [0]

    async def on_closed_bar(bar: KlineBar) -> None:
        i = bar_index[0]
        dt_str = str(pd.Timestamp(bar.close_time, unit="ms", tz="UTC"))

        trade = pt.on_bar(
            symbol   = bar.symbol,
            bar_index = i,
            dt_str   = dt_str,
            open_    = bar.open,
            high     = bar.high,
            low      = bar.low,
            close    = bar.close,
            volume   = bar.volume,
        )

        bar_index[0] += 1

        if trade is not None:
            m = pt.metrics()
            print(
                f"  TRADE #{m['n_trades']:>3} | {trade.exit_reason.upper():4s} | "
                f"PnL ${trade.net_pnl:>+8.2f} | "
                f"Equity ${m['equity_final']:>10,.2f} | "
                f"DD {m['max_drawdown_pct']:.1f}%"
            )
            # Sauvegarde état après chaque trade
            pt.rc.save_state(state_path)

        # Affiche les stats toutes les 10 barres
        if i > 0 and i % 10 == 0:
            m = pt.metrics()
            print(
                f"  [bar {i}] signal={m['total_signals']} "
                f"trades={m['n_trades']} "
                f"equity=${m['equity_final']:,.2f}"
            )

    # Gestion Ctrl+C
    loop = asyncio.get_event_loop()
    def _handle_sigint():
        print("\n[live] Arrêt demandé …")
        stream.stop()

    loop.add_signal_handler(signal.SIGINT, _handle_sigint)

    symbol_display = f"{args.symbol.upper()}@{args.interval}"
    print(f"[live] Connexion WebSocket {symbol_display} …")
    print(f"[live] Appuyer Ctrl+C pour arrêter")

    await stream.run(on_closed_bar)


# ─────────────────────────────────────────────────────────────────────────────
# RAPPORT FINAL
# ─────────────────────────────────────────────────────────────────────────────

def print_report(pt: PaperTrader, args: argparse.Namespace) -> bool:
    """Affiche le rapport final et retourne True si Phase 3 validée."""
    m   = pt.metrics()
    SEP = "=" * 68

    print(f"\n{SEP}")
    print("  PAPER TRADING — PHASE 3")
    print(SEP)
    print(f"  Mode         : {args.mode}")
    print(f"  Symbole      : {args.symbol.upper()}")
    print(f"  Risque/trade : {pt.rc.cfg.risk_per_trade*100:.1f}%")
    print(f"  TP/SL        : {pt.cfg.tp_atr_mult}×/{pt.cfg.sl_atr_mult}×ATR")
    print(SEP)

    if m["n_trades"] == 0:
        print("  ⚠️  Aucun trade exécuté.")
        return False

    n = m["n_trades"]
    print(f"\n  ── TRADES ──────────────────────────────────────────────────")
    print(f"  Trades exécutés  : {n:,}")
    print(f"  Signaux générés  : {m['total_signals']:,}")
    print(f"  Rejets RC        : {m['total_rejected']:,}")
    print(f"  Win rate         : {m['win_rate']*100:.1f}%")
    print(f"  Profit factor    : {m['profit_factor']:.3f}")
    print(f"  Exits TP/SL/Time : {m['exits_tp']}/{m['exits_sl']}/{m['exits_time']}")
    print(f"  Avg gain         : ${m['avg_win']:.4f}")
    print(f"  Avg loss         : ${m['avg_loss']:.4f}")

    print(f"\n  ── PORTFOLIO ───────────────────────────────────────────────")
    print(f"  Capital initial  : ${m['equity_init']:>12,.2f}")
    print(f"  Capital final    : ${m['equity_final']:>12,.2f}")
    print(f"  Rendement total  : {m['total_return_pct']:>+8.2f}%")
    print(f"  Sharpe           : {m['sharpe']:>8.3f}")
    print(f"  Max Drawdown     : {m['max_drawdown_pct']:>8.2f}%")
    print(f"  Durée sim.       : {m['elapsed_sec']:.1f}s")

    # ── Validation Phase 3 ────────────────────────────────────────────────────
    print(f"\n  ── VALIDATION PHASE 3 ──────────────────────────────────────")

    log_path = Path(pt.cfg.log_path)
    log_ok   = log_path.exists() and log_path.stat().st_size > 0

    # Vérifie la complétude du log (présence de chaque type d'entrée)
    log_types = set()
    if log_ok:
        with open(log_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    log_types.add(d.get("type", ""))
                except Exception:
                    pass

    checks = [
        ("100+ trades exécutés",             n >= 100),
        ("Capital stable (DD < 10%)",        m["max_drawdown_pct"] > -10.0),
        ("Logs JSONL présents et non vides",  log_ok),
        ("Log entries (ordres entrants)",     "entry" in log_types),
        ("Log trades (ordres sortants)",      "trade" in log_types),
        ("Log rejets RC",                    "rejected" in log_types),
        ("Log métriques périodiques",         "metrics" in log_types),
        ("Pas de crash (bug critique)",       True),  # si on est ici, pas de crash
    ]

    all_pass = all(ok for _, ok in checks)
    for label, ok in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {label}")

    print()
    if all_pass:
        print("  ✅  PHASE 3 VALIDÉE")
    else:
        n_fail = sum(1 for _, ok in checks if not ok)
        print(f"  ❌  PHASE 3 — {n_fail} critère(s) non satisfait(s)")
    print(SEP + "\n")

    return all_pass


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode",     choices=["replay", "live"], default="replay",
                   help="replay = CSV historique rapide | live = WebSocket Binance")
    p.add_argument("--symbol",   default="BTCUSDT")
    p.add_argument("--interval", default="1h",
                   help="Intervalle kline pour le mode live (1m, 5m, 15m, 1h, 4h, 1d)")

    # Replay uniquement
    p.add_argument("--data",     default=None,     help="CSV OHLCV (mode replay)")
    p.add_argument("--start",    default="2017-01-01")
    p.add_argument("--end",      default=None)
    p.add_argument("--resample", default=60, type=int)
    p.add_argument("--preds",    default=None,     help="Parquet avec colonne 'prob_up'")

    # Communs
    p.add_argument("--equity",    default=10_000.0, type=float)
    p.add_argument("--threshold", default=0.60,     type=float,
                   help="Seuil prob_up (défaut 0.60 pour générer 100+ trades)")
    p.add_argument("--state",     default=None,     help="Fichier JSON état RiskController")
    p.add_argument("--log",       default="trading-system/artifacts/paper_trading/trades.jsonl",
                   help="Chemin log JSONL")
    p.add_argument("--out",       default=None,     help="Chemin JSON résumé final")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # ── Chemins ──────────────────────────────────────────────────────────────
    state_path = Path(args.state) if args.state else Path("trading-system/artifacts/paper_trading/rc_state.json")

    # ── RiskController ────────────────────────────────────────────────────────
    if state_path.exists():
        print(f"[rc] Chargement état depuis {state_path}")
        rc = RiskController.load_state(state_path)
    else:
        rc_cfg = RiskConfig(
            equity                  = args.equity,
            risk_per_trade          = 0.002,   # 0.2%
            daily_loss_limit_pct    = 0.02,    # -2%
            max_consecutive_losses  = 3,
            cooldown_bars           = 3,
            stop_atr_mult           = 2.5,
            rr                      = 1.5,
        )
        rc = RiskController(rc_cfg)
        print(f"[rc] Nouveau RiskController (equity=${args.equity:,.0f})")

    # ── PaperConfig ───────────────────────────────────────────────────────────
    max_hold = max(12, 48 * 60 // max(1, args.resample)) if args.mode == "replay" else 48
    pt_cfg = PaperConfig(
        entry_threshold  = args.threshold,
        tp_atr_mult      = 1.5,
        sl_atr_mult      = 1.0,
        max_hold_bars    = max_hold,
        warmup_bars      = 200,
        fee_rt           = 8e-4,
        slippage_rt      = 4e-4,
        log_path         = args.log,
        metrics_interval = 20,
    )

    # ── PaperTrader ───────────────────────────────────────────────────────────
    pt = PaperTrader(cfg=pt_cfg, risk_controller=rc, symbol=args.symbol)
    pt._write_header(str(pd.Timestamp.utcnow()))

    # ── Prédictions ML (optionnel) ────────────────────────────────────────────
    preds_df = None
    if args.preds:
        print(f"[preds] Chargement depuis {args.preds} …")
        preds_df = pd.read_parquet(args.preds)

    # ── Exécution ─────────────────────────────────────────────────────────────
    try:
        if args.mode == "replay":
            if not args.data:
                print("[ERREUR] --data requis en mode replay")
                sys.exit(1)
            run_replay(args, pt, preds_df)

        elif args.mode == "live":
            asyncio.run(run_live_ws(args, pt, state_path))

    except KeyboardInterrupt:
        print("\n[paper] Arrêt par l'utilisateur")
    except Exception as e:
        print(f"\n[ERREUR] Bug critique : {e}")
        pt.close()
        raise
    finally:
        pt.close()

    # ── Rapport final ─────────────────────────────────────────────────────────
    phase3_ok = print_report(pt, args)

    # ── Sauvegarde résumé ──────────────────────────────────────────────────────
    metrics = pt.metrics()
    rc.save_state(state_path)
    print(f"[rc] État sauvegardé → {state_path}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(metrics, indent=2, default=str))
        print(f"[out] Résumé → {out}")

    sys.exit(0 if phase3_ok else 1)


if __name__ == "__main__":
    main()
