"""
paper_trader.py — Moteur de Paper Trading
==========================================

Simule l'exécution d'ordres sans interaction avec une vraie bourse.

Pipeline par barre :
  1. Réception d'une barre fermée (KlineBar ou dict OHLCV)
  2. Mise à jour des features (ATR, EMA, RSI, …) — fenêtre glissante
  3. Calcul du signal heuristique ou utilisation d'une prédiction ML
  4. Appel RiskController.decide() → action / reject
  5. Si BUY/SELL : simulation d'un fill au close + slippage
  6. Surveillance des positions ouvertes (TP/SL/time)
  7. on_fill_pnl() sur chaque sortie
  8. Logging complet en JSONL

Modes :
  - live   : reçoit des KlineBar depuis le WebSocket Binance
  - replay : lit des barres depuis un DataFrame (CSV historique)
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PaperConfig:
    # Stratégie
    entry_threshold : float = 0.55
    tp_atr_mult     : float = 1.5
    sl_atr_mult     : float = 1.0
    max_hold_bars   : int   = 48      # barres max avant time-stop

    # Features lookback (fenêtre glissante)
    atr_period      : int   = 14
    warmup_bars     : int   = 200     # barres minimum avant d'émettre des signaux

    # Coûts (round-trip, fraction)
    fee_rt          : float = 8e-4    # 8 bps
    slippage_rt     : float = 4e-4    # 4 bps

    # Logging
    log_path        : str   = "artifacts/paper_trading/trades.jsonl"
    metrics_interval: int   = 10      # affiche les métriques tous les N trades

    # Signal heuristique
    channel_lookback: int   = 20      # N barres pour le channel breakout
    ema_fast_span   : int   = 8
    ema_slow_span   : int   = 21
    ema_200_span    : int   = 200


# ─────────────────────────────────────────────────────────────────────────────
# Position ouverte
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    bar_index   : int
    entry_bar   : int
    entry_px    : float
    tp_px       : float
    sl_px       : float
    qty         : float
    atr         : float
    risk_budget : float
    direction   : str       # "BUY" | "SELL"
    signal_prob : float
    edge_final  : float


# ─────────────────────────────────────────────────────────────────────────────
# Trade log (sortie)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id    : int
    symbol      : str
    direction   : str
    entry_bar   : int
    exit_bar    : int
    dt_entry    : str
    dt_exit     : str
    entry_px    : float
    exit_px     : float
    tp_px       : float
    sl_px       : float
    qty         : float
    notional    : float
    gross_pnl   : float
    cost        : float
    net_pnl     : float
    exit_reason : str
    hold_bars   : int
    prob_up     : float
    edge_final  : float
    equity      : float
    day_pnl     : float
    consec_loss : int
    rc_reason   : str

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre glissante de features (causal, sans lookahead)
# ─────────────────────────────────────────────────────────────────────────────

class FeatureWindow:
    """
    Maintient une fenêtre glissante d'OHLCV et calcule les indicateurs
    de façon causale (seulement les données passées et présentes).
    """

    def __init__(self, cfg: PaperConfig, maxlen: int = 500):
        self.cfg    = cfg
        self._opens  : Deque[float] = deque(maxlen=maxlen)
        self._highs  : Deque[float] = deque(maxlen=maxlen)
        self._lows   : Deque[float] = deque(maxlen=maxlen)
        self._closes : Deque[float] = deque(maxlen=maxlen)
        self._volumes: Deque[float] = deque(maxlen=maxlen)
        self._atrs   : Deque[float] = deque(maxlen=maxlen)
        self._ema_f  : float = 0.0
        self._ema_s  : float = 0.0
        self._ema_200: float = 0.0
        self._atr    : float = 0.0
        self._rsi_gain: float = 0.0
        self._rsi_loss: float = 0.0
        self._vol_ma : float = 0.0
        self._count  : int   = 0
        # Coefficients EMA
        self._k_f   = 2.0 / (cfg.ema_fast_span   + 1)
        self._k_s   = 2.0 / (cfg.ema_slow_span   + 1)
        self._k_200 = 2.0 / (cfg.ema_200_span    + 1)
        self._k_atr = 2.0 / (cfg.atr_period      + 1)
        self._k_rsi = 2.0 / (14 + 1)
        self._k_vol = 2.0 / (60 + 1)

    def update(self, o: float, h: float, l: float, c: float, v: float) -> None:
        """Intègre une nouvelle barre et met à jour tous les indicateurs."""
        n = self._count

        # ── ATR ───────────────────────────────────────────────────────────────
        if n == 0:
            tr = h - l
        else:
            prev_c = self._closes[-1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))

        if n == 0:
            self._atr = tr
        else:
            self._atr = self._atr * (1 - self._k_atr) + tr * self._k_atr

        # ── EMAs ──────────────────────────────────────────────────────────────
        if n == 0:
            self._ema_f   = c
            self._ema_s   = c
            self._ema_200 = c
        else:
            self._ema_f   = self._ema_f   * (1 - self._k_f)   + c * self._k_f
            self._ema_s   = self._ema_s   * (1 - self._k_s)   + c * self._k_s
            self._ema_200 = self._ema_200 * (1 - self._k_200) + c * self._k_200

        # ── RSI ───────────────────────────────────────────────────────────────
        if n > 0:
            delta = c - self._closes[-1]
            gain  = max(delta, 0.0)
            loss  = max(-delta, 0.0)
            if n == 1:
                self._rsi_gain = gain
                self._rsi_loss = loss
            else:
                self._rsi_gain = self._rsi_gain * (1 - self._k_rsi) + gain * self._k_rsi
                self._rsi_loss = self._rsi_loss * (1 - self._k_rsi) + loss * self._k_rsi

        # ── Volume MA ─────────────────────────────────────────────────────────
        self._vol_ma = self._vol_ma * (1 - self._k_vol) + v * self._k_vol

        # ── Stockage ─────────────────────────────────────────────────────────
        self._opens.append(o)
        self._highs.append(h)
        self._lows.append(l)
        self._closes.append(c)
        self._volumes.append(v)
        self._atrs.append(self._atr)
        self._count += 1

    @property
    def ready(self) -> bool:
        return self._count >= self.cfg.warmup_bars

    @property
    def last_close(self) -> float:
        return self._closes[-1] if self._closes else 0.0

    @property
    def last_high(self) -> float:
        return self._highs[-1] if self._highs else 0.0

    @property
    def last_low(self) -> float:
        return self._lows[-1] if self._lows else 0.0

    @property
    def atr(self) -> float:
        return self._atr

    @property
    def rsi(self) -> float:
        if self._rsi_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + self._rsi_gain / max(self._rsi_loss, 1e-10))

    @property
    def vol_ratio(self) -> float:
        return self._volumes[-1] / max(self._vol_ma, 1e-9) if self._volumes else 1.0

    @property
    def channel_high(self) -> float:
        """Max des high sur les N-1 dernières barres (sans la barre courante)."""
        lookback = self.cfg.channel_lookback
        highs_list = list(self._highs)
        if len(highs_list) < 2:
            return float("inf")
        return max(highs_list[-min(lookback, len(highs_list)-1):-1])

    @property
    def atr_pct(self) -> float:
        c = self.last_close
        return self._atr / max(c, 1e-9)

    def signal(self) -> float:
        """
        Calcule prob_up via channel breakout + tendance (causal).
        Retourne une valeur entre 0.30 et 0.70.
        """
        c = self.last_close
        if c <= 0 or self._count < 2:
            return 0.30

        # Channel breakout
        breakout = c > self.channel_high

        # Tendance long terme
        in_uptrend = (c > self._ema_200) and (self._ema_f > self._ema_s)

        # RSI pas surachété
        rsi = self.rsi
        rsi_ok = rsi < 75.0

        # Volume confirmant
        vol_confirm = self.vol_ratio > 1.1

        # ATR stable (pas de spike de volatilité)
        # Utilise les 60 derniers ATR pour la moyenne
        atrs_list = list(self._atrs)
        if len(atrs_list) >= 10:
            atr_mean = np.mean(atrs_list[-60:]) if len(atrs_list) >= 60 else np.mean(atrs_list)
            vol_stable = self._atr < 4.0 * max(atr_mean, 1e-9)
        else:
            vol_stable = True

        base   = breakout and in_uptrend and rsi_ok and vol_stable
        strong = base and vol_confirm

        if strong:
            return 0.67
        if base:
            return 0.62
        return 0.30

    def features_dict(self) -> Dict[str, float]:
        """Dict de features pour RiskController."""
        return {
            "atr_14": self._atr,
            "rv_60" : self.atr_pct * 0.1,   # approximation RV
        }


# ─────────────────────────────────────────────────────────────────────────────
# PaperTrader — moteur principal
# ─────────────────────────────────────────────────────────────────────────────

class PaperTrader:
    """
    Moteur de paper trading. Reçoit des barres OHLCV fermées et simule
    l'exécution d'ordres avec gestion de position, TP/SL et logging.

    Utilisation :
        pt = PaperTrader(cfg, risk_controller)
        for bar in bars:
            pt.on_bar(symbol, bar_index, dt_str,
                      open, high, low, close, volume)
    """

    def __init__(
        self,
        cfg: PaperConfig,
        risk_controller: Any,      # RiskController (importé dynamiquement)
        symbol: str = "BTCUSDT",
        pred_fn: Optional[Callable[[], float]] = None,
    ):
        self.cfg    = cfg
        self.rc     = risk_controller
        self.symbol = symbol
        self.pred_fn = pred_fn   # si fourni, remplace le signal heuristique

        self._fw         = FeatureWindow(cfg)
        self._position   : Optional[OpenPosition] = None
        self._trade_id   = 0
        self._bar_count  = 0
        self._trades     : List[TradeRecord] = []
        self._log_file   : Optional[Any] = None
        self._start_time = time.time()

        # Buffer OHLCV pour la vérification TP/SL sur barres futures
        self._ohlcv_buffer: Deque[Dict] = deque(maxlen=cfg.max_hold_bars + 5)

        # Métriques temps réel
        self.total_signals   = 0
        self.total_rejected  = 0

        # Initialise le log
        self._init_log()

    def _init_log(self) -> None:
        log_path = Path(self.cfg.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(log_path, "a", buffering=1)  # line-buffered

    def _write_log(self, record: dict) -> None:
        if self._log_file:
            self._log_file.write(json.dumps(record, default=str) + "\n")

    def _write_header(self, dt: str) -> None:
        """Écrit un header de session dans le log."""
        self._write_log({
            "type"   : "session_start",
            "symbol" : self.symbol,
            "dt"     : dt,
            "equity" : self.rc.state.equity,
            "config" : {
                "entry_threshold": self.cfg.entry_threshold,
                "tp_atr_mult"    : self.cfg.tp_atr_mult,
                "sl_atr_mult"    : self.cfg.sl_atr_mult,
                "max_hold_bars"  : self.cfg.max_hold_bars,
                "fee_rt_bps"     : self.cfg.fee_rt * 10000,
                "slippage_rt_bps": self.cfg.slippage_rt * 10000,
            },
        })

    def close(self) -> None:
        """Ferme le fichier de log."""
        if self._log_file:
            self._write_log({"type": "session_end", "summary": self.metrics()})
            self._log_file.close()
            self._log_file = None

    # ─────────────────────────────────────────────────────────────────────────
    # Méthode principale : réception d'une barre
    # ─────────────────────────────────────────────────────────────────────────

    def on_bar(
        self,
        symbol: str,
        bar_index: int,
        dt_str: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        prob_up_override: Optional[float] = None,
    ) -> Optional[TradeRecord]:
        """
        Traite une nouvelle barre fermée.

        Retourne :
            TradeRecord si un trade a été clôturé sur cette barre, None sinon.
        """
        rt = self.cfg.fee_rt + self.cfg.slippage_rt

        # ── 1. Mise à jour feature window ────────────────────────────────────
        self._fw.update(open_, high, low, close, volume)
        self._bar_count += 1

        # Stocke la barre pour vérification TP/SL rétroactive
        self._ohlcv_buffer.append({
            "bar_index": bar_index,
            "dt"       : dt_str,
            "open"     : open_,
            "high"     : high,
            "low"      : low,
            "close"    : close,
        })

        # ── 2. Vérification TP/SL/time sur position ouverte ──────────────────
        closed_trade = None
        if self._position is not None:
            closed_trade = self._check_exit(bar_index, dt_str, high, low, close, rt)

        # ── 3. Reset journalier si changement de jour ─────────────────────────
        day = dt_str[:10]
        if day != self.rc.state.current_day:
            self.rc.reset_day(day_str=day)

        # ── 4. Décision d'entrée (seulement si pas de position ouverte) ───────
        if self._position is None and self._fw.ready:
            entry_trade = self._try_enter(
                bar_index, dt_str, close, volume,
                prob_up_override=prob_up_override,
            )
            # entry_trade retourne None (on entre au bar suivant)

        return closed_trade

    def _check_exit(
        self,
        bar_index: int,
        dt_str: str,
        high: float,
        low: float,
        close: float,
        rt_cost: float,
    ) -> Optional[TradeRecord]:
        """Vérifie si la position ouverte doit être clôturée."""
        pos = self._position
        if pos is None:
            return None

        exit_px     = None
        exit_reason = None

        # SL en priorité (pire cas intra-barre)
        if low <= pos.sl_px:
            exit_px     = pos.sl_px
            exit_reason = "sl"
        elif high >= pos.tp_px:
            exit_px     = pos.tp_px
            exit_reason = "tp"
        elif (bar_index - pos.entry_bar) >= self.cfg.max_hold_bars:
            exit_px     = close
            exit_reason = "time"

        if exit_px is None or exit_reason is None:
            return None

        # ── PnL ───────────────────────────────────────────────────────────────
        gross_pnl = (exit_px - pos.entry_px) * pos.qty
        cost      = pos.entry_px * pos.qty * rt_cost
        net_pnl   = gross_pnl - cost
        notional  = pos.entry_px * pos.qty

        # ── Mise à jour RiskController ─────────────────────────────────────────
        self.rc.on_fill_pnl(net_pnl)
        self._position = None

        # ── Création du record ────────────────────────────────────────────────
        self._trade_id += 1
        rec = TradeRecord(
            trade_id    = self._trade_id,
            symbol      = self.symbol,
            direction   = pos.direction,
            entry_bar   = pos.entry_bar,
            exit_bar    = bar_index,
            dt_entry    = "",   # rempli à l'entrée
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

        # ── Log ───────────────────────────────────────────────────────────────
        log_entry = rec.to_dict()
        log_entry["type"] = "trade"
        self._write_log(log_entry)

        # ── Métriques perioidiques ────────────────────────────────────────────
        if len(self._trades) % self.cfg.metrics_interval == 0:
            self._write_log({"type": "metrics", **self.metrics()})

        return rec

    def _try_enter(
        self,
        bar_index: int,
        dt_str: str,
        close: float,
        volume: float,
        prob_up_override: Optional[float] = None,
    ) -> bool:
        """
        Tente d'entrer en position sur le close de la barre courante.
        L'exécution est simulée au close (mode paper).
        Retourne True si un ordre a été placé.
        """
        # ── Signal ────────────────────────────────────────────────────────────
        if prob_up_override is not None:
            prob_up = float(prob_up_override)
        elif self.pred_fn is not None:
            prob_up = float(self.pred_fn())
        else:
            prob_up = self._fw.signal()

        self.total_signals += 1

        if prob_up <= self.cfg.entry_threshold:
            return False

        # ── Filtre volatilité (ATR% > Q25 approximatif) ───────────────────────
        atr_pct = self._fw.atr_pct
        if atr_pct < 0.001:
            return False

        # ── RiskController ────────────────────────────────────────────────────
        edge_final = prob_up - 0.5
        scale      = min(1.0, (prob_up - 0.5) / 0.2)

        decision = self.rc.decide(
            price      = close,
            edge_final = edge_final,
            scale      = scale,
            bar_index  = bar_index,
            features   = self._fw.features_dict(),
        )

        if decision["action"] == "HOLD":
            self.total_rejected += 1
            self._write_log({
                "type"   : "rejected",
                "bar"    : bar_index,
                "dt"     : dt_str,
                "reason" : decision["reason"],
                "prob_up": round(prob_up, 4),
                "equity" : round(self.rc.state.equity, 2),
            })
            return False

        # ── Simulation fill au close + slippage ───────────────────────────────
        slip = close * self.cfg.slippage_rt * 0.5   # slippage à l'entrée seulement
        entry_px = close + slip if decision["action"] == "BUY" else close - slip

        atr_i  = self._fw.atr
        tp_px  = entry_px + self.cfg.tp_atr_mult * atr_i
        sl_px  = entry_px - self.cfg.sl_atr_mult * atr_i
        qty    = decision["qty"]

        self._position = OpenPosition(
            bar_index  = bar_index,
            entry_bar  = bar_index,
            entry_px   = entry_px,
            tp_px      = tp_px,
            sl_px      = sl_px,
            qty        = qty,
            atr        = atr_i,
            risk_budget = decision["risk_budget"],
            direction  = decision["action"],
            signal_prob = prob_up,
            edge_final = edge_final,
        )

        self._write_log({
            "type"       : "entry",
            "bar"        : bar_index,
            "dt"         : dt_str,
            "direction"  : decision["action"],
            "prob_up"    : round(prob_up, 4),
            "entry_px"   : round(entry_px, 4),
            "tp_px"      : round(tp_px, 4),
            "sl_px"      : round(sl_px, 4),
            "qty"        : round(qty, 8),
            "risk_budget": round(decision["risk_budget"], 4),
            "equity"     : round(self.rc.state.equity, 2),
        })
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Métriques
    # ─────────────────────────────────────────────────────────────────────────

    def metrics(self) -> dict:
        trades = self._trades
        n = len(trades)
        if n == 0:
            return {
                "n_trades"      : 0,
                "equity_init"   : self.rc.cfg.equity,
                "equity_final"  : round(self.rc.state.equity, 2),
                "equity"        : round(self.rc.state.equity, 2),
                "total_pnl"     : 0.0,
                "win_rate"      : 0.0,
                "profit_factor" : 0.0,
                "sharpe"        : 0.0,
                "max_drawdown"  : 0.0,
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

        win_rate = len(wins) / n if n > 0 else 0.0
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) < 0 else float("inf")

        # Equity curve pour drawdown / Sharpe
        eq_series = [equity_init]
        for t in trades:
            eq_series.append(eq_series[-1] + t.net_pnl)
        eq_arr = np.array(eq_series)
        peak   = np.maximum.accumulate(eq_arr)
        dd_arr = (eq_arr - peak) / np.maximum(peak, 1e-9)
        max_dd = float(dd_arr.min())

        # Sharpe approximatif sur les PnL des trades
        pnl_arr = np.array(pnls)
        sharpe = 0.0
        if len(pnl_arr) > 1:
            mu, std = pnl_arr.mean(), pnl_arr.std()
            if std > 0:
                sharpe = (mu / std) * np.sqrt(252)

        tp_hits = sum(1 for t in trades if t.exit_reason == "tp")
        sl_hits = sum(1 for t in trades if t.exit_reason == "sl")
        tm_hits = sum(1 for t in trades if t.exit_reason == "time")

        return {
            "n_trades"       : n,
            "equity_init"    : equity_init,
            "equity_final"   : round(equity_final, 2),
            "total_pnl"      : round(total_pnl, 4),
            "total_return_pct": round(total_pnl / max(equity_init, 1) * 100, 2),
            "win_rate"       : round(win_rate, 3),
            "profit_factor"  : round(pf, 3),
            "sharpe"         : round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_win"        : round(float(np.mean(wins)),  4) if wins   else 0.0,
            "avg_loss"       : round(float(np.mean(losses)), 4) if losses else 0.0,
            "exits_tp"       : tp_hits,
            "exits_sl"       : sl_hits,
            "exits_time"     : tm_hits,
            "total_signals"  : self.total_signals,
            "total_rejected" : self.total_rejected,
            "elapsed_sec"    : round(time.time() - self._start_time, 1),
        }

    @property
    def trades(self) -> List[TradeRecord]:
        return list(self._trades)
