"""
API SERVER FOR ALPHA DASHBOARD
===============================
Serveur FastAPI pour exposer les données de trading alpha au frontend React.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import uvicorn
import logging
import sys
import random
import subprocess
import threading
import glob
from pydantic import BaseModel

# ── Chemin projet racine (pour importer ai.*) ─────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai" / "TRAIN"))

from mongo_utils import fetch_historical_from_mongo, normalize_symbol, get_db
from pymongo.errors import PyMongoError

# Add TRAIN to path for S3 access (déjà ajouté au-dessus)
from data.s3_data_source import S3DataSource

# Import data integrity analyzer
from data_integrity_analyzer import DataIntegrityAnalyzer

# ── PredictionEngine (inférence live) ────────────────────────────────────────
from prediction_engine import engine as _prediction_engine, init_engine


async def _background_refresh_loop() -> None:
    """Rafraîchit la prédiction toutes les 60 secondes."""
    while True:
        await asyncio.sleep(60)
        if _prediction_engine.ready:
            await _prediction_engine.refresh()


async def _autonomous_trading_loop() -> None:
    """Boucle autonome : trade toutes les 60 s à partir de la dernière prédiction."""
    await asyncio.sleep(5)   # laisser le temps au engine de s'initialiser
    while True:
        try:
            await _run_autonomous_trade()
        except Exception as exc:
            logging.getLogger("api_server").error(f"[Autonomous] erreur: {exc}", exc_info=True)
        await asyncio.sleep(60)


async def _fetch_ema_signal(symbol: str = "BTCUSDT") -> dict:
    """
    Signal de fallback basé sur EMA 7/25 (données Binance 1h).
    Retourne dict(action, price, confidence, reason) ou None si erreur réseau.
    """
    try:
        import httpx, pandas as pd
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": "1h", "limit": 50},
            )
            r.raise_for_status()
        candles = r.json()
        closes = [float(k[4]) for k in candles]
        price  = closes[-1]

        def ema(series, n):
            k = 2 / (n + 1)
            e = series[0]
            for v in series[1:]:
                e = v * k + e * (1 - k)
            return e

        ema7  = ema(closes[-7:],  7)
        ema25 = ema(closes[-25:], 25)
        ema7_prev  = ema(closes[-8:-1],  7)
        ema25_prev = ema(closes[-26:-1], 25)

        spread = abs(ema7 - ema25) / ema25
        bullish = ema7 > ema25

        # État courant : LONG si haussier, SHORT si baissier
        if bullish:
            action = "LONG"
            reason = f"EMA7({ema7:.0f}) > EMA25({ema25:.0f}) — tendance haussière (spread {spread*100:.2f}%)"
            confidence = min(0.9, 0.55 + spread * 15)
        else:
            action = "SHORT"
            reason = f"EMA7({ema7:.0f}) < EMA25({ema25:.0f}) — tendance baissière (spread {spread*100:.2f}%)"
            confidence = min(0.9, 0.55 + spread * 15)

        return {"action": action, "price": price, "confidence": confidence, "reason": reason}
    except Exception as exc:
        logging.getLogger("api_server").warning(f"[EMA fallback] erreur: {exc}")
        return None


async def _run_autonomous_trade() -> None:
    """Exécute une itération de trading autonome.

    Priorité 1 : signal du PredictionEngine ML (si modèles chargés).
    Priorité 2 : signal EMA 7/25 de fallback (Binance direct).
    """
    log = logging.getLogger("api_server")

    pred = _prediction_engine.last_prediction if _prediction_engine.ready else None
    signal = "UNAVAILABLE"
    action_taken = "NONE"
    symbol = "BTCUSDT"
    price = 0.0
    confidence = 0.0
    reason = "Pas de prédiction"
    signal_source = "ML"

    if pred:
        signal     = pred.get("action", "HOLD")
        price      = float(pred.get("current_price", 0))
        confidence = float(pred.get("confidence", 0))
        reason     = pred.get("reason", "AI autonome")
        symbol     = pred.get("symbol", "BTCUSDT")
    else:
        # Fallback : EMA 7/25
        signal_source = "EMA"
        fb = await _fetch_ema_signal(symbol)
        if fb:
            signal     = fb["action"]
            price      = fb["price"]
            confidence = fb["confidence"]
            reason     = fb["reason"]
            log.info(f"[Fallback EMA] {signal} @ {price:.0f} — {reason}")

    state = _load_portfolio_state()

    # Mettre à jour les prix courants
    if price > 0:
        for pos in state.get("positions", []):
            if pos.get("symbol") == symbol:
                pos["current_price"] = price

    # Vérifier stop-loss / take-profit
    if price > 0:
        for pos in list(state.get("positions", [])):
            if pos.get("symbol") != symbol:
                continue
            entry_price = float(pos.get("entry_price", 0))
            if entry_price <= 0:
                continue
            pnl_pct = (price - entry_price) / entry_price * 100
            if pnl_pct <= -AUTONOMOUS_STOP_LOSS_PCT:
                state = _apply_trade_logic(state, symbol, "SELL", price, 1.0,
                                           f"Stop Loss {pnl_pct:.1f}%")
                action_taken = "STOP_LOSS"
                log.info(f"[Autonomous] STOP_LOSS {symbol} pnl={pnl_pct:.1f}%")
                break
            elif pnl_pct >= AUTONOMOUS_TAKE_PROFIT_PCT:
                state = _apply_trade_logic(state, symbol, "SELL", price, 1.0,
                                           f"Take Profit +{pnl_pct:.1f}%")
                action_taken = "TAKE_PROFIT"
                log.info(f"[Autonomous] TAKE_PROFIT {symbol} pnl=+{pnl_pct:.1f}%")
                break

    # Exécuter le signal AI (seulement si pas de stop/TP déclenché)
    if action_taken == "NONE" and price > 0 and signal not in ("HOLD", "UNAVAILABLE"):
        existing = next(
            (p for p in state.get("positions", []) if p.get("symbol") == symbol), None)
        if signal == "LONG" and not existing:
            state = _apply_trade_logic(state, symbol, "BUY", price, confidence, reason)
            action_taken = "BUY"
            log.info(f"[Autonomous] BUY {symbol} @ {price} conf={confidence:.3f}")
        elif signal == "SHORT" and existing:
            state = _apply_trade_logic(state, symbol, "SELL", price, confidence, reason)
            action_taken = "SELL"
            log.info(f"[Autonomous] SELL {symbol} @ {price} conf={confidence:.3f}")

    # Snapshot minute
    _append_history(state, signal=signal, action_taken=action_taken)
    _persist_portfolio_state(state)
    stats = _calculate_stats(state)
    log.info(
        f"[Autonomous/{signal_source}] signal={signal} action={action_taken} "
        f"price={price:.0f} value=${stats['total_value']:.0f} pnl={stats['total_pnl_percent']:.2f}%"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: F811
    # Startup
    logger = logging.getLogger("api_server")
    try:
        await init_engine()
        if _prediction_engine.ready:
            logger.info("PredictionEngine initialisé")
        else:
            logger.warning("PredictionEngine non chargé: aucun run ML valide disponible")
    except Exception as e:
        logger.error(f"PredictionEngine non disponible: {e}")
    asyncio.create_task(_background_refresh_loop())
    asyncio.create_task(_autonomous_trading_loop())
    yield
    # Shutdown (rien à faire)


# Import ML endpoints
from ml_endpoints import ml_router

app = FastAPI(title="Alpha Trading API", version="2.0", lifespan=lifespan)

# Include ML architecture router
app.include_router(ml_router)

# ============================================================================
# TRAINING JOB MANAGEMENT
# ============================================================================

# Training jobs storage (in-memory)
training_jobs = {}  # {job_id: {...job_info...}}
training_lock = threading.Lock()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PORTFOLIO_COLLECTION = os.getenv("PORTFOLIO_COLLECTION", "portfolio_state")
PORTFOLIO_DOC_ID = "default"
PORTFOLIO_INITIAL_CAPITAL = float(os.getenv("PORTFOLIO_INITIAL_CAPITAL", "100000"))
AUTONOMOUS_STOP_LOSS_PCT  = float(os.getenv("AUTONOMOUS_STOP_LOSS_PCT",  "3.0"))
AUTONOMOUS_TAKE_PROFIT_PCT = float(os.getenv("AUTONOMOUS_TAKE_PROFIT_PCT", "6.0"))

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
FRONTEND_ORIGIN_REGEX = os.getenv("FRONTEND_ORIGIN_REGEX", r"https?://.*:3000")


# CORS pour permettre les requêtes depuis React
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=FRONTEND_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_latest_dataset_path() -> Path:
    """Trouver le dataset le plus récent."""
    datasets_path = Path("datasets/alpha_trading")
    if not datasets_path.exists():
        raise HTTPException(status_code=404, detail="No datasets found")

    dataset_folders = sorted(datasets_path.glob("dataset_*"), reverse=True)
    if not dataset_folders:
        raise HTTPException(status_code=404, detail="No dataset folders found")

    return dataset_folders[0]

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Alpha Trading API",
        "version": "2.0",
        "status": "operational",
        "endpoints": [
            "/dataset/summary",
            "/dataset/signals",
            "/dataset/ohlcv/{symbol}",
            "/dataset/funding-rates",
            "/dataset/fear-greed",
            "/dataset/sentiment",
            "/dataset/macro",
            "/dataset/derivatives",
            "/market/all-cryptos",
            "/market/ticker",
            "/market/klines",
            "/market/orderbook",
            "/market/trades",
            "/portfolio/state",
            "/portfolio/trade",
            "/portfolio/reset",
        ]
    }

@app.get("/dataset/summary")
async def get_dataset_summary():
    """Récupérer le résumé du dataset."""
    dataset_path = get_latest_dataset_path()

    # Charger metadata
    metadata_file = dataset_path / "metadata.json"
    if metadata_file.exists():
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    # Compter les records par source
    data_sources = {}
    for parquet_file in dataset_path.glob("*.parquet"):
        try:
            df = pd.read_parquet(parquet_file)
            data_sources[parquet_file.stem] = {
                "records": len(df),
                "columns": list(df.columns),
                "size_mb": parquet_file.stat().st_size / (1024 * 1024)
            }
        except Exception as e:
            data_sources[parquet_file.stem] = {"error": str(e)}

    return {
        "dataset_name": dataset_path.name,
        "metadata": metadata,
        "data_sources": data_sources,
        "total_records": sum(
            source.get("records", 0)
            for source in data_sources.values()
        )
    }


# ============================================================================
# PORTFOLIO MANAGEMENT (MongoDB)
# ============================================================================

class TradeRequest(BaseModel):
    symbol: str
    action: str
    price: float
    confidence: Optional[float] = 0.5
    reason: Optional[str] = None

class TrainingStartRequest(BaseModel):
    config: str = "pipeline"
    device: str = "auto"          # "cpu", "cuda", "auto"
    debug_mode: bool = False
    mode: str = "combined"        # "long", "short", "combined"
    data_path: Optional[str] = None
    test_from: int = 2024
    auto_calibrate: bool = True
    skip_tcn: bool = False
    require_short_stability: bool = True
    tradeable_q: float = 0.70
    cost: float = 0.001
    filter_threshold_long: float = 0.40
    direction_threshold_long: float = 0.52
    filter_threshold_short: float = 0.45
    direction_threshold_short: float = 0.55
    risk_long: float = 0.002
    risk_short: float = 0.001
    max_losses_long: int = 3
    max_losses_short: int = 2
    cooldown_long: int = 2
    cooldown_short: int = 3
    grid: bool = False
    compare_models: bool = False
    regression: bool = False
    top_pct: float = 0.01
    margin: float = 0.001
    epochs: int = 100
    batch_size: int = 128
    learning_rate: float = 0.001
    symbol: str = "BTCUSDT"


# ============================================================================
# TRAINING HELPER FUNCTIONS
# ============================================================================

def parse_training_log(log_file_path: str) -> Dict[str, any]:
    """Parse training log file to extract current metrics."""
    metrics = {
        "current_epoch": 0,
        "total_epochs": 0,
        "train_loss": 0.0,
        "val_loss": 0.0,
        "val_sharpe": 0.0,
        "learning_rate": 0.0
    }

    if not Path(log_file_path).exists():
        return metrics

    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()

        # Parse from the end of file (most recent metrics)
        for line in reversed(lines[-50:]):  # Check last 50 lines
            # Pattern: Epoch 5/50 | Train Loss: 0.452341 | Val Loss: 0.389234 | Val Sharpe: 1.2345 | LR: 1.00e-04
            if "Epoch" in line and "/" in line:
                import re
                epoch_match = re.search(r'Epoch (\d+)/(\d+)', line)
                if epoch_match:
                    metrics["current_epoch"] = int(epoch_match.group(1))
                    metrics["total_epochs"] = int(epoch_match.group(2))

                train_loss_match = re.search(r'Train Loss: (-?[\d.]+)', line)
                if train_loss_match:
                    metrics["train_loss"] = float(train_loss_match.group(1))

                val_loss_match = re.search(r'Val Loss: (-?[\d.]+)', line)
                if val_loss_match:
                    metrics["val_loss"] = float(val_loss_match.group(1))

                sharpe_match = re.search(r'Val Sharpe: (-?[\d.]+)', line)
                if sharpe_match:
                    metrics["val_sharpe"] = float(sharpe_match.group(1))

                lr_match = re.search(r'LR: ([\d.e-]+)', line)
                if lr_match:
                    metrics["learning_rate"] = float(lr_match.group(1))

                break  # Found the most recent epoch line

    except Exception as e:
        logger.error(f"Error parsing training log: {e}")

    return metrics


def monitor_training_process(job_id: str):
    """Background thread to monitor training process and update job status."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    process = job["process"]

    try:
        while True:
            # Check if process is still running
            poll_result = process.poll()

            if poll_result is not None:
                # Process has finished
                with training_lock:
                    if job.get("status") == "stopped":
                        job["end_time"] = job.get("end_time") or datetime.utcnow()
                        logger.info(f"Training job {job_id} already stopped")
                        break
                    job["end_time"] = datetime.utcnow()
                    validation = _finalize_training_validation(job, poll_result)
                    if poll_result == 0 and validation["status"] in {"passed", "warning"}:
                        job["status"] = "completed"
                        logger.info(f"Training job {job_id} completed successfully")
                    else:
                        job["status"] = "failed"
                        job["error"] = validation["message"]
                        logger.error(f"Training job {job_id} failed validation: {validation['message']}")
                break

            # Update metrics from log file
            metrics = parse_training_log(job["log_file"])
            with training_lock:
                job["current_epoch"] = metrics["current_epoch"]
                job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                job["current_loss"] = metrics["train_loss"]
                job["current_val_loss"] = metrics["val_loss"]
                job["current_sharpe"] = metrics["val_sharpe"]
                _refresh_training_components(job, final=False)

                if job["total_epochs"] > 0:
                    epoch_progress = (metrics["current_epoch"] / job["total_epochs"]) * 100.0
                    job["progress_pct"] = max(_component_progress(job), epoch_progress)
                else:
                    job["progress_pct"] = _component_progress(job)

            # Sleep for 2 seconds before next check
            threading.Event().wait(2.0)

        save_training_metadata(job_id)

    except Exception as e:
        logger.error(f"Error monitoring training job {job_id}: {e}")
        with training_lock:
            job["status"] = "failed"
            job["error"] = str(e)
            _set_component_status(job, "full_pipeline", "failed", str(e))
        save_training_metadata(job_id)


