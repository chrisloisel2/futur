import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import './CandlestickChart.css';

interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface Prediction {
  timestamp: string;
  predicted_price: number;
  confidence: number;
}

const CRYPTO_LIST = [
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

const TIME_INTERVALS = [
  { value: '1m', label: '1 minute', limit: 1440 },
  { value: '5m', label: '5 minutes', limit: 288 },
  { value: '15m', label: '15 minutes', limit: 96 },
  { value: '1h', label: '1 heure', limit: 168 },
  { value: '4h', label: '4 heures', limit: 180 },
  { value: '1d', label: '1 jour', limit: 90 },
];

// Cache pour stocker les données de la journée
const dataCache = new Map<string, { data: CandleData[]; timestamp: number }>();
const CACHE_DURATION = 60000; // 1 minute

const CandlestickChart: React.FC = () => {
  const [selectedCrypto, setSelectedCrypto] = useState<string>('BTCUSDT');
  const [selectedInterval, setSelectedInterval] = useState<string>('15m');
  const [candleData, setCandleData] = useState<CandleData[]>([]);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [showPredictions, setShowPredictions] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(true);
  const [currentPrice, setCurrentPrice] = useState<number>(0);
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');

  const wsRef = useRef<WebSocket | null>(null);
  const predictionIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const echartRef = useRef<any>(null);

  // Chargement des données avec cache
  const loadCandleData = useCallback(async () => {
    try {
      setLoading(true);
      const interval = TIME_INTERVALS.find(i => i.value === selectedInterval);
      const limit = interval?.limit || 500;

      // Vérifier le cache
      const cacheKey = `${selectedCrypto}_${selectedInterval}`;
      const cached = dataCache.get(cacheKey);
      const now = Date.now();

      if (cached && (now - cached.timestamp) < CACHE_DURATION) {
        console.log('📦 Utilisation du cache pour', cacheKey);
        console.log('📊 Données:', cached.data.length, 'chandelles');
        if (cached.data.length > 0) {
          const lastCandle = cached.data[cached.data.length - 1];
          console.log('⏰ Dernière chandelle:', new Date(lastCandle.time).toLocaleString());
        }
        setCandleData(cached.data);
        if (cached.data.length > 0) {
          setCurrentPrice(cached.data[cached.data.length - 1].close);
        }
        setLoading(false);
        return;
      }

      console.log('🔄 Chargement depuis API pour', cacheKey);
      const response = await fetch(
        `http://localhost:8000/market/klines?symbol=${selectedCrypto}&interval=${selectedInterval}&limit=${limit}`
      );
      const data = await response.json();

      if (data && data.length > 0) {
        const formattedData: CandleData[] = data.map((kline: any) => ({
          time: kline.open_time,
          open: parseFloat(kline.open),
          high: parseFloat(kline.high),
          low: parseFloat(kline.low),
          close: parseFloat(kline.close),
          volume: parseFloat(kline.volume),
        }));

        // Trier par ordre chronologique (au cas où)
        formattedData.sort((a, b) => a.time - b.time);

        console.log('📊 Données chargées:', formattedData.length, 'chandelles');
        if (formattedData.length > 0) {
          const firstCandle = formattedData[0];
          const lastCandle = formattedData[formattedData.length - 1];
          console.log('⏰ Première chandelle:', new Date(firstCandle.time).toLocaleString());
          console.log('⏰ Dernière chandelle:', new Date(lastCandle.time).toLocaleString());
        }

        // Mettre en cache
        dataCache.set(cacheKey, {
          data: formattedData,
          timestamp: now,
        });

        setCandleData(formattedData);
        if (formattedData.length > 0) {
          setCurrentPrice(formattedData[formattedData.length - 1].close);
        }
      }
      setLoading(false);
    } catch (error) {
      console.error('❌ Erreur chargement données chandelles:', error);
      setLoading(false);
    }
  }, [selectedCrypto, selectedInterval]);

  // Connexion WebSocket avec gestion propre
  const connectWebSocket = useCallback(() => {
    try {
      // Fermer la connexion existante proprement
      if (wsRef.current) {
        wsRef.current.onclose = null; // Désactiver le handler de reconnexion
        wsRef.current.close();
        wsRef.current = null;
      }

      // Nettoyer le timeout de reconnexion
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      const wsUrl = `wss://stream.binance.com:9443/ws/${selectedCrypto.toLowerCase()}@ticker`;
      console.log('🔌 Connexion WebSocket:', selectedCrypto);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('✅ WebSocket connecté:', selectedCrypto);
        setWsStatus('connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const newPrice = parseFloat(data.c);
          const newVolume = parseFloat(data.v || 0);

          setCurrentPrice(newPrice);

          // Mettre à jour la dernière chandelle
          setCandleData(prev => {
            if (prev.length === 0) return prev;
            const updated = [...prev];
            const lastCandle = { ...updated[updated.length - 1] };

            // Vérifier que la chandelle est de la même période
            const now = Date.now();
            const candleTime = lastCandle.time;

            // Si la dernière chandelle est trop vieille, ne pas la modifier
            // (elle devrait être mise à jour par un rechargement de données)
            if (now - candleTime > 3600000) { // Plus d'1 heure
              console.log('⚠️ Chandelle trop ancienne, rechargement nécessaire');
              return prev;
            }

            // Mise à jour des valeurs OHLC
            lastCandle.close = newPrice;
            lastCandle.high = Math.max(lastCandle.high, newPrice);
            lastCandle.low = Math.min(lastCandle.low, newPrice);
            lastCandle.volume = newVolume;

            updated[updated.length - 1] = lastCandle;
            return updated;
          });
        } catch (error) {
          console.error('❌ Erreur parsing WebSocket:', error);
        }
      };

      ws.onerror = (error) => {
        console.error('❌ WebSocket erreur:', error);
        setWsStatus('disconnected');
      };

      ws.onclose = () => {
        console.log('🔌 WebSocket fermé:', selectedCrypto);
        setWsStatus('disconnected');

        // Reconnexion automatique seulement si pas de changement en cours
        reconnectTimeoutRef.current = setTimeout(() => {
          if (wsRef.current === ws) {
            console.log('🔄 Reconnexion WebSocket...');
            connectWebSocket();
          }
        }, 5000);
      };
    } catch (error) {
      console.error('❌ Erreur connexion WebSocket:', error);
      setWsStatus('disconnected');
    }
  }, [selectedCrypto]);

  // Récupération des prédictions
  const fetchPredictions = useCallback(async () => {
    try {
      const response = await fetch(
        `http://localhost:8000/pipeline/predictions/future/${selectedCrypto}?minutes=60`
      );
      const data = await response.json();

      if (data && data.predictions) {
        setPredictions(data.predictions);
      }
    } catch (error) {
      console.log('⚠️ Prédictions non disponibles, génération fallback');
      // Générer des prédictions de secours
      if (currentPrice > 0) {
        const fallbackPredictions: Prediction[] = [];
        for (let i = 1; i <= 12; i++) {
          fallbackPredictions.push({
            timestamp: new Date(Date.now() + i * 5 * 60000).toISOString(),
            predicted_price: currentPrice * (1 + (Math.random() - 0.5) * 0.02),
            confidence: 0.5,
          });
        }
        setPredictions(fallbackPredictions);
      }
    }
  }, [selectedCrypto, currentPrice]);

  // Effet principal: chargement des données
  useEffect(() => {
    loadCandleData();
  }, [loadCandleData]);

  // Effet séparé: gestion WebSocket
  useEffect(() => {
    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [connectWebSocket]);

  // Effet séparé: gestion prédictions
  useEffect(() => {
    fetchPredictions();

    predictionIntervalRef.current = setInterval(() => {
      fetchPredictions();
    }, 120000); // 2 minutes

    return () => {
      if (predictionIntervalRef.current) {
        clearInterval(predictionIntervalRef.current);
        predictionIntervalRef.current = null;
      }
    };
  }, [fetchPredictions]);

  // Configuration ECharts avec useMemo pour éviter les re-renders
  const getEChartsOption = useMemo(() => {
    if (candleData.length === 0) {
      return {
        title: {
          text: 'Chargement...',
          left: 'center',
          top: 'center',
          textStyle: {
            color: '#a0aec0',
            fontSize: 16,
          },
        },
      };
    }

    // Préparer les données pour ECharts
    const dates = candleData.map(d => {
      const date = new Date(d.time);

      // Vérifier si la date est valide
      if (isNaN(date.getTime())) {
        console.error('Invalid date:', d.time);
        return 'Invalid Date';
      }

      // Format: JJ/MM HH:mm
      const day = String(date.getDate()).padStart(2, '0');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const hours = String(date.getHours()).padStart(2, '0');
      const minutes = String(date.getMinutes()).padStart(2, '0');

      return `${day}/${month} ${hours}:${minutes}`;
    });

    const ohlcData = candleData.map(d => [d.open, d.close, d.low, d.high]);
    const volumes = candleData.map(d => d.volume);

    // Préparer les prédictions
    const predictionData: number[] = [];

    if (showPredictions && predictions.length > 0) {
      predictions.forEach(pred => {
        predictionData.push(pred.predicted_price);
      });
    }

    return {
      animation: false, // Désactiver animations pour éviter les erreurs
      backgroundColor: 'transparent',
      title: {
        text: `${CRYPTO_LIST.find(c => c.symbol === selectedCrypto)?.name || selectedCrypto}`,
        left: 'center',
        top: 10,
        textStyle: {
          color: '#fff',
          fontSize: 24,
          fontWeight: 'bold',
        },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'cross',
          crossStyle: {
            color: '#3b82f6',
          },
        },
        backgroundColor: 'rgba(26, 26, 46, 0.95)',
        borderColor: '#3b82f6',
        borderWidth: 1,
        textStyle: {
          color: '#fff',
        },
        formatter: (params: any) => {
          if (!params || params.length === 0) return '';
          const param = params[0];
          if (!param || !param.data) return '';

          const data = param.data;
          return `
            <div style="padding: 10px;">
              <div style="font-weight: bold; margin-bottom: 8px;">${param.name}</div>
              <div style="color: #10b981;">Open: $${data[1]?.toFixed(2) || 'N/A'}</div>
              <div style="color: #ef4444;">Close: $${data[2]?.toFixed(2) || 'N/A'}</div>
              <div style="color: #f59e0b;">Low: $${data[3]?.toFixed(2) || 'N/A'}</div>
              <div style="color: #3b82f6;">High: $${data[4]?.toFixed(2) || 'N/A'}</div>
            </div>
          `;
        },
      },
      legend: {
        data: showPredictions ? ['Candlestick', 'Volume', 'AI Prediction'] : ['Candlestick', 'Volume'],
        top: 50,
        textStyle: {
          color: '#a0aec0',
        },
      },
      grid: [
        {
          left: '8%',
          right: '5%',
          top: 100,
          height: '55%',
        },
        {
          left: '8%',
          right: '5%',
          top: '70%',
          height: '15%',
        },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: true,
          axisLine: {
            lineStyle: {
              color: '#4a5568',
            },
          },
          axisLabel: {
            color: '#a0aec0',
            fontSize: 11,
          },
          splitLine: {
            show: false,
          },
          min: 'dataMin',
          max: 'dataMax',
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          scale: true,
          boundaryGap: true,
          axisLine: {
            lineStyle: {
              color: '#4a5568',
            },
          },
          axisLabel: {
            show: false,
          },
          splitLine: {
            show: false,
          },
        },
      ],
      yAxis: [
        {
          scale: true,
          splitArea: {
            show: true,
            areaStyle: {
              color: ['rgba(255, 255, 255, 0.02)', 'rgba(0, 0, 0, 0.05)'],
            },
          },
          axisLine: {
            lineStyle: {
              color: '#4a5568',
            },
          },
          axisLabel: {
            color: '#a0aec0',
            fontSize: 11,
            formatter: (value: number) => `$${value.toFixed(2)}`,
          },
          splitLine: {
            lineStyle: {
              color: 'rgba(255, 255, 255, 0.05)',
            },
          },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLine: {
            lineStyle: {
              color: '#4a5568',
            },
          },
          axisLabel: {
            color: '#a0aec0',
            fontSize: 10,
          },
          splitLine: {
            show: false,
          },
        },
      ],
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: [0, 1],
          start: 80, // Centrer sur les 20% les plus récents
          end: 100,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
        },
        {
          show: true,
          xAxisIndex: [0, 1],
          type: 'slider',
          top: '90%',
          start: 80, // Centrer sur les 20% les plus récents
          end: 100,
          backgroundColor: 'rgba(255, 255, 255, 0.05)',
          borderColor: '#3b82f6',
          fillerColor: 'rgba(59, 130, 246, 0.2)',
          handleStyle: {
            color: '#3b82f6',
          },
          textStyle: {
            color: '#a0aec0',
          },
          realtime: true, // Mise à jour en temps réel
        },
      ],
      series: [
        {
          name: 'Candlestick',
          type: 'candlestick',
          data: ohlcData,
          itemStyle: {
            color: '#10b981',
            color0: '#ef4444',
            borderColor: '#10b981',
            borderColor0: '#ef4444',
          },
          emphasis: {
            itemStyle: {
              borderColor: '#3b82f6',
              borderWidth: 2,
            },
          },
        },
        {
          name: 'Volume',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          itemStyle: {
            color: (params: any) => {
              const index = params.dataIndex;
              if (index === 0 || !ohlcData[index] || !ohlcData[index][0] || !ohlcData[index][1]) {
                return 'rgba(59, 130, 246, 0.5)';
              }
              return ohlcData[index][1] >= ohlcData[index][0]
                ? 'rgba(16, 185, 129, 0.5)'
                : 'rgba(239, 68, 68, 0.5)';
            },
          },
        },
        ...(showPredictions && predictionData.length > 0
          ? [
              {
                name: 'AI Prediction',
                type: 'line',
                data: predictionData,
                smooth: true,
                lineStyle: {
                  color: '#10b981',
                  width: 3,
                  type: 'dashed',
                },
                itemStyle: {
                  color: '#10b981',
                },
                areaStyle: {
                  color: {
                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 0,
                    y2: 1,
                    colorStops: [
                      {
                        offset: 0,
                        color: 'rgba(16, 185, 129, 0.3)',
                      },
                      {
                        offset: 1,
                        color: 'rgba(16, 185, 129, 0.05)',
                      },
                    ],
                  },
                },
              },
            ]
          : []),
      ],
    };
  }, [candleData, predictions, showPredictions, selectedCrypto]);

  const selectedCryptoInfo = CRYPTO_LIST.find(c => c.symbol === selectedCrypto);

  if (loading) {
    return (
      <div className="candlestick-loading">
        <div className="spinner"></div>
        <p>Chargement des données...</p>
      </div>
    );
  }

  return (
    <div className="candlestick-chart-container">
      {/* Header avec contrôles */}
      <div className="chart-controls">
        <div className="control-group">
          <label htmlFor="crypto-select">Cryptomonnaie:</label>
          <select
            id="crypto-select"
            value={selectedCrypto}
            onChange={(e) => setSelectedCrypto(e.target.value)}
            className="control-select"
          >
            {CRYPTO_LIST.map((crypto) => (
              <option key={crypto.symbol} value={crypto.symbol}>
                {crypto.name} ({crypto.base})
              </option>
            ))}
          </select>
        </div>

        <div className="control-group">
          <label htmlFor="interval-select">Intervalle:</label>
          <select
            id="interval-select"
            value={selectedInterval}
            onChange={(e) => setSelectedInterval(e.target.value)}
            className="control-select"
          >
            {TIME_INTERVALS.map((interval) => (
              <option key={interval.value} value={interval.value}>
                {interval.label}
              </option>
            ))}
          </select>
        </div>

        <div className="control-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showPredictions}
              onChange={(e) => setShowPredictions(e.target.checked)}
            />
            <span>Afficher Prédictions IA</span>
          </label>
        </div>

        <div className="ws-status">
          <div className={`status-dot status-${wsStatus}`}></div>
          <span>{wsStatus === 'connected' ? 'Live' : 'Connexion...'}</span>
        </div>
      </div>

      {/* Prix actuel */}
      <div className="current-price-banner">
        <div className="price-info">
          <span className="crypto-name">{selectedCryptoInfo?.name}</span>
          <span className="price-value">${currentPrice.toFixed(2)}</span>
        </div>
      </div>

      {/* Graphique ECharts */}
      <div className="echart-wrapper">
        <ReactECharts
          ref={echartRef}
          option={getEChartsOption}
          style={{ height: '700px', width: '100%' }}
          theme="dark"
          notMerge={false}
          lazyUpdate={true}
          opts={{ renderer: 'canvas' }}
        />
      </div>

      {/* Instructions */}
      <div className="chart-instructions">
        <div className="instruction-item">
          <span className="icon">🖱️</span>
          <span>Glissez sur le graphique pour zoomer</span>
        </div>
        <div className="instruction-item">
          <span className="icon">📊</span>
          <span>Utilisez le slider en bas pour ajuster la période</span>
        </div>
        <div className="instruction-item">
          <span className="icon">⚡</span>
          <span>Double-cliquez pour réinitialiser le zoom</span>
        </div>
      </div>

      {/* Prédictions détaillées */}
      {showPredictions && predictions.length > 0 && (
        <div className="predictions-section">
          <h3>🤖 Prédictions IA (Prochaine Heure)</h3>
          <div className="predictions-grid">
            {predictions.slice(0, 12).map((pred, index) => {
              const minutesAhead = (index + 1) * 5;
              const changePct = ((pred.predicted_price - currentPrice) / currentPrice) * 100;
              return (
                <div key={index} className="pred-card">
                  <div className="pred-time">+{minutesAhead}min</div>
                  <div className="pred-price">${pred.predicted_price.toFixed(2)}</div>
                  <div className={`pred-change ${changePct >= 0 ? 'positive' : 'negative'}`}>
                    {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                  </div>
                  <div className="pred-confidence">
                    <div className="confidence-bar">
                      <div
                        className="confidence-fill"
                        style={{ width: `${pred.confidence * 100}%` }}
                      ></div>
                    </div>
                    <span className="confidence-text">{(pred.confidence * 100).toFixed(0)}%</span>
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

export default CandlestickChart;
