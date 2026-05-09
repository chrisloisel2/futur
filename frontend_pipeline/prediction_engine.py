"""
frontend_pipeline/prediction_engine.py — MOTEUR D'INFÉRENCE LIVE
=================================================================

Orchestre la chaîne complète :
  Binance (OHLCV 1h) → features → cascade 7 niveaux → signal

Exposé comme singleton `engine` utilisé par api_server.py.

Architecture interne :
  Level 0 : filtre tradeable  (filter_model.pkl)
  Level 1 : régime de marché  (régime du dernier bar)
  Level 2 : signal directionnel (long/short best_model.pkl)
  Level 7 : taille de position, stop, take-profit

Usage :
    from prediction_engine import engine
    await engine.refresh()
    pred = engine.last_prediction
"""
from __future__ import annotations

import asyncio
import json
import logging
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

# ── Chemin vers la racine du projet ──────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ai.level_0.live_features import compute_live_features
from ai.level_0.feature_engineering import compute_long_features, compute_short_features
from ai.level_0.features import FEATURES_LONG, FEATURES_SHORT
from ai.level_1.rules import REGIME_NO_SHORT, REGIME_SHORTABLE, REGIME_NEUTRAL
from ai.level_7.config import make_long_risk_config, make_short_risk_config
from config.strategy_flags import SHORT_ENABLED
from risk.uncertainty_gate import gate_signal as _gate_signal

# Features du modèle de régime (chargées dynamiquement depuis bear_regime_metrics.json)
REGIME_MODEL_DEFAULT_FEATURES = [
    "dist_ema_50", "ema_spread_50_200", "dist_ema_200", "ema_spread_20_50",
    "mom_logret_24", "mom_logret_72", "mom_sharpe_24", "rsi_14",
    "delta_taker_cumul_12", "sell_vol_ratio_24", "dist_from_local_high_24",
    "rv_ratio_24_72",
]

# Features réelles du filtre (train_pipeline.py → SNAPSHOT_FEATURES, 39 features)
FEATURES_FILTER = [
    "rv_12", "rv_24", "rv_48", "rv_72", "rv_168",
    "rv_ratio_24_72", "rv_ratio_12_48",
    "atr_pct_14", "boll_width_20",
    "mom_logret_6", "mom_logret_12", "mom_logret_24", "mom_logret_72",
    "mom_sharpe_6", "mom_sharpe_12", "mom_sharpe_24",
    "rsi_14", "cci_20",
    "dist_ema_20", "dist_ema_50", "dist_ema_200",
    "ema_spread_20_50", "ema_spread_50_200",
    "boll_pos_20", "close_in_bar", "intrabar_range_pct",
    "eff_ratio_12", "eff_ratio_24",
    "taker_buy_ratio_base", "delta_taker_pressure",
    "vol_ratio_24", "trades_ratio_24",
    "zscore_close_24", "zscore_ret_24", "skew_ret_24",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]  # 39 features — correspondant au scaler du modèle entraîné