def save_training_metadata(job_id: str):
    """Save training job metadata to JSON file."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id].copy()

    # Remove non-serializable fields
    job.pop("process", None)

    # Convert datetime objects
    if "start_time" in job and isinstance(job["start_time"], datetime):
        job["start_time"] = job["start_time"].isoformat()
    if "end_time" in job and isinstance(job["end_time"], datetime):
        job["end_time"] = job["end_time"].isoformat()

    # Save to JSON file alongside checkpoint
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    metadata_file = checkpoints_dir / f"{job_id}_metadata.json"
    try:
        with open(metadata_file, 'w') as f:
            json.dump(job, f, indent=2)
        logger.info(f"Saved metadata for job {job_id} to {metadata_file}")
    except Exception as e:
        logger.error(f"Error saving metadata: {e}")


def get_available_configs() -> List[str]:
    """Get list of available training configurations."""
    configs_dir = Path(__file__).parent.parent / "ai" / "configs"
    if not configs_dir.exists():
        return []

    config_files = list(configs_dir.glob("train_*.yaml"))
    return [f.name for f in sorted(config_files)]


TRAINING_SYMBOL_UNIVERSE = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "MATICUSDT",
]

TRAINING_COMPONENT_SPECS = [
    {
        "id": "data_contract",
        "name": "Données + split chrono",
        "required_modes": ("long", "short", "combined"),
    },
    {
        "id": "labels",
        "name": "Labels long/short/tradeable",
        "required_modes": ("long", "short", "combined"),
    },
    {
        "id": "filter",
        "name": "Filtre tradeable",
        "required_modes": ("long", "short", "combined"),
    },
    {
        "id": "edge_long",
        "name": "Edge model LONG",
        "required_modes": ("long", "combined"),
    },
    {
        "id": "edge_short",
        "name": "Edge model SHORT",
        "required_modes": ("short", "combined"),
    },
    {
        "id": "regime",
        "name": "Gate régime bear",
        "required_modes": (),
    },
    {
        "id": "specialists",
        "name": "Experts par contexte",
        "required_modes": (),
    },
    {
        "id": "backtest_long",
        "name": "Backtest LONG",
        "required_modes": ("long", "combined"),
    },
    {
        "id": "backtest_short",
        "name": "Backtest SHORT",
        "required_modes": ("short", "combined"),
    },
    {
        "id": "backtest_combined",
        "name": "Backtest pipeline complet",
        "required_modes": ("combined",),
    },
    {
        "id": "full_pipeline",
        "name": "Contrat artefacts + synthèse",
        "required_modes": ("long", "short", "combined"),
    },
]

TRAINING_STAGE_MARKERS = [
    ("data_contract", ("CHARGEMENT DES DONNÉES", "SPLIT CHRONOLOGIQUE")),
    ("labels", ("CONSTRUCTION DES LABELS",)),
    ("filter", ("STAGE 1", "FILTRE TRADEABLE")),
    ("edge_long", ("EDGE MODEL LONG",)),
    ("edge_short", ("EDGE MODEL SHORT",)),
    ("regime", ("META-MODÈLE RÉGIME BEAR",)),
    ("specialists", ("STAGE 3", "EXPERTS PAR CONTEXTE")),
    ("backtest_long", ("BACKTEST LONG",)),
    ("backtest_short", ("BACKTEST SHORT", "WALK-FORWARD SHORT")),
    ("backtest_combined", ("BACKTEST COMBINÉ",)),
    ("full_pipeline", ("Pipeline terminé", "pipeline_summary.json")),
]


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _normalise_training_mode(mode: str) -> str:
    mode = (mode or "combined").lower().strip()
    if mode not in {"long", "short", "combined"}:
        raise HTTPException(status_code=400, detail=f"Training mode invalide: {mode}")
    return mode


def _symbol_base(symbol: str) -> str:
    symbol = normalize_symbol(symbol).replace("/", "").upper()
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _training_data_candidates(symbol: str) -> List[Path]:
    symbol = normalize_symbol(symbol).replace("/", "").upper()
    base = _symbol_base(symbol)
    return [
        _ROOT / "data" / f"{symbol}_1h_features.csv",
        _ROOT / "data" / f"{base}USDT_1h_features.csv",
        _ROOT / "data" / f"{base}USD_1h_features.csv",
        _ROOT / "data" / f"bundle_{base.lower()}" / "features_merged.parquet",
        _ROOT / "data" / f"bundle_{base.lower()}" / "raw" / "base_ohlcv.parquet",
    ]


def resolve_training_data(
    symbol: str,
    raise_on_missing: bool = True,
    data_path: Optional[str] = None,
) -> Optional[Path]:
    """Resolve a local dataset with enough history for the canonical pipeline."""
    symbol_key = normalize_symbol(symbol).replace("/", "").upper()
    if data_path:
        candidate = Path(data_path).expanduser()
        if not candidate.is_absolute():
            candidate = _ROOT / candidate
        if candidate.exists():
            return candidate
        if raise_on_missing:
            raise HTTPException(
                status_code=404,
                detail=f"Dataset demandé introuvable pour {symbol_key}: {candidate}",
            )
        return None

    env_keys = [
        f"FUTUR_TRAINING_DATA_{symbol_key}",
        f"FUTUR_TRAINING_DATA_{_symbol_base(symbol_key)}",
        "FUTUR_TRAINING_DATA",
    ]
    for env_key in env_keys:
        raw = os.getenv(env_key)
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = _ROOT / candidate
        if candidate.exists():
            return candidate

    for candidate in _training_data_candidates(symbol_key):
        if candidate.exists():
            return candidate

    if raise_on_missing:
        checked = ", ".join(str(p.relative_to(_ROOT)) for p in _training_data_candidates(symbol_key))
        raise HTTPException(
            status_code=404,
            detail=(
                f"Aucun dataset d'entraînement local trouvé pour {symbol_key}. "
                f"Chemins essayés: {checked}. Ajoute FUTUR_TRAINING_DATA_{symbol_key} "
                "ou charge un historique complet avant de lancer ce symbole."
            ),
        )
    return None


def _build_training_components(mode: str) -> List[Dict[str, Any]]:
    mode = _normalise_training_mode(mode)
    components: List[Dict[str, Any]] = []
    for idx, spec in enumerate(TRAINING_COMPONENT_SPECS):
        required = mode in spec["required_modes"]
        components.append({
            "id": spec["id"],
            "name": spec["name"],
            "status": "pending" if required else "skipped",
            "required": required,
            "order": idx,
            "message": "En attente" if required else "Optionnel pour ce mode",
            "metrics": {},
            "started_at": None,
            "ended_at": None,
        })
    return components


def _component(job: Dict[str, Any], component_id: str) -> Optional[Dict[str, Any]]:
    for component in job.get("components", []):
        if component.get("id") == component_id:
            return component
    return None


def _set_component_status(
    job: Dict[str, Any],
    component_id: str,
    status: str,
    message: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> None:
    component = _component(job, component_id)
    if component is None:
        return

    terminal = {"passed", "failed", "warning", "skipped"}
    if component.get("status") in terminal and status == "running":
        return

    component["status"] = status
    if message is not None:
        component["message"] = message
    if metrics:
        component["metrics"] = {**component.get("metrics", {}), **metrics}
    if status == "running" and not component.get("started_at"):
        component["started_at"] = _utc_now_iso()
    if status in terminal and not component.get("ended_at"):
        component["ended_at"] = _utc_now_iso()


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning(f"Impossible de lire {path}: {exc}")
        return None


def _tail_log_text(log_file: str, max_lines: int = 400) -> str:
    path = Path(log_file)
    if not path.exists():
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-max_lines:])
    except Exception:
        return ""


def _mark_running_stage_from_logs(job: Dict[str, Any]) -> None:
    text = _tail_log_text(job.get("log_file", ""))
    if not text:
        return

    last_component = None
    last_pos = -1
    for component_id, markers in TRAINING_STAGE_MARKERS:
        positions = [text.rfind(marker) for marker in markers]
        pos = max(positions) if positions else -1
        if pos > last_pos:
            last_pos = pos
            last_component = component_id

    if last_component:
        _set_component_status(job, last_component, "running", "Étape active dans les logs")

    lowered = text.lower()
    if "split chronologique impossible" in lowered or "colonnes manquantes" in lowered:
        _set_component_status(job, "data_contract", "failed", "Contrat dataset invalide")
    if "traceback" in lowered or "pipeline failed" in lowered:
        active = last_component or "full_pipeline"
        _set_component_status(job, active, "failed", "Erreur détectée dans les logs")


def _best_edge_metrics(metrics: Optional[Dict[str, Any]], side: str) -> Dict[str, Any]:
    if not metrics:
        return {}
    models = metrics.get("models") if isinstance(metrics.get("models"), list) else []
    if not models:
        return {}
    best = max(models, key=lambda item: float(item.get("macro_f1", 0.0) or 0.0))
    return {
        "model": best.get("model"),
        "auc": best.get("auc"),
        "macro_f1": best.get("macro_f1"),
        f"precision_{side}": best.get(f"precision_{side}"),
        f"recall_{side}": best.get(f"recall_{side}"),
    }


def _summarise_backtest(summary: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    metrics = {
        "trades": summary.get("n_trades", 0),
        "profit_factor": summary.get("profit_factor"),
        "sharpe": summary.get("sharpe_annualized"),
        "max_drawdown": summary.get("max_drawdown"),
        "total_return_pct": summary.get("total_return_pct"),
        "win_rate": summary.get("win_rate"),
    }

    trades = int(summary.get("n_trades", 0) or 0)
    pf = float(summary.get("profit_factor", 0.0) or 0.0)
    max_dd = float(summary.get("max_drawdown", 0.0) or 0.0)

    warnings = []
    if trades <= 0:
        warnings.append("aucun trade")
    if pf < 1.0:
        warnings.append("profit factor < 1.0")
    if max_dd > 0.25:
        warnings.append("drawdown > 25%")

    if warnings:
        return "warning", "Backtest produit, gates prudents non satisfaits: " + ", ".join(warnings), metrics
    return "passed", "Backtest produit et contrôles de base OK", metrics


def _short_disabled_reason(run_dir: Path) -> Optional[str]:
    summary = _safe_read_json(run_dir / "pipeline_summary.json") or {}
    return summary.get("short_disabled_reason")


def _refresh_training_components(job: Dict[str, Any], final: bool = False) -> None:
    """Update component states from logs and generated artifacts."""
    if not job.get("components"):
        job["components"] = _build_training_components(job.get("mode", "combined"))

    _mark_running_stage_from_logs(job)

    run_dir = Path(job.get("run_dir", ""))
    mode = job.get("mode", "combined")
    short_disabled = _short_disabled_reason(run_dir) if run_dir.exists() else None

    if not run_dir.exists():
        if final:
            _set_component_status(job, "data_contract", "failed", "Dossier de run introuvable")
        return

    labels = _safe_read_json(run_dir / "labels.json")
    if labels:
        try:
            from core.artifacts import validate_pipeline_label_stats
            validate_pipeline_label_stats(labels)
            _set_component_status(job, "data_contract", "passed", "Dataset chargé et split chronologique exécuté")
            _set_component_status(job, "labels", "passed", "Contrat de labels validé", {
                "n_total": labels.get("n_total"),
                "n_long": labels.get("n_long"),
                "n_short": labels.get("n_short"),
            })
        except Exception as exc:
            _set_component_status(job, "labels", "failed", f"Contrat labels invalide: {exc}")
    elif final:
        _set_component_status(job, "data_contract", "failed", "Chargement/split non validé")
        _set_component_status(job, "labels", "failed", "labels.json absent")

    try:
        from core.artifacts.pipeline import (
            component_enabled,
            resolve_edge_component,
            resolve_filter_component,
            resolve_regime_component,
        )

        filter_component = resolve_filter_component(run_dir)
        filter_meta = _safe_read_json(filter_component.metadata) if filter_component.metadata else None
        if filter_component.model and filter_component.scaler:
            _set_component_status(job, "filter", "passed", "Modèle filtre et scaler présents", {
                "val_auc": filter_meta.get("val_auc") if filter_meta else None,
                "val_f1": filter_meta.get("val_f1") if filter_meta else None,
                "thr_long": filter_meta.get("threshold_long") if filter_meta else None,
                "thr_short": filter_meta.get("threshold_short") if filter_meta else None,
            })
        elif final:
            _set_component_status(job, "filter", "failed", "Artefacts filtre incomplets")

        for side in ("long", "short"):
            component_id = f"edge_{side}"
            required = bool(_component(job, component_id) and _component(job, component_id).get("required"))
            edge_component = resolve_edge_component(run_dir, side)
            edge_meta = _safe_read_json(edge_component.metadata) if edge_component.metadata else None
            edge_metrics = _safe_read_json((edge_component.directory or run_dir / component_id) / "metrics.json")

            if edge_component.model and edge_component.scaler:
                enabled = component_enabled(edge_meta, default=True)
                status = "passed" if enabled else "warning"
                message = "Artefacts edge présents"
                if not enabled:
                    message = edge_meta.get("disabled_reason") if edge_meta else None
                    message = f"Entraîné mais non déployé: {message or 'gate de robustesse'}"
                _set_component_status(job, component_id, status, message, _best_edge_metrics(edge_metrics, side))
            elif final and required:
                if side == "short" and short_disabled:
                    _set_component_status(job, component_id, "warning", f"SHORT rejeté par robustesse: {short_disabled}")
                else:
                    _set_component_status(job, component_id, "failed", f"Artefacts edge {side} absents")

        regime_component = resolve_regime_component(run_dir)
        if regime_component.model and regime_component.scaler:
            _set_component_status(job, "regime", "passed", "Gate régime bear disponible")
        elif final:
            _set_component_status(job, "regime", "skipped", "Gate régime non produit")
    except Exception as exc:
        logger.warning(f"Validation artefacts modèle impossible: {exc}")
        if final:
            _set_component_status(job, "full_pipeline", "failed", f"Validation artefacts impossible: {exc}")

    specialists_dir = run_dir / "specialists"
    if specialists_dir.exists() and any(specialists_dir.iterdir()):
        _set_component_status(job, "specialists", "passed", "Experts de contexte produits")
    elif final:
        _set_component_status(job, "specialists", "skipped", "Experts absents ou non retenus")

    for bt_id in ("backtest_long", "backtest_short", "backtest_combined"):
        component = _component(job, bt_id)
        required = bool(component and component.get("required"))
        summary_path = run_dir / bt_id / "summary.json"
        bt_summary = _safe_read_json(summary_path)
        if bt_summary:
            status, message, metrics = _summarise_backtest(bt_summary)
            _set_component_status(job, bt_id, status, message, metrics)
        elif final and required:
            if "short" in bt_id and short_disabled:
                _set_component_status(job, bt_id, "warning", f"Non produit car SHORT rejeté: {short_disabled}")
            elif bt_id == "backtest_combined" and short_disabled and mode == "combined":
                _set_component_status(job, bt_id, "warning", f"Combiné non produit car SHORT rejeté: {short_disabled}")
            else:
                _set_component_status(job, bt_id, "failed", "Résumé backtest absent")

    manifest_ok = (run_dir / "manifest.json").exists()
    summary_ok = (run_dir / "pipeline_summary.json").exists()
    if manifest_ok and summary_ok:
        status = "passed"
        message = "Manifest et synthèse pipeline présents"
        if any(c.get("status") == "warning" and c.get("required") for c in job.get("components", [])):
            status = "warning"
            message = "Pipeline terminé avec gates non déployables à revoir"
        _set_component_status(job, "full_pipeline", status, message, {
            "run_dir": str(run_dir),
        })
    elif final:
        _set_component_status(job, "full_pipeline", "failed", "Manifest ou pipeline_summary manquant")


def _component_progress(job: Dict[str, Any]) -> float:
    components = [c for c in job.get("components", []) if c.get("required")]
    if not components:
        return 0.0
    terminal = {"passed", "warning", "failed", "skipped"}
    done = sum(1 for c in components if c.get("status") in terminal)
    running_bonus = 0.35 if any(c.get("status") == "running" for c in components) else 0.0
    return min(100.0, ((done + running_bonus) / len(components)) * 100.0)


def _finalize_training_validation(job: Dict[str, Any], process_exit_code: int) -> Dict[str, Any]:
    _refresh_training_components(job, final=True)
    components = job.get("components", [])
    required = [c for c in components if c.get("required")]
    failed = [c for c in required if c.get("status") == "failed"]
    warnings = [c for c in required if c.get("status") == "warning"]

    if process_exit_code != 0:
        status = "failed"
        message = f"Process exited with code {process_exit_code}"
    elif failed:
        status = "failed"
        message = "Validation rejetée: " + ", ".join(c["name"] for c in failed)
    elif warnings:
        status = "warning"
        message = "Pipeline terminé, mais certains gates ne sont pas déployables"
    else:
        status = "passed"
        message = "Toutes les validations requises sont passées"

    validation = {
        "status": status,
        "message": message,
        "required": len(required),
        "passed": sum(1 for c in required if c.get("status") == "passed"),
        "warnings": len(warnings),
        "failed": len(failed),
        "run_dir": job.get("run_dir"),
    }
    job["validation_summary"] = validation
    job["progress_pct"] = 100.0
    return validation


def _training_cli_settings(request: TrainingStartRequest) -> Dict[str, Any]:
    """Serializable snapshot of all front-driven pipeline controls."""
    return {
        "mode": request.mode,
        "data_path": request.data_path,
        "test_from": request.test_from,
        "auto_calibrate": request.auto_calibrate,
        "skip_tcn": request.skip_tcn,
        "require_short_stability": request.require_short_stability,
        "tradeable_q": request.tradeable_q,
        "cost": request.cost,
        "filter_threshold_long": request.filter_threshold_long,
        "direction_threshold_long": request.direction_threshold_long,
        "filter_threshold_short": request.filter_threshold_short,
        "direction_threshold_short": request.direction_threshold_short,
        "risk_long": request.risk_long,
        "risk_short": request.risk_short,
        "max_losses_long": request.max_losses_long,
        "max_losses_short": request.max_losses_short,
        "cooldown_long": request.cooldown_long,
        "cooldown_short": request.cooldown_short,
        "grid": request.grid,
        "compare_models": request.compare_models,
        "regression": request.regression,
        "top_pct": request.top_pct,
        "margin": request.margin,
    }


# ============================================================================
# AWS TRAINING HELPER FUNCTIONS
# ============================================================================

def launch_aws_training(job_id: str, config: str, instance_type: str, aws_region: str, debug_mode: bool) -> Dict:
    """Launch training on AWS EC2 via shell script wrapper."""

    # Path to the launch script
    script_path = Path(__file__).parent.parent / "ai" / "scripts" / "launch_aws_training.sh"

    if not script_path.exists():
        raise FileNotFoundError(f"AWS launch script not found: {script_path}")

    # Build command
    config_name = config.replace(".yaml", "")  # Script adds .yaml automatically
    cmd = [
        "bash",
        str(script_path),
        config_name,
        instance_type
    ]

    # Environment variables
    env = os.environ.copy()
    env["AWS_REGION"] = aws_region
    env["KEY_NAME"] = "trading-ml-key"
    env["SECURITY_GROUP"] = "trading-ml-sg"
    env["S3_BUCKET"] = "qbia"

    # Log file for the launch script output
    log_file = Path(f"/tmp/training_aws_{job_id}.log")

    # Launch the process
    logger.info(f"Launching AWS training: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=open(log_file, 'w'),
        stderr=subprocess.STDOUT,
        cwd=str(script_path.parent.parent)
    )

    return {
        "process": process,
        "log_file": str(log_file),
        "is_aws": True,
        "instance_type": instance_type,
        "aws_region": aws_region
    }


def parse_aws_instance_info(job_id: str) -> Optional[Dict]:
    """Parse AWS instance info from the JSON file created by launch script."""

    info_file = Path("/tmp/aws_training_instance.json")

    if not info_file.exists():
        return None

    try:
        with open(info_file, 'r') as f:
            data = json.load(f)

        return {
            "instance_id": data.get("instance_id"),
            "public_ip": data.get("public_ip"),
            "instance_type": data.get("instance_type"),
            "s3_models_path": data.get("s3_models_path"),
            "launched_at": data.get("launched_at")
        }
    except Exception as e:
        logger.error(f"Error parsing AWS instance info: {e}")
        return None


def parse_training_log_from_text(log_text: str) -> Dict[str, any]:
    """Parse training metrics from log text (used for SSH logs)."""
    import re

    metrics = {
        "current_epoch": 0,
        "total_epochs": 0,
        "train_loss": 0.0,
        "val_loss": 0.0,
        "val_sharpe": 0.0,
        "learning_rate": 0.0
    }

    try:
        lines = log_text.split('\n')

        # Parse from the end (most recent metrics)
        for line in reversed(lines[-50:]):
            if "Epoch" in line and "/" in line:
                epoch_match = re.search(r'Epoch (\d+)/(\d+)', line)
                if epoch_match:
                    metrics["current_epoch"] = int(epoch_match.group(1))
                    metrics["total_epochs"] = int(epoch_match.group(2))

                train_loss_match = re.search(r'Train Loss: (-?[\d.]+)', line)
                if train_loss_match:
                    metrics["train_loss"] = float(train_loss_match.group(1))

                val_loss_match = re.search(r'Val Loss: (-?[\d.]+)', line)
                if val_loss_match:
                    metrics["val_loss"] = float(val_loss_match.group(1))

                sharpe_match = re.search(r'Val Sharpe: (-?[\d.]+)', line)
                if sharpe_match:
                    metrics["val_sharpe"] = float(sharpe_match.group(1))

                lr_match = re.search(r'LR: ([\d.e-]+)', line)
                if lr_match:
                    metrics["learning_rate"] = float(lr_match.group(1))

                break  # Found the most recent epoch line

    except Exception as e:
        logger.error(f"Error parsing training log text: {e}")

    return metrics


def monitor_aws_training(job_id: str):
    """Background thread to monitor AWS training job."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    # Wait for AWS instance info to become available
    max_wait = 300  # 5 minutes
    waited = 0
    aws_info = None

    logger.info(f"Waiting for AWS instance info for job {job_id}...")

    while waited < max_wait and aws_info is None:
        aws_info = parse_aws_instance_info(job_id)
        if aws_info:
            break
        threading.Event().wait(10)
        waited += 10

    if not aws_info:
        logger.error(f"Failed to get AWS instance info for job {job_id}")
        with training_lock:
            job["status"] = "failed"
            job["error"] = "Failed to get AWS instance info after 5 minutes"
        return

    # Update job with AWS info
    logger.info(f"AWS instance launched: {aws_info['instance_id']} at {aws_info['public_ip']}")
    with training_lock:
        job["aws_instance_id"] = aws_info["instance_id"]
        job["aws_public_ip"] = aws_info["public_ip"]
        job["aws_s3_path"] = aws_info["s3_models_path"]
        job["status"] = "running"  # Change from "launching" to "running"

    # Monitor via SSH
    instance_ip = aws_info["public_ip"]
    key_path = os.path.expanduser("~/.ssh/trading-ml-key.pem")

    # Wait a bit for SSH to be available
    logger.info(f"Waiting for SSH access to {instance_ip}...")
    threading.Event().wait(30)

    while True:
        # Check if local process (launch script) is still running
        process = job.get("process")
        if process and process.poll() is not None:
            # Launch script has finished
            with training_lock:
                if process.returncode == 0:
                    job["status"] = "completed"
                    logger.info(f"AWS training job {job_id} completed successfully")
                else:
                    job["status"] = "failed"
                    job["error"] = f"AWS launch script failed with code {process.returncode}"
                    logger.error(f"AWS training job {job_id} failed")

                job["end_time"] = datetime.utcnow()
                save_training_metadata(job_id)
            break

        # Retrieve logs from EC2 instance via SSH
        try:
            ssh_cmd = [
                "ssh",
                "-i", key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                f"ubuntu@{instance_ip}",
                "tail -50 /home/ubuntu/trading-ml/training.log 2>/dev/null || echo 'Log not ready'"
            ]

            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and "Log not ready" not in result.stdout:
                # Parse logs for metrics
                metrics = parse_training_log_from_text(result.stdout)

                with training_lock:
                    job["current_epoch"] = metrics["current_epoch"]
                    job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                    job["current_loss"] = metrics["train_loss"]
                    job["current_val_loss"] = metrics["val_loss"]
                    job["current_sharpe"] = metrics["val_sharpe"]

                    if job["total_epochs"] > 0:
                        job["progress_pct"] = (metrics["current_epoch"] / job["total_epochs"]) * 100.0

        except subprocess.TimeoutExpired:
            logger.warning(f"SSH timeout for job {job_id}")
        except Exception as e:
            logger.error(f"Error monitoring AWS training job {job_id}: {e}")

        # Wait before next check
        threading.Event().wait(10)


