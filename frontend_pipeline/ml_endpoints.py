"""
ML ARCHITECTURE ENDPOINTS
=========================
Endpoints pour exposer les données des 5 niveaux de l'architecture ML.
"""
import random
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List
import asyncio
import json

ml_router = APIRouter(prefix="/ml", tags=["ML Architecture"])

# ============================================================================
# MOCK DATA GENERATORS (À remplacer par les vrais modèles)
# ============================================================================

def generate_level0_data():
    """Génère des données mock pour Level 0 - Global Gating."""
    score = random.uniform(0.3, 0.9)
    threshold = 0.5

    return {
        "tradeability_score": score,
        "is_tradeable": score > threshold,
        "threshold": threshold,
        "features": {
            "realized_return": random.uniform(-0.02, 0.02),
            "realized_volatility": random.uniform(0.01, 0.05),
            "max_drawdown": random.uniform(-0.1, -0.01)
        },
        "quantiles": {
            "p10": random.uniform(0.1, 0.3),
            "p50": random.uniform(0.4, 0.6),
            "p90": random.uniform(0.7, 0.9)
        },
        "window_size": 256,
        "horizon": 12,
        "status": "active",
        "history": [
            {
                "timestamp": datetime.now().isoformat(),
                "score": random.uniform(0.3, 0.9),
                "tradeable": random.choice([True, False])
            }
            for _ in range(20)
        ]
    }

def generate_level1_data():
    """Génère des données mock pour Level 1 - Context Detectors."""
    direction_pred = random.choice(['down', 'flat', 'up'])
    patterns = random.sample(['impulse', 'reversal', 'breakout', 'squeeze'], k=random.randint(1, 3))

    return {
        "detectors": {
            "tradeability": {
                "name": "Tradeability Detector",
                "type": "binary",
                "output": {"tradeable": random.choice([True, False])},
                "confidence": random.uniform(0.6, 0.95),
                "active": True
            },
            "direction": {
                "name": "Direction Detector",
                "type": "3-class",
                "output": {
                    "predicted": direction_pred,
                    "down": random.uniform(0.1, 0.8),
                    "flat": random.uniform(0.1, 0.8),
                    "up": random.uniform(0.1, 0.8)
                },
                "confidence": random.uniform(0.6, 0.95),
                "active": True
            },
            "pattern": {
                "name": "Pattern Detector",
                "type": "multi-label",
                "output": {
                    "impulse": random.uniform(0.2, 0.9),
                    "reversal": random.uniform(0.2, 0.9),
                    "breakout": random.uniform(0.2, 0.9),
                    "squeeze": random.uniform(0.2, 0.9)
                },
                "confidence": random.uniform(0.6, 0.95),
                "active": True
            },
            "event": {
                "name": "Event Detector",
                "type": "rare_events",
                "output": {"type": random.choice(["volume_spike", "tail_risk", "none"])},
                "confidence": random.uniform(0.6, 0.95),
                "active": True
            },
            "pairwise": {
                "name": "Pairwise Context",
                "type": "4-class",
                "output": {
                    "trending": random.uniform(0.2, 0.8),
                    "mean_reverting": random.uniform(0.2, 0.8),
                    "high_vol": random.uniform(0.2, 0.8),
                    "low_vol": random.uniform(0.2, 0.8)
                },
                "confidence": random.uniform(0.6, 0.95),
                "active": True
            }
        },
        "direction": direction_pred,
        "active_patterns": patterns,
        "regime": random.choice(["trending", "mean_reverting", "high_vol", "low_vol"]),
        "status": "active"
    }

def generate_level2_data():
    """Génère des données mock pour Level 2 - Conditional Specialists."""
    experts = ['impulse', 'reversal', 'breakout', 'squeeze']
    weights = [random.uniform(0, 1) for _ in range(4)]
    total_weight = sum(weights)
    normalized_weights = [w / total_weight for w in weights]

    expert_data = {}
    for i, expert in enumerate(experts):
        expert_data[expert] = {
            "predicted_return": random.uniform(-0.05, 0.05),  # Augmenté de ±3% à ±5%
            "predicted_volatility": random.uniform(0.015, 0.04),  # Ajusté
            "confidence": normalized_weights[i],
            "active": normalized_weights[i] > 0.2
        }

    return {
        "router": {
            "mode": random.choice(["soft", "hard"]),
            "weights": {
                "impulse": normalized_weights[0],
                "reversal": normalized_weights[1],
                "breakout": normalized_weights[2],
                "squeeze": normalized_weights[3]
            },
            "selected_expert": experts[normalized_weights.index(max(normalized_weights))]
        },
        "experts": expert_data,
        "predicted_return": sum(expert_data[e]["predicted_return"] * normalized_weights[i]
                                for i, e in enumerate(experts)),
        "predicted_volatility": sum(expert_data[e]["predicted_volatility"] * normalized_weights[i]
                                     for i, e in enumerate(experts)),
        "active_expert": experts[normalized_weights.index(max(normalized_weights))],
        "status": "active"
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
    """Récupère la dernière prédiction complète (tous les niveaux)."""
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": "BTCUSDT",
        "level0": generate_level0_data(),
        "level1": generate_level1_data(),
        "level2": generate_level2_data(),
        "level3": generate_level3_data(),
        "level4": generate_level4_data()
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
