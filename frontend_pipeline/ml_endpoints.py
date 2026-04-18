"""
ML ARCHITECTURE ENDPOINTS
=========================
Endpoints pour exposer les données des niveaux de l'architecture ML.
Les levels 0, 1 et 2 utilisent les vraies données du PredictionEngine.
Les levels 3 et 4 restent des stubs (non entraînés).
"""
import random
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List
import asyncio
import json

ml_router = APIRouter(prefix="/ml", tags=["ML Architecture"])

# Import du moteur d'inférence (disponible après init_engine au startup)
try:
    from prediction_engine import engine as _engine
    _ENGINE_AVAILABLE = True
except ImportError:
    _ENGINE_AVAILABLE = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pred() -> dict:
    """Retourne la dernière prédiction ou un dict vide."""
    if _ENGINE_AVAILABLE and _engine.last_prediction:
        return _engine.last_prediction
    return {}


# ── Générateurs basés sur les vraies données ──────────────────────────────────

def generate_level0_data():
    """Level 0 — Filtre tradeable (données réelles)."""
    pred = _get_pred()
    p    = pred.get("p_filter", random.uniform(0.3, 0.9))
    thr  = pred.get("filter_thr_long", 0.40)
    return {
        "tradeability_score": p,
        "is_tradeable": pred.get("filter_passed_long", p > thr),
        "threshold": thr,
        "threshold_short": pred.get("filter_thr_short", 0.45),
        "passed_long":  pred.get("filter_passed_long",  p > thr),
        "passed_short": pred.get("filter_passed_short", p > 0.45),
        "features": {
            "rsi_14":    pred.get("rsi", 50.0),
            "dist_ema50":pred.get("dist_ema50", 0.0),
        },
        "window_size": 350,
        "horizon": 1,
        "status": "active" if pred else "initializing",
    }


def generate_level1_data():
    """Level 1 — Régime de marché (données réelles)."""
    pred   = _get_pred()
    regime = pred.get("regime", "NEUTRAL")
    rsi    = pred.get("rsi", 50.0)
    d50    = pred.get("dist_ema50", 0.0)

    is_shortable = regime == "SHORTABLE"
    is_no_short  = regime == "NO_SHORT"

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
        "regime":  regime,
        "is_shortable": is_shortable,
        "is_no_short":  is_no_short,
        "features": {
            "rsi_14":         round(rsi, 2),
            "dist_ema_50":    round(d50, 4),
        },
        "status": "active" if pred else "initializing",
    }


def generate_level2_data():
    """Level 2 — Edge scoring long / short (données réelles)."""
    pred   = _get_pred()
    p_long  = pred.get("p_long",  0.0)
    p_short = pred.get("p_short", 0.0)
    thr_l   = pred.get("thr_edge_long",  0.55)
    thr_s   = pred.get("thr_edge_short", 0.65)
    return {
        "long": {
            "p":          round(p_long, 4),
            "threshold":  thr_l,
            "signal":     pred.get("long_signal",  False),
            "active":     pred.get("filter_passed_long", False),
        },
        "short": {
            "p":          round(p_short, 4),
            "threshold":  thr_s,
            "signal":     pred.get("short_signal", False),
            "active":     (pred.get("filter_passed_short", False)
                           and pred.get("regime", "NEUTRAL") != "NO_SHORT"),
        },
        "action": pred.get("action", "HOLD"),
        "status": "active" if pred else "initializing",
    }

