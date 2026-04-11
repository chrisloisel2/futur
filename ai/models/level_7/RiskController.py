# risk_controller.py
"""
RiskController — Phase 2 : Gestionnaire de Risque

Responsabilités :
  - Sizing automatique (0.2% equity risqué par trade sur le SL)
  - Stop quotidien à -2% du capital journalier
  - Arrêt après 3 pertes consécutives
  - Cooldown entre trades
  - Persistence de l'état (JSON) pour survie aux redémarrages
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def _f(x: Any, default: float = 0.0) -> float:
    """Cast vers float, remplace NaN/inf par default."""
    try:
        v = float(x)
        return float(default) if not np.isfinite(v) else v
    except Exception:
        return float(default)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    # ── Capital ──────────────────────────────────────────────────────────────
    equity: float = 10_000.0

    # ── Sizing (0.2% equity risqué par trade) ────────────────────────────────
    risk_per_trade: float = 0.002           # fraction du capital risqué sur SL
    max_gross_exposure: float = 1.0         # cap notionnel total / equity
    max_position_notional: float = 1.0      # cap notionnel par position / equity

    # ── Stop distance (ATR ou RV) ─────────────────────────────────────────────
    use_atr: bool = True
    atr_key: str = "atr_14"                 # clé dans le dict features
    rv_key: str = "rv_60"                   # fallback si ATR indisponible
    stop_atr_mult: float = 2.5              # stop = 2.5 × ATR
    stop_rv_mult: float = 3.0              # fallback stop = 3 × RV
    min_stop_pct: float = 0.001             # 0.1% minimum
    max_stop_pct: float = 0.03              # 3% maximum

    # ── Take profit ───────────────────────────────────────────────────────────
    rr: float = 1.5                         # take_profit = rr × stop_dist

    # ── Filtres trade ─────────────────────────────────────────────────────────
    min_abs_edge: float = 0.05              # |edge_final| minimum
    min_scale: float = 0.15                 # scale (confiance) minimum
    cooldown_bars: int = 3                  # bars entre trades

    # ── Protections journalières ──────────────────────────────────────────────
    daily_loss_limit_pct: float = 0.02      # stop après -2% journalier
    max_consecutive_losses: int = 3         # stop après 3 pertes consécutives


# ─────────────────────────────────────────────────────────────────────────────
# État mutable
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskState:
    # Equity courante (mise à jour après chaque trade)
    equity: float = 10_000.0

    # Day tracking
    day_start_equity: float = 10_000.0
    day_pnl: float = 0.0
    day_trades: int = 0
    current_day: str = ""                   # YYYY-MM-DD UTC

    # Trade tracking
    last_trade_bar: int = -10_000
    consecutive_losses: int = 0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RiskState":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ─────────────────────────────────────────────────────────────────────────────
# RiskController
# ─────────────────────────────────────────────────────────────────────────────

class RiskController:
    """
    Gestionnaire de risque pour trading algorithmique.

    API principale :
      - decide(price, edge_final, scale, bar_index, features, ...) → dict
      - on_fill_pnl(realized_pnl, new_equity=None)
      - reset_day(equity=None, day_str=None)
      - save_state(path) / load_state(path)

    Décision retournée :
      {
        "action"      : "BUY" | "SELL" | "HOLD",
        "qty"         : float,
        "notional"    : float,
        "stop_price"  : float | None,
        "take_profit" : float | None,
        "stop_pct"    : float,
        "risk_budget" : float,
        "reason"      : str,        # "ok" ou raison du rejet
      }
    """

    def __init__(self, cfg: RiskConfig, state: Optional[RiskState] = None):
        self.cfg = cfg
        self.state = state if state is not None else RiskState(
            equity=cfg.equity,
            day_start_equity=cfg.equity,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Mise à jour PnL
    # ─────────────────────────────────────────────────────────────────────────

    def on_fill_pnl(self, realized_pnl: float, new_equity: Optional[float] = None) -> None:
        """Appeler après chaque sortie de position (TP/SL/time)."""
        pnl = _f(realized_pnl, 0.0)
        st = self.state
        st.day_pnl += pnl
        st.total_trades += 1

        if pnl < 0:
            st.consecutive_losses += 1
            st.total_losses += 1
        elif pnl > 0:
            st.consecutive_losses = 0
            st.total_wins += 1
        # pnl == 0 ne remet pas à zéro les pertes consécutives (conservateur)

        if new_equity is not None:
            st.equity = _f(new_equity, st.equity)
        else:
            st.equity += pnl

    def reset_day(self, equity: Optional[float] = None, day_str: str = "") -> None:
        """Réinitialise les compteurs journaliers (à appeler à minuit UTC)."""
        st = self.state
        eq = _f(equity, st.equity) if equity is not None else st.equity
        st.day_start_equity = eq
        st.equity = eq
        st.day_pnl = 0.0
        st.day_trades = 0
        st.consecutive_losses = 0
        st.current_day = day_str

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def save_state(self, path: str | Path) -> None:
        """Sauvegarde l'état courant en JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "config": asdict(self.cfg),
            "state": self.state.to_dict(),
        }
        p.write_text(json.dumps(data, indent=2))

    @classmethod
    def load_state(cls, path: str | Path) -> "RiskController":
        """Charge un RiskController depuis un fichier JSON sauvegardé."""
        data = json.loads(Path(path).read_text())
        cfg = RiskConfig(**data["config"])
        state = RiskState.from_dict(data["state"])
        return cls(cfg=cfg, state=state)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers internes
    # ─────────────────────────────────────────────────────────────────────────

    def _daily_stop_triggered(self) -> Tuple[bool, str]:
        """Vérifie si un arrêt journalier doit être activé."""
        st = self.state
        eq0 = st.day_start_equity
        if eq0 <= 0:
            return True, "equity_zero"

        # Limite perte journalière : -2% du capital de début de journée
        daily_loss_limit = self.cfg.daily_loss_limit_pct * eq0
        if st.day_pnl <= -daily_loss_limit:
            return True, f"daily_loss_{abs(st.day_pnl):.2f}_limit_{daily_loss_limit:.2f}"

        # Limite pertes consécutives : 3
        if st.consecutive_losses >= self.cfg.max_consecutive_losses:
            return True, f"consecutive_losses_{st.consecutive_losses}"

        return False, ""

    def _cooldown_ok(self, bar_index: int) -> bool:
        return (bar_index - self.state.last_trade_bar) >= self.cfg.cooldown_bars

    def _stop_distance_pct(self, price: float, features: Dict[str, Any]) -> float:
        price = max(price, 1e-9)

        if self.cfg.use_atr:
            atr = _f(features.get(self.cfg.atr_key, 0.0), 0.0)
            if atr > 0:
                stop_pct = (self.cfg.stop_atr_mult * atr) / price
                return _clip(stop_pct, self.cfg.min_stop_pct, self.cfg.max_stop_pct)

        # Fallback : volatilité réalisée
        rv = _f(features.get(self.cfg.rv_key, 0.0), 0.0)
        stop_pct = self.cfg.stop_rv_mult * abs(rv)
        return _clip(stop_pct, self.cfg.min_stop_pct, self.cfg.max_stop_pct)

    def _hold(self, reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "qty": 0.0,
            "notional": 0.0,
            "stop_price": None,
            "take_profit": None,
            "stop_pct": 0.0,
            "risk_budget": 0.0,
            "reason": reason,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Décision principale
    # ─────────────────────────────────────────────────────────────────────────

    def decide(
        self,
        *,
        price: float,
        edge_final: float,
        scale: float,
        bar_index: int,
        features: Dict[str, Any],
        tradeable: Optional[bool] = None,
        current_gross_exposure_frac: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Prend une décision de trading.

        Paramètres :
          price                     : prix courant (Close ou Open du bar suivant)
          edge_final                : signal signé (> 0 = long, < 0 = short)
          scale                     : confiance du modèle (0..1)
          bar_index                 : index entier de la barre courante
          features                  : dict avec 'atr_14' et/ou 'rv_60'
          tradeable                 : gating Level 0 (None = non utilisé)
          current_gross_exposure_frac: exposition brute courante / equity

        Retourne : dict avec action, qty, notional, stop_price, take_profit, reason
        """
        price     = _f(price, 0.0)
        edge_final = _f(edge_final, 0.0)
        scale     = _f(scale, 0.0)

        # ── Garde-fous basiques ───────────────────────────────────────────────
        if price <= 0:
            return self._hold("bad_price")

        # ── Arrêt journalier ──────────────────────────────────────────────────
        stopped, stop_reason = self._daily_stop_triggered()
        if stopped:
            return self._hold(f"daily_stop:{stop_reason}")

        # ── Gating Level 0 ────────────────────────────────────────────────────
        if tradeable is False:
            return self._hold("not_tradeable")

        # ── Filtres signal ────────────────────────────────────────────────────
        if scale < self.cfg.min_scale:
            return self._hold(f"low_scale:{scale:.3f}")

        if abs(edge_final) < self.cfg.min_abs_edge:
            return self._hold(f"low_edge:{edge_final:.3f}")

        # ── Cooldown ──────────────────────────────────────────────────────────
        if not self._cooldown_ok(bar_index):
            return self._hold(f"cooldown:{self.state.last_trade_bar}+{self.cfg.cooldown_bars}")

        # ── Cap d'exposition ──────────────────────────────────────────────────
        if current_gross_exposure_frac >= self.cfg.max_gross_exposure:
            return self._hold(f"exposure_cap:{current_gross_exposure_frac:.2f}")

        # ── Direction ─────────────────────────────────────────────────────────
        action = "BUY" if edge_final > 0 else "SELL"

        # ── Stop distance ─────────────────────────────────────────────────────
        stop_pct  = self._stop_distance_pct(price, features)
        stop_dist = stop_pct * price

        # ── Sizing : risk_budget / stop_dist ──────────────────────────────────
        equity      = self.state.equity
        risk_budget = equity * self.cfg.risk_per_trade   # 0.2% equity
        qty         = risk_budget / max(stop_dist, 1e-9)

        # Cap notionnel
        max_notional = equity * self.cfg.max_position_notional
        notional     = qty * price
        if notional > max_notional:
            qty      = max_notional / price
            notional = qty * price

        # ── Prix de stop et take profit ───────────────────────────────────────
        if action == "BUY":
            stop_price  = price - stop_dist
            take_profit = price + self.cfg.rr * stop_dist
        else:
            stop_price  = price + stop_dist
            take_profit = price - self.cfg.rr * stop_dist

        # ── Enregistre le dernier trade ───────────────────────────────────────
        self.state.last_trade_bar = int(bar_index)
        self.state.day_trades    += 1

        return {
            "action"      : action,
            "qty"         : float(qty),
            "notional"    : float(notional),
            "stop_price"  : float(stop_price),
            "take_profit" : float(take_profit),
            "stop_pct"    : float(stop_pct),
            "risk_budget" : float(risk_budget),
            "reason"      : "ok",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Résumé
    # ─────────────────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        st = self.state
        win_rate = st.total_wins / max(st.total_trades, 1)
        return {
            "equity"            : st.equity,
            "total_trades"      : st.total_trades,
            "total_wins"        : st.total_wins,
            "total_losses"      : st.total_losses,
            "win_rate"          : round(win_rate, 3),
            "consecutive_losses": st.consecutive_losses,
            "day_pnl"           : st.day_pnl,
            "day_start_equity"  : st.day_start_equity,
            "pnl_pct"           : round((st.equity - self.cfg.equity) / max(self.cfg.equity, 1) * 100, 2),
        }
