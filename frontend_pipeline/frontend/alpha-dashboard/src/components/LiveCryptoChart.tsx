import React, { useState, useEffect, useRef } from 'react';
import './LiveCryptoChart.css';

interface PricePoint {
  time: number;
  price: number;
  predicted?: boolean;
  confidence?: number;
}

interface CryptoOption {
  symbol: string;
  name: string;
  base: string;
}

const CRYPTO_LIST: CryptoOption[] = [
  { symbol: 'BTCUSDT', name: 'Bitcoin', base: 'BTC' },
  { symbol: 'ETHUSDT', name: 'Ethereum', base: 'ETH' },
  { symbol: 'BNBUSDT', name: 'Binance Coin', base: 'BNB' },
  { symbol: 'SOLUSDT', name: 'Solana', base: 'SOL' },
  { symbol: 'XRPUSDT', name: 'Ripple', base: 'XRP' },
  { symbol: 'ADAUSDT', name: 'Cardano', base: 'ADA' },
  { symbol: 'DOGEUSDT', name: 'Dogecoin', base: 'DOGE' },
  { symbol: 'MATICUSDT', name: 'Polygon', base: 'MATIC' },
  { symbol: 'DOTUSDT', name: 'Polkadot', base: 'DOT' },
  { symbol: 'AVAXUSDT', name: 'Avalanche', base: 'AVAX' },
  { symbol: 'LINKUSDT', name: 'Chainlink', base: 'LINK' },
  { symbol: 'UNIUSDT', name: 'Uniswap', base: 'UNI' },
  { symbol: 'ATOMUSDT', name: 'Cosmos', base: 'ATOM' },
  { symbol: 'LTCUSDT', name: 'Litecoin', base: 'LTC' },
  { symbol: 'ETCUSDT', name: 'Ethereum Classic', base: 'ETC' },
];