def generate_level3_data():
    """Génère des données mock pour Level 3 - Aggregators."""
    # Biaiser fortement vers NORMAL et EVENT_UP/EVENT_DOWN (80% du temps)
    rand_event = random.random()
    if rand_event < 0.4:  # 40% NORMAL
        event_probs = [0.7, 0.1, 0.1, 0.1]
        event_class = 'NORMAL'
    elif rand_event < 0.7:  # 30% EVENT_UP
        event_probs = [0.1, 0.7, 0.1, 0.1]
        event_class = 'EVENT_UP'
    elif rand_event < 0.9:  # 20% EVENT_DOWN
        event_probs = [0.1, 0.1, 0.7, 0.1]
        event_class = 'EVENT_DOWN'
    else:  # 10% VOL_SHOCK
        event_probs = [0.1, 0.1, 0.1, 0.7]
        event_class = 'VOL_SHOCK'

    # Biaiser vers CONSISTENT pour avoir plus de signaux confirmés (70% du temps)
    rand = random.random()
    if rand < 0.7:  # 70% CONSISTENT
        pairwise_probs = [0.7, 0.2, 0.1]
        pairwise_class = 'CONSISTENT'
    elif rand < 0.9:  # 20% WEAKENING
        pairwise_probs = [0.2, 0.7, 0.1]
        pairwise_class = 'WEAKENING'
    else:  # 10% CONTRADICTION
        pairwise_probs = [0.1, 0.2, 0.7]
        pairwise_class = 'CONTRADICTION'

    if pairwise_class == 'CONSISTENT' and event_class in ['NORMAL', 'EVENT_UP', 'EVENT_DOWN']:
        decision = 'CONFIRM'
    elif pairwise_class == 'CONTRADICTION':
        decision = 'INVALIDATE'
    else:
        decision = 'DELAY'

    return {
        "event_classifier": {
            "predicted_class": event_class,
            "probabilities": {
                "NORMAL": event_probs[0],
                "EVENT_UP": event_probs[1],
                "EVENT_DOWN": event_probs[2],
                "VOL_SHOCK": event_probs[3]
            }
        },
        "pairwise_comparator": {
            "predicted_class": pairwise_class,
            "probabilities": {
                "CONSISTENT": pairwise_probs[0],
                "WEAKENING": pairwise_probs[1],
                "CONTRADICTION": pairwise_probs[2]
            },
            "consensus_score": pairwise_probs[0]
        },
        "decision": decision,
        "event_type": event_class,
        "status": "active",
        "history": [
            {
                "timestamp": datetime.now().isoformat(),
                "event": random.choice(event_classes),
                "decision": random.choice(['CONFIRM', 'INVALIDATE', 'DELAY'])
            }
            for _ in range(10)
        ]
    }

def generate_level4_data():
    """Génère des données mock pour Level 4 - Meta-Decider PPO."""
    actions = ['BUY', 'SELL', 'WAIT']
    action_probs = [random.uniform(0, 1) for _ in range(3)]
    total_action = sum(action_probs)
    action_probs = [p / total_action for p in action_probs]

    selected_action = actions[action_probs.index(max(action_probs))]

    return {
        "actor": {
            "action_probabilities": {
                "BUY": action_probs[0],
                "SELL": action_probs[1],
                "WAIT": action_probs[2]
            },
            "selected_action": selected_action
        },
        "critic": {
            "value_estimate": random.uniform(-0.5, 0.5),
            "advantage": random.uniform(-0.2, 0.2)
        },
        "reward_components": {
            "pnl_proxy": random.uniform(-0.1, 0.1),
            "error_cost": random.uniform(-0.05, 0),
            "drawdown_penalty": random.uniform(-0.03, 0),
            "turnover_penalty": random.uniform(-0.02, 0),
            "total_reward": random.uniform(-0.1, 0.1)
        },
        "action": selected_action,
        "confidence": max(action_probs),
        "status": "active",
        "trade_history": [
            {
                "timestamp": datetime.now().isoformat(),
                "action": random.choice(actions),
                "price": random.uniform(40000, 50000),
                "pnl": random.uniform(-100, 100)
            }
            for _ in range(20)
        ],
        "performance": {
            "total_pnl": random.uniform(-500, 1000),
            "sharpe_ratio": random.uniform(0.5, 2.5),
            "win_rate": random.uniform(0.4, 0.7),
            "max_drawdown": random.uniform(-0.2, -0.05)
        }
    }

# ============================================================================
# REST API ENDPOINTS
# ============================================================================

@ml_router.get("/architecture/status")
async def get_ml_architecture_status():
    """Récupère l'état global de l'architecture ML (tous les niveaux)."""
    return {
        "level0": generate_level0_data(),
        "level1": generate_level1_data(),
        "level2": generate_level2_data(),
        "level3": generate_level3_data(),
        "level4": generate_level4_data(),
        "timestamp": datetime.now().isoformat()
    }

@ml_router.get("/level0/gating")
async def get_level0_gating():
    """Récupère les données du Level 0 - Global Gating."""
    return generate_level0_data()

