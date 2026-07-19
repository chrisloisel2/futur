"""
src/institutional/engines/exit_engine/infer.py
─────────────────────────────────────────────────────────────────────────────
EXIT_ENGINE V1 — sortie optimale (améliore TOUS les moteurs alpha).

Wrapper du fleet de sortie persisté (ai/level_2/exit_model_v1.py, 19
spécialistes). S'utilise comme `exit_hook` du PortfolioBacktester : réévalue
chaque position ouverte à chaque heure et décide HOLD / EXIT.

Gate (cf. brief) : ne passe LIVE que s'il améliore le PF portefeuille de +10%,
réduit le DD de 10%, ne dégrade pas le ROI médian > 5%, améliore le worst month.
Tant que non prouvé : status SHADOW.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.engines.legacy_bridge import MODELS_DIR, _ensure_imports, load_enriched
from src.institutional.engines.exit_engine.labels import EXIT_MARKET_FEATURES

logger = logging.getLogger(__name__)


class ExitEngineV1:
    """Moteur de sortie — stateful (suit chaque position entre les appels du hook)."""

    def __init__(self, assets: List[str], status: str = "SHADOW", threshold: Optional[float] = None):
        self.engine_id = "EXIT_ENGINE"
        self.status = status
        self.assets = assets
        self._fleet = self._load_fleet()
        if threshold is not None and self._fleet is not None:
            try:
                self._fleet.threshold_ = float(threshold)
            except Exception:
                pass
        self._market: Dict[str, pd.DataFrame] = {}
        self._track: Dict[tuple, dict] = {}

    @staticmethod
    def _load_fleet():
        _ensure_imports()
        import joblib
        path = MODELS_DIR / "exit_model_v1.pkl"
        if not path.exists():
            logger.warning("exit model absent: %s", path)
            return None
        try:
            return joblib.load(path)
        except Exception as e:
            logger.warning("load exit fleet échec: %s", e)
            return None

    @property
    def available(self) -> bool:
        return self._fleet is not None

    def preload(self, start: str, end: str) -> None:
        """Précharge les features marché de sortie pour tous les assets."""
        for a in self.assets:
            df = load_enriched(a, required_cols=list(EXIT_MARKET_FEATURES), start=start, end=end)
            if df is not None and not df.empty:
                self._market[a] = df.set_index("datetime").sort_index()

    def _market_row(self, asset: str, ts: pd.Timestamp) -> Optional[pd.Series]:
        df = self._market.get(asset)
        if df is None or df.empty:
            return None
        idx = df.index.searchsorted(ts, side="right") - 1
        return df.iloc[idx] if idx >= 0 else None

    def _position_state(self, pos, ts: pd.Timestamp, price: float, mkt: pd.Series) -> dict:
        key = (pos.engine_id, pos.asset, pos.entry_time)
        tr = self._track.get(key)
        ret = price / pos.entry_price - 1.0
        if tr is None:
            tr = {
                "max_ret": ret, "min_ret": ret, "rets": [ret],
                "entry_rsi": float(mkt.get("rsi_13", mkt.get("rsi_20", 0.0)) or 0.0),
                "entry_adx": float(mkt.get("adx_20", 0.0) or 0.0),
                "entry_trend_score": float(mkt.get("trend_score", 0.0) or 0.0),
                "entry_momentum_score": float(mkt.get("momentum_score", 0.0) or 0.0),
                "entry_cpir": float(mkt.get("close_position_in_range", 0.0) or 0.0),
            }
            self._track[key] = tr
        else:
            tr["max_ret"] = max(tr["max_ret"], ret)
            tr["min_ret"] = min(tr["min_ret"], ret)
            tr["rets"].append(ret)

        bars_held = int((ts - pos.entry_time) / pd.Timedelta(hours=1))
        bars_remaining = max(0, int((pos.planned_exit - ts) / pd.Timedelta(hours=1)))
        rets = tr["rets"]
        return {
            "bars_held": bars_held,
            "bars_remaining": bars_remaining,
            "bars_frac": bars_held / max(bars_held + bars_remaining, 1),
            "unrealized_ret": ret,
            "unrealized_ret_bps": ret * 10000.0,
            "max_ret_so_far": tr["max_ret"],
            "min_ret_so_far": tr["min_ret"],
            "drawdown_from_peak": ret - tr["max_ret"],
            "recovery_from_trough": ret - tr["min_ret"],
            "is_profitable": 1.0 if ret > 0 else 0.0,
            "pnl_velocity_1": ret - (rets[-2] if len(rets) >= 2 else ret),
            "pnl_velocity_3": ret - (rets[-4] if len(rets) >= 4 else rets[0]),
            "pnl_normalized": ret / 0.02,
            "entry_rsi": tr["entry_rsi"],
            "entry_adx": tr["entry_adx"],
            "entry_trend_score": tr["entry_trend_score"],
            "entry_momentum_score": tr["entry_momentum_score"],
            "entry_close_position_in_range": tr["entry_cpir"],
        }

    def should_exit(self, pos, asset: str, ts: pd.Timestamp, price: float) -> bool:
        """Hook backtester : True = sortir maintenant."""
        if self._fleet is None:
            return False
        mkt = self._market_row(asset, ts)
        if mkt is None:
            return False
        state = self._position_state(pos, ts, price, mkt)
        try:
            do_exit, _p = self._fleet.should_exit(mkt, state)
            return bool(do_exit)
        except Exception as e:
            logger.debug("exit should_exit échec: %s", e)
            return False

    def as_hook(self):
        return self.should_exit
