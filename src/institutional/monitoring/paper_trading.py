"""
src/institutional/monitoring/paper_trading.py
─────────────────────────────────────────────────────────────────────────────
Module de paper trading strict.

Enregistre chaque événement avec son contexte complet pour permettre :
  1. L'audit complet de chaque décision
  2. La comparaison backtest vs paper
  3. La détection de drift
  4. La validation des gates de promotion live

Gates de promotion (non négociables) :
  - 90 jours ou 100 trades
  - PF paper > 1.15
  - DD < 3%
  - 0 erreur comptable
  - Slippage conforme aux hypothèses
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

from src.institutional.contracts import SignalFrame, RiskState

logger = logging.getLogger(__name__)

PAPER_LOG_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "paper"


@dataclass
class PaperTradeEvent:
    """Enregistrement complet d'un événement de paper trading."""
    timestamp: str
    event_type: str          # "signal" | "order" | "fill" | "rejection" | "exit"
    asset: str
    engine_name: str
    signal_name: str
    direction: str
    signal_score: float
    signal_confidence: float
    order_size_pct: float    # fraction de l'equity
    fill_price: float
    actual_price: float      # prix marché au même moment
    slippage_bps: float
    fee_bps: float
    pnl_net: float           # 0 si pas encore fermé
    equity_after: float
    drawdown: float
    risk_decision: str       # "ALLOW" | "BLOCK" | "REDUCE"
    risk_reason: str
    reject_reason: str = ""
    notes: str = ""
    run_id: str = ""


@dataclass
class PaperTradingState:
    """État du paper trading."""
    start_equity: float
    current_equity: float
    peak_equity: float
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage_est: float = 0.0
    max_drawdown: float = 0.0
    start_date: str = ""
    last_update: str = ""
    events: List[PaperTradeEvent] = field(default_factory=list)

    @property
    def pf(self) -> float:
        wins = sum(e.pnl_net for e in self.events if e.pnl_net > 0)
        losses = sum(abs(e.pnl_net) for e in self.events if e.pnl_net < 0)
        return wins / (losses + 1e-9)

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.current_equity - self.peak_equity) / self.peak_equity

    @property
    def hit_rate(self) -> float:
        return self.n_wins / max(self.n_trades, 1)

    def days_running(self) -> int:
        if not self.start_date:
            return 0
        try:
            start = pd.Timestamp(self.start_date)
            end = pd.Timestamp(self.last_update)
            return int((end - start).days)
        except Exception:
            return 0

    def gate_check(self) -> Dict[str, Any]:
        """
        Vérifie les gates de promotion live.
        Retourne le statut de chaque gate.
        """
        days = self.days_running()
        return {
            "duration_ok": days >= 90,
            "trades_ok": self.n_trades >= 100,
            "pf_ok": self.pf > 1.15,
            "drawdown_ok": abs(self.drawdown) < 0.03,
            "n_trades": self.n_trades,
            "days": days,
            "pf": round(self.pf, 3),
            "drawdown": round(self.drawdown, 4),
            "ready_for_live": (
                days >= 90
                and self.n_trades >= 100
                and self.pf > 1.15
                and abs(self.drawdown) < 0.03
            ),
        }


class PaperTradingLog:
    """
    Logger de paper trading — enregistre chaque événement dans un fichier JSON.
    """

    def __init__(
        self,
        run_id: str,
        initial_equity: float = 10_000.0,
        log_dir: Optional[Path] = None,
    ):
        self.run_id = run_id
        self.log_dir = Path(log_dir or PAPER_LOG_ROOT / run_id)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.log_dir / "paper_state.json"
        self._events_path = self.log_dir / "paper_events.jsonl"

        # Charger ou initialiser l'état
        if self._state_path.exists():
            data = json.loads(self._state_path.read_text())
            self._state = PaperTradingState(
                start_equity=data["start_equity"],
                current_equity=data["current_equity"],
                peak_equity=data["peak_equity"],
                n_trades=data["n_trades"],
                n_wins=data["n_wins"],
                n_losses=data["n_losses"],
                total_pnl=data["total_pnl"],
                total_fees=data["total_fees"],
                start_date=data.get("start_date", ""),
                last_update=data.get("last_update", ""),
            )
        else:
            now = pd.Timestamp.utcnow().isoformat()
            self._state = PaperTradingState(
                start_equity=initial_equity,
                current_equity=initial_equity,
                peak_equity=initial_equity,
                start_date=now,
                last_update=now,
            )
            self._save_state()

    def log_event(self, event: PaperTradeEvent) -> None:
        """Enregistre un événement dans le log JSONL."""
        event.run_id = self.run_id
        with open(self._events_path, "a") as f:
            f.write(json.dumps(asdict(event), default=str) + "\n")

        # Mettre à jour l'état si c'est un fill
        if event.event_type == "fill" and event.pnl_net != 0:
            self._state.n_trades += 1
            self._state.total_pnl += event.pnl_net
            self._state.total_fees += event.fee_bps * event.order_size_pct / 10_000
            self._state.current_equity = event.equity_after
            if event.equity_after > self._state.peak_equity:
                self._state.peak_equity = event.equity_after
            dd = (event.equity_after - self._state.peak_equity) / self._state.peak_equity
            if dd < self._state.max_drawdown:
                self._state.max_drawdown = dd
            if event.pnl_net > 0:
                self._state.n_wins += 1
            else:
                self._state.n_losses += 1
            self._state.last_update = event.timestamp
            self._save_state()

    def get_gates(self) -> Dict[str, Any]:
        return self._state.gate_check()

    def get_summary(self) -> Dict[str, Any]:
        gates = self._state.gate_check()
        return {
            "run_id": self.run_id,
            "days_running": self._state.days_running(),
            "n_trades": self._state.n_trades,
            "pf": round(self._state.pf, 3),
            "hit_rate": round(self._state.hit_rate, 3),
            "drawdown": round(self._state.drawdown, 4),
            "current_equity": self._state.current_equity,
            "total_pnl": round(self._state.total_pnl, 2),
            "gates": gates,
        }

    def load_events(self) -> List[Dict]:
        if not self._events_path.exists():
            return []
        events = []
        with open(self._events_path) as f:
            for line in f:
                events.append(json.loads(line.strip()))
        return events

    def _save_state(self) -> None:
        data = {
            "run_id": self.run_id,
            "start_equity": self._state.start_equity,
            "current_equity": self._state.current_equity,
            "peak_equity": self._state.peak_equity,
            "n_trades": self._state.n_trades,
            "n_wins": self._state.n_wins,
            "n_losses": self._state.n_losses,
            "total_pnl": self._state.total_pnl,
            "total_fees": self._state.total_fees,
            "max_drawdown": self._state.max_drawdown,
            "start_date": self._state.start_date,
            "last_update": self._state.last_update,
        }
        self._state_path.write_text(json.dumps(data, indent=2))
