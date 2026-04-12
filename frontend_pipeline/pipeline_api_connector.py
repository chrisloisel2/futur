"""
Pipeline API Connector - Connecte la pipeline temps réel à l'API FastAPI
"""
import asyncio
import sys
from pathlib import Path
from typing import Dict, List
import logging
from datetime import datetime

# Ajouter le path de la pipeline
pipeline_path = Path(__file__).parent.parent / "ai" / "models" / "pipeline"
sys.path.insert(0, str(pipeline_path))

from realtime_pipeline import RealTimePipeline
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PipelineAPIConnector:
    """Connecteur entre la pipeline temps réel et l'API."""

    def __init__(self):
        self.pipeline = None
        self.is_running = False
        self.last_update = {}
        self.config_path = Path(__file__).parent / "pipeline_config.json"

    async def start_pipeline(self, config: Dict = None):
        """Démarrer la pipeline avec la configuration donnée."""
        if self.is_running:
            logger.warning("⚠️ Pipeline already running")
            return {"status": "running", "message": "Pipeline already running"}

        # Charger la configuration (fichier + override éventuel)
        base_config = self._get_default_config()
        if config:
            config = self._deep_merge_dicts(base_config, config)
        else:
            config = base_config

        try:
            self.pipeline = RealTimePipeline(config)
            self.is_running = True

            # Lancer la pipeline dans un task séparé
            asyncio.create_task(self.pipeline.run())

            logger.info("✅ Pipeline started successfully")
            return {"status": "started", "message": "Real-time pipeline is now running"}

        except Exception as e:
            logger.error(f"❌ Error starting pipeline: {e}")
            self.is_running = False
            return {"status": "error", "message": str(e)}

    async def stop_pipeline(self):
        """Arrêter la pipeline."""
        if not self.is_running or not self.pipeline:
            logger.warning("⚠️ Pipeline not running")
            return

        try:
            await self.pipeline.cleanup()
            self.is_running = False
            logger.info("✅ Pipeline stopped successfully")
            return {"status": "stopped", "message": "Pipeline has been stopped"}

        except Exception as e:
            logger.error(f"❌ Error stopping pipeline: {e}")
            return {"status": "error", "message": str(e)}

    def get_predictions(self) -> Dict:
        """Obtenir les prédictions actuelles."""
        if not self.is_running or not self.pipeline:
            return {}

        predictions = self.pipeline.get_predictions()

        # Formater pour l'API
        formatted = {}
        for symbol, pred in predictions.items():
            formatted[symbol] = {
                "signal": pred.get("signal", "HOLD"),
                "confidence": pred.get("confidence", 0.0),
                "price": pred.get("price", 0.0),
                "timestamp": pred.get("timestamp", 0),
                "indicators": pred.get("indicators", {})
            }

        return formatted

    def get_prediction(self, symbol: str) -> Dict:
        """Obtenir la prédiction pour un symbole spécifique."""
        predictions = self.get_predictions()
        return predictions.get(symbol, {
            "signal": "HOLD",
            "confidence": 0.0,
            "message": "No prediction available"
        })

    def get_stats(self) -> Dict:
        """Obtenir les statistiques de la pipeline."""
        if not self.is_running or not self.pipeline:
            return {
                "status": "stopped",
                "message": "Pipeline is not running"
            }

        stats = self.pipeline.get_stats()

        runtime = (datetime.now() - stats['start_time']).seconds
        trades_per_sec = stats['trades_processed'] / runtime if runtime > 0 else 0

        return {
            "status": "running",
            "runtime_seconds": runtime,
            "trades_processed": stats['trades_processed'],
            "trades_per_second": round(trades_per_sec, 2),
            "predictions_made": stats['predictions_made'],
            "symbols_tracked": stats['symbols_tracked'],
            "start_time": stats['start_time'].isoformat(),
            "collectors": self.pipeline.get_collector_status()
        }

    def get_features(self, symbol: str) -> Dict:
        """Obtenir les features calculées pour un symbole."""
        if not self.is_running or not self.pipeline:
            return {}

        features = self.pipeline.feature_processor.calculate_features(symbol)
        return features if features else {}

    def get_active_symbols(self) -> List[str]:
        """Obtenir la liste des symboles actifs."""
        if not self.is_running or not self.pipeline:
            return []

        return list(self.pipeline.active_symbols)

    def _get_default_config(self) -> Dict:
        """Charger la configuration par défaut depuis pipeline_config.json."""
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, "r") as f:
                    return json.load(f)
        except Exception as exc:
            logger.error(f"⚠️ Impossible de charger pipeline_config.json: {exc}")

        # Fallback minimal
        return {
            'window_size': 100,
            'buffer_size': 1000,
            'storage_path': 'datasets/realtime',
            'collectors': {
                'finnhub': {
                    'enabled': True,
                    'api_key': 'YOUR_FINNHUB_API_KEY',  # À remplacer
                    'stocks': ['AAPL', 'TSLA', 'MSFT', 'GOOGL', 'AMZN'],
                    'crypto': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT'],
                    'crypto_exchange': 'BINANCE'
                }
            }
        }

    def _deep_merge_dicts(self, base: Dict, override: Dict) -> Dict:
        """Fusionner récursivement deux dictionnaires."""
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged


# Instance globale du connector
pipeline_connector = PipelineAPIConnector()
