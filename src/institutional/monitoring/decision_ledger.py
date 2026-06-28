"""
src/institutional/monitoring/decision_ledger.py
─────────────────────────────────────────────────────────────────────────────
Decision Ledger — journal COMPLET des décisions horaires.

Chaque heure, pour chaque actif, pour chaque moteur, on écrit une ligne —
même quand on ne trade pas. C'est ce qui transforme le silence du modèle en
dataset d'apprentissage :

    E[return | p proche du seuil]
    E[return | rejet par régime / suppressor / seuil trop strict]

Le "non-trade ledger" n'est pas un second fichier : c'est la vue filtrée
(decision_zone != A_TRADE) du même journal (cf. `non_trades()`).

Stockage : parquet (source de vérité) + miroir CSV, sous
    artifacts/institutional/ledger/decisions.parquet
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_DIR = Path(__file__).parents[3] / "artifacts" / "institutional" / "ledger"

# Colonnes obligatoires du ledger (cf. brief Étape 2).
LEDGER_COLUMNS: List[str] = [
    "timestamp", "engine_id", "asset", "direction", "status", "regime",
    "p_success", "threshold_A", "threshold_B", "decision_zone", "reason",
    "expected_return", "expected_cost", "score_net", "holding_hours",
    "position_open", "portfolio_exposure", "kill_switch_state",
    "future_return_1h", "future_return_4h", "future_return_8h", "future_return_24h",
    "realized_shadow_result",
]

_FUTURE_HORIZONS = (1, 4, 8, 24)
_DIR_SIGN = {"LONG": 1.0, "SHORT_HEDGE": -1.0, "CASH": 0.0}


class DecisionLedger:
    """
    Journal append-only des décisions (trades ET non-trades).

    Usage
    -----
    ledger = DecisionLedger()
    ledger.record(opp, tau_a=0.63, tau_b=0.52, position_open=False,
                  portfolio_exposure=0.0, kill_switch_state=False)
    ...
    ledger.flush()                       # écrit parquet + csv
    ledger.reconcile_forward_returns(prices)   # remplit future_return_*
    """

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            path = DEFAULT_LEDGER_DIR / "decisions.parquet"
        self.path = Path(path)
        self.csv_path = self.path.with_suffix(".csv")
        self._buffer: List[dict] = []

    # ── écriture ────────────────────────────────────────────────────────────────
    def record(
        self,
        opp: Opportunity,
        *,
        tau_a: float,
        tau_b: float,
        position_open: bool = False,
        portfolio_exposure: float = 0.0,
        kill_switch_state: bool = False,
    ) -> dict:
        """Bufferise une ligne de décision (forward returns remplis plus tard)."""
        row = {
            "timestamp": pd.Timestamp(opp.timestamp),
            "engine_id": opp.engine_id,
            "asset": opp.asset,
            "direction": opp.direction,
            "status": opp.status,
            "regime": opp.regime,
            "p_success": float(opp.p_success),
            "threshold_A": float(tau_a),
            "threshold_B": float(tau_b),
            "decision_zone": opp.decision_zone,
            "reason": opp.reason,
            "expected_return": float(opp.expected_return),
            "expected_cost": float(opp.expected_cost),
            "score_net": float(opp.score_net),
            "holding_hours": float(opp.expected_holding_hours),
            "position_open": bool(position_open),
            "portfolio_exposure": float(portfolio_exposure),
            "kill_switch_state": bool(kill_switch_state),
            "future_return_1h": np.nan,
            "future_return_4h": np.nan,
            "future_return_8h": np.nan,
            "future_return_24h": np.nan,
            "realized_shadow_result": np.nan,
        }
        self._buffer.append(row)
        return row

    def flush(self) -> int:
        """Écrit le buffer sur disque (merge + dédup sur (timestamp, engine, asset))."""
        if not self._buffer:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = pd.DataFrame(self._buffer, columns=LEDGER_COLUMNS)
        if self.path.exists():
            old = pd.read_parquet(self.path)
            combined = pd.concat([old, new], ignore_index=True)
        else:
            combined = new
        combined = combined.drop_duplicates(
            subset=["timestamp", "engine_id", "asset"], keep="last"
        ).sort_values(["timestamp", "engine_id", "asset"]).reset_index(drop=True)
        combined.to_parquet(self.path, index=False)
        combined.to_csv(self.csv_path, index=False)
        n = len(self._buffer)
        self._buffer.clear()
        logger.info("[DecisionLedger] flush %d rows → %s (total %d)", n, self.path, len(combined))
        return n

    # ── lecture ──────────────────────────────────────────────────────────────────
    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=LEDGER_COLUMNS)
        df = pd.read_parquet(self.path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    def non_trades(self) -> pd.DataFrame:
        """Vue non-trade : tout ce qui n'a pas donné un trade réel (B + C)."""
        df = self.load()
        return df[df["decision_zone"] != "A_TRADE"].copy()

    # ── réconciliation forward returns ─────────────────────────────────────────
    def reconcile_forward_returns(
        self,
        prices: Dict[str, pd.Series],
    ) -> int:
        """
        Remplit future_return_{1,4,8,24}h et realized_shadow_result depuis les prix.

        `prices` : dict asset → pd.Series de close indexée par timestamp UTC.
        realized_shadow_result = rendement net sur l'horizon de détention,
        signé par la direction (LONG +, SHORT_HEDGE −, CASH 0), moins le coût.
        """
        df = self.load()
        if df.empty:
            return 0

        for asset, s in prices.items():
            s = s.sort_index()
            if s.index.tz is None:
                s.index = s.index.tz_localize("UTC")
            mask = df["asset"] == asset
            if not mask.any():
                continue
            ts = pd.DatetimeIndex(df.loc[mask, "timestamp"].values, tz="UTC")
            p0 = s.reindex(ts, method="ffill").to_numpy()

            for h in _FUTURE_HORIZONS:
                ph = s.reindex(ts + pd.Timedelta(hours=h), method="ffill").to_numpy()
                with np.errstate(divide="ignore", invalid="ignore"):
                    df.loc[mask, f"future_return_{h}h"] = ph / p0 - 1.0

            # rendement réalisé sur l'horizon de détention exact de chaque ligne
            holding = df.loc[mask, "holding_hours"].fillna(8.0).to_numpy()
            target = ts + pd.to_timedelta(np.round(holding).astype(int), unit="h")
            ph = s.reindex(target, method="ffill").to_numpy()
            with np.errstate(divide="ignore", invalid="ignore"):
                gross = ph / p0 - 1.0
            signs = df.loc[mask, "direction"].map(_DIR_SIGN).fillna(0.0).to_numpy()
            cost = df.loc[mask, "expected_cost"].fillna(0.0).to_numpy()
            df.loc[mask, "realized_shadow_result"] = gross * signs - cost

        df.to_parquet(self.path, index=False)
        df.to_csv(self.csv_path, index=False)
        n = int(df["realized_shadow_result"].notna().sum())
        logger.info("[DecisionLedger] reconcile: %d/%d lignes avec forward returns", n, len(df))
        return n

    # ── résumé ───────────────────────────────────────────────────────────────────
    def summary(self, near_miss_band: float = 0.03) -> dict:
        """
        Compte A/B/C + PnL théorique des B (shadow) + near-miss PnL des C.

        near-miss = rejets dont p est dans [tau_B - band, tau_B) : "presque pris".
        Répond à : le bot a-t-il évité du bruit ou raté de bons trades ?
        """
        df = self.load()
        if df.empty:
            return {"n": 0}
        zones = df["decision_zone"].value_counts().to_dict()
        b = df[df["decision_zone"] == "B_SHADOW"]
        c = df[df["decision_zone"] == "C_REJECT"]
        near = c[c["p_success"] >= (c["threshold_B"] - near_miss_band)]

        def _mean(x: pd.Series) -> Optional[float]:
            x = x.dropna()
            return float(x.mean()) if len(x) else None

        return {
            "n": int(len(df)),
            "n_A_trade": int(zones.get("A_TRADE", 0)),
            "n_B_shadow": int(zones.get("B_SHADOW", 0)),
            "n_C_reject": int(zones.get("C_REJECT", 0)),
            "shadow_pnl_mean": _mean(b["realized_shadow_result"]),
            "shadow_pnl_sum": float(b["realized_shadow_result"].dropna().sum()) if len(b) else 0.0,
            "near_miss_count": int(len(near)),
            "near_miss_pnl_mean": _mean(near["realized_shadow_result"]),
            "a_trade_pnl_mean": _mean(df[df["decision_zone"] == "A_TRADE"]["realized_shadow_result"]),
            "pct_explained": 1.0,  # 100% des décisions sont journalisées par construction
        }