# ============================================================================
# REMOTE SERVER TRAINING HELPER FUNCTIONS
# ============================================================================

def launch_remote_training(job_id: str, config: str, remote_host: str, remote_user: str, device: str, debug_mode: bool) -> Dict:
    """Launch training on remote server via SSH."""

    # Local paths
    project_root = Path(__file__).parent.parent
    config_path = project_root / "ai" / "configs" / config

    # Remote paths
    remote_work_dir = f"/tmp/training_{job_id}"
    remote_log_path = f"{remote_work_dir}/training.log"

    # Local log file
    log_file = Path(f"/tmp/training_remote_{job_id}.log")

    try:
        # Create a launch script that will:
        # 1. Create remote working directory
        # 2. Transfer necessary files (config, training scripts, requirements)
        # 3. Setup Python environment if needed
        # 4. Launch training in background

        logger.info(f"Setting up remote training on {remote_user}@{remote_host}")

        # SSH key path (to bypass Tailscale SSH)
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        ssh_base_args = [
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey"
        ]

        # Step 1: Create remote directory
        ssh_cmd = ["ssh"] + ssh_base_args + [
            f"{remote_user}@{remote_host}",
            f"mkdir -p {remote_work_dir}"
        ]
        subprocess.run(ssh_cmd, check=True, timeout=10)

        # Step 2: Transfer the entire ai directory (configs, train.py, etc.)
        logger.info(f"Transferring training files to remote server...")
        rsync_cmd = [
            "rsync",
            "-avz",
            "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no -o PreferredAuthentications=publickey",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", ".git",
            "--exclude", "checkpoints*",
            "--exclude", "datasets",
            str(project_root / "ai") + "/",
            f"{remote_user}@{remote_host}:{remote_work_dir}/ai/"
        ]
        subprocess.run(rsync_cmd, check=True, timeout=120)

        # Step 3: Build and execute training command on remote server
        debug_flag = "--debug_mode" if debug_mode else ""
        remote_train_cmd = f"""
cd {remote_work_dir}/ai && \
nohup python train.py \
    --config configs/{config} \
    --device {device} \
    {debug_flag} \
    > {remote_log_path} 2>&1 &
echo $!
"""

        logger.info(f"Starting training on remote server...")
        ssh_launch = ["ssh"] + ssh_base_args + [
            f"{remote_user}@{remote_host}",
            remote_train_cmd
        ]

        result = subprocess.run(
            ssh_launch,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise Exception(f"Failed to launch remote training: {result.stderr}")

        # Get the remote process PID
        remote_pid = result.stdout.strip().split('\n')[-1]
        logger.info(f"Remote training started with PID {remote_pid}")

        # Write initial log
        with open(log_file, 'w') as f:
            f.write(f"Remote training launched on {remote_host}\n")
            f.write(f"Remote work directory: {remote_work_dir}\n")
            f.write(f"Remote PID: {remote_pid}\n")
            f.write(f"Config: {config}\n")
            f.write(f"Device: {device}\n\n")

        return {
            "log_file": str(log_file),
            "remote_log_path": remote_log_path,
            "remote_work_dir": remote_work_dir,
            "remote_pid": remote_pid,
            "is_remote": True
        }

    except Exception as e:
        logger.error(f"Error launching remote training: {e}")
        raise


def monitor_remote_training(job_id: str):
    """Background thread to monitor remote training job."""
    with training_lock:
        if job_id not in training_jobs:
            return
        job = training_jobs[job_id]

    remote_host = job["remote_host"]
    remote_user = job["remote_user"]
    remote_log_path = job["remote_log_path"]
    remote_work_dir = job["remote_work_dir"]

    # Update status to running
    with training_lock:
        job["status"] = "running"

    logger.info(f"Monitoring remote training job {job_id} on {remote_host}")

    # SSH key path (to bypass Tailscale SSH)
    ssh_key = os.path.expanduser("~/.ssh/id_rsa")

    consecutive_errors = 0
    max_consecutive_errors = 5

    while True:
        try:
            # Fetch logs from remote server
            ssh_cmd = [
                "ssh",
                "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "PreferredAuthentications=publickey",
                "-o", "ConnectTimeout=5",
                f"{remote_user}@{remote_host}",
                f"tail -50 {remote_log_path} 2>/dev/null || echo 'Log not ready'"
            ]

            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and "Log not ready" not in result.stdout:
                consecutive_errors = 0  # Reset error counter

                # Parse logs for metrics
                metrics = parse_training_log_from_text(result.stdout)

                with training_lock:
                    job["current_epoch"] = metrics["current_epoch"]
                    job["total_epochs"] = metrics["total_epochs"] or job["total_epochs"]
                    job["current_loss"] = metrics["train_loss"]
                    job["current_val_loss"] = metrics["val_loss"]
                    job["current_sharpe"] = metrics["val_sharpe"]

                    if job["total_epochs"] > 0:
                        job["progress_pct"] = (metrics["current_epoch"] / job["total_epochs"]) * 100.0

                # Check if training is complete by looking for completion markers
                if "Training completed" in result.stdout or "All epochs completed" in result.stdout:
                    logger.info(f"Remote training job {job_id} completed!")

                    # Retrieve the trained model
                    retrieve_remote_model(job_id, job)

                    with training_lock:
                        job["status"] = "completed"
                        job["end_time"] = datetime.utcnow()
                        save_training_metadata(job_id)
                    break

            else:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors monitoring job {job_id}")
                    with training_lock:
                        job["status"] = "failed"
                        job["error"] = "Lost connection to remote server"
                        job["end_time"] = datetime.utcnow()
                    break

        except subprocess.TimeoutExpired:
            logger.warning(f"SSH timeout for remote job {job_id}")
            consecutive_errors += 1
        except Exception as e:
            logger.error(f"Error monitoring remote training job {job_id}: {e}")
            consecutive_errors += 1

        if consecutive_errors >= max_consecutive_errors:
            with training_lock:
                job["status"] = "failed"
                job["error"] = f"Monitoring failed after {max_consecutive_errors} consecutive errors"
                job["end_time"] = datetime.utcnow()
            break

        # Wait before next check
        threading.Event().wait(10)


def retrieve_remote_model(job_id: str, job: Dict):
    """Retrieve trained model from remote server using scp."""
    remote_host = job["remote_host"]
    remote_user = job["remote_user"]
    remote_work_dir = job["remote_work_dir"]

    # Local checkpoint directory
    local_checkpoint_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    local_checkpoint_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Retrieving model from remote server for job {job_id}")

    # SSH key path (to bypass Tailscale SSH)
    ssh_key = os.path.expanduser("~/.ssh/id_rsa")

    try:
        # Find the latest checkpoint on remote server
        ssh_find = [
            "ssh",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}",
            f"ls -t {remote_work_dir}/ai/checkpoints_light/*.pt 2>/dev/null | head -1"
        ]

        result = subprocess.run(
            ssh_find,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(f"No model file found on remote server for job {job_id}")
            return

        remote_model_path = result.stdout.strip()
        model_filename = Path(remote_model_path).name
        local_model_path = local_checkpoint_dir / model_filename

        # Use scp to retrieve the model
        logger.info(f"Downloading {model_filename} from remote server...")
        scp_cmd = [
            "scp",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}:{remote_model_path}",
            str(local_model_path)
        ]

        subprocess.run(scp_cmd, check=True, timeout=120)

        logger.info(f"Successfully retrieved model: {local_model_path}")

        with training_lock:
            job["model_path"] = str(local_model_path)
            job["model_filename"] = model_filename

        # Cleanup remote directory (optional)
        cleanup_cmd = [
            "ssh",
            "-i", ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "PreferredAuthentications=publickey",
            f"{remote_user}@{remote_host}",
            f"rm -rf {remote_work_dir}"
        ]
        subprocess.run(cleanup_cmd, timeout=10)
        logger.info(f"Cleaned up remote directory: {remote_work_dir}")

    except Exception as e:
        logger.error(f"Error retrieving model from remote server: {e}")
        with training_lock:
            job["error"] = f"Model retrieval failed: {str(e)}"