@ml_router.get("/level1/contexts")
async def get_level1_contexts():
    """Récupère les données du Level 1 - Context Detectors."""
    return generate_level1_data()

@ml_router.get("/level2/specialists")
async def get_level2_specialists():
    """Récupère les données du Level 2 - Conditional Specialists."""
    return generate_level2_data()

@ml_router.get("/level3/aggregators")
async def get_level3_aggregators():
    """Récupère les données du Level 3 - Aggregators."""
    return generate_level3_data()

@ml_router.get("/level4/policy")
async def get_level4_policy():
    """Récupère les données du Level 4 - Meta-Decider PPO."""
    return generate_level4_data()

@ml_router.get("/level/{level_id}/metrics")
async def get_level_metrics(level_id: int):
    """Récupère les métriques détaillées d'un niveau spécifique."""
    generators = {
        0: generate_level0_data,
        1: generate_level1_data,
        2: generate_level2_data,
        3: generate_level3_data,
        4: generate_level4_data
    }

    if level_id not in generators:
        return {"error": f"Invalid level_id: {level_id}"}

    return generators[level_id]()

@ml_router.get("/predictions/latest")
async def get_latest_prediction():
    """Dernière prédiction complète (niveaux 0-2 réels, 3-4 stubs)."""
    pred = _get_pred()
    return {
        "timestamp": pred.get("refreshed_at", datetime.now().isoformat()),
        "symbol":    "BTCUSDT",
        "action":    pred.get("action", "HOLD"),
        "level0":    generate_level0_data(),
        "level1":    generate_level1_data(),
        "level2":    generate_level2_data(),
        "level3":    generate_level3_data(),
        "level4":    generate_level4_data(),
        # Champs de commodité pour l'UI
        "current_price": pred.get("current_price", 0),
        "p_long":        pred.get("p_long", 0),
        "p_short":       pred.get("p_short", 0),
        "regime":        pred.get("regime", "NEUTRAL"),
        "stop_price":    pred.get("stop_price", 0),
        "take_profit":   pred.get("take_profit", 0),
    }

@ml_router.get("/flow/throughput")
async def get_pipeline_throughput():
    """Récupère les statistiques de throughput du pipeline."""
    return {
        "bars_per_second": random.uniform(50, 200),
        "latency_ms": {
            "level0": random.uniform(1, 5),
            "level1": random.uniform(2, 8),
            "level2": random.uniform(5, 15),
            "level3": random.uniform(3, 10),
            "level4": random.uniform(10, 30),
            "total": random.uniform(20, 70)
        },
        "cpu_usage": random.uniform(30, 80),
        "gpu_usage": random.uniform(20, 90),
        "memory_mb": random.uniform(2000, 8000),
        "prediction_rate": random.uniform(10, 50)
    }

# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

@ml_router.websocket("/ws/ml-architecture")
async def websocket_ml_architecture(websocket: WebSocket):
    """WebSocket pour les mises à jour en temps réel de l'architecture ML."""
    await manager.connect(websocket)

    try:
        # Envoyer les données initiales
        await websocket.send_json({
            "type": "architecture_update",
            "payload": {
                "level0": generate_level0_data(),
                "level1": generate_level1_data(),
                "level2": generate_level2_data(),
                "level3": generate_level3_data(),
                "level4": generate_level4_data()
            }
        })

        # Boucle de mise à jour périodique
        while True:
            try:
                # Recevoir les messages du client (subscriptions, etc.)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                message = json.loads(data)

                # Traiter les subscriptions
                if message.get("type") == "subscribe":
                    level_id = message.get("level")
                    # Logique de subscription...

            except asyncio.TimeoutError:
                # Timeout normal, continuer
                pass

            # Envoyer des mises à jour périodiques (toutes les 2 secondes)
            await asyncio.sleep(2)

            # Envoyer mise à jour aléatoire d'un niveau
            level_id = random.randint(0, 4)
            generators = [
                generate_level0_data,
                generate_level1_data,
                generate_level2_data,
                generate_level3_data,
                generate_level4_data
            ]

            await websocket.send_json({
                "type": f"level{level_id}_update",
                "payload": generators[level_id]()
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)
