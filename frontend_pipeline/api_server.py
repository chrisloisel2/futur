"""
API SERVER FOR ALPHA DASHBOARD
===============================
Serveur FastAPI pour exposer les données de trading alpha au frontend React.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import uvicorn
import logging


from mongo_utils import fetch_historical_from_mongo, normalize_symbol

app = FastAPI(title="Alpha Trading API", version="2.0")


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# CORS pour permettre les requêtes depuis React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
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
    """Générer des trades récents simulés."""
    try:
        dataset_path = get_latest_dataset_path()
        ohlcv_file = dataset_path / "binance_ohlcv.parquet"

        if not ohlcv_file.exists():
            raise HTTPException(status_code=404, detail="OHLCV data not found")

        df = pd.read_parquet(ohlcv_file)
        df_symbol = df[df['symbol'] == symbol].sort_values('timestamp').tail(10)

        if len(df_symbol) == 0:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        import random
        trades = []

        for _, row in df_symbol.iterrows():
            # Générer quelques trades par candle
            for i in range(5):
                price_range = float(row['high']) - float(row['low'])
                price = float(row['low']) + random.random() * price_range
                quantity = random.uniform(0.01, 2.0)

                timestamp = row['timestamp']
                if hasattr(timestamp, 'timestamp'):
                    time_ms = int(timestamp.timestamp() * 1000) + i * 1000
                else:
                    time_ms = int(pd.Timestamp(timestamp).timestamp() * 1000) + i * 1000

                trades.append({
                    "id": time_ms,
                    "price": f"{price:.2f}",
                    "quantity": f"{quantity:.4f}",
                    "time": time_ms,
                    "isBuyerMaker": random.choice([True, False])
                })

        # Trier par temps et limiter
        trades.sort(key=lambda x: x['time'], reverse=True)
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
# REAL-TIME PIPELINE ENDPOINTS
# ============================================================================

# Import du connector
from pipeline_api_connector import pipeline_connector

@app.post("/pipeline/start")
async def start_pipeline(config: Optional[Dict] = None):
    """Démarrer la pipeline temps réel."""
    try:
        result = await pipeline_connector.start_pipeline(config)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pipeline/stop")
async def stop_pipeline():
    """Arrêter la pipeline."""
    try:
        result = await pipeline_connector.stop_pipeline()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/status")
async def get_pipeline_status():
    """Obtenir le statut et les stats de la pipeline."""
    try:
        stats = pipeline_connector.get_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/predictions")
async def get_all_predictions():
    """Obtenir toutes les prédictions actuelles."""
    try:
        predictions = pipeline_connector.get_predictions()
        return {
            "count": len(predictions),
            "predictions": predictions,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/prediction/{symbol}")
async def get_prediction(symbol: str):
    """Obtenir la prédiction pour un symbole spécifique."""
    try:
        prediction = pipeline_connector.get_prediction(symbol.upper())
        return prediction
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/features/{symbol}")
async def get_features(symbol: str):
    """Obtenir les features calculées pour un symbole."""
    try:
        features = pipeline_connector.get_features(symbol.upper())
        if not features:
            raise HTTPException(status_code=404, detail=f"No features available for {symbol}")
        return features
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pipeline/symbols")
async def get_active_symbols():
    """Obtenir la liste des symboles actifs."""
    try:
        symbols = pipeline_connector.get_active_symbols()
        return {
            "count": len(symbols),
            "symbols": symbols
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("🚀 ALPHA TRADING API SERVER")
    print("=" * 80)
    print("\nStarting server on http://localhost:8000")
    print("API Documentation: http://localhost:8000/docs")
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

    uvicorn.run(app, host="0.0.0.0", port=8000)