def _portfolio_collection():
    try:
        coll = get_db()[PORTFOLIO_COLLECTION]
        coll.create_index("updated_at")
        return coll
    except Exception as exc:
        logger.warning(f"MongoDB portfolio unavailable: {exc}")
        return None


def _default_portfolio_state():
    now = datetime.utcnow()
    return {
        "_id": PORTFOLIO_DOC_ID,
        "initial_capital": PORTFOLIO_INITIAL_CAPITAL,
        "cash": PORTFOLIO_INITIAL_CAPITAL,
        "positions": [],
        "trades": [],
        "history": [],
        "updated_at": now,
    }


def _load_portfolio_state():
    coll = _portfolio_collection()
    if coll is None:
        return _default_portfolio_state()

    try:
        state = coll.find_one({"_id": PORTFOLIO_DOC_ID})
        if state is None:
            state = _default_portfolio_state()
            coll.insert_one(state)
        return state
    except PyMongoError as exc:
        logger.error(f"Mongo load portfolio failed: {exc}")
        return _default_portfolio_state()


def _calculate_stats(state: Dict):
    positions = state.get("positions", [])
    cash = float(state.get("cash", 0))
    initial_capital = float(state.get("initial_capital", PORTFOLIO_INITIAL_CAPITAL))

    invested = sum(
        float(pos.get("quantity", 0)) * float(pos.get("current_price", pos.get("entry_price", 0)))
        for pos in positions
    )
    total_value = cash + invested
    total_pnl = total_value - initial_capital
    total_pnl_percent = (total_pnl / initial_capital * 100) if initial_capital else 0

    return {
        "total_value": total_value,
        "cash": cash,
        "invested": invested,
        "total_pnl": total_pnl,
        "total_pnl_percent": total_pnl_percent,
    }


def _append_history(state: Dict, signal: str = "HOLD", action_taken: str = "NONE"):
    stats = _calculate_stats(state)
    history = state.get("history", [])
    history.append({
        "timestamp": datetime.utcnow(),
        "total_value": stats["total_value"],
        "cash": stats["cash"],
        "invested": stats["invested"],
        "pnl": stats["total_pnl"],
        "pnl_percent": stats["total_pnl_percent"],
        "signal": signal,
        "action_taken": action_taken,
    })
    state["history"] = history[-2000:]


def _persist_portfolio_state(state: Dict):
    coll = _portfolio_collection()
    state["updated_at"] = datetime.utcnow()

    if coll is None:
        return state
    try:
        coll.update_one({"_id": PORTFOLIO_DOC_ID}, {"$set": state}, upsert=True)
    except PyMongoError as exc:
        logger.error(f"Mongo save portfolio failed: {exc}")
    return state


def _format_state_for_response(state: Dict):
    def _iso(dt):
        return dt.isoformat() if isinstance(dt, datetime) else dt

    positions = []
    for pos in state.get("positions", []):
        entry_time = pos.get("entry_time") or datetime.utcnow()
        current_price = float(pos.get("current_price", pos.get("entry_price", 0)))
        quantity = float(pos.get("quantity", 0))
        entry_price = float(pos.get("entry_price", 0))
        positions.append({
            "symbol": pos.get("symbol"),
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": current_price,
            "value": current_price * quantity,
            "pnl": (current_price - entry_price) * quantity,
            "pnl_percent": ((current_price - entry_price) / entry_price * 100) if entry_price else 0,
            "entry_time": _iso(entry_time),
        })

    trades = []
    for t in state.get("trades", []):
        trades.append({
            "id": t.get("id"),
            "timestamp": _iso(t.get("timestamp")),
            "symbol": t.get("symbol"),
            "action": t.get("action"),
            "quantity": float(t.get("quantity", 0)),
            "price": float(t.get("price", 0)),
            "total": float(t.get("total", 0)),
            "reason": t.get("reason"),
            "confidence": float(t.get("confidence", 0)),
        })

    history = []
    for h in state.get("history", []):
        history.append({
            "timestamp":   _iso(h.get("timestamp")),
            "total_value": float(h.get("total_value", 0)),
            "cash":        float(h.get("cash", 0)),
            "invested":    float(h.get("invested", 0)),
            "pnl":         float(h.get("pnl", 0)),
            "pnl_percent": float(h.get("pnl_percent", 0)),
            "signal":      h.get("signal", "HOLD"),
            "action_taken": h.get("action_taken", "NONE"),
        })

    stats = _calculate_stats(state)

    return {
        "initial_capital": float(state.get("initial_capital", PORTFOLIO_INITIAL_CAPITAL)),
        "cash": stats["cash"],
        "positions": positions,
        "trades": sorted(trades, key=lambda x: x["timestamp"], reverse=True)[:200],
        "history": history,
        "stats": stats,
        "updated_at": _iso(state.get("updated_at")),
    }


def _apply_trade_logic(state: Dict, symbol: str, action: str, price: float,
                       confidence: float = 0.0, reason: str = "AI signal") -> Dict:
    """Pure trade logic — no history append, no MongoDB persist."""
    symbol = symbol.upper()
    action = action.upper()
    now = datetime.utcnow()
    positions = state.get("positions", [])
    cash = float(state.get("cash", 0))

    if action == "BUY":
        investment = cash * 0.10
        if investment < 10 or price <= 0:
            return state
        quantity = investment / price
        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        if existing:
            old_qty = float(existing.get("quantity", 0))
            new_qty = old_qty + quantity
            entry_price = (float(existing.get("entry_price", 0)) * old_qty + price * quantity) / new_qty
            existing.update({"quantity": new_qty, "entry_price": entry_price, "current_price": price})
        else:
            positions.append({"symbol": symbol, "quantity": quantity,
                               "entry_price": price, "current_price": price, "entry_time": now})
        cash -= investment
        state["trades"] = [{
            "id": f"{int(now.timestamp() * 1000)}-{symbol}",
            "timestamp": now, "symbol": symbol, "action": "BUY",
            "quantity": quantity, "price": price, "total": quantity * price,
            "reason": reason, "confidence": confidence,
        }] + state.get("trades", [])[:999]

    elif action == "SELL":
        existing = next((p for p in positions if p.get("symbol") == symbol), None)
        if not existing:
            return state
        quantity = float(existing.get("quantity", 0))
        trade_total = quantity * price
        cash += trade_total
        positions = [p for p in positions if p.get("symbol") != symbol]
        state["trades"] = [{
            "id": f"{int(now.timestamp() * 1000)}-{symbol}",
            "timestamp": now, "symbol": symbol, "action": "SELL",
            "quantity": quantity, "price": price, "total": trade_total,
            "reason": reason, "confidence": confidence,
        }] + state.get("trades", [])[:999]

    state["positions"] = positions
    state["cash"] = cash
    return state


def _apply_trade(state: Dict, payload: TradeRequest):
    """Trade + history snapshot + MongoDB persist (used by HTTP endpoints)."""
    state = _apply_trade_logic(
        state,
        payload.symbol, payload.action, float(payload.price),
        float(payload.confidence or 0), payload.reason or "AI signal",
    )
    _append_history(state)
    _persist_portfolio_state(state)
    return state


@app.get("/portfolio/state")
async def get_portfolio_state():
    state = _load_portfolio_state()
    return _format_state_for_response(state)


@app.get("/portfolio/history")
async def get_portfolio_history():
    state = _load_portfolio_state()
    return {"history": _format_state_for_response(state)["history"]}


@app.post("/portfolio/trade")
async def post_portfolio_trade(payload: TradeRequest):
    state = _load_portfolio_state()
    state = _apply_trade(state, payload)
    return _format_state_for_response(state)


@app.post("/portfolio/reset")
async def reset_portfolio():
    state = _default_portfolio_state()
    _append_history(state, signal="RESET", action_taken="RESET")
    _persist_portfolio_state(state)
    return _format_state_for_response(state)


@app.get("/portfolio/events")
async def get_portfolio_events():
    """Retourne tous les événements (trades + snapshots) triés par timestamp pour le replay."""
    state = _load_portfolio_state()

    def _iso(dt):
        return dt.isoformat() if isinstance(dt, datetime) else (dt or "")

    events = []
    for t in state.get("trades", []):
        events.append({
            "type": "trade",
            "timestamp": _iso(t.get("timestamp")),
            "symbol": t.get("symbol"),
            "action": t.get("action"),
            "price": float(t.get("price", 0)),
            "quantity": float(t.get("quantity", 0)),
            "total": float(t.get("total", 0)),
            "reason": t.get("reason", ""),
            "confidence": float(t.get("confidence", 0)),
        })

    for h in state.get("history", []):
        events.append({
            "type": "snapshot",
            "timestamp": _iso(h.get("timestamp")),
            "total_value": float(h.get("total_value", 0)),
            "cash": float(h.get("cash", 0)),
            "invested": float(h.get("invested", 0)),
            "pnl": float(h.get("pnl", 0)),
            "pnl_percent": float(h.get("pnl_percent", 0)),
            "signal": h.get("signal", "HOLD"),
            "action_taken": h.get("action_taken", "NONE"),
        })

    events.sort(key=lambda x: x["timestamp"])
    return {"events": events, "count": len(events)}


@app.get("/dataset/signals")
async def get_signals():
    """Récupérer les signaux alpha détectés."""
    dataset_path = get_latest_dataset_path()
    signals_file = dataset_path / "alpha_signals_report.json"

    if not signals_file.exists():
        return {"signals": [], "count": 0}

    with open(signals_file, 'r') as f:
        signals = json.load(f)

    # Statistiques sur les signaux
    df = pd.DataFrame(signals)

    stats = {
        "total": len(signals),
        "by_direction": df['direction'].value_counts().to_dict() if 'direction' in df.columns else {},
        "by_strength": df['strength'].value_counts().to_dict() if 'strength' in df.columns else {},
        "by_asset": df['asset'].value_counts().to_dict() if 'asset' in df.columns else {},
        "by_type": df['signal_type'].value_counts().to_dict() if 'signal_type' in df.columns else {},
    }

    return {
        "signals": signals,
        "stats": stats
    }

@app.get("/dataset/ohlcv/{symbol:path}")
async def get_ohlcv(symbol: str, limit: int = 1000):
    """Récupérer les données OHLCV pour un symbol.
    Supporte BTC/USDT et BTCUSDT formats."""

    # Essayer d'abord avec les données historiques (format BTC/USDT)
    try:
        # Normaliser le symbole: enlever le slash si présent
        symbol_normalized = symbol.replace('/', '_').upper()
        historical_dir = Path("datasets/historical_crypto")

        # Chercher le fichier historique
        pattern = f"{symbol_normalized}_1h_*.parquet"
        files = list(historical_dir.glob(pattern))

        if files:
            # Utiliser le fichier le plus récent
            latest_file = sorted(files, reverse=True)[0]
            df = pd.read_parquet(latest_file)

            # Appliquer la limite
            df = df.tail(limit)

            # S'assurer que timestamp est en datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp')

            return {
                "symbol": symbol,
                "data": df.to_dict(orient='records'),
                "count": len(df)
            }
    except Exception as e:
        logger.warning(f"Failed to load from historical data: {e}")

    # Fallback: essayer avec les données alpha trading
    dataset_path = get_latest_dataset_path()
    ohlcv_file = dataset_path / "binance_ohlcv.parquet"

    if not ohlcv_file.exists():
        raise HTTPException(status_code=404, detail="OHLCV data not found")

    df = pd.read_parquet(ohlcv_file)

    # Essayer avec et sans slash
    df_symbol = df[df['symbol'] == symbol].tail(limit)
    if len(df_symbol) == 0:
        # Essayer sans slash
        symbol_no_slash = symbol.replace('/', '')
        df_symbol = df[df['symbol'] == symbol_no_slash].tail(limit)

    if len(df_symbol) == 0:
        available_symbols = df['symbol'].unique().tolist()
        raise HTTPException(
            status_code=404,
            detail=f"Symbol {symbol} not found. Available: {available_symbols[:10]}"
        )

    # Convertir en format pour ECharts
    df_symbol['timestamp'] = pd.to_datetime(df_symbol['timestamp'])
    df_symbol = df_symbol.sort_values('timestamp')

    return {
        "symbol": symbol,
        "data": df_symbol.to_dict(orient='records'),
        "count": len(df_symbol)
    }

@app.get("/dataset/funding-rates")
async def get_funding_rates():
    """Récupérer les funding rates."""
    dataset_path = get_latest_dataset_path()
    funding_file = dataset_path / "funding_rates.parquet"

    if not funding_file.exists():
        raise HTTPException(status_code=404, detail="Funding rates not found")

    df = pd.read_parquet(funding_file)

    # Grouper par symbol et prendre les dernières valeurs
    latest_by_symbol = df.groupby('symbol').last().reset_index()

    return {
        "data": latest_by_symbol.to_dict(orient='records'),
        "count": len(latest_by_symbol)
    }

