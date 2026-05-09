"""
ML ARCHITECTURE ENDPOINTS
=========================
Levels 0-2 : données réelles depuis PredictionEngine.
Levels 3-4 : non entraînés — retournent status disabled explicitement.

Règle : aucun random ne prétend être une vraie prédiction.
"""
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List
import asyncio
import json

ml_router = APIRouter(prefix="/ml", tags=["ML Architecture"])

try:
    from prediction_engine import engine as _engine
    _ENGINE_AVAILABLE = True
except ImportError:
    _ENGINE_AVAILABLE = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pred() -> dict:
    if _ENGINE_AVAILABLE and _engine.last_prediction:
        return _engine.last_prediction
    return {}


_DISABLED_STUB = {
    "status":     "disabled",
    "deployable": False,
    "reason":     "model_not_connected",
    "action":     "WAIT",
    "confidence": 0,
}


# ── Générateurs Level 0-2 (données réelles) ───────────────────────────────────

def generate_level0_data():
    """Level 0 — Filtre tradeable (données réelles)."""
    pred = _get_pred()
    p    = pred.get("p_filter", 0.0)
    thr  = pred.get("filter_thr_long", 0.40)
    return {
        "tradeability_score": p,
        "is_tradeable": pred.get("filter_passed_long", False),
        "threshold": thr,
        "threshold_short": pred.get("filter_thr_short", 0.45),
        "passed_long":  pred.get("filter_passed_long", False),
        "passed_short": pred.get("filter_passed_short", False),
        "features": {
            "rsi_14":     pred.get("rsi", None),
            "dist_ema50": pred.get("dist_ema50", None),
        },
        "window_size": 350,
        "horizon": 1,
        "status": "active" if pred else "initializing",
    }


def generate_level1_data():
    """Level 1 — Régime de marché (données réelles)."""
    pred   = _get_pred()
    regime = pred.get("regime", "NEUTRAL")
    return {
        "detectors": {
            "regime": {
                "name":       "Regime Filter (déterministe)",
                "type":       "3-class",
                "output":     {"predicted": regime},
                "confidence": 1.0,
                "active":     True,
            },
        },
        "regime":       regime,
        "is_shortable": regime == "SHORTABLE",
        "is_no_short":  regime == "NO_SHORT",
        "features": {
            "rsi_14":      round(pred.get("rsi", 0.0), 2),
            "dist_ema_50": round(pred.get("dist_ema50", 0.0), 4),
        },
        "status": "active" if pred else "initializing",
    }


def generate_level2_data():
    """Level 2 — Edge scoring long / short (données réelles)."""
    pred    = _get_pred()
    p_long  = pred.get("p_long",  0.0)
    p_short = pred.get("p_short", 0.0)
    thr_l   = pred.get("thr_edge_long",  0.55)
    thr_s   = pred.get("thr_edge_short", 0.65)
    return {
        "long": {
            "p":         round(p_long, 4),
            "threshold": thr_l,
            "signal":    pred.get("long_signal", False),
            "active":    pred.get("filter_passed_long", False),
        },
        "short": {
            "p":         round(p_short, 4),
            "threshold": thr_s,
            "signal":    False,
            "active":    False,
            "note":      "SHORT disabled: failed validation (PF < 1)",
        },
        "action": pred.get("action", "HOLD"),
        "status": "active" if pred else "initializing",
    }


# ── Level 3-4 : non déployés — réponses honnêtes ─────────────────────────────

def generate_level3_data():
    """Level 3 — Aggregators : non entraînés, désactivés."""
    return {
        **_DISABLED_STUB,
        "level": 3,
        "name": "Aggregators (EventClassifier + PairwiseComparator)",
        "note": "Not trained. Pending future development.",
    }


def generate_level4_data():
    """Level 4 — Meta-Decider PPO : non entraîné, désactivé."""
    return {
        **_DISABLED_STUB,
        "level": 4,
        "name": "Meta-Decider PPO",
        "note": "Not trained. Pending future development.",
    }


# ── REST endpoints ─────────────────────────────────────────────────────────────

@ml_router.get("/architecture/status")
async def get_ml_architecture_status():
    return {
        "level0": generate_level0_data(),
        "level1": generate_level1_data(),
        "level2": generate_level2_data(),
        "level3": generate_level3_data(),
        "level4": generate_level4_data(),
        "timestamp": datetime.now().isoformat(),
        "short_enabled": False,
        "short_disabled_reason": "unstable PF < 1 across tested years, negative expectancy",
    }


@ml_router.get("/level0/gating")
async def get_level0_gating():
    return generate_level0_data()


@ml_router.get("/level1/contexts")
async def get_level1_contexts():
    return generate_level1_data()


@ml_router.get("/level2/specialists")
async def get_level2_specialists():
    return generate_level2_data()


@ml_router.get("/level3/aggregators")
async def get_level3_aggregators():
    return generate_level3_data()


@ml_router.get("/level4/policy")
async def get_level4_policy():
    return generate_level4_data()


@ml_router.get("/level/{level_id}/metrics")
async def get_level_metrics(level_id: int):
    generators = {
        0: generate_level0_data,
        1: generate_level1_data,
        2: generate_level2_data,
        3: generate_level3_data,
        4: generate_level4_data,
    }
    if level_id not in generators:
        return {"error": f"Invalid level_id: {level_id}"}
    return generators[level_id]()


@ml_router.get("/predictions/latest")
async def get_latest_prediction():
    pred = _get_pred()
    return {
        "timestamp":     pred.get("refreshed_at", datetime.now().isoformat()),
        "symbol":        "BTCUSDT",
        "action":        pred.get("action", "HOLD"),
        "level0":        generate_level0_data(),
        "level1":        generate_level1_data(),
        "level2":        generate_level2_data(),
        "level3":        generate_level3_data(),
        "level4":        generate_level4_data(),
        "current_price": pred.get("current_price", 0),
        "p_long":        pred.get("p_long", 0),
        "p_short":       0,
        "regime":        pred.get("regime", "NEUTRAL"),
        "stop_price":    pred.get("stop_price", 0),
        "take_profit":   pred.get("take_profit", 0),
    }


@ml_router.get("/flow/throughput")
async def get_pipeline_throughput():
    """Métriques réelles du moteur d'inférence, ou status disabled si indisponible."""
    if not _ENGINE_AVAILABLE:
        return {
            "status":  "disabled",
            "reason":  "prediction_engine_not_available",
            "deployable": False,
        }
    return {
        "status": "active",
        "note": "latency metrics not yet instrumented",
        "bars_per_second": None,
        "latency_ms": None,
        "cpu_usage": None,
        "gpu_usage": None,
        "memory_mb": None,
        "prediction_rate": None,
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@ml_router.websocket("/ws/ml-architecture")
async def websocket_ml_architecture(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "architecture_update",
            "payload": {
                "level0": generate_level0_data(),
                "level1": generate_level1_data(),
                "level2": generate_level2_data(),
                "level3": generate_level3_data(),
                "level4": generate_level4_data(),
            }
        })

        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

            await asyncio.sleep(2)

            # Mise à jour réelle : niveaux 0-2 seulement
            import random as _random
            level_id = _random.randint(0, 2)
            generators = [generate_level0_data, generate_level1_data, generate_level2_data]
            await websocket.send_json({
                "type":    f"level{level_id}_update",
                "payload": generators[level_id](),
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)