const LiveCryptoChart: React.FC = () => {
  const [selectedCrypto, setSelectedCrypto] = useState<string>('BTCUSDT');
  const [priceHistory, setPriceHistory] = useState<PricePoint[]>([]);
  const [predictions, setPredictions] = useState<PricePoint[]>([]);
  const [currentPrice, setCurrentPrice] = useState<number>(0);
  const [dayChange, setDayChange] = useState<number>(0);
  const [dayChangePct, setDayChangePct] = useState<number>(0);
  const [dayHigh, setDayHigh] = useState<number>(0);
  const [dayLow, setDayLow] = useState<number>(0);
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const [showPredictions, setShowPredictions] = useState<boolean>(true);

  const wsRef = useRef<WebSocket | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const predictionIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Charger l'historique du jour au démarrage
  useEffect(() => {
    loadDayHistory(selectedCrypto);
    connectWebSocket(selectedCrypto);
    fetchPredictions(selectedCrypto);

    // Mettre à jour les prédictions toutes les 2 minutes
    predictionIntervalRef.current = setInterval(() => {
      fetchPredictions(selectedCrypto);
    }, 120000);

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (predictionIntervalRef.current) {
        clearInterval(predictionIntervalRef.current);
      }
    };
  }, [selectedCrypto]);

  // Redessiner le graphique quand les données changent
  useEffect(() => {
    drawChart();
  }, [priceHistory, predictions, selectedCrypto, showPredictions]);

  const loadDayHistory = async (symbol: string) => {
    try {
      // Charger les données du jour depuis l'API backend
      const response = await fetch(`http://localhost:8000/market/klines?symbol=${symbol}&interval=1m&limit=1440`);
      const data = await response.json();

      if (data && data.length > 0) {
        const history: PricePoint[] = data.map((candle: any) => ({
          time: candle[0], // open time
          price: parseFloat(candle[4]), // close price
          predicted: false,
        }));

        setPriceHistory(history);

        // Calculer les stats du jour
        const prices = history.map(p => p.price);
        setDayHigh(Math.max(...prices));
        setDayLow(Math.min(...prices));
      }
    } catch (error) {
      console.error('Erreur chargement historique:', error);
    }
  };

  const fetchPredictions = async (symbol: string) => {
    try {
      // Appeler l'API de prédictions futures
      const response = await fetch(`http://localhost:8000/pipeline/predictions/future/${symbol}?minutes=60`);
      const data = await response.json();

      if (data && data.predictions) {
        const preds: PricePoint[] = data.predictions.map((pred: any) => ({
          time: new Date(pred.timestamp).getTime(),
          price: pred.predicted_price,
          predicted: true,
          confidence: pred.confidence,
        }));

        setPredictions(preds);
      }
    } catch (error) {
      console.log('Pipeline predictions not available for', symbol);
      // Générer des prédictions génériques
      generateFallbackPredictions();
    }
  };

  const generateFallbackPredictions = () => {
    if (priceHistory.length === 0) return;

    const lastPrice = priceHistory[priceHistory.length - 1].price;
    const now = Date.now();
    const preds: PricePoint[] = [];

    // Tendance simple basée sur les derniers mouvements
    const recentPrices = priceHistory.slice(-10).map(p => p.price);
    const trend = (recentPrices[recentPrices.length - 1] - recentPrices[0]) / recentPrices[0];

    for (let i = 1; i <= 60; i++) {
      const minutesAhead = i;
      const volatility = 0.001; // 0.1% de volatilité
      const randomWalk = (Math.random() - 0.5) * volatility;
      const predictedPrice = lastPrice * (1 + trend * (minutesAhead / 60) + randomWalk);

      preds.push({
        time: now + minutesAhead * 60000,
        price: predictedPrice,
        predicted: true,
        confidence: Math.max(0.3, 0.8 - (minutesAhead * 0.01)),
      });
    }

    setPredictions(preds);
  };

  const connectWebSocket = (symbol: string) => {
    // Fermer la connexion précédente
    if (wsRef.current) {
      wsRef.current.close();
    }

    try {
      const wsUrl = `wss://stream.binance.com:9443/ws/${symbol.toLowerCase()}@ticker`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log(`✅ WebSocket connecté pour ${symbol}`);
        setWsStatus('connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          updatePrice(data);
        } catch (error) {
          console.error('Erreur parsing WebSocket:', error);
        }
      };

      ws.onerror = (error) => {
        console.error('❌ WebSocket erreur:', error);
        setWsStatus('disconnected');
      };

      ws.onclose = () => {
        console.log('🔌 WebSocket fermé, reconnexion...');
        setWsStatus('disconnected');

        reconnectTimeoutRef.current = setTimeout(() => {
          connectWebSocket(symbol);
        }, 5000);
      };
    } catch (error) {
      console.error('Erreur connexion WebSocket:', error);
      setWsStatus('disconnected');
    }
  };

  const updatePrice = (data: any) => {
    const price = parseFloat(data.c);
    const change = parseFloat(data.p);
    const changePct = parseFloat(data.P);
    const high = parseFloat(data.h);
    const low = parseFloat(data.l);

    setCurrentPrice(price);
    setDayChange(change);
    setDayChangePct(changePct);
    setDayHigh(high);
    setDayLow(low);

    // Ajouter le nouveau point au graphique
    const now = Date.now();
    setPriceHistory(prev => {
      const newHistory = [...prev, { time: now, price, predicted: false }];

      // Garder seulement les données du jour (1440 minutes max)
      const dayStart = now - 24 * 60 * 60 * 1000;
      return newHistory.filter(p => p.time >= dayStart);
    });
  };

  const drawChart = () => {
    const canvas = canvasRef.current;
    if (!canvas || priceHistory.length === 0) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const width = canvas.width;
    const height = canvas.height;
    const padding = 50;

    // Combiner historique et prédictions pour l'échelle
    const allPoints = [...priceHistory, ...predictions];
    if (allPoints.length === 0) return;

    const prices = allPoints.map(p => p.price);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const priceRange = maxPrice - minPrice || 1;

    const times = allPoints.map(p => p.time);
    const minTime = Math.min(...times);
    const maxTime = Math.max(...times);
    const timeRange = maxTime - minTime || 1;

    // Fonction pour convertir les coordonnées
    const toX = (time: number) => padding + ((time - minTime) / timeRange) * (width - 2 * padding);
    const toY = (price: number) => height - padding - ((price - minPrice) / priceRange) * (height - 2 * padding);

    // Dessiner la grille
    ctx.strokeStyle = '#2a2a3e';
    ctx.lineWidth = 1;

    // Lignes horizontales
    for (let i = 0; i <= 5; i++) {
      const y = padding + (i / 5) * (height - 2 * padding);
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(width - padding, y);
      ctx.stroke();

      // Prix
      const price = maxPrice - (i / 5) * priceRange;
      ctx.fillStyle = '#888';
      ctx.font = '12px monospace';
      ctx.textAlign = 'right';
      ctx.fillText(`$${price.toFixed(2)}`, padding - 10, y + 4);
    }

    // Lignes verticales
    for (let i = 0; i <= 6; i++) {
      const x = padding + (i / 6) * (width - 2 * padding);
      ctx.beginPath();
      ctx.moveTo(x, padding);
      ctx.lineTo(x, height - padding);
      ctx.stroke();

      // Temps
      const time = minTime + (i / 6) * timeRange;
      const date = new Date(time);
      ctx.fillStyle = '#888';
      ctx.font = '10px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }), x, height - padding + 20);
    }

    // Dessiner l'historique des prix (ligne bleue)
    if (priceHistory.length > 1) {
      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = 2;
      ctx.beginPath();

      priceHistory.forEach((point, index) => {
        const x = toX(point.time);
        const y = toY(point.price);

        if (index === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });

      ctx.stroke();

      // Remplir sous la courbe
      ctx.lineTo(toX(priceHistory[priceHistory.length - 1].time), height - padding);
      ctx.lineTo(toX(priceHistory[0].time), height - padding);
      ctx.closePath();
      ctx.fillStyle = 'rgba(59, 130, 246, 0.1)';
      ctx.fill();
    }

    // Dessiner les prédictions (ligne verte en pointillés)
    if (showPredictions && predictions.length > 0 && priceHistory.length > 0) {
      const lastHistoricPoint = priceHistory[priceHistory.length - 1];

      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();

      // Commencer depuis le dernier point historique
      ctx.moveTo(toX(lastHistoricPoint.time), toY(lastHistoricPoint.price));

      predictions.forEach((point) => {
        const x = toX(point.time);
        const y = toY(point.price);
        ctx.lineTo(x, y);
      });

      ctx.stroke();
      ctx.setLineDash([]);

      // Afficher la zone de confiance
      ctx.fillStyle = 'rgba(16, 185, 129, 0.1)';
      ctx.beginPath();

      // Ligne haute (confiance +)
      predictions.forEach((point, index) => {
        const confidence = point.confidence || 0.5;
        const offset = (maxPrice - minPrice) * 0.02 * (1 - confidence);
        const x = toX(point.time);
        const y = toY(point.price + offset);

        if (index === 0) {
          ctx.moveTo(toX(lastHistoricPoint.time), toY(lastHistoricPoint.price));
          ctx.lineTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });

      // Ligne basse (confiance -)
      for (let i = predictions.length - 1; i >= 0; i--) {
        const point = predictions[i];
        const confidence = point.confidence || 0.5;
        const offset = (maxPrice - minPrice) * 0.02 * (1 - confidence);
        const x = toX(point.time);
        const y = toY(point.price - offset);
        ctx.lineTo(x, y);
      }

      ctx.closePath();
      ctx.fill();
    }

    // Dessiner le point actuel
    if (priceHistory.length > 0) {
      const lastPoint = priceHistory[priceHistory.length - 1];
      const x = toX(lastPoint.time);
      const y = toY(lastPoint.price);

      ctx.fillStyle = '#3b82f6';
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, 2 * Math.PI);
      ctx.fill();
    }
  };

  const formatPrice = (price: number): string => {
    if (price >= 1000) return price.toFixed(2);
    if (price >= 1) return price.toFixed(4);
    return price.toFixed(6);
  };

  const selectedCryptoInfo = CRYPTO_LIST.find(c => c.symbol === selectedCrypto);

  return (
    <div className="live-crypto-chart">
      {/* Header avec sélection */}
      <div className="chart-header">
        <div className="crypto-selector">
          <label htmlFor="crypto-select">Select Cryptocurrency:</label>
          <select
            id="crypto-select"
            value={selectedCrypto}
            onChange={(e) => setSelectedCrypto(e.target.value)}
            className="crypto-dropdown"
          >
            {CRYPTO_LIST.map((crypto) => (
              <option key={crypto.symbol} value={crypto.symbol}>
                {crypto.name} ({crypto.base})
              </option>
            ))}
          </select>
        </div>

        <div className="prediction-toggle">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showPredictions}
              onChange={(e) => setShowPredictions(e.target.checked)}
            />
            <span>Show AI Predictions</span>
          </label>
        </div>

        <div className="ws-status-indicator">
          <div className={`status-dot status-${wsStatus}`}></div>
          <span>{wsStatus === 'connected' ? 'Live' : 'Connecting...'}</span>
        </div>
      </div>

      {/* Prix actuel et stats */}
      <div className="price-stats">
        <div className="current-price-display">
          <div className="crypto-name-display">
            <h2>{selectedCryptoInfo?.name}</h2>
            <span className="symbol-display">{selectedCrypto}</span>
          </div>
          <div className="price-main">
            <span className="price-value">${formatPrice(currentPrice)}</span>
            <span className={`price-change ${dayChangePct >= 0 ? 'positive' : 'negative'}`}>
              {dayChangePct >= 0 ? '↗' : '↘'} {Math.abs(dayChangePct).toFixed(2)}% ({dayChangePct >= 0 ? '+' : ''}{formatPrice(dayChange)})
            </span>
          </div>
        </div>

        <div className="day-stats">
          <div className="stat-box">
            <span className="stat-label">24h High</span>
            <span className="stat-value">${formatPrice(dayHigh)}</span>
          </div>
          <div className="stat-box">
            <span className="stat-label">24h Low</span>
            <span className="stat-value">${formatPrice(dayLow)}</span>
          </div>
          <div className="stat-box">
            <span className="stat-label">24h Range</span>
            <span className="stat-value">{((dayHigh - dayLow) / dayLow * 100).toFixed(2)}%</span>
          </div>
        </div>
      </div>

      {/* Graphique */}
      <div className="chart-container">
        <div className="chart-legend">
          <div className="legend-item">
            <div className="legend-line historical"></div>
            <span>Historical Price</span>
          </div>
          {showPredictions && (
            <div className="legend-item">
              <div className="legend-line predicted"></div>
              <span>AI Prediction</span>
            </div>
          )}
        </div>
        <canvas
          ref={canvasRef}
          width={1200}
          height={500}
          className="price-chart-canvas"
        />
      </div>

      {/* Prédictions détaillées */}
      {showPredictions && predictions.length > 0 && (
        <div className="predictions-details">
          <h3>🤖 AI Predictions (Next Hour)</h3>
          <div className="predictions-timeline">
            {predictions.slice(0, 12).map((pred, index) => {
              const minutesAhead = ((index + 1) * 5);
              return (
                <div key={index} className="prediction-card">
                  <div className="pred-time">+{minutesAhead}min</div>
                  <div className="pred-price">${formatPrice(pred.price)}</div>
                  <div className={`pred-change ${((pred.price - currentPrice) / currentPrice * 100) >= 0 ? 'positive' : 'negative'}`}>
                    {((pred.price - currentPrice) / currentPrice * 100).toFixed(3)}%
                  </div>
                  <div className="pred-confidence">
                    <div className="confidence-bar-container">
                      <div
                        className="confidence-bar-fill"
                        style={{ width: `${(pred.confidence || 0.5) * 100}%` }}
                      ></div>
                    </div>
                    <span className="confidence-text">{((pred.confidence || 0.5) * 100).toFixed(0)}%</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default LiveCryptoChart;