@app.get("/dataset/fear-greed")
async def get_fear_greed():
    """Récupérer le Fear & Greed Index."""
    dataset_path = get_latest_dataset_path()
    fg_file = dataset_path / "fear_greed_index.parquet"

    if not fg_file.exists():
        raise HTTPException(status_code=404, detail="Fear & Greed data not found")

    df = pd.read_parquet(fg_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')

    return {
        "data": df.to_dict(orient='records'),
        "latest": df.iloc[-1].to_dict() if len(df) > 0 else None,
        "count": len(df)
    }

@app.get("/dataset/sentiment")
async def get_sentiment():
    """Récupérer les données de sentiment Reddit."""
    dataset_path = get_latest_dataset_path()
    reddit_file = dataset_path / "reddit_sentiment.parquet"

    if not reddit_file.exists():
        raise HTTPException(status_code=404, detail="Sentiment data not found")

    df = pd.read_parquet(reddit_file)

    # Top posts par engagement
    df['engagement'] = df['score'] + df['num_comments']
    top_posts = df.nlargest(20, 'engagement')

    # Statistiques par subreddit
    by_subreddit = df.groupby('subreddit').agg({
        'score': 'sum',
        'num_comments': 'sum',
        'title': 'count'
    }).reset_index()
    by_subreddit.columns = ['subreddit', 'total_score', 'total_comments', 'post_count']

    return {
        "top_posts": top_posts.to_dict(orient='records'),
        "by_subreddit": by_subreddit.to_dict(orient='records'),
        "total_posts": len(df)
    }

@app.get("/dataset/macro")
async def get_macro():
    """Récupérer les données macroéconomiques."""
    dataset_path = get_latest_dataset_path()

    data = {}

    # FRED data
    fred_file = dataset_path / "fred_economic.parquet"
    if fred_file.exists():
        df_fred = pd.read_parquet(fred_file)
        data['fred'] = df_fred.groupby('series').tail(30).to_dict(orient='records')

    # Stock indices
    indices_file = dataset_path / "stock_indices.parquet"
    if indices_file.exists():
        df_indices = pd.read_parquet(indices_file)
        data['indices'] = df_indices.to_dict(orient='records')

    return data

@app.get("/dataset/derivatives")
async def get_derivatives():
    """Récupérer les données dérivés."""
    dataset_path = get_latest_dataset_path()

    data = {}

    # Funding rates
    funding_file = dataset_path / "funding_rates.parquet"
    if funding_file.exists():
        df = pd.read_parquet(funding_file)
        data['funding_rates'] = df.groupby('symbol').last().reset_index().to_dict(orient='records')

    # Open interest
    oi_file = dataset_path / "open_interest.parquet"
    if oi_file.exists():
        df = pd.read_parquet(oi_file)
        data['open_interest'] = df.to_dict(orient='records')

    # Long/Short ratio
    ls_file = dataset_path / "long_short_ratio.parquet"
    if ls_file.exists():
        df = pd.read_parquet(ls_file)
        data['long_short_ratio'] = df.groupby('symbol').last().reset_index().to_dict(orient='records')

    return data


# ============================================================================
# HISTORICAL DATA ENDPOINTS (crypto OHLCV)
# ============================================================================

HISTORICAL_DATA_DIR = Path("datasets/historical_crypto")


def load_historical_data(symbol: str, limit: Optional[int] = None, interval: str = "1h") -> Optional[pd.DataFrame]:
    """
    Charger les données historiques d'une crypto.
    Essaie MongoDB d'abord, puis bascule sur les fichiers Parquet locaux.
    """
    norm_symbol = normalize_symbol(symbol)

    # Mongo (si les données ont été ingérées)
    df = fetch_historical_from_mongo(norm_symbol, limit=limit, interval=interval)
    if df is not None and not df.empty:
        return df

    # Fichiers locaux en fallback
    safe_symbol = norm_symbol.replace("/", "_")
    pattern = f"{safe_symbol}_{interval}_*.parquet"
    files = list(HISTORICAL_DATA_DIR.glob(pattern))

    if not files:
        return None

    latest_file = max(files, key=lambda f: f.stat().st_mtime)
    df = pd.read_parquet(latest_file)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    if limit:
        df = df.tail(limit)

    return df


def _build_historical_response(symbol: str, limit: Optional[int], interval: Optional[str]):
    interval = interval or "1h"
    df = load_historical_data(symbol, limit=limit, interval=interval)

    print(df)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"Crypto {symbol} not found")

    df = df.sort_values("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "success": True,
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "count": len(df),
        "data": df.to_dict("records"),
    }


@app.get("/api/historical/{symbol:path}")
async def get_historical_symbol(symbol: str, limit: Optional[int] = None, interval: Optional[str] = "1h"):
    """Données historiques via paramètre dans le path: /api/historical/BTC/USDT."""
    return _build_historical_response(symbol, limit, interval)


@app.get("/api/historical/")
async def get_historical_query(symbol: str, limit: Optional[int] = None, interval: Optional[str] = "1h"):
    """Données historiques via query string: /api/historical/?symbol=BTC/USDT&limit=500."""
    return _build_historical_response(symbol, limit, interval)

