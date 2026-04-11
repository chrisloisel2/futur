"""
live_trader.py — Couche d'exécution réelle (Phase 4)
=====================================================

Hérite de la logique de signal / features de PaperTrader mais envoie
de vrais ordres via BinanceRestClient.

Différences clés vs PaperTrader :
  - Fill au marché via REST (pas simulation au close)
  - TP/SL gérés par un ordre OCO après l'entrée
  - Surveillance active : vérifie l'état de l'OCO toutes les N secondes
  - Safety limits codées en dur :
      * MAX_ORDER_USDT = 200      → notionnel max par ordre
      * MAX_DAILY_LOSS_PCT = 0.02 → -2% = arrêt de la journée
      * MAX_POSITION = 1          → 1 trade actif max
  - État persisté en JSON (survit aux redémarrages)

Flux par barre (WebSocket kline fermée) :
  1. FeatureWindow.update(ohlcv)
  2. Si position ouverte → check OCO status (tp/sl hit ?)
  3. reset_day si nouveau jour
  4. Si pas de position → signal → RiskController.decide() → market order
  5. Si BUY → oco_order(tp, sl) → enregistre l'état
  6. Log JSONL complet
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Dict, List

from pipeline.execution.paper_trader import (
    FeatureWindow,
    PaperConfig,
    TradeRecord,
)
from infra.exchange.binance_rest import (
    BinanceRestClient,
    BinanceApiError,
    OrderFill,
    OcoResult,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Safety constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_ORDER_USDT      = 200.0   # notionnel absolu max (capital Phase 4)
MAX_POSITION        = 1       # positions simultanées max
OCO_CHECK_INTERVAL  = 30.0    # secondes entre deux vérifs d'OCO
MIN_ATR_PCT         = 0.001   # filtre volatilité minimum


# ─────────────────────────────────────────────────────────────────────────────
# État de position ouverte (persisté en JSON)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LivePosition:
    """Position ouverte avec son OCO associé."""
    symbol         : str
    entry_bar      : int
    entry_time     : str          # ISO8601
    entry_px       : float
    qty            : float
    tp_px          : float
    sl_px          : float
    sl_stop_px     : float
    oco_list_id    : int          # 0 = pas d'OCO (ex. testnet sans OCO)
    entry_order_id : int
    risk_budget    : float
    atr            : float
    signal_prob    : float
    edge_final     : float

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "LivePosition":
        return cls(**d)


# ─────────────────────────────────────────────────────────────────────────────
# LiveTrader
# ─────────────────────────────────────────────────────────────────────────────

class LiveTrader:
    """
    Moteur de trading live.

    Paramètres :
        cfg          : PaperConfig (thresholds, ATR, TP/SL multipliers, log_path)
        risk_ctrl    : RiskController (sizing, daily stop, consecutive losses)
        client       : BinanceRestClient (async)
        symbol       : "BTCUSDT"
        state_path   : chemin JSON pour persister la position ouverte
        dry_run      : si True, affiche les ordres sans les envoyer
    """

    def __init__(
        self,
        cfg        : PaperConfig,
        risk_ctrl  : Any,
        client     : BinanceRestClient,
        symbol     : str  = "BTCUSDT",
        state_path : str  = "artifacts/live/position.json",
        dry_run    : bool = False,
    ):
        self.cfg        = cfg
        self.rc         = risk_ctrl
        self.client     = client
        self.symbol     = symbol.upper()
        self.state_path = Path(state_path)
        self.dry_run    = dry_run

        self._fw         = FeatureWindow(cfg)
        self._position  : Optional[LivePosition] = None
        self._trade_id  = 0
        self._trades    : List[TradeRecord] = []
        self._bar_count = 0
        self._start_time = time.time()
        self._step_size  = 1e-6   # rempli au démarrage via get_step_size()

        self.total_signals  = 0
        self.total_rejected = 0

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_log()
        self._load_position()

    # ── Log ───────────────────────────────────────────────────────────────────

    def _init_log(self) -> None:
        log_path = Path(self.cfg.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(log_path, "a", buffering=1)

    def _log(self, record: Dict) -> None:
        self._log_file.write(json.dumps(record, default=str) + "\n")
        logger.info(record)

    def close(self) -> None:
        self._log({"type": "session_end", "summary": self.metrics()})
        self._log_file.close()

    # ── Persistance de position ───────────────────────────────────────────────

    def _save_position(self) -> None:
        data = self._position.to_dict() if self._position else None
        self.state_path.write_text(json.dumps(data, indent=2))

    def _load_position(self) -> None:
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text())
            if raw is not None:
                self._position = LivePosition.from_dict(raw)
                logger.info(f"[live] Position restaurée : {self._position.symbol} "
                            f"entry={self._position.entry_px} qty={self._position.qty}")

    # ── Initialisation async (step size) ─────────────────────────────────────

    async def initialize(self) -> None:
        """À appeler une fois avant la boucle principale (récupère step_size)."""
        balance = self.rc.state.equity   # fallback

        if self.dry_run:
            # Dry-run : pas d'appel API, on utilise un step_size par défaut
            self._step_size = 1e-5   # BTCUSDT standard
            logger.info(f"[live] DRY RUN — step_size={self._step_size} (défaut)")
        else:
            try:
                self._step_size = await self.client.get_step_size(self.symbol)
                logger.info(f"[live] step_size {self.symbol} = {self._step_size}")
            except Exception as e:
                logger.warning(f"[live] Impossible de récupérer step_size : {e}")

            try:
                balance = await self.client.get_usdt_balance()
                logger.info(f"[live] Solde USDT : ${balance:.2f}")
            except Exception as e:
                logger.warning(f"[live] Impossible de lire le solde : {e}")

        self._log({
            "type"   : "session_start",
            "symbol" : self.symbol,
            "balance": round(balance, 2),
            "equity" : round(self.rc.state.equity, 2),
            "dry_run": self.dry_run,
        })

    # ── Boucle principale ─────────────────────────────────────────────────────

    async def on_bar(
        self,
        bar_index: int,
        dt_str   : str,
        open_    : float,
        high     : float,
        low      : float,
        close    : float,
        volume   : float,
        prob_up_override: Optional[float] = None,
    ) -> Optional[TradeRecord]:
        """Traite une barre fermée. Retourne un TradeRecord si un trade est clôturé."""

        # 1. Feature window
        self._fw.update(open_, high, low, close, volume)
        self._bar_count += 1

        # 2. Vérification OCO (position ouverte ?)
        closed = None
        if self._position is not None:
            closed = await self._check_oco_status(bar_index, dt_str, high, low, close)

        # 3. Reset journalier
        day = dt_str[:10]
        if day != self.rc.state.current_day:
            self.rc.reset_day(day_str=day)

        # 4. Entrée potentielle
        if self._position is None and self._fw.ready:
            await self._try_enter(bar_index, dt_str, close, prob_up_override)

        return closed

    # ── Vérification OCO ─────────────────────────────────────────────────────

    async def _check_oco_status(
        self,
        bar_index: int,
        dt_str   : str,
        high     : float,
        low      : float,
        close    : float,
    ) -> Optional[TradeRecord]:
        """
        Vérifie si l'OCO a été touché (TP ou SL rempli).
        Appelé à chaque barre fermée.
        """
        pos = self._position
        if pos is None:
            return None

        # Time-stop : forcer la sortie au marché si max_hold_bars dépassé
        bars_held = bar_index - pos.entry_bar
        if bars_held >= self.cfg.max_hold_bars:
            return await self._force_exit(bar_index, dt_str, close, "time")

        # Si pas d'OCO (dry_run ou testnet), vérification manuelle TP/SL
        if pos.oco_list_id == 0:
            if low <= pos.sl_px:
                return await self._force_exit(bar_index, dt_str, pos.sl_px, "sl")
            if high >= pos.tp_px:
                return await self._force_exit(bar_index, dt_str, pos.tp_px, "tp")
            return None

        # Vérification REST de l'OCO
        try:
            oco_data = await self.client.get_oco_order(pos.oco_list_id)
            list_status = oco_data.get("listStatusType", "")
            list_order_status = oco_data.get("listOrderStatus", "")

            if list_status in ("ALL_DONE",) or list_order_status in ("ALL_DONE",):
                # Détermine lequel a été rempli
                exit_px, exit_reason = self._resolve_oco_exit(oco_data, pos)
                return self._record_exit(bar_index, dt_str, exit_px, exit_reason, pos)
        except BinanceApiError as e:
            logger.warning(f"[live] Erreur OCO check : {e}")

        return None

    def _resolve_oco_exit(self, oco_data: Dict, pos: "LivePosition") -> tuple:
        """Détermine le prix et la raison de sortie d'un OCO terminé."""
        for report in oco_data.get("orderReports", []):
            if report.get("status") == "FILLED":
                exit_px = float(report.get("price", 0) or report.get("stopPrice", 0))
                order_type = report.get("type", "")
                if "STOP" in order_type:
                    return exit_px or pos.sl_px, "sl"
                else:
                    return exit_px or pos.tp_px, "tp"
        return pos.entry_px, "unknown"

    async def _force_exit(
        self,
        bar_index   : int,
        dt_str      : str,
        target_price: float,
        reason      : str,
    ) -> Optional[TradeRecord]:
        """
        Sortie forcée au marché (time-stop ou urgence).
        Annule l'OCO ouvert si besoin.
        """
        pos = self._position
        if pos is None:
            return None

        # Annuler l'OCO avant de vendre au marché
        if pos.oco_list_id and not self.dry_run:
            try:
                await self.client.cancel_oco(self.symbol, pos.oco_list_id)
                logger.info(f"[live] OCO {pos.oco_list_id} annulé (sortie forcée)")
            except BinanceApiError as e:
                logger.warning(f"[live] Impossible d'annuler OCO : {e}")

        # Ordre de vente au marché
        exit_px = target_price
        if not self.dry_run:
            try:
                fill = await self.client.market_order(
                    self.symbol, "SELL", pos.qty,
                    client_oid=f"exit_{self._trade_id + 1}",
                )
                exit_px = fill.avg_price if fill.avg_price > 0 else target_price
            except BinanceApiError as e:
                logger.error(f"[live] Erreur sortie marché : {e}")
        else:
            logger.info(f"[DRY] Vente {pos.qty} {self.symbol} ~ {exit_px:.2f} ({reason})")

        return self._record_exit(bar_index, dt_str, exit_px, reason, pos)

    def _record_exit(
        self,
        bar_index  : int,
        dt_str     : str,
        exit_px    : float,
        exit_reason: str,
        pos        : "LivePosition",
    ) -> TradeRecord:
        """Crée un TradeRecord, met à jour RC et efface la position."""
        rt        = self.cfg.fee_rt + self.cfg.slippage_rt
        gross_pnl = (exit_px - pos.entry_px) * pos.qty
        cost      = pos.entry_px * pos.qty * rt
        net_pnl   = gross_pnl - cost
        notional  = pos.entry_px * pos.qty

        self.rc.on_fill_pnl(net_pnl)
        self._position = None
        self._save_position()

        self._trade_id += 1
        rec = TradeRecord(
            trade_id    = self._trade_id,
            symbol      = self.symbol,
            direction   = "BUY",
            entry_bar   = pos.entry_bar,
            exit_bar    = bar_index,
            dt_entry    = pos.entry_time,
            dt_exit     = dt_str,
            entry_px    = round(pos.entry_px, 4),
            exit_px     = round(float(exit_px), 4),
            tp_px       = round(pos.tp_px, 4),
            sl_px       = round(pos.sl_px, 4),
            qty         = round(pos.qty, 8),
            notional    = round(notional, 4),
            gross_pnl   = round(gross_pnl, 4),
            cost        = round(cost, 4),
            net_pnl     = round(net_pnl, 4),
            exit_reason = exit_reason,
            hold_bars   = bar_index - pos.entry_bar,
            prob_up     = round(pos.signal_prob, 4),
            edge_final  = round(pos.edge_final, 4),
            equity      = round(self.rc.state.equity, 2),
            day_pnl     = round(self.rc.state.day_pnl, 4),
            consec_loss = self.rc.state.consecutive_losses,
            rc_reason   = "fill",
        )
        self._trades.append(rec)

        log_entry = rec.to_dict()
        log_entry["type"] = "trade"
        self._log(log_entry)

        if len(self._trades) % self.cfg.metrics_interval == 0:
            self._log({"type": "metrics", **self.metrics()})

        return rec

    # ── Entrée ────────────────────────────────────────────────────────────────

    async def _try_enter(
        self,
        bar_index       : int,
        dt_str          : str,
        close           : float,
        prob_up_override: Optional[float] = None,
    ) -> bool:
        """
        Décide si on entre, place l'ordre marché et l'OCO TP/SL.
        Retourne True si un ordre a été placé.
        """
        # Signal
        prob_up = (float(prob_up_override)
                   if prob_up_override is not None
                   else self._fw.signal())
        self.total_signals += 1

        if prob_up <= self.cfg.entry_threshold:
            return False

        # Filtre volatilité
        if self._fw.atr_pct < MIN_ATR_PCT:
            return False

        # RiskController
        edge_final = prob_up - 0.5
        scale      = min(1.0, edge_final / 0.2)

        decision = self.rc.decide(
            price      = close,
            edge_final = edge_final,
            scale      = scale,
            bar_index  = bar_index,
            features   = self._fw.features_dict(),
        )

        if decision["action"] == "HOLD":
            self.total_rejected += 1
            self._log({
                "type"   : "rejected",
                "bar"    : bar_index,
                "dt"     : dt_str,
                "reason" : decision["reason"],
                "prob_up": round(prob_up, 4),
                "equity" : round(self.rc.state.equity, 2),
            })
            return False

        # Calcul qty (cap $200 notionnel)
        atr_i      = self._fw.atr
        qty_raw    = decision["qty"]
        notional   = qty_raw * close
        if notional > MAX_ORDER_USDT:
            qty_raw  = MAX_ORDER_USDT / close
        qty = BinanceRestClient.round_qty(qty_raw, self._step_size)

        if qty <= 0 or qty * close < 10.0:
            logger.warning(f"[live] Quantité trop faible ({qty:.8f}), trade ignoré")
            return False

        tp_px       = close + self.cfg.tp_atr_mult * atr_i
        sl_px       = close - self.cfg.sl_atr_mult * atr_i
        sl_stop_px  = sl_px * 1.001   # stop trigger légèrement au-dessus du limit

        # ── Ordre d'entrée au marché ──────────────────────────────────────────
        entry_px   = close  # fallback
        entry_oid  = 0
        if not self.dry_run:
            try:
                fill = await self.client.market_order(
                    self.symbol, "BUY", qty,
                    client_oid=f"entry_{self._trade_id + 1}",
                )
                entry_px  = fill.avg_price if fill.avg_price > 0 else close
                entry_oid = fill.order_id
                logger.info(f"[live] Entrée remplie @ {entry_px:.2f} qty={fill.qty_filled:.6f}")
            except BinanceApiError as e:
                logger.error(f"[live] Erreur ordre entrée : {e}")
                self.rc.state.day_trades -= 1   # rollback compteur
                return False
        else:
            logger.info(f"[DRY] Achat {qty:.6f} {self.symbol} ~ {close:.2f}")

        # Recalcul TP/SL sur entry_px réel
        tp_px      = entry_px + self.cfg.tp_atr_mult * atr_i
        sl_px      = entry_px - self.cfg.sl_atr_mult * atr_i
        sl_stop_px = sl_px * 1.001

        # ── Ordre OCO (TP + SL) ───────────────────────────────────────────────
        oco_list_id = 0
        if not self.dry_run:
            try:
                oco = await self.client.oco_order(
                    symbol        = self.symbol,
                    side          = "SELL",
                    qty           = qty,
                    tp_price      = round(tp_px, 2),
                    sl_stop_price = round(sl_stop_px, 2),
                    sl_limit_price= round(sl_px, 2),
                    client_oid    = f"oco_{self._trade_id + 1}",
                )
                oco_list_id = oco.order_list_id
                logger.info(f"[live] OCO placé — TP={tp_px:.2f} SL={sl_px:.2f} id={oco_list_id}")
            except BinanceApiError as e:
                logger.error(f"[live] Erreur OCO : {e} — sortie manuelle nécessaire !")
        else:
            logger.info(f"[DRY] OCO SELL {qty:.6f} TP={tp_px:.2f} SL={sl_px:.2f}")

        # ── Enregistre la position ────────────────────────────────────────────
        self._position = LivePosition(
            symbol         = self.symbol,
            entry_bar      = bar_index,
            entry_time     = dt_str,
            entry_px       = entry_px,
            qty            = qty,
            tp_px          = round(tp_px, 4),
            sl_px          = round(sl_px, 4),
            sl_stop_px     = round(sl_stop_px, 4),
            oco_list_id    = oco_list_id,
            entry_order_id = entry_oid,
            risk_budget    = decision["risk_budget"],
            atr            = atr_i,
            signal_prob    = prob_up,
            edge_final     = edge_final,
        )
        self._save_position()

        self._log({
            "type"       : "entry",
            "bar"        : bar_index,
            "dt"         : dt_str,
            "direction"  : "BUY",
            "prob_up"    : round(prob_up, 4),
            "entry_px"   : round(entry_px, 4),
            "tp_px"      : round(tp_px, 4),
            "sl_px"      : round(sl_px, 4),
            "qty"        : round(qty, 8),
            "risk_budget": round(decision["risk_budget"], 4),
            "oco_id"     : oco_list_id,
            "equity"     : round(self.rc.state.equity, 2),
            "dry_run"    : self.dry_run,
        })
        return True

    # ── Métriques ─────────────────────────────────────────────────────────────

    def metrics(self) -> Dict:
        import numpy as np
        trades = self._trades
        n = len(trades)
        if n == 0:
            return {
                "n_trades"      : 0,
                "equity_init"   : self.rc.cfg.equity,
                "equity_final"  : round(self.rc.state.equity, 2),
                "total_pnl"     : 0.0,
                "win_rate"      : 0.0,
                "profit_factor" : 0.0,
                "sharpe"        : 0.0,
                "max_drawdown_pct": 0.0,
                "total_signals" : self.total_signals,
                "total_rejected": self.total_rejected,
            }

        pnls   = [t.net_pnl for t in trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        equity_init  = self.rc.cfg.equity
        equity_final = self.rc.state.equity
        total_pnl    = equity_final - equity_init

        win_rate = len(wins) / n
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) < 0 else float("inf")

        eq_series = [equity_init]
        for t in trades:
            eq_series.append(eq_series[-1] + t.net_pnl)
        eq_arr = np.array(eq_series)
        peak   = np.maximum.accumulate(eq_arr)
        dd_arr = (eq_arr - peak) / np.maximum(peak, 1e-9)
        max_dd = float(dd_arr.min())

        pnl_arr = np.array(pnls)
        sharpe  = 0.0
        if len(pnl_arr) > 1:
            mu, std = pnl_arr.mean(), pnl_arr.std()
            if std > 0:
                sharpe = (mu / std) * np.sqrt(252)

        return {
            "n_trades"        : n,
            "equity_init"     : equity_init,
            "equity_final"    : round(equity_final, 2),
            "total_pnl"       : round(total_pnl, 4),
            "total_return_pct": round(total_pnl / max(equity_init, 1) * 100, 2),
            "win_rate"        : round(win_rate, 3),
            "profit_factor"   : round(pf, 3),
            "sharpe"          : round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "exits_tp"        : sum(1 for t in trades if t.exit_reason == "tp"),
            "exits_sl"        : sum(1 for t in trades if t.exit_reason == "sl"),
            "exits_time"      : sum(1 for t in trades if t.exit_reason == "time"),
            "total_signals"   : self.total_signals,
            "total_rejected"  : self.total_rejected,
            "elapsed_sec"     : round(time.time() - self._start_time, 1),
        }

    @property
    def trades(self) -> List[TradeRecord]:
        return list(self._trades)

    @property
    def has_position(self) -> bool:
        return self._position is not None
