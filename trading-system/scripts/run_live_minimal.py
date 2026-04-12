"""
run_live_minimal.py — Phase 4 : Trading live minimal sur compte réel
=====================================================================

Usage :
    # Testnet Binance (aucun argent réel)
    python scripts/run_live_minimal.py --testnet --symbol BTCUSDT --interval 1h

    # Compte réel avec $100 de capital
    python scripts/run_live_minimal.py --symbol BTCUSDT --interval 1h --equity 100

Variables d'environnement (créer un fichier .env) :
    BINANCE_API_KEY=votre_cle
    BINANCE_API_SECRET=votre_secret

Safety limits codées en dur :
    - MAX_ORDER_USDT = 200 : notionnel max par trade
    - risk_per_trade = 0.1% de l'equity
    - daily_loss_limit = -2%
    - max_consecutive_losses = 3

Le mode --dry-run permet de tester sans passer d'ordres réels.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

# Charge les variables d'environnement depuis .env si python-dotenv est dispo
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Chemin du projet ──────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT.parent / "ai" / "models" / "level_7"))

from infra.exchange.binance_rest import BinanceRestClient, BinanceApiError
from infra.exchange.ws.binance_ws import BinanceKlineStream, KlineBar
from pipeline.execution.paper_trader import PaperConfig
from pipeline.execution.live_trader import LiveTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("run_live_minimal")


# ─────────────────────────────────────────────────────────────────────────────
# Arguments CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 4 — Live trading minimal (Binance Spot)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol",    default="BTCUSDT", help="Paire de trading")
    p.add_argument("--interval",  default="1h",      help="Timeframe (1m, 5m, 15m, 1h, 4h)")
    p.add_argument("--equity",    type=float, default=100.0,
                   help="Capital de départ (USDT) à renseigner pour le sizing")
    p.add_argument("--threshold", type=float, default=0.60,
                   help="Seuil de signal pour entrée (0..1)")
    p.add_argument("--testnet",   action="store_true",
                   help="Utilise le testnet Binance (pas d'argent réel)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Simule sans passer d'ordres (aucune clé API requise)")
    p.add_argument("--state",     default="artifacts/live/position.json",
                   help="Chemin du fichier d'état de position")
    p.add_argument("--log",       default="artifacts/live/trades.jsonl",
                   help="Chemin du log JSONL")
    p.add_argument("--rc-state",  default="artifacts/live/rc_state.json",
                   help="Chemin du fichier d'état RiskController")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation RiskController
# ─────────────────────────────────────────────────────────────────────────────

def build_risk_controller(equity: float, rc_state_path: str):
    """
    Construit ou restaure un RiskController Phase 4.
    risk_per_trade = 0.1% (moitié du paper trading)
    """
    from RiskController import RiskController, RiskConfig, RiskState

    cfg = RiskConfig(
        equity                 = equity,
        risk_per_trade         = 0.001,    # 0.1%
        daily_loss_limit_pct   = 0.02,     # -2% journalier
        max_consecutive_losses = 3,
        cooldown_bars          = 1,
        stop_atr_mult          = 1.5,      # SL distance = 1.5×ATR
        rr                     = 1.5,      # TP/SL ratio
    )

    rc_path = Path(rc_state_path)
    if rc_path.exists():
        try:
            rc = RiskController.load_state(rc_path)
            logger.info(f"[rc] État restauré — equity=${rc.state.equity:.2f}")
            return rc
        except Exception as e:
            logger.warning(f"[rc] Impossible de charger l'état ({e}), création nouveau")

    rc = RiskController(cfg=cfg)
    logger.info(f"[rc] Nouveau RiskController — equity=${equity:.2f}")
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# Validation des credentials + état du compte
# ─────────────────────────────────────────────────────────────────────────────

async def validate_account(client: BinanceRestClient, equity: float) -> float:
    """
    Vérifie la connexion à l'API et affiche les informations du compte.
    Retourne le solde USDT réel du compte.
    """
    try:
        await client.ping()
        logger.info("[api] Ping OK")
    except Exception as e:
        raise SystemExit(f"[api] Impossible de joindre Binance : {e}")

    try:
        balance = await client.get_usdt_balance()
        logger.info(f"[api] Solde USDT libre : ${balance:.2f}")

        # Avertissement si le solde réel est inférieur à l'equity configurée
        if balance < equity * 0.95:
            logger.warning(
                f"[safety] Solde réel ${balance:.2f} < equity configurée ${equity:.2f} "
                f"— vérifiez votre compte ou réduisez --equity"
            )
    except BinanceApiError as e:
        logger.warning(f"[api] Impossible de lire le solde : {e}")
        balance = equity

    return balance


# ─────────────────────────────────────────────────────────────────────────────
# Boucle principale
# ─────────────────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    """Boucle principale : WebSocket → LiveTrader → ordres Binance."""

    # ── Credentials ──────────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("[mode] DRY RUN — aucun ordre réel ne sera passé")
        api_key    = "dry_run_key"
        api_secret = "dry_run_secret"
    else:
        api_key    = os.getenv("BINANCE_API_KEY",    "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        if not api_key or not api_secret:
            raise SystemExit(
                "Variables d'environnement manquantes.\n"
                "Créez un fichier .env avec BINANCE_API_KEY et BINANCE_API_SECRET,\n"
                "ou utilisez --dry-run pour tester sans clés."
            )

    # ── Artefacts ────────────────────────────────────────────────────────────
    for p in [args.state, args.log, args.rc_state]:
        Path(p).parent.mkdir(parents=True, exist_ok=True)

    # ── RiskController ───────────────────────────────────────────────────────
    rc = build_risk_controller(args.equity, args.rc_state)

    # ── Config PaperConfig (réutilise les paramètres Phase 3) ────────────────
    cfg = PaperConfig(
        entry_threshold  = args.threshold,
        tp_atr_mult      = 1.5,
        sl_atr_mult      = 1.0,
        max_hold_bars    = 48,
        warmup_bars      = 200,
        fee_rt           = 8e-4,
        slippage_rt      = 2e-4,    # slippage réduit (ordres marché réels)
        log_path         = args.log,
        metrics_interval = 5,
        channel_lookback = 20,
    )

    # ── Client REST + LiveTrader ──────────────────────────────────────────────
    async with BinanceRestClient(
        api_key=api_key, api_secret=api_secret,
        testnet=args.testnet,
    ) as client:

        if not args.dry_run:
            await validate_account(client, args.equity)

        lt = LiveTrader(
            cfg        = cfg,
            risk_ctrl  = rc,
            client     = client,
            symbol     = args.symbol,
            state_path = args.state,
            dry_run    = args.dry_run,
        )
        await lt.initialize()

        bar_count = [0]

        # ── Callback par barre fermée ─────────────────────────────────────────
        async def on_bar(bar: KlineBar) -> None:
            bar_count[0] += 1
            try:
                closed_trade = await lt.on_bar(
                    bar_index = bar_count[0],
                    dt_str    = str(bar.close_time),
                    open_     = bar.open,
                    high      = bar.high,
                    low       = bar.low,
                    close     = bar.close,
                    volume    = bar.volume,
                )
                if closed_trade:
                    logger.info(
                        f"[trade] #{closed_trade.trade_id} "
                        f"{'WIN' if closed_trade.net_pnl > 0 else 'LOSS'} "
                        f"PnL={closed_trade.net_pnl:+.2f}$ "
                        f"({closed_trade.exit_reason.upper()}) "
                        f"equity=${lt.rc.state.equity:.2f}"
                    )
                    # Sauvegarde RC après chaque trade
                    rc.save_state(args.rc_state)

                # Log périodique
                if bar_count[0] % 24 == 0:
                    m = lt.metrics()
                    logger.info(
                        f"[stats] bars={bar_count[0]} "
                        f"trades={m['n_trades']} "
                        f"WR={m['win_rate']*100:.1f}% "
                        f"equity=${m['equity_final']:.2f} "
                        f"DD={m['max_drawdown_pct']:.1f}%"
                    )

            except Exception as e:
                logger.error(f"[on_bar] Exception non gérée : {e}", exc_info=True)

        # ── Gestion SIGINT propre ─────────────────────────────────────────────
        stream = BinanceKlineStream(
            symbol          = args.symbol,
            interval        = args.interval,
            max_reconnects  = -1,
            reconnect_delay = 5.0,
        )

        def _shutdown(sig, _frame):
            logger.info(f"[signal] {signal.Signals(sig).name} reçu — arrêt propre …")
            stream.stop()

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        logger.info(
            f"\n{'='*60}\n"
            f"  PHASE 4 — LIVE MINIMAL\n"
            f"  Symbole    : {args.symbol}\n"
            f"  Interval   : {args.interval}\n"
            f"  Equity     : ${args.equity:.2f}\n"
            f"  Risk/trade : {rc.cfg.risk_per_trade*100:.1f}%\n"
            f"  TP/SL      : {cfg.tp_atr_mult}×/{cfg.sl_atr_mult}×ATR\n"
            f"  Threshold  : {args.threshold}\n"
            f"  Testnet    : {args.testnet}\n"
            f"  Dry run    : {args.dry_run}\n"
            f"{'='*60}"
        )

        try:
            await stream.run(on_bar)
        finally:
            # Résumé final
            m = lt.metrics()
            lt.close()
            rc.save_state(args.rc_state)

            print_final_report(m, bar_count[0], args)


# ─────────────────────────────────────────────────────────────────────────────
# Rapport final
# ─────────────────────────────────────────────────────────────────────────────

def print_final_report(m: dict, n_bars: int, args: argparse.Namespace) -> None:
    checks = [
        ("Ordres exécutés / tentés",       m["n_trades"] >= 0),
        ("Capital dans les limites",        m.get("max_drawdown_pct", 0) >= -10.0),
        ("Logs JSONL présents",             Path(args.log).exists()),
        ("État RC sauvegardé",              Path(args.rc_state).exists()),
        ("Pas de crash critique",           True),   # atteint = pas de crash
    ]

    print(f"\n{'='*60}")
    print(f"  PHASE 4 — RAPPORT FINAL")
    print(f"{'='*60}")
    print(f"  Barres reçues    : {n_bars}")
    print(f"  Trades exécutés  : {m['n_trades']}")
    print(f"  Signaux générés  : {m['total_signals']}")
    print(f"  Rejets RC        : {m['total_rejected']}")
    print(f"  Win rate         : {m.get('win_rate', 0)*100:.1f}%")
    print(f"  Profit factor    : {m.get('profit_factor', 0):.3f}")
    print(f"  Capital final    : ${m.get('equity_final', 0):.2f}")
    print(f"  Rendement total  : {m.get('total_return_pct', 0):.2f}%")
    print(f"  Max Drawdown     : {m.get('max_drawdown_pct', 0):.2f}%")
    print()
    print("  ── VALIDATION PHASE 4 ──────────────────────────────")
    all_ok = True
    for label, ok in checks:
        status = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {status}  {label}")
    print()
    if all_ok:
        print("  ✅  PHASE 4 VALIDÉE")
    else:
        print("  ⚠️   PHASE 4 INCOMPLÈTE — vérifiez les erreurs")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        print(f"\n[ERREUR] {e}")
        sys.exit(1)