@app.get("/market/all-cryptos")
async def get_all_cryptos():
    """Récupérer toutes les cryptos avec leurs prix actuels et précédents."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            return {"cryptos": [], "count": 0, "message": "No OHLCV data available"}

        df = pd.read_parquet(ohlcv_file)

        # Pour chaque symbol, récupérer les dernières valeurs
        cryptos_data = []
        for symbol in df['symbol'].unique():
            df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

            if len(df_symbol) >= 2:
                latest = df_symbol.iloc[-1]
                previous = df_symbol.iloc[-2]

                # Convertir en float pour éviter les erreurs de type
                latest_close = float(latest['close'])
                previous_close = float(previous['close'])

                # Calculer les variations
                price_change = latest_close - previous_close
                price_change_pct = (price_change / previous_close) * 100

                # Calculer 24h change (environ 24 candles de 1h)
                h24_ago_idx = max(0, len(df_symbol) - 24)
                h24_ago = df_symbol.iloc[h24_ago_idx]
                h24_ago_close = float(h24_ago['close'])
                h24_change = latest_close - h24_ago_close
                h24_change_pct = (h24_change / h24_ago_close) * 100

                crypto_info = {
                    "symbol": symbol,
                    "name": symbol.replace('USDT', ''),
                    "current_price": latest_close,
                    "previous_price": previous_close,
                    "open": float(latest['open']),
                    "high": float(latest['high']),
                    "low": float(latest['low']),
                    "volume": float(latest['volume']),
                    "price_change": price_change,
                    "price_change_pct": price_change_pct,
                    "h24_high": float(df_symbol.tail(24)['high'].astype(float).max()),
                    "h24_low": float(df_symbol.tail(24)['low'].astype(float).min()),
                    "h24_volume": float(df_symbol.tail(24)['volume'].astype(float).sum()),
                    "h24_change": h24_change,
                    "h24_change_pct": h24_change_pct,
                    "timestamp": latest['timestamp'].isoformat() if hasattr(latest['timestamp'], 'isoformat') else str(latest['timestamp']),
                    "is_positive": price_change >= 0,
                }

                cryptos_data.append(crypto_info)

        # Trier par volume 24h décroissant
        cryptos_data.sort(key=lambda x: x['h24_volume'], reverse=True)

        # Statistiques globales
        stats = {
            "total_cryptos": len(cryptos_data),
            "gainers": len([c for c in cryptos_data if c['h24_change_pct'] > 0]),
            "losers": len([c for c in cryptos_data if c['h24_change_pct'] < 0]),
            "neutral": len([c for c in cryptos_data if c['h24_change_pct'] == 0]),
            "top_gainer": max(cryptos_data, key=lambda x: x['h24_change_pct']) if cryptos_data else None,
            "top_loser": min(cryptos_data, key=lambda x: x['h24_change_pct']) if cryptos_data else None,
            "highest_volume": max(cryptos_data, key=lambda x: x['h24_volume']) if cryptos_data else None,
        }

        return {
            "cryptos": cryptos_data,
            "count": len(cryptos_data),
            "stats": stats,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading crypto data: {str(e)}")

@app.get("/market/ticker")
async def get_ticker(symbol: str = "BTCUSDT"):
    """Récupérer le ticker pour un symbol spécifique."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        latest = df_symbol.iloc[-1]

        # Calculer 24h stats
        h24_data = df_symbol.tail(24)
        h24_change = latest['close'] - h24_data.iloc[0]['close']
        h24_change_pct = (h24_change / h24_data.iloc[0]['close']) * 100

        return {
            "symbol": symbol,
            "price": float(latest['close']),
            "priceChange24h": float(h24_change),
            "priceChangePercent24h": float(h24_change_pct),
            "high24h": float(h24_data['high'].max()),
            "low24h": float(h24_data['low'].min()),
            "volume24h": float(h24_data['volume'].sum()),
            "quoteVolume24h": float(h24_data['volume'].sum() * h24_data['close'].mean()),
            "timestamp": latest['timestamp'].isoformat() if hasattr(latest['timestamp'], 'isoformat') else str(latest['timestamp']),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/klines")
async def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500):
    """Récupérer les klines (candlestick data) depuis Binance API."""
    try:
        import requests

        # Map d'intervalles
        interval_map = {
            '1m': '1m',
            '5m': '5m',
            '15m': '15m',
            '1h': '1h',
            '4h': '4h',
            '1d': '1d'
        }

        binance_interval = interval_map.get(interval, '1h')

        # Appel à l'API Binance
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": binance_interval,
            "limit": min(limit, 1000)  # Binance max = 1000
        }

        response = requests.get(url, params=params)

        if response.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Failed to fetch data for {symbol}")

        data = response.json()

        # Convertir au format attendu
        klines = []
        for candle in data:
            klines.append({
                "time": int(candle[0] / 1000),  # Convert ms to seconds
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5])
            })

        return klines

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/orderbook")
async def get_orderbook(symbol: str = "BTCUSDT", depth: int = 20):
    """Générer un order book simulé basé sur les données OHLCV."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp')

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        latest = df_symbol.iloc[-1]
        base_price = float(latest['close'])

        # Générer asks (ordres de vente)
        asks = []
        for i in range(depth):
            price = base_price + (i + 1) * (base_price * 0.0001)  # 0.01% par niveau
            quantity = (20 - i) * 0.1  # Quantité décroissante
            asks.append({
                "price": f"{price:.2f}",
                "quantity": f"{quantity:.4f}",
                "total": f"{price * quantity:.2f}"
            })

        # Générer bids (ordres d'achat)
        bids = []
        for i in range(depth):
            price = base_price - (i + 1) * (base_price * 0.0001)
            quantity = (20 - i) * 0.1
            bids.append({
                "price": f"{price:.2f}",
                "quantity": f"{quantity:.4f}",
                "total": f"{price * quantity:.2f}"
            })

        return {
            "asks": asks,
            "bids": bids
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market/trades")
async def get_recent_trades(symbol: str = "BTCUSDT", limit: int = 50):
    """Trades récents depuis OHLCV (prix VWAP par candle, volume réel)."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp').tail(limit)

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        trades = []
        for i, (_, row) in enumerate(df_symbol.iterrows()):
            vwap = float(row.get("vwap_60m", (float(row["high"]) + float(row["low"])) / 2))
            volume = float(row.get("volume", 0))
            timestamp = row["timestamp"]
            time_ms = int(timestamp.timestamp() * 1000) if hasattr(timestamp, "timestamp") else int(pd.Timestamp(timestamp).timestamp() * 1000)
            trades.append({
                "id":          time_ms + i,
                "price":       f"{vwap:.2f}",
                "quantity":    f"{volume:.4f}",
                "time":        time_ms,
                "isBuyerMaker": bool(row.get("taker_buy_ratio_base", 0.5) < 0.5),
                "source":      "ohlcv_candle",
            })

        trades.sort(key=lambda x: x["time"], reverse=True)
        return trades[:limit]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        dataset_path = get_latest_dataset_path()
        return {
            "status": "healthy",
            "dataset": dataset_path.name,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# ============================================================================
# BACKTEST HISTORY — données historiques du dernier run
# ============================================================================

def _latest_run_dir() -> Optional[Path]:
    runs = _ROOT / "runs" / "pipeline"
    if not runs.exists():
        return None
    for d in sorted(runs.iterdir(), reverse=True):
        if d.is_dir() and (d / "pipeline_summary.json").exists():
            return d
    return None


@app.get("/backtest/history")
async def get_backtest_history():
    """Données backtest du dernier run : trades long + short + equity + stats."""
    run_dir = _latest_run_dir()
    if not run_dir:
        raise HTTPException(status_code=404, detail="Aucun run trouvé")

    result = {"run_id": run_dir.name, "long": None, "short": None}

    for side in ("long", "short"):
        bd = run_dir / f"backtest_{side}"
        if not bd.exists():
            continue
        try:
            summary = json.loads((bd / "summary.json").read_text()) if (bd / "summary.json").exists() else {}
            trades  = json.loads((bd / "trades.json").read_text())  if (bd / "trades.json").exists() else []
            equity  = json.loads((bd / "equity_curve.json").read_text()) if (bd / "equity_curve.json").exists() else []
            result[side] = {"summary": summary, "trades": trades, "equity": equity}
        except Exception as e:
            logger.warning(f"backtest {side}: {e}")

    return result


# ============================================================================
# REAL-TIME PIPELINE ENDPOINTS — PredictionEngine (vrais modèles ML)
# ============================================================================

@app.post("/pipeline/start")
async def start_pipeline():
    """Démarre / force un refresh immédiat du moteur d'inférence."""
    try:
        await _prediction_engine.refresh()
        return {"status": "started", "message": "Prédiction rafraîchie"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/pipeline/stop")
async def stop_pipeline():
    """No-op — le moteur tourne en continu."""
    return {"status": "stopped", "message": "Le moteur continue en arrière-plan"}


@app.get("/pipeline/status")
async def get_pipeline_status():
    """Statut du moteur d'inférence."""
    return _prediction_engine.status()


@app.get("/pipeline/predictions")
async def get_all_predictions():
    """Dernière prédiction du pipeline (format compat frontend)."""
    pred = _prediction_engine.last_prediction
    if pred is None:
        # Premier appel : forcer un refresh
        try:
            await _prediction_engine.refresh()
            pred = _prediction_engine.last_prediction
        except Exception as e:
            return {
                "count": 0,
                "predictions": [],
                "message": f"Initialisation en cours: {e}",
                "timestamp": datetime.utcnow().isoformat(),
            }
    if pred is None:
        return {"count": 0, "predictions": [], "timestamp": datetime.utcnow().isoformat()}
    return {
        "count": 1,
        "predictions": [pred],
        "timestamp": pred.get("refreshed_at", datetime.utcnow().isoformat()),
    }


@app.get("/pipeline/signal")
async def get_signal():
    """Prédiction complète avec cascade pipeline — utilisé par le nouveau frontend."""
    pred = _prediction_engine.last_prediction
    if pred is None:
        try:
            await _prediction_engine.refresh()
            pred = _prediction_engine.last_prediction
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Modèle non disponible: {e}")
    if pred is None:
        raise HTTPException(status_code=503, detail="Aucune prédiction disponible")
    return {
        **pred,
        "history": _prediction_engine.prediction_history[-50:],
    }


@app.get("/pipeline/prediction/{symbol}")
async def get_prediction_by_symbol(symbol: str):
    """Prédiction pour un symbole spécifique (BTCUSDT uniquement pour l'instant)."""
    pred = _prediction_engine.last_prediction
    if pred is None:
        raise HTTPException(status_code=404, detail="Aucune prédiction disponible")
    return pred


@app.get("/pipeline/symbols")
async def get_active_symbols():
    """Symboles actifs."""
    return {"count": 3, "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}


# ─── Multi-symbol signal engine ──────────────────────────────────────────────
try:
    from signal_engine import get_signal as _get_signal_v2, get_all_signals as _get_all_signals
    _SIGNAL_ENGINE_OK = True
except Exception as _e:
    logger.warning(f"signal_engine non disponible: {_e}")
    _SIGNAL_ENGINE_OK = False


@app.get("/v2/signal/{symbol}")
async def get_signal_v2(symbol: str):
    """Signal technique complet pour un symbole (BTC/ETH/SOL)."""
    if not _SIGNAL_ENGINE_OK:
        raise HTTPException(status_code=503, detail="Signal engine non disponible")
    try:
        return _get_signal_v2(symbol.upper())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v2/signals/all")
async def get_all_signals_v2():
    """Signaux BTC + ETH + SOL en un seul appel."""
    if not _SIGNAL_ENGINE_OK:
        raise HTTPException(status_code=503, detail="Signal engine non disponible")
    try:
        return _get_all_signals()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Backtest endpoint ───────────────────────────────────────────────────────
_backtest_cache: Dict = {}
_backtest_running = False

@app.get("/backtest/full")
async def run_full_backtest(symbol: str = "BTC/USDT", since: str = "2021-01-01", force: bool = False):
    """Backtest end-to-end Level 0→7 sur données historiques MongoDB."""
    global _backtest_running
    cache_key = f"{symbol}:{since}"

    if not force and cache_key in _backtest_cache:
        return _backtest_cache[cache_key]

    if _backtest_running:
        return {"status": "running", "message": "Backtest déjà en cours, réessaie dans 30s"}

    try:
        sys.path.insert(0, str(_ROOT / "scripts"))
        from backtest_engine import run_backtest
        _backtest_running = True
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_backtest(symbol, since)
        )
        _backtest_cache[cache_key] = result
        return result
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _backtest_running = False

@app.get("/pipeline/predictions/future/{symbol}")
async def get_future_predictions(symbol: str, minutes: int = 5):
    """Prédictions futures — désactivé : extrapolation aléatoire supprimée."""
    return {
        "status":     "disabled",
        "deployable": False,
        "reason":     "model_not_connected",
        "action":     "WAIT",
        "confidence": 0,
        "symbol":     symbol.upper(),
        "note":       "Future price extrapolation requires a calibrated model output, not random drift.",
    }


# ============================================================================
# TRAINING MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/training/configs")
async def get_training_configs():
    configs = get_available_configs()
    return {"success": True, "configs": configs or ["pipeline", "pipeline_1m", "regime_only"],
            "count": len(configs) or 3}


@app.get("/training/symbols")
async def get_training_symbols():
    """Symboles proposés par l'UI, avec disponibilité du dataset local."""
    symbols = []
    for symbol in TRAINING_SYMBOL_UNIVERSE:
        data_path = resolve_training_data(symbol, raise_on_missing=False)
        symbols.append({
            "symbol": symbol,
            "base": _symbol_base(symbol),
            "ready": data_path is not None,
            "data_path": str(data_path) if data_path else None,
        })
    return {
        "success": True,
        "symbols": symbols,
        "count": len(symbols),
    }


@app.get("/training/architecture")
async def get_model_architecture():
    """Architecture réelle du modèle ML."""
    from pymongo import MongoClient as _MC
    try:
        db  = _MC("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)["trader"]
        n_bars   = db["historical_ohlcv"].count_documents({})
        n_1m     = db["ohlcv_1m"].count_documents({})
        doc      = db["historical_ohlcv"].find_one() or {}
        n_feats  = len([k for k in doc.keys() if k not in ("_id","timestamp","symbol","interval","source","ingested_at")])
    except Exception:
        n_bars, n_1m, n_feats = 76341, 0, 88

    return {
        "data": {
            "bars_1h":   n_bars,
            "bars_1m":   n_1m,
            "features":  n_feats,
            "period":    "2017-2026",
            "symbols":   TRAINING_SYMBOL_UNIVERSE,
        },
        "levels": [
            {
                "id":   0,
                "name": "Global Gate",
                "type": "Quantile Calibration",
                "inputs": 24,
                "outputs": 1,
                "desc": "Filtre tradeable / wait",
                "params": {"features": 24, "threshold_long": 0.40, "threshold_short": 0.45, "warmup": 512},
                "color": "#6366f1",
            },
            {
                "id":   1,
                "name": "Event Classifier",
                "type": "TCN Dilated Causal",
                "inputs": 53,
                "outputs": 5,
                "desc": "Régime CHOP / UP / DOWN + tradeability + entropy",
                "params": {"d_model": 64, "n_layers": 3, "n_regimes": 3, "dropout": 0.2, "dilation": "1×2×4"},
                "color": "#8b5cf6",
            },
            {
                "id":   2,
                "name": "Edge Scorer",
                "type": "TCN + Dual Head",
                "inputs": 53,
                "outputs": 2,
                "desc": "Score directionnel + volatilité prédite",
                "params": {"d_model": 96, "n_layers": 3, "dropout": 0.15, "dilation": "1×2×4", "heads": ["edge", "rv"]},
                "color": "#06b6d4",
            },
            {
                "id":   3,
                "name": "Specialist Router",
                "type": "XGBoost × 6",
                "inputs": 53,
                "outputs": 6,
                "desc": "6 experts spécialisés par régime de marché",
                "params": {
                    "specialists": ["TREND_LONG", "TREND_SHORT", "MEAN_REVERSION", "BREAKOUT", "HIGH_VOL", "NEUTRAL"],
                    "features_long": 53, "features_short": 50, "min_samples": 300
                },
                "color": "#10b981",
            },
            {
                "id":   7,
                "name": "Risk Controller",
                "type": "Rules + Kelly",
                "inputs": 4,
                "outputs": 4,
                "desc": "Sizing Kelly + Stop ATR + Kill-switch quotidien",
                "params": {
                    "risk_per_trade": "0.2%", "stop_atr_mult": 2.5,
                    "max_stop_pct": "3%", "daily_stop": "-2%", "max_consec_losses": 3
                },
                "color": "#f59e0b",
            },
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.post("/training/start")
async def start_training(request: TrainingStartRequest):
    """Lance un entraînement LOCAL uniquement."""
    try:
        mode = _normalise_training_mode(request.mode)
        symbol = normalize_symbol(request.symbol).replace("/", "").upper()
        if request.test_from <= 2023:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Split chronologique invalide: le test doit commencer en 2024 "
                    "ou plus tard, car le train utilise ≤2022 et la validation 2023."
                ),
            )
        job_id = f"train_{symbol.lower()}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"

        if False:  # no-op block — garde les fonctions AWS pour ne pas casser les imports
            # Launch on AWS EC2
            logger.info(f"Launching AWS training job {job_id} on {request.instance_type}")

            aws_result = launch_aws_training(
                job_id,
                request.config,
                request.instance_type,
                request.aws_region,
                request.debug_mode
            )

            # Create job entry for AWS
            job = {
                "job_id": job_id,
                "status": "launching",  # Special status for AWS
                "config_path": str(config_path),
                "device": "cuda",  # Always GPU on AWS
                "debug_mode": request.debug_mode,
                "is_aws": True,
                "instance_type": request.instance_type,
                "aws_region": request.aws_region,
                "process": aws_result["process"],
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_epoch": 0,
                "total_epochs": 50,
                "progress_pct": 0.0,
                "current_loss": 0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file": aws_result["log_file"],
                "error": None,
                "aws_instance_id": None,  # Will be filled by monitor
                "aws_public_ip": None,
                "aws_s3_path": None
            }

            with training_lock:
                training_jobs[job_id] = job

            # Start AWS monitoring thread
            monitor_thread = threading.Thread(target=monitor_aws_training, args=(job_id,), daemon=True)
            monitor_thread.start()

            logger.info(f"Started AWS training job {job_id} with config {request.config}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "launching",
                "is_aws": True,
                "is_remote": False,
                "instance_type": request.instance_type,
                "message": f"Training launching on AWS EC2 ({request.instance_type})"
            }

        elif False and request.training_location == "remote":
            # Launch on remote server via SSH
            logger.info(f"Launching remote training job {job_id} on {request.remote_host}")

            remote_result = launch_remote_training(
                job_id,
                request.config,
                request.remote_host,
                request.remote_user,
                request.device,
                request.debug_mode
            )

            # Create job entry for remote server
            job = {
                "job_id": job_id,
                "status": "launching",
                "config_path": str(config_path),
                "device": request.device,
                "debug_mode": request.debug_mode,
                "is_aws": False,
                "is_remote": True,
                "remote_host": request.remote_host,
                "remote_user": request.remote_user,
                "process": remote_result.get("process"),
                "start_time": datetime.utcnow(),
                "end_time": None,
                "current_epoch": 0,
                "total_epochs": 50,
                "progress_pct": 0.0,
                "current_loss": 0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file": remote_result["log_file"],
                "remote_log_path": remote_result["remote_log_path"],
                "remote_work_dir": remote_result["remote_work_dir"],
                "error": None
            }

            with training_lock:
                training_jobs[job_id] = job

            # Start remote monitoring thread
            monitor_thread = threading.Thread(target=monitor_remote_training, args=(job_id,), daemon=True)
            monitor_thread.start()

            logger.info(f"Started remote training job {job_id} on {request.remote_host}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "launching",
                "is_aws": False,
                "is_remote": True,
                "remote_host": request.remote_host,
                "message": f"Training launching on remote server ({request.remote_host})"
            }

        else:
            # LOCAL — seul mode supporté
            log_file = Path(f"/tmp/training_{job_id}.log")
            data_path = resolve_training_data(symbol, data_path=request.data_path)
            train_script = _ROOT / "train.py"
            run_root = _ROOT / "runs" / "pipeline"
            run_dir = run_root / job_id

            env = os.environ.copy()
            env["PYTHONPATH"] = str(_ROOT)
            env["PYTHONUNBUFFERED"] = "1"
            env["FUTUR_MONGO_URI"] = "mongodb://localhost:27017"
            env["FUTUR_MONGO_DB"]  = "trader"

            cmd = [
                sys.executable,
                str(train_script),
                "pipeline",
                "--data", str(data_path),
                "--out", str(run_root),
                "--run-id", job_id,
                "--mode", mode,
                "--test-from", str(request.test_from),
                "--tradeable-q", str(request.tradeable_q),
                "--cost", str(request.cost),
                "--filter-thr-long", str(request.filter_threshold_long),
                "--direction-thr-long", str(request.direction_threshold_long),
                "--filter-thr-short", str(request.filter_threshold_short),
                "--direction-thr-short", str(request.direction_threshold_short),
                "--risk-long", str(request.risk_long),
                "--risk-short", str(request.risk_short),
                "--max-losses-long", str(request.max_losses_long),
                "--max-losses-short", str(request.max_losses_short),
                "--cooldown-long", str(request.cooldown_long),
                "--cooldown-short", str(request.cooldown_short),
                "--top-pct", str(request.top_pct),
                "--margin", str(request.margin),
            ]

            if request.auto_calibrate:
                cmd.append("--auto-calibrate")

            if request.require_short_stability and mode in {"short", "combined"}:
                cmd.append("--require-short-stability")

            if request.skip_tcn:
                cmd.append("--skip-tcn")

            if request.grid:
                cmd.append("--grid")

            if request.compare_models:
                cmd.append("--compare-models")

            if request.regression:
                cmd.append("--regression")

            process = subprocess.Popen(
                cmd,
                stdout=open(log_file, "w", buffering=1),
                stderr=subprocess.STDOUT,
                cwd=str(_ROOT),
                env=env,
            )

            job = {
                "job_id":        job_id,
                "config":        request.config,
                "symbol":        symbol,
                "mode":          mode,
                "device":        request.device,
                "epochs":        request.epochs,
                "batch_size":    request.batch_size,
                "learning_rate": request.learning_rate,
                "debug_mode":    request.debug_mode,
                "skip_tcn":      request.skip_tcn,
                "require_short_stability": request.require_short_stability,
                "cli_settings":  _training_cli_settings(request),
                "status":        "running",
                "is_aws":        False,
                "is_remote":     False,
                "process":       process,
                "start_time":    datetime.utcnow(),
                "end_time":      None,
                "current_epoch": 0,
                "total_epochs":  0,
                "progress_pct":  0.0,
                "current_loss":  0.0,
                "current_val_loss": 0.0,
                "current_sharpe": 0.0,
                "log_file":      str(log_file),
                "data_path":     str(data_path),
                "run_root":      str(run_root),
                "run_dir":       str(run_dir),
                "command":       cmd,
                "components":    _build_training_components(mode),
                "validation_summary": {
                    "status": "running",
                    "message": "Validation en attente des artefacts",
                    "required": 0,
                    "passed": 0,
                    "warnings": 0,
                    "failed": 0,
                    "run_dir": str(run_dir),
                },
                "error":         None,
            }
            _set_component_status(job, "data_contract", "running", "Chargement dataset et split chronologique")

            with training_lock:
                training_jobs[job_id] = job

            threading.Thread(target=monitor_training_process, args=(job_id,), daemon=True).start()
            logger.info(f"Training local démarré: {job_id} ({symbol}, mode={mode})")

            return {
                "success":   True,
                "job_id":    job_id,
                "status":    "running",
                "is_aws":    False,
                "is_remote": False,
                "run_dir":   str(run_dir),
                "message":   f"Entraînement local démarré ({symbol}, mode={mode})"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting training: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/jobs")
async def get_all_training_jobs():
    """Get all training jobs (last 24 hours)."""
    with training_lock:
        jobs_list = []
        cutoff_time = datetime.utcnow() - timedelta(hours=24)

        for job_id, job in training_jobs.items():
            if job["start_time"] > cutoff_time:
                job_copy = job.copy()
                job_copy.pop("process", None)  # Remove non-serializable field

                # Convert datetime to ISO format
                if isinstance(job_copy.get("start_time"), datetime):
                    job_copy["start_time"] = job_copy["start_time"].isoformat()
                if isinstance(job_copy.get("end_time"), datetime):
                    job_copy["end_time"] = job_copy["end_time"].isoformat()

                jobs_list.append(job_copy)

        # Sort by start time (most recent first)
        jobs_list.sort(key=lambda x: x["start_time"], reverse=True)

    return {
        "success": True,
        "jobs": jobs_list,
        "count": len(jobs_list)
    }

@app.get("/training/status/{job_id}")
async def get_training_status(job_id: str):
    """Get status of a specific training job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id].copy()
        job.pop("process", None)

        # Convert datetime to ISO format
        if isinstance(job.get("start_time"), datetime):
            job["start_time"] = job["start_time"].isoformat()
        if isinstance(job.get("end_time"), datetime):
            job["end_time"] = job["end_time"].isoformat()

    return {
        "success": True,
        "job": job
    }


@app.get("/training/verification/{job_id}")
async def get_training_verification(job_id: str):
    """Retourne la vérification composant par composant d'un job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")
        job = training_jobs[job_id]
        _refresh_training_components(job, final=job.get("status") not in {"running", "launching"})
        job_copy = job.copy()
        job_copy.pop("process", None)

    return {
        "success": True,
        "job_id": job_id,
        "components": job_copy.get("components", []),
        "validation_summary": job_copy.get("validation_summary", {}),
        "run_dir": job_copy.get("run_dir"),
    }

@app.post("/training/stop/{job_id}")
async def stop_training(job_id: str):
    """Stop a running training job - AWS or local."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id]

        if job["status"] not in ["running", "launching"]:
            raise HTTPException(status_code=400, detail=f"Job {job_id} is not running (status: {job['status']})")

    try:
        if job.get("is_aws"):
            # Terminate AWS EC2 instance
            instance_id = job.get("aws_instance_id")
            aws_region = job.get("aws_region", "eu-west-3")

            if instance_id:
                logger.info(f"Terminating AWS instance {instance_id} for job {job_id}")

                # Use AWS CLI to terminate instance
                terminate_cmd = [
                    "aws", "ec2", "terminate-instances",
                    "--instance-ids", instance_id,
                    "--region", aws_region
                ]

                result = subprocess.run(
                    terminate_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    logger.info(f"Successfully terminated AWS instance {instance_id}")
                else:
                    logger.error(f"Failed to terminate instance: {result.stderr}")

            # Also terminate the local launch script process
            process = job.get("process")
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

            with training_lock:
                job["status"] = "stopped"
                job["end_time"] = datetime.utcnow()

            save_training_metadata(job_id)
            logger.info(f"Stopped AWS training job {job_id}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "stopped",
                "message": f"Training job {job_id} stopped (AWS instance terminated)"
            }

        else:
            # Stop local training
            process = job["process"]

            # Try graceful shutdown first
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                process.kill()
                process.wait()

            with training_lock:
                job["status"] = "stopped"
                job["end_time"] = datetime.utcnow()

            save_training_metadata(job_id)
            logger.info(f"Stopped local training job {job_id}")

            return {
                "success": True,
                "job_id": job_id,
                "status": "stopped",
                "message": f"Training job {job_id} stopped"
            }

    except Exception as e:
        logger.error(f"Error stopping training job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/logs/{job_id}")
async def get_training_logs(job_id: str, lines: int = 100):
    """Get training logs for a specific job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        log_file = training_jobs[job_id]["log_file"]

    if not Path(log_file).exists():
        return {
            "success": True,
            "job_id": job_id,
            "logs": [],
            "message": "Log file not yet created"
        }

    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "success": True,
            "job_id": job_id,
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines)
        }

    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/models")
async def get_model_versions():
    """Get all trained model versions with metadata."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"

    if not checkpoints_dir.exists():
        return {
            "success": True,
            "models": [],
            "count": 0,
            "message": "Checkpoints directory not found"
        }

    models = []

    # Scan for .pt files
    for pt_file in sorted(checkpoints_dir.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True):
        model_info = {
            "filename": pt_file.name,
            "path": str(pt_file),
            "created_at": datetime.fromtimestamp(pt_file.stat().st_mtime).isoformat(),
            "size_mb": round(pt_file.stat().st_size / (1024 * 1024), 2),
            "metadata": {}
        }

        # Look for corresponding metadata file
        metadata_file = pt_file.parent / f"{pt_file.stem}_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    model_info["metadata"] = json.load(f)
            except Exception as e:
                logger.warning(f"Error loading metadata for {pt_file.name}: {e}")

        # Check if production model (symlink check)
        production_link = checkpoints_dir / "model_production.pt"
        if production_link.exists() and production_link.resolve() == pt_file:
            model_info["is_production"] = True
        else:
            model_info["is_production"] = False

        models.append(model_info)

    return {
        "success": True,
        "models": models,
        "count": len(models)
    }

@app.post("/training/models/{filename}/set-production")
async def set_production_model(filename: str):
    """Mark a model as the production model."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    model_file = checkpoints_dir / filename

    if not model_file.exists():
        raise HTTPException(status_code=404, detail=f"Model {filename} not found")

    production_link = checkpoints_dir / "model_production.pt"

    try:
        # Remove existing symlink if it exists
        if production_link.exists() or production_link.is_symlink():
            production_link.unlink()

        # Create new symlink
        production_link.symlink_to(model_file.name)

        logger.info(f"Set {filename} as production model")

        return {
            "success": True,
            "filename": filename,
            "message": f"Model {filename} is now set as production"
        }

    except Exception as e:
        logger.error(f"Error setting production model: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/models/{filename}/metadata")
async def get_model_metadata(filename: str):
    """Get detailed metadata for a specific model."""
    checkpoints_dir = Path(__file__).parent.parent / "ai" / "checkpoints_light"
    model_file = checkpoints_dir / filename

    if not model_file.exists():
        raise HTTPException(status_code=404, detail=f"Model {filename} not found")

    metadata_file = checkpoints_dir / f"{Path(filename).stem}_metadata.json"

    if not metadata_file.exists():
        return {
            "success": True,
            "filename": filename,
            "metadata": {},
            "message": "No metadata file found"
        }

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        return {
            "success": True,
            "filename": filename,
            "metadata": metadata
        }

    except Exception as e:
        logger.error(f"Error loading metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/training/aws-cost/{job_id}")
async def get_training_cost(job_id: str):
    """Calculate estimated cost for an AWS training job."""
    with training_lock:
        if job_id not in training_jobs:
            raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

        job = training_jobs[job_id]

    if not job.get("is_aws"):
        return {
            "success": True,
            "job_id": job_id,
            "cost_usd": 0.0,
            "message": "Not an AWS job"
        }

    # Instance pricing (hourly rates in USD)
    instance_prices = {
        "g4dn.xlarge": 0.526,      # T4 GPU, 16GB RAM
        "g4dn.2xlarge": 0.752,     # T4 GPU, 32GB RAM
        "p3.2xlarge": 3.06,        # V100 GPU, 61GB RAM
        "t3.large": 0.0832,        # CPU only, 8GB RAM
        "t3.xlarge": 0.1664        # CPU only, 16GB RAM
    }

    instance_type = job.get("instance_type", "g4dn.xlarge")
    hourly_rate = instance_prices.get(instance_type, 0.526)

    # Calculate duration
    start_time = job.get("start_time")
    end_time = job.get("end_time") or datetime.utcnow()

    # Convert to datetime if string
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    if isinstance(end_time, str):
        end_time = datetime.fromisoformat(end_time)

    duration_hours = (end_time - start_time).total_seconds() / 3600.0
    estimated_cost = duration_hours * hourly_rate

    return {
        "success": True,
        "job_id": job_id,
        "instance_type": instance_type,
        "hourly_rate_usd": hourly_rate,
        "duration_hours": round(duration_hours, 2),
        "cost_usd": round(estimated_cost, 2),
        "status": job["status"],
        "is_running": job["status"] in ["running", "launching"]
    }


# ============================================================================
# S3 DATA ENDPOINTS - Full Dataset Exploration
# ============================================================================

s3_data_source = None

def get_s3_source():
    """Get or create S3 data source singleton."""
    global s3_data_source
    if s3_data_source is None:
        s3_data_source = S3DataSource(
            bucket="qbia",
            prefix="bourse/mintrad",
            cache_dir="/tmp/trading_data_cache"
        )
    return s3_data_source

@app.get("/s3/years")
async def get_s3_years():
    """Obtenir toutes les années disponibles dans S3."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()
        return {
            "success": True,
            "years": years,
            "count": len(years)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching years: {str(e)}")

@app.get("/s3/symbols/{year}")
async def get_s3_symbols(year: int):
    """Obtenir tous les symboles disponibles pour une année."""
    try:
        s3 = get_s3_source()
        symbols = s3.list_available_symbols(year)
        return {
            "success": True,
            "year": year,
            "symbols": symbols,
            "count": len(symbols)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching symbols: {str(e)}")

@app.get("/s3/data/{symbol}/{year}")
async def get_s3_symbol_data(symbol: str, year: int, limit: Optional[int] = 10000):
    """Obtenir les données d'un symbole pour une année."""
    try:
        s3 = get_s3_source()
        df = s3.fetch_symbol_data(symbol.upper(), year)

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {symbol} in {year}")

        # Limit data for frontend performance
        if limit and len(df) > limit:
            df = df.tail(limit)

        # Convert timestamp to ISO format
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "success": True,
            "symbol": symbol.upper(),
            "year": year,
            "count": len(df),
            "data": df.to_dict(orient='records'),
            "stats": {
                "min_price": float(df['low'].min()),
                "max_price": float(df['high'].max()),
                "avg_price": float(df['close'].mean()),
                "total_volume": float(df['volume'].sum()),
                "start_date": df['timestamp'].iloc[0] if len(df) > 0 else None,
                "end_date": df['timestamp'].iloc[-1] if len(df) > 0 else None,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data: {str(e)}")

@app.get("/s3/overview")
async def get_s3_overview():
    """Obtenir une vue d'ensemble de toutes les données S3 disponibles."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()

        overview = {
            "success": True,
            "years": [],
            "total_symbols": 0,
            "symbols_by_year": {}
        }

        for year in years:
            symbols = s3.list_available_symbols(year)
            overview["symbols_by_year"][str(year)] = {
                "count": len(symbols),
                "symbols": symbols
            }
            overview["total_symbols"] += len(symbols)

        overview["years"] = years

        return overview
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching overview: {str(e)}")

@app.get("/s3/latest/{symbol}")
async def get_s3_latest_data(symbol: str, limit: int = 1000):
    """Obtenir les dernières données disponibles pour un symbole (année la plus récente)."""
    try:
        s3 = get_s3_source()
        years = s3.list_available_years()

        if not years:
            raise HTTPException(status_code=404, detail="No years available")

        # Try latest year first
        latest_year = max(years)
        df = s3.fetch_symbol_data(symbol.upper(), latest_year)

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

        # Limit data
        if len(df) > limit:
            df = df.tail(limit)

        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "success": True,
            "symbol": symbol.upper(),
            "year": latest_year,
            "count": len(df),
            "data": df.to_dict(orient='records')
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching latest data: {str(e)}")


# ============================================================================
# AI METRICS & MODEL PERFORMANCE ENDPOINTS
# ============================================================================

@app.get("/ai/model-metrics")
async def get_model_metrics():
    """Métriques du modèle IA — lit les vrais résultats de backtest si disponibles."""
    try:
        run_dir = _latest_run_dir()
        if run_dir:
            summary_file = run_dir / "pipeline_summary.json"
            if summary_file.exists():
                with open(summary_file) as f:
                    summary = json.load(f)
                bt_long = summary.get("backtest_long", {})
                return {
                    "success": True,
                    "source": "backtest",
                    "deployable": bt_long.get("deployable", False),
                    "metrics": {
                        "profit_factor":  bt_long.get("profit_factor"),
                        "win_rate":       bt_long.get("win_rate"),
                        "expectancy":     bt_long.get("expectancy"),
                        "n_trades":       bt_long.get("n_trades"),
                        "max_drawdown":   bt_long.get("max_drawdown_pct"),
                        "sharpe_ratio":   bt_long.get("sharpe"),
                        "model_version":  summary.get("model_version", "unknown"),
                        "last_updated":   summary.get("timestamp", run_dir.name),
                    },
                }
        return {
            "success": False,
            "status": "disabled",
            "deployable": False,
            "reason": "model_not_connected",
            "note": "No backtest run found. Train a model first.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching model metrics: {str(e)}")

@app.get("/ai/feature-importance")
async def get_feature_importance():
    """Obtenir l'importance des features du modèle (SHAP values)."""
    # En production, ces valeurs viendraient de l'analyse SHAP du modèle
    features = [
        {"feature": "Price Momentum", "importance": 0.24, "description": "Rate of price change over time"},
        {"feature": "Volume Profile", "importance": 0.19, "description": "Trading volume patterns"},
        {"feature": "RSI (14)", "importance": 0.15, "description": "Relative Strength Index"},
        {"feature": "MACD Signal", "importance": 0.13, "description": "Moving Average Convergence Divergence"},
        {"feature": "Bollinger Bands", "importance": 0.11, "description": "Price volatility indicator"},
        {"feature": "Order Book Imbalance", "importance": 0.09, "description": "Bid-ask pressure"},
        {"feature": "Funding Rate", "importance": 0.08, "description": "Perpetual contract funding"},
        {"feature": "Fear & Greed Index", "importance": 0.06, "description": "Market sentiment"},
        {"feature": "Cross-Asset Correlation", "importance": 0.05, "description": "BTC/ETH correlation"},
        {"feature": "Temporal Attention", "importance": 0.04, "description": "Transformer attention weights"}
    ]

    return {
        "success": True,
        "features": features
    }

@app.get("/ai/decision-explanation/{symbol}")
async def get_decision_explanation(symbol: str):
    """Explication de la décision — basée sur la vraie prédiction courante."""
    try:
        pred = _prediction_engine.last_prediction if _prediction_engine else None
        if not pred:
            return {
                "status":     "disabled",
                "deployable": False,
                "reason":     "model_not_connected",
                "symbol":     symbol.upper(),
                "action":     "WAIT",
                "confidence": 0,
                "note":       "No live prediction available. Engine not running.",
            }

        action     = pred.get("action", "HOLD")
        confidence = pred.get("confidence", 0.0)
        return {
            "success":   True,
            "symbol":    symbol.upper(),
            "action":    action,
            "confidence": round(confidence, 4),
            "timestamp": pred.get("refreshed_at", datetime.utcnow().isoformat()),
            "source":    "live_prediction_engine",
            "p_long":    pred.get("p_long", 0),
            "regime":    pred.get("regime", "NEUTRAL"),
            "note":      "SHAP/attention explanations not yet implemented.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating explanation: {str(e)}")

@app.get("/ai/model-architecture")
async def get_model_architecture():
    """Obtenir les informations sur l'architecture du modèle."""
    return {
        "success": True,
        "architecture": {
            "model_type": "Multi-Modal Transformer",
            "encoder_layers": 2,
            "attention_heads": 4,
            "hidden_dimension": 128,
            "dropout": 0.1,
            "input_features": [
                "Price OHLCV",
                "Volume metrics",
                "Technical indicators",
                "Sentiment data",
                "Macro indicators"
            ],
            "output": "Price direction prediction with confidence",
            "training_samples": "~500K",
            "last_trained": "2024-12-14"
        }
    }

# ============================================================================
# DATA INTEGRITY ENDPOINTS
# ============================================================================

@app.get("/data-integrity/all")
async def get_all_data_integrity():
    """Analyse l'intégrité de toutes les cryptos."""
    try:
        analyzer = DataIntegrityAnalyzer()
        results = analyzer.analyze_all_cryptos()
        return results
    except Exception as e:
        logger.error(f"Error analyzing data integrity: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data-integrity/{crypto}")
async def get_crypto_data_integrity(crypto: str):
    """Analyse l'intégrité d'une crypto spécifique."""
    try:
        analyzer = DataIntegrityAnalyzer()
        result = analyzer.analyze_crypto_data(crypto.upper())
        return result
    except Exception as e:
        logger.error(f"Error analyzing {crypto}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data-integrity/available-cryptos")
async def get_available_cryptos():
    """Liste les cryptos disponibles dans le cache."""
    try:
        analyzer = DataIntegrityAnalyzer()
        cryptos = analyzer.get_available_cryptos()
        return {
            "cryptos": cryptos,
            "count": len(cryptos)
        }
    except Exception as e:
        logger.error(f"Error getting available cryptos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataset/crypto-data/{crypto}")
async def get_crypto_historical_data(
    crypto: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 1000
):
    """Récupère les données historiques d'une crypto avec prix et métadonnées."""
    try:
        s3_cache_path = Path("ai/cache/s3_data")
        pattern = f"{crypto.upper()}USDT_*.parquet"
        files = list(s3_cache_path.glob(pattern))

        if not files:
            raise HTTPException(status_code=404, detail=f"No data found for {crypto}")

        # Charger les données
        dfs = []
        for file in sorted(files):
            df = pd.read_parquet(file)
            dfs.append(df)

        full_df = pd.concat(dfs, ignore_index=True)

        # Filtrer par date si nécessaire
        if 'timestamp' in full_df.columns:
            full_df['timestamp'] = pd.to_datetime(full_df['timestamp'])

            # Filtrer les dates futures
            now = pd.Timestamp.now(tz='UTC')
            full_df = full_df[full_df['timestamp'] <= now]

            full_df = full_df.sort_values('timestamp')

            if start_date:
                full_df = full_df[full_df['timestamp'] >= start_date]
            if end_date:
                full_df = full_df[full_df['timestamp'] <= end_date]

        # Limiter le nombre de lignes
        if len(full_df) > limit:
            # Prendre des échantillons uniformément répartis
            indices = np.linspace(0, len(full_df) - 1, limit, dtype=int)
            full_df = full_df.iloc[indices]

        # Convertir en JSON
        data = full_df.to_dict('records')

        # Convertir les timestamps en strings
        for record in data:
            if 'timestamp' in record:
                record['timestamp'] = str(record['timestamp'])

        return {
            "crypto": crypto.upper(),
            "total_rows": len(data),
            "data": data,
            "columns": full_df.columns.tolist()
        }

    except Exception as e:
        logger.error(f"Error fetching crypto data for {crypto}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MONGODB STATS ENDPOINTS
# ============================================================================

@app.get("/data/mongodb/stats")
async def get_mongodb_stats():
    """Statistiques des collections MongoDB et fraicheur des données."""
    try:
        db = get_db()

        # Ping MongoDB
        mongo_ok = True
        try:
            db.client.admin.command("ping")
        except Exception:
            mongo_ok = False

        # Collections à surveiller: (collection_name, display, icon, db_name)
        targets = [
            # OHLCV multi-timeframe
            ("historical_ohlcv",    "OHLCV 1h (2017→)",     "📊", None),
            ("ohlcv_1m",            "OHLCV 1m (BTC+ETH+SOL)","⚡", None),
            ("ohlcv_5m",            "OHLCV 5m",             "📈", None),
            ("ohlcv_15m",           "OHLCV 15m",            "📈", None),
            ("ohlcv_4h",            "OHLCV 4h",             "📈", None),
            ("ohlcv_1d",            "OHLCV 1d",             "📈", None),
            # Alpha / Derivatives
            ("derivatives_funding", "Funding Rates",         "💹", None),
            ("derivatives_oi",      "Open Interest",         "📉", None),
            ("derivatives_ls",      "L/S Ratio Global",      "⚖️",  None),
            ("derivatives_ls_top",  "L/S Top Traders",       "🏆", None),
            ("options_btc",         "Options BTC (Deribit)", "🎯", None),
            # Sentiment / Macro
            ("sentiment_fng",       "Fear & Greed",          "😱", None),
            ("macro_global",        "Macro (DXY/SPX/VIX)",   "🌍", None),
            ("coingecko_global",    "CoinGecko Global",      "🦎", None),
            ("coingecko_coins",     "CoinGecko Coins",       "🪙", None),
            # On-chain
            ("whale_transactions",  "Whale Transactions",    "🐋", None),
            ("onchain_btc",         "On-chain BTC",          "⛓️",  None),
            # News / Intel
            ("articles",            "News Articles",         "📰", "market_intel"),
            ("signals",             "Intel Signals",         "📡", "market_intel"),
            # Portfolio
            ("portfolio_state",     "Portfolio State",       "💼", None),
        ]

        collections_info = []
        for coll_name, display, icon, alt_db in targets:
            try:
                if alt_db:
                    coll = db.client[alt_db][coll_name]
                else:
                    coll = db[coll_name]

                count = coll.estimated_document_count()

                last_update = None
                for ts_field in ["timestamp", "created_at", "updated_at", "date", "time", "fetched_at"]:
                    doc = coll.find_one(
                        {ts_field: {"$exists": True}},
                        sort=[(ts_field, -1)],
                        projection={ts_field: 1},
                    )
                    if doc:
                        ts_val = doc[ts_field]
                        if hasattr(ts_val, "isoformat"):
                            last_update = ts_val.isoformat()
                        else:
                            last_update = str(ts_val)
                        break

                collections_info.append({
                    "name": coll_name,
                    "display": display,
                    "icon": icon,
                    "count": count,
                    "last_update": last_update,
                    "status": "ok",
                })
            except Exception as e:
                collections_info.append({
                    "name": coll_name,
                    "display": display,
                    "icon": icon,
                    "count": 0,
                    "last_update": None,
                    "status": "error",
                    "error": str(e),
                })

        return {
            "mongo_connected": mongo_ok,
            "collections": collections_info,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"MongoDB stats error: {e}")
        return {
            "mongo_connected": False,
            "collections": [],
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


# ============================================================================
# SCRAPER MONITORING ENDPOINTS
# ============================================================================

# Global scraper process registry
_scraper_processes: Dict[str, Dict] = {}
_scraper_lock = threading.Lock()

_PY3 = "/usr/bin/python3"   # Python système avec tous les packages

SCRAPERS_REGISTRY = {
    "data_daemon": {
        "display": "Data Daemon",
        "description": "Multi-source auto: Binance 1m/5m/15m/4h, Bybit, OKX, Deribit IV, Mempool, Macro, CoinGecko",
        "cmd": [_PY3, str(_ROOT / "scripts" / "data_daemon.py")],
        "cwd": str(_ROOT),
        "icon": "⚡",
        "category": "data",
    },
    "ohlcv_1m_history": {
        "display": "OHLCV 1m History",
        "description": "Télécharge BTC 1m depuis 2021 → ~2.5M bars haute résolution",
        "cmd": [_PY3, str(_ROOT / "scripts" / "fetch_1m_history.py")],
        "cwd": str(_ROOT),
        "icon": "📊",
        "category": "data",
    },
    "alpha_ingest": {
        "display": "Alpha Ingest",
        "description": "Funding 2020→, Fear&Greed 2018→, OI, L/S, enrichissement OHLCV",
        "cmd": [_PY3, str(_ROOT / "scripts" / "ingest_alpha_data.py"), "--update"],
        "cwd": str(_ROOT),
        "icon": "🔬",
        "category": "data",
    },
    "whale_mempool": {
        "display": "Whale On-Chain",
        "description": "Transactions BTC >100 BTC depuis mempool.space + Blockchair (100% gratuit)",
        "cmd": [_PY3, str(_ROOT / "scripts" / "fetch_whale_onchain.py")],
        "cwd": str(_ROOT),
        "icon": "🐋",
        "category": "data",
    },
    "news_rss": {
        "display": "News RSS + NLP",
        "description": "17 sources RSS (CoinTelegraph, Decrypt, Bitcoin Mag, Google News…) + VADER sentiment",
        "cmd": [_PY3, str(_ROOT / "scripts" / "fetch_news.py"), "--update"],
        "cwd": str(_ROOT),
        "icon": "📰",
        "category": "news",
    },
    "api_collectors": {
        "display": "API Collectors",
        "description": "Fear & Greed + Funding + CoinGecko markets (collecte continue)",
        "cmd": [_PY3, str(_ROOT / "scrapers" / "marketintel" / "api_collectors" / "run_api_collectors.py")],
        "cwd": str(_ROOT),
        "icon": "📡",
        "category": "api",
    },
}


def _scraper_status(name: str) -> str:
    info = _scraper_processes.get(name, {})
    proc = info.get("process")
    if proc is None:
        return "stopped"
    rc = proc.poll()
    if rc is None:
        return "running"
    return "completed" if rc == 0 else "error"


@app.get("/scrapers/list")
async def list_scrapers():
    """Liste tous les scrapers avec leur statut courant."""
    result = []
    with _scraper_lock:
        for name, info in SCRAPERS_REGISTRY.items():
            proc_info = _scraper_processes.get(name, {})
            status = _scraper_status(name)

            log_file = proc_info.get("log_file", f"/tmp/scraper_{name}.log")
            last_lines: list = []
            try:
                lp = Path(log_file)
                if lp.exists():
                    with open(lp) as f:
                        last_lines = [l.rstrip() for l in f.readlines()[-15:]]
            except Exception:
                pass

            started_at = proc_info.get("started_at")
            result.append({
                "name": name,
                "display": info["display"],
                "description": info["description"],
                "icon": info["icon"],
                "category": info["category"],
                "status": status,
                "pid": proc_info.get("process").pid if proc_info.get("process") and proc_info["process"].poll() is None else None,
                "started_at": started_at.isoformat() if started_at else None,
                "last_lines": last_lines,
            })
    return {"scrapers": result, "timestamp": datetime.utcnow().isoformat()}


@app.post("/scrapers/{name}/start")
async def start_scraper(name: str):
    """Lance un scraper en tâche de fond."""
    if name not in SCRAPERS_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Scraper '{name}' inconnu")

    with _scraper_lock:
        if _scraper_status(name) == "running":
            return {"success": False, "message": f"Scraper '{name}' déjà en cours"}

    info = SCRAPERS_REGISTRY[name]
    log_file = Path(f"/tmp/scraper_{name}.log")

    try:
        env = os.environ.copy()
        cwd = info.get("cwd", str(_ROOT))
        process = subprocess.Popen(
            info["cmd"],
            stdout=open(log_file, "w"),
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        with _scraper_lock:
            _scraper_processes[name] = {
                "process": process,
                "log_file": str(log_file),
                "started_at": datetime.utcnow(),
            }
        logger.info(f"Scraper '{name}' démarré PID={process.pid}")
        return {"success": True, "message": f"Scraper '{name}' démarré", "pid": process.pid}
    except Exception as e:
        logger.error(f"Erreur démarrage scraper '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scrapers/{name}/stop")
async def stop_scraper(name: str):
    """Arrête un scraper en cours."""
    if name not in SCRAPERS_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Scraper '{name}' inconnu")

    with _scraper_lock:
        proc_info = _scraper_processes.get(name, {})
        proc = proc_info.get("process")

    if proc is None or proc.poll() is not None:
        return {"success": False, "message": f"Scraper '{name}' n'est pas en cours"}

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info(f"Scraper '{name}' arrêté")
        return {"success": True, "message": f"Scraper '{name}' arrêté"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scrapers/{name}/logs")
async def get_scraper_logs(name: str, lines: int = 100):
    """Récupère les dernières lignes de logs d'un scraper."""
    if name not in SCRAPERS_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Scraper '{name}' inconnu")

    with _scraper_lock:
        proc_info = _scraper_processes.get(name, {})
    log_file = proc_info.get("log_file", f"/tmp/scraper_{name}.log")

    try:
        lp = Path(log_file)
        if not lp.exists():
            return {"name": name, "logs": [], "message": "Aucun log disponible"}
        with open(lp) as f:
            all_lines = f.readlines()
        return {
            "name": name,
            "logs": [l.rstrip() for l in all_lines[-lines:]],
            "total_lines": len(all_lines),
            "log_file": str(lp),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/system/deployment-status")
async def get_deployment_status():
    """
    Statut de déploiement du système.
    Source de vérité unique pour savoir si le bot peut trader.
    """
    try:
        from config.deployment_status import (
            LIVE_ENABLED, PAPER_ENABLED, SHORT_ENABLED as _SHORT,
            COMBINED_ENABLED, DEPLOYMENT_STATUS, DEPLOYMENT_REASON,
            LONG_KNOWN_METRICS, MIN_LONG_TRADES_FOR_PAPER, MIN_LONG_TRADES_FOR_LIVE,
        )
        from config.strategy_flags import MIN_LONG_TRADES_FOR_DEPLOY

        # Essaie de lire les derniers résultats de backtest si disponibles
        long_status = LONG_KNOWN_METRICS.copy()
        run_dir = _latest_run_dir()
        if run_dir:
            try:
                summary_path = run_dir / "pipeline_summary.json"
                if summary_path.exists():
                    with open(summary_path) as f:
                        summary = json.load(f)
                    bt = summary.get("backtest_long", {})
                    if bt:
                        long_status = {
                            "n_trades":            bt.get("n_trades", 0),
                            "profit_factor":       bt.get("profit_factor", 0),
                            "win_rate":            bt.get("win_rate", 0),
                            "expectancy_per_trade": bt.get("expectancy_per_trade", 0),
                            "sharpe_annualized":   bt.get("sharpe_annualized", 0),
                            "max_drawdown_pct":    bt.get("max_drawdown", 0) * 100,
                            "total_return_pct":    bt.get("total_return_pct", 0),
                            "run_id":              run_dir.name,
                            "note":                "Sharpe irréaliste si n_trades < 50." if bt.get("n_trades", 0) < 50 else "",
                        }
            except Exception:
                pass

        n_trades = long_status.get("n_trades", 0)
        deployable_long = (
            n_trades >= MIN_LONG_TRADES_FOR_DEPLOY
            and long_status.get("profit_factor", 0) >= 1.20
            and long_status.get("expectancy_per_trade", 0) > 0
        )

        if n_trades < MIN_LONG_TRADES_FOR_PAPER:
            long_status_str = "promising_but_insufficient_sample"
            long_reason     = f"only {n_trades} trades, minimum required {MIN_LONG_TRADES_FOR_PAPER}"
        elif deployable_long:
            long_status_str = "deployable_paper"
            long_reason     = "paper trading gate passed"
        else:
            long_status_str = "backtest_failed_validation"
            long_reason     = "validation gates not met"

        return {
            "live_enabled":     LIVE_ENABLED,
            "paper_enabled":    PAPER_ENABLED and deployable_long,
            "short_enabled":    False,
            "combined_enabled": False,
            "deployment_status": DEPLOYMENT_STATUS if not deployable_long else "PAPER_ONLY",
            "reason":           DEPLOYMENT_REASON if not deployable_long else "long_only_paper_validated",
            "short_disabled_reason": "unstable PF < 1 across tested years, negative expectancy",
            "combined_disabled_reason": "COMBINED rejected: SHORT component fails validation",
            "long_only": {
                "status":            long_status_str,
                "deployable":        deployable_long,
                "reason":            long_reason,
                "n_trades":          n_trades,
                "min_required_paper": MIN_LONG_TRADES_FOR_PAPER,
                "min_required_live":  MIN_LONG_TRADES_FOR_LIVE,
                "metrics":           long_status,
            },
            "warnings": [
                "Sharpe irréaliste si n_trades < 50 — ne pas utiliser comme indicateur de performance."
                if n_trades < 50 else None,
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
    except ImportError as e:
        return {
            "live_enabled": False,
            "paper_enabled": False,
            "short_enabled": False,
            "combined_enabled": False,
            "deployment_status": "NOT_DEPLOYABLE",
            "reason": f"deployment_status config missing: {e}",
            "timestamp": datetime.utcnow().isoformat(),
        }


@app.get("/backtest/long-only")
async def get_long_only_backtest_summary():
    """Résumé du dernier backtest LONG-only."""
    run_dir = _latest_run_dir()
    if not run_dir:
        raise HTTPException(status_code=404, detail="Aucun run trouvé")
    try:
        with open(run_dir / "pipeline_summary.json") as f:
            summary = json.load(f)
        bt = summary.get("backtest_long", {})
        if not bt:
            return {"status": "no_backtest_long", "deployable": False}
        n = bt.get("n_trades", 0)
        from config.strategy_flags import MIN_LONG_TRADES_FOR_DEPLOY, MIN_PROFIT_FACTOR
        deployable = (
            n >= MIN_LONG_TRADES_FOR_DEPLOY
            and bt.get("profit_factor", 0) >= MIN_PROFIT_FACTOR
            and bt.get("expectancy_per_trade", 0) > 0
        )
        return {
            "status":     "promising_but_insufficient_sample" if n < MIN_LONG_TRADES_FOR_DEPLOY else ("deployable" if deployable else "failed_validation"),
            "deployable": deployable,
            "n_trades":   n,
            "min_required": MIN_LONG_TRADES_FOR_DEPLOY,
            "run_id":     run_dir.name,
            "metrics":    bt,
            "warning":    f"Sharpe={bt.get('sharpe_annualized', 0):.1f} est irréaliste avec {n} trades." if n < 50 else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("🚀 ALPHA TRADING API SERVER")
    print("=" * 80)
    print(f"\nStarting server on http://{API_HOST}:{API_PORT}")
    print(f"API Documentation: http://{API_HOST}:{API_PORT}/docs")
    print("\n📊 MARKET DATA ENDPOINTS:")
    print("  - GET /market/all-cryptos      - All cryptos with current & previous prices")
    print("  - GET /market/ticker           - Ticker data for a symbol")
    print("  - GET /market/klines           - Candlestick data (OHLCV)")
    print("  - GET /market/orderbook        - Order book depth")
    print("  - GET /market/trades           - Recent trades")
    print("\n📈 DATASET ENDPOINTS:")
    print("  - GET /dataset/summary         - Dataset summary")
    print("  - GET /dataset/signals         - Alpha signals")
    print("  - GET /dataset/ohlcv/{symbol}  - OHLCV data")
    print("  - GET /dataset/fear-greed      - Fear & Greed Index")
    print("  - GET /dataset/sentiment       - Reddit sentiment")
    print("  - GET /dataset/macro           - Macro data")
    print("  - GET /dataset/derivatives     - Derivatives data")
    print("\n🤖 REAL-TIME PIPELINE ENDPOINTS:")
    print("  - POST /pipeline/start         - Start real-time pipeline")
    print("  - POST /pipeline/stop          - Stop pipeline")
    print("  - GET /pipeline/status         - Pipeline status & stats")
    print("  - GET /pipeline/predictions    - All current predictions")
    print("  - GET /pipeline/prediction/{symbol} - Prediction for symbol")
    print("  - GET /pipeline/features/{symbol}   - Features for symbol")
    print("  - GET /pipeline/symbols        - Active symbols")
    print("\n" + "=" * 80 + "\n")

    uvicorn.run(app, host=API_HOST, port=API_PORT)