logger = logging.getLogger(__name__)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
SYMBOL   = "BTCUSDT"
INTERVAL = "1h"
LIMIT    = 350   # 350 barres pour ema_200 stable (besoin de ~230)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_pkl(path: Path) -> Any:
    with open(path, "rb") as f:
        return pickle.load(f)


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _find_latest_run() -> Optional[Path]:
    """Dernier run avec filtre + (long ou short)."""
    runs = ROOT / "runs" / "pipeline"
    if not runs.exists():
        return None
    for d in sorted(runs.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        has_filter = (d / "filter" / "filter_model.pkl").exists()
        has_long   = (d / "long"   / "best_model.pkl").exists()
        has_short  = (d / "short"  / "best_model.pkl").exists()
        if has_filter and (has_long or has_short):
            return d
    return None


def _apply_calibrator(cal, p: float) -> float:
    if cal is None:
        return p
    try:
        arr = np.array([p])
        try:
            return float(cal.predict(arr)[0])
        except AttributeError:
            return float(cal.predict_proba(arr.reshape(-1, 1))[0, 1])
    except Exception:
        return p


# ─────────────────────────────────────────────────────────────────────────────
# PredictionEngine
# ─────────────────────────────────────────────────────────────────────────────

class PredictionEngine:
    """
    Moteur d'inférence stateful : charge les artefacts une fois,
    rafraîchit la prédiction à la demande.
    """

    def __init__(self) -> None:
        self._run_dir: Optional[Path]     = None
        self._last_prediction: Optional[Dict[str, Any]] = None
        self._last_refresh: Optional[datetime]           = None
        self._prediction_history: List[Dict[str, Any]]  = []
        self._ready = False

        # Artefacts Level 0 — filtre
        self.clf_filter    = None
        self.scaler_filter = None
        self.thr_long      = 0.40
        self.thr_short     = 0.45

        # Artefacts Level 2 — long
        self.clf_long    = None
        self.scaler_long = None
        self.thr_edge_long = 0.55

        # Artefacts Level 2 — short
        self.clf_short    = None
        self.scaler_short = None
        self.cal_short    = None
        self.thr_edge_short = 0.65

        # Artefacts Level 1 — régime (optionnel)
        self.clf_regime    = None
        self.scaler_regime = None
        self.regime_features: List[str] = REGIME_MODEL_DEFAULT_FEATURES
        self.regime_threshold: float = 0.86   # activation threshold bear

        # Artefacts Level 7
        self._risk_long  = make_long_risk_config()
        self._risk_short = make_short_risk_config()

    # ── Chargement ────────────────────────────────────────────────────────────

    def load(self, run_dir: Optional[Path] = None) -> None:
        """
        Charge les artefacts depuis run_dir (ou auto-détecte le dernier run).
        Lève ValueError si aucun run valide n'est trouvé.
        """
        run_dir = Path(run_dir) if run_dir else _find_latest_run()
        if run_dir is None:
            raise ValueError("Aucun run valide trouvé dans runs/pipeline/")

        self._run_dir = run_dir

        # Filtre
        fd = run_dir / "filter"
        self.clf_filter    = _load_pkl(fd / "filter_model.pkl")
        self.scaler_filter = _load_pkl(fd / "filter_scaler.pkl")
        meta = _load_json(fd / "metrics.json")
        self.thr_long  = meta.get("calibrated_threshold_long",
                                  meta.get("recommended_threshold_long", 0.40))
        self.thr_short = meta.get("calibrated_threshold_short",
                                  meta.get("recommended_threshold_short", 0.45))

        # Long (optionnel)
        ld = run_dir / "long"
        if ld.exists() and (ld / "best_model.pkl").exists():
            self.clf_long    = _load_pkl(ld / "best_model.pkl")
            self.scaler_long = _load_pkl(ld / "scaler.pkl")
            cal_p = ld / "calibration_metrics.json"
            lmeta = _load_json(cal_p) if cal_p.exists() else {}
            self.thr_edge_long = lmeta.get("recommended_threshold", 0.55)

        # Short (optionnel)
        sd = run_dir / "short"
        if sd.exists() and (sd / "best_model.pkl").exists():
            self.clf_short    = _load_pkl(sd / "best_model.pkl")
            self.scaler_short = _load_pkl(sd / "scaler.pkl")
            cal_pkl = sd / "calibrator.pkl"
            self.cal_short = _load_pkl(cal_pkl) if cal_pkl.exists() else None
            smeta = _load_json(sd / "calibration_metrics.json") \
                    if (sd / "calibration_metrics.json").exists() else {}
            self.thr_edge_short = smeta.get("recommended_threshold", 0.65)

        # Régime (optionnel) — modèle bear entraîné
        rd = run_dir / "regime"
        if rd.exists() and (rd / "bear_regime_model.pkl").exists():
            self.clf_regime    = _load_pkl(rd / "bear_regime_model.pkl")
            self.scaler_regime = _load_pkl(rd / "bear_regime_scaler.pkl")
            rmeta = _load_json(rd / "bear_regime_metrics.json") \
                    if (rd / "bear_regime_metrics.json").exists() else {}
            self.regime_features  = rmeta.get("features", REGIME_MODEL_DEFAULT_FEATURES)
            self.regime_threshold = rmeta.get("activation_threshold", 0.86)
            logger.info(
                f"Régime bear chargé — {len(self.regime_features)} features "
                f"thr={self.regime_threshold:.2f} AUC={rmeta.get('val_auc', '?')}"
            )

        self._ready = True
        logger.info(
            f"PredictionEngine prêt — run={run_dir.name} "
            f"long={'OK' if self.clf_long else '—'} "
            f"short={'OK' if self.clf_short else '—'}"
        )

    # ── Fetch Binance ─────────────────────────────────────────────────────────

    async def _fetch_candles(self, symbol: str = SYMBOL) -> "pd.DataFrame":
        import pandas as pd

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                BINANCE_KLINES,
                params={"symbol": symbol, "interval": INTERVAL, "limit": LIMIT},
            )
            r.raise_for_status()
            data = r.json()

        cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
        ]
        df = pd.DataFrame(data, columns=cols)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume",
                  "quote_asset_volume",
                  "taker_buy_base_asset_volume",
                  "taker_buy_quote_asset_volume"):
            df[c] = df[c].astype(float)
        df["number_of_trades"] = df["number_of_trades"].astype(int)
        df = df.set_index("datetime").sort_index()
        return df

    # ── Inférence ─────────────────────────────────────────────────────────────

    def _row_to_array(self, row, features: List[str]) -> np.ndarray:
        vals = []
        for f in features:
            v = row.get(f, 0.0)
            vals.append(float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else 0.0)
        return np.array([vals], dtype=np.float32)

    async def predict(self, symbol: str = SYMBOL) -> Dict[str, Any]:
        if not self._ready:
            raise RuntimeError("Engine non initialisé — appeler load() d'abord")

        df_raw = await self._fetch_candles(symbol)
        current_price = float(df_raw["close"].iloc[-1])
        bar_time      = df_raw.index[-1].isoformat()

        # ── Feature engineering ──────────────────────────────────────────────
        # compute_short_features() doit être appelé inconditionnellement car
        # le modèle de régime bear en a besoin (delta_taker_cumul_12, etc.)
        df = compute_live_features(df_raw)
        df = compute_long_features(df)
        df = compute_short_features(df)

        last = df.iloc[-1]
        row  = {col: float(last[col]) for col in df.columns
                if col not in ("open_time", "close_time", "ignore")}

        # ── Level 0 : filtre tradeable ────────────────────────────────────────
        x_f = self._row_to_array(row, FEATURES_FILTER)
        p_filter = float(
            self.clf_filter.predict_proba(
                self.scaler_filter.transform(x_f)
            )[0, 1]
        )
        passes_long  = p_filter >= self.thr_long
        passes_short = p_filter >= self.thr_short

        # ── Level 1 : régime ─────────────────────────────────────────────────
        dist_ema50 = row.get("dist_ema_50", 0.0)
        rsi        = row.get("rsi_14", 50.0)

        if self.clf_regime is not None:
            # Modèle entraîné bear_regime
            x_r = self._row_to_array(row, self.regime_features)
            p_bear = float(
                self.clf_regime.predict_proba(
                    self.scaler_regime.transform(x_r)
                )[0, 1]
            )
            if p_bear >= self.regime_threshold:
                regime = REGIME_SHORTABLE
            elif dist_ema50 < -0.05:
                regime = REGIME_NO_SHORT
            else:
                regime = REGIME_NEUTRAL
        else:
            # Fallback déterministe si pas de modèle de régime
            ema_spread = row.get("ema_spread_50_200", 0.0)
            if dist_ema50 < 0 and ema_spread < 0 and rsi < 48:
                regime = REGIME_SHORTABLE
            elif dist_ema50 < -0.05 and ema_spread < -0.02:
                regime = REGIME_NO_SHORT
            else:
                regime = REGIME_NEUTRAL

        # ── Level 2 : signal long ─────────────────────────────────────────────
        p_long      = 0.0
        long_signal = False
        if self.clf_long and passes_long:
            x_l    = self._row_to_array(row, FEATURES_LONG)
            p_long = float(
                self.clf_long.predict_proba(
                    self.scaler_long.transform(x_l)
                )[0, 1]
            )
            long_signal = p_long >= self.thr_edge_long

        # ── Level 2 : signal short (désactivé si SHORT_ENABLED=False) ─────────
        p_short_raw = 0.0
        p_short     = 0.0
        short_signal = False
        if (SHORT_ENABLED
                and self.clf_short and passes_short
                and not long_signal
                and regime == REGIME_SHORTABLE):
            x_s         = self._row_to_array(row, FEATURES_SHORT)
            p_short_raw = float(
                self.clf_short.predict_proba(
                    self.scaler_short.transform(x_s)
                )[0, 1]
            )
            p_short      = _apply_calibrator(self.cal_short, p_short_raw)
            short_signal = p_short >= self.thr_edge_short

        # ── Décision brute ────────────────────────────────────────────────────
        if long_signal:
            action_raw = "LONG"
            cfg        = self._risk_long
        elif short_signal:
            action_raw = "SHORT"
            cfg        = self._risk_short
        else:
            action_raw = "HOLD"
            cfg        = None

        # ── Uncertainty gate (Level filtre post-signal) ───────────────────────
        rv_24 = float(row.get("rv_24", 0.03))
        _ug_input = {"p_long": p_long, "rv_24": rv_24}
        _ug_result = _gate_signal(_ug_input, width_threshold=0.30)
        uncertainty_info = _ug_result.get("uncertainty", {})

        action = action_raw
        if action_raw == "LONG" and not uncertainty_info.get("allow_trade", True):
            action = "WAIT"
        size_multiplier = uncertainty_info.get("size_multiplier", 1.0) if action == "LONG" else 0.0

        # ── Level 7 : risk sizing ─────────────────────────────────────────────
        qty = stop_price = take_profit = 0.0
        if cfg:
            p_edge = p_long if action == "LONG" else p_short
            kelly  = max(0.0, cfg.kelly_fraction * (p_edge - (1 - p_edge)))
            kelly  = min(kelly, cfg.max_position_pct)
            sp_pct = cfg.stop_loss_pct
            tp_pct = cfg.stop_loss_pct * cfg.risk_reward_ratio
            if action == "LONG":
                stop_price  = round(current_price * (1 - sp_pct), 2)
                take_profit = round(current_price * (1 + tp_pct), 2)
            else:
                stop_price  = round(current_price * (1 + sp_pct), 2)
                take_profit = round(current_price * (1 - tp_pct), 2)
            qty = round(kelly * 10_000 / current_price, 6)

        # ── Raison lisible ────────────────────────────────────────────────────
        if action == "HOLD":
            if not (passes_long or passes_short):
                reason = f"filtre bas ({p_filter:.3f} < {self.thr_long:.2f})"
            elif regime == REGIME_NO_SHORT and not long_signal:
                reason = f"régime défavorable ({regime})"
            else:
                reason = (
                    f"p_long={p_long:.3f} < {self.thr_edge_long:.2f} · "
                    f"p_short={p_short:.3f} < {self.thr_edge_short:.2f}"
                )
        else:
            p_disp = p_long if action == "LONG" else p_short
            reason = f"signal {action} · p={p_disp:.3f}"

        pred = {
            # Identifiant
            "symbol":        symbol,
            "run_id":        self._run_dir.name if self._run_dir else "",
            "timestamp":     bar_time,
            "refreshed_at":  datetime.now(timezone.utc).isoformat(),
            # Prix
            "current_price": current_price,
            # Décision finale (avec uncertainty gate appliqué)
            "action_raw":   action_raw,
            "action":       action,
            "action_final": action,
            "reason":  reason,
            # Level 0
            "p_filter":           round(p_filter, 4),
            "filter_thr_long":    self.thr_long,
            "filter_thr_short":   self.thr_short,
            "filter_passed_long": passes_long,
            "filter_passed_short":passes_short,
            # Level 1
            "regime":    regime,
            "dist_ema50": round(dist_ema50, 4),
            "rsi":        round(rsi, 2),
            # Level 2
            "p_long":       round(p_long, 4),
            "p_short":      round(p_short, 4) if SHORT_ENABLED else 0.0,
            "p_short_raw":  round(p_short_raw, 4),
            # Uncertainty gate
            "uncertainty": uncertainty_info,
            "size_multiplier": round(size_multiplier, 4),
            "thr_edge_long":  self.thr_edge_long,
            "thr_edge_short": self.thr_edge_short,
            "long_signal":  long_signal,
            "short_signal": short_signal,
            # Level 7
            "qty":          qty,
            "stop_price":   stop_price,
            "take_profit":  take_profit,
            # Compat frontend ancien format
            "confidence":    round(max(p_long, p_short), 4),
            "direction":     ("up" if action == "LONG"
                              else "down" if action == "SHORT"
                              else "neutral"),
            "change_pct":    round((p_long - 0.5) * 100 if action == "LONG"
                                   else (0.5 - p_short) * 100 if action == "SHORT"
                                   else 0.0, 2),
            "predicted_price": round(
                current_price * (1 + (p_long - 0.5) * 0.02), 2
            ),
        }
        return pred

    # ── Refresh (mise en cache) ────────────────────────────────────────────────

    async def refresh(self, symbol: str = SYMBOL) -> None:
        try:
            pred = await self.predict(symbol)
            self._last_prediction = pred
            self._last_refresh = datetime.now(timezone.utc)
            # Historique des 200 dernières prédictions
            self._prediction_history.append({
                "timestamp": pred["refreshed_at"],
                "action":    pred["action"],
                "p_long":    pred["p_long"],
                "p_short":   pred["p_short"],
                "p_filter":  pred["p_filter"],
                "price":     pred["current_price"],
            })
            self._prediction_history = self._prediction_history[-200:]
            logger.info(
                f"[{symbol}] {pred['action']} "
                f"p_long={pred['p_long']:.3f} p_short={pred['p_short']:.3f} "
                f"regime={pred['regime']}"
            )
        except Exception as e:
            logger.error(f"PredictionEngine.refresh() failed: {e}", exc_info=True)

    # ── Accesseurs ────────────────────────────────────────────────────────────

    @property
    def last_prediction(self) -> Optional[Dict[str, Any]]:
        return self._last_prediction

    @property
    def prediction_history(self) -> List[Dict[str, Any]]:
        return self._prediction_history

    @property
    def ready(self) -> bool:
        return self._ready

    def status(self) -> Dict[str, Any]:
        return {
            "status":         "running" if self._ready else "initializing",
            "run_id":         self._run_dir.name if self._run_dir else None,
            "symbols":        [SYMBOL],
            "has_long_model": self.clf_long is not None,
            "has_short_model":self.clf_short is not None,
            "last_refresh":   self._last_refresh.isoformat() if self._last_refresh else None,
            "uptime":         0,
            "predictions_count": len(self._prediction_history),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton global
# ─────────────────────────────────────────────────────────────────────────────

engine = PredictionEngine()


async def init_engine(run_dir: Optional[Path] = None) -> None:
    """
    Initialise et charge le moteur.
    À appeler au démarrage de l'API (startup event).
    """
    try:
        engine.load(run_dir)
        await engine.refresh()
    except ValueError as e:
        logger.warning(f"PredictionEngine non initialisé: {e}")
    except Exception as e:
        logger.error(f"Impossible d'initialiser PredictionEngine: {e}", exc_info=True)
