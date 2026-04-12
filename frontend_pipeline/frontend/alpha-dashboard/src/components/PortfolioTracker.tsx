import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import './PortfolioTracker.css';
import { PortfolioCache } from '../services/PortfolioCache';

interface TradingConfig {
  selectedModel: string;
  selectedCrypto: string;
  confidenceThreshold: number;
  strengthThreshold: number;
  stopLoss: number;
  takeProfit: number;
  temperature: number;
  maxPositionSize: number;
}

interface Position {
  symbol: string;
  quantity: number;
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
  value: number;
  entryTime: Date;
}

interface Trade {
  id: string;
  timestamp: Date;
  symbol: string;
  action: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  total: number;
  reason: string;
  confidence: number;
}

interface PortfolioStats {
  totalValue: number;
  cash: number;
  invested: number;
  totalPnL: number;
  totalPnLPercent: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  bestTrade: number;
  worstTrade: number;
  sharpeRatio: number;
  maxDrawdown: number;
}

interface AIPrediction {
  symbol: string;
  predictedPrice: number;
  currentPrice: number;
  confidence: number;
  action: 'BUY' | 'SELL' | 'HOLD';
  reason: string;
  strength: number;
}

interface PortfolioApiState {
  initial_capital?: number;
  cash?: number;
  positions?: any[];
  trades?: any[];
  history?: any[];
  stats?: any;
}

const AVAILABLE_MODELS = [
  { id: 'production_v1', name: 'Production V1 - Baseline', path: 'trading-system/artifacts/models/edge/production_v1.pt' },
  { id: 'production_v1_best_trading', name: 'Production V1 - Best Trading', path: 'trading-system/artifacts/models/edge/production_v1_best_trading.pt' },
  { id: 'production_v3', name: 'Production V3', path: 'trading-system/artifacts/models/edge/production_v3.pt' },
  { id: 'production_v3_best_trading', name: 'Production V3 - Best Trading', path: 'trading-system/artifacts/models/edge/production_v3_best_trading.pt' },
  { id: 'production_v4_2', name: 'Production V4.2 (Latest)', path: 'trading-system/artifacts/models/edge/production_v4_2.pt' },
  { id: 'production_v4_2_best_trading', name: 'Production V4.2 - Best Trading', path: 'trading-system/artifacts/models/edge/production_v4_2_best_trading.pt' },
];

const CRYPTO_LIST = [
  { symbol: 'BTCUSDT', name: 'Bitcoin', emoji: '₿' },
  { symbol: 'ETHUSDT', name: 'Ethereum', emoji: 'Ξ' },
  { symbol: 'BNBUSDT', name: 'Binance Coin', emoji: '🔶' },
  { symbol: 'SOLUSDT', name: 'Solana', emoji: '◎' },
  { symbol: 'XRPUSDT', name: 'Ripple', emoji: '✖' },
];

const PortfolioTracker: React.FC = () => {
  // Charger le cache au démarrage pour restauration instantanée
  const loadCachedData = () => {
    const cached = PortfolioCache.load();
    if (cached) {
      const hydrated = PortfolioCache.hydrate(cached);
      return hydrated;
    }
    return null;
  };

  const cachedData = loadCachedData();

  const [initialCapital, setInitialCapital] = useState<number>(cachedData?.initialCapital || 10000);
  const [cash, setCash] = useState<number>(cachedData?.cash || 10000);
  const [positions, setPositions] = useState<Position[]>(cachedData?.positions || []);
  const [tradeHistory, setTradeHistory] = useState<Trade[]>(cachedData?.tradeHistory || []);
  const [stats, setStats] = useState<PortfolioStats | null>(null);
  const [predictions, setPredictions] = useState<AIPrediction[]>(cachedData?.predictions || []);
  const [portfolioHistory, setPortfolioHistory] = useState<{time: Date, value: number}[]>(cachedData?.portfolioHistory || []);
  const [showConfig, setShowConfig] = useState<boolean>(false);
  const [autoTradeEnabled, setAutoTradeEnabled] = useState<boolean>(true);

  // Configuration du trading
  const [config, setConfig] = useState<TradingConfig>(cachedData?.config || {
    selectedModel: 'production_v4_2_best_trading',
    selectedCrypto: 'BTCUSDT',
    confidenceThreshold: 0.7,
    strengthThreshold: 0.6,
    stopLoss: 2.0,
    takeProfit: 5.0,
    temperature: 0.5,
    maxPositionSize: 0.2, // 20% du capital max par position
  });

  const applyPortfolioState = (data: PortfolioApiState) => {
    if (!data) return;

    if (typeof data.initial_capital === 'number') {
      setInitialCapital(data.initial_capital);
    }
    if (typeof data.cash === 'number') {
      setCash(data.cash);
    }

    if (Array.isArray(data.positions)) {
      const mapped = data.positions.map(pos => {
        const entryPrice = Number(pos.entry_price ?? pos.entryPrice ?? pos.price ?? 0);
        const currentPrice = Number(pos.current_price ?? pos.currentPrice ?? entryPrice);
        const quantity = Number(pos.quantity ?? 0);
        const entryTime = new Date(pos.entry_time ?? pos.entryTime ?? Date.now());
        const value = currentPrice * quantity;
        const pnl = (currentPrice - entryPrice) * quantity;
        const pnlPercent = entryPrice ? ((currentPrice - entryPrice) / entryPrice) * 100 : 0;
        return {
          symbol: pos.symbol,
          quantity,
          entryPrice,
          currentPrice,
          pnl,
          pnlPercent,
          value,
          entryTime
        } as Position;
      });
      setPositions(mapped);
    }

    if (Array.isArray(data.trades)) {
      const mappedTrades = data.trades.map(trade => ({
        id: trade.id,
        timestamp: new Date(trade.timestamp ?? Date.now()),
        symbol: trade.symbol,
        action: (trade.action || 'BUY').toUpperCase() === 'SELL' ? 'SELL' : 'BUY',
        quantity: Number(trade.quantity ?? 0),
        price: Number(trade.price ?? 0),
        total: Number(trade.total ?? 0),
        reason: trade.reason || 'N/A',
        confidence: Number(trade.confidence ?? 0)
      })) as Trade[];
      setTradeHistory(mappedTrades);
    }

    if (Array.isArray(data.history)) {
      const mappedHistory = data.history.map(point => ({
        time: new Date(point.timestamp ?? Date.now()),
        value: Number(point.total_value ?? point.value ?? 0)
      }));
      setPortfolioHistory(mappedHistory);
    }
  };

  const loadPortfolioState = async () => {
    try {
      const response = await fetch('http://localhost:8000/portfolio/state');
      const data = await response.json();
      applyPortfolioState(data);
    } catch (error) {
      console.warn('Unable to load portfolio state from API, falling back to local state.', error);
    }
  };

  useEffect(() => {
    loadPortfolioState();
  }, []);

  // Note: Old helper functions (normalizePredictionAction, buildReason, determineAction, clamp01, normalizePredictions)
  // have been replaced by transformMLPipelineToSignals which works with the full ML pipeline architecture

  const generateSimulatedPredictions = (): AIPrediction[] => {
    const symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT'];
    return symbols.map(symbol => ({
      symbol,
      predictedPrice: Math.random() * 1000 + 100,
      currentPrice: Math.random() * 1000 + 100,
      confidence: Math.random() * 0.4 + 0.5,
      action: (['BUY', 'SELL', 'HOLD'][Math.floor(Math.random() * 3)]) as 'BUY' | 'SELL' | 'HOLD',
      reason: 'Simulated AI prediction',
      strength: Math.random()
    }));
  };

  /**
   * Transforme les données de la pipeline ML complète en signaux de trading
   * Pipeline: Global Gating → Context Detectors → Specialists → Aggregators → Meta-Decider
   */
  const transformMLPipelineToSignals = (mlData: any, currentPrice: number): AIPrediction => {
    const symbol = config.selectedCrypto;

    // Level 0: Global Gating - Vérifier si le marché est tradeable
    const level0 = mlData.level0 || {};
    const isTradeable = level0.is_tradeable ?? true;

    // Level 1: Context Detectors - Détecter la direction
    const level1 = mlData.level1 || {};
    const detectors = level1.detectors || {};
    const directionDetector = detectors.direction || {};
    const directionOutput = directionDetector.output || {};
    const directionConfidence = directionDetector.confidence || 0.5;

    // Probabilités de direction
    const upProb = directionOutput.up || 0.33;
    const downProb = directionOutput.down || 0.33;
    const flatProb = directionOutput.flat || 0.34;

    // Level 2: Conditional Specialists - Prédiction de retour
    const level2 = mlData.level2 || {};
    const predictedReturn = level2.predicted_return || 0;
    const predictedVolatility = level2.predicted_volatility || 0.02;
    const activeExpert = level2.active_expert || 'unknown';

    // Level 3: Aggregators - Décision finale
    const level3 = mlData.level3 || {};
    const decision = level3.decision || 'DELAY';
    const eventClassifier = level3.event_classifier || {};
    const pairwiseComparator = level3.pairwise_comparator || {};
    const eventType = eventClassifier.predicted_class || 'NORMAL';
    const consensusScore = pairwiseComparator.consensus_score || 0.5;

    // Level 4: Meta-Decider - Action recommandée
    const level4 = mlData.level4 || {};
    const actor = level4.actor || {};
    const actionProbs = actor.action_probabilities || { BUY: 0.33, SELL: 0.33, WAIT: 0.34 };
    const selectedAction = actor.selected_action || 'WAIT';

    // Calculer le prix prédit basé sur le retour prédit
    const predictedPrice = currentPrice * (1 + predictedReturn);

    // Déterminer l'action finale basée sur toute la pipeline
    let finalAction: 'BUY' | 'SELL' | 'HOLD' = 'HOLD';
    let finalConfidence = 0.5;
    let finalStrength = 0.5;
    let reason = '';

    // Si le marché n'est pas tradeable, HOLD
    if (!isTradeable) {
      finalAction = 'HOLD';
      reason = 'Market not tradeable (Global Gating)';
      finalConfidence = 0.3;
      finalStrength = 0.1;
    }
    // Si la décision est INVALIDATE, HOLD
    else if (decision === 'INVALIDATE') {
      finalAction = 'HOLD';
      reason = `Signal invalidated: ${eventType} (Aggregator)`;
      finalConfidence = 0.2;
      finalStrength = 0.1;
    }
    // Si la décision est DELAY, HOLD
    else if (decision === 'DELAY') {
      finalAction = 'HOLD';
      reason = `Signal delayed: ${eventType} (Aggregator)`;
      finalConfidence = 0.4;
      finalStrength = 0.3;
    }
    // Si CONFIRM, utiliser la recommandation du Meta-Decider
    else if (decision === 'CONFIRM') {
      if (selectedAction === 'BUY' && predictedReturn > 0) {
        finalAction = 'BUY';
        finalConfidence = Math.max(actionProbs.BUY, upProb, directionConfidence);
        finalStrength = Math.abs(predictedReturn) / predictedVolatility; // Signal-to-noise ratio
        reason = `${activeExpert} specialist: ${eventType} detected, ${(predictedReturn * 100).toFixed(2)}% expected return`;
      } else if (selectedAction === 'SELL' && predictedReturn < 0) {
        finalAction = 'SELL';
        finalConfidence = Math.max(actionProbs.SELL, downProb, directionConfidence);
        finalStrength = Math.abs(predictedReturn) / predictedVolatility;
        reason = `${activeExpert} specialist: ${eventType} detected, ${(predictedReturn * 100).toFixed(2)}% expected return`;
      } else {
        finalAction = 'HOLD';
        finalConfidence = actionProbs.WAIT || flatProb;
        finalStrength = 0.3;
        reason = `Weak signal: consensus ${(consensusScore * 100).toFixed(0)}%`;
      }
    }

    // Ajuster la confiance basée sur le consensus
    finalConfidence = finalConfidence * consensusScore;

    // Normaliser strength entre 0 et 1
    finalStrength = Math.min(1, Math.max(0, finalStrength));

    return {
      symbol,
      predictedPrice,
      currentPrice,
      confidence: finalConfidence,
      action: finalAction,
      reason,
      strength: finalStrength
    };
  };

  // Fetch AI predictions depuis la pipeline ML complète
  useEffect(() => {
    const fetchPredictions = async () => {
      try {
        // Prix par défaut pour fallback (approximatifs au 3 jan 2026)
        const fallbackPrices: { [key: string]: number } = {
          'BTCUSDT': 93000,
          'ETHUSDT': 3400,
          'BNBUSDT': 680,
          'SOLUSDT': 200,
          'XRPUSDT': 2.5
        };

        // 1. Récupérer le prix actuel du crypto sélectionné
        let currentPrice = 0;
        try {
          const tickerResponse = await fetch(`http://localhost:8000/market/ticker?symbol=${config.selectedCrypto}`);
          if (tickerResponse.ok) {
            const tickerData = await tickerResponse.json();
            currentPrice = parseFloat(tickerData.lastPrice || tickerData.price || '0');
          }
        } catch (err) {
          console.warn('Ticker API unavailable, using fallback price');
        }

        // Utiliser le prix de fallback si l'API a échoué
        if (!currentPrice || currentPrice === 0) {
          currentPrice = fallbackPrices[config.selectedCrypto] || 50000;
          console.log(`Using fallback price for ${config.selectedCrypto}: $${currentPrice}`);
        }

        // 2. Récupérer les données de toute la pipeline ML (5 niveaux)
        const mlResponse = await fetch('http://localhost:8000/ml/architecture/status');
        const mlData = await mlResponse.json();

        // 3. Transformer les données ML en signal de trading pour le crypto sélectionné
        const mainSignal = transformMLPipelineToSignals(mlData, currentPrice);

        // 4. Pour les autres cryptos, on peut soit :
        //    - Les ignorer (afficher seulement le crypto sélectionné)
        //    - Utiliser des données simulées
        //    - Faire des appels séparés (plus coûteux)
        // Pour l'instant, on se concentre sur le crypto principal
        const allPredictions: AIPrediction[] = [mainSignal];

        // Optionnel: ajouter des prédictions pour les autres cryptos (pour l'affichage)
        const otherCryptos = CRYPTO_LIST.filter(c => c.symbol !== config.selectedCrypto).slice(0, 2);
        for (const crypto of otherCryptos) {
          let otherPrice = fallbackPrices[crypto.symbol] || 1000;

          try {
            const otherTickerResponse = await fetch(`http://localhost:8000/market/ticker?symbol=${crypto.symbol}`);
            if (otherTickerResponse.ok) {
              const otherTickerData = await otherTickerResponse.json();
              const fetchedPrice = parseFloat(otherTickerData.lastPrice || otherTickerData.price || '0');
              if (fetchedPrice > 0) {
                otherPrice = fetchedPrice;
              }
            }
          } catch (err) {
            // Utiliser le fallback
          }

          // Utiliser la même pipeline ML mais pour un autre crypto
          const otherSignal = transformMLPipelineToSignals(mlData, otherPrice);
          allPredictions.push({ ...otherSignal, symbol: crypto.symbol });
        }

        setPredictions(allPredictions);
        console.log('✅ ML Pipeline signals updated:', allPredictions);

      } catch (error) {
        console.warn('ML Pipeline not available, using simulated predictions', error);
        setPredictions(generateSimulatedPredictions());
      }
    };

    fetchPredictions();
    const interval = setInterval(fetchPredictions, 30000); // Update every 30s
    return () => clearInterval(interval);
  }, [config.selectedCrypto, config.selectedModel, config.temperature]);

  // Auto trading based on AI predictions avec paramètres configurables
  useEffect(() => {
    if (!autoTradeEnabled || predictions.length === 0) return;

    const runAutoTrades = async () => {
      for (const pred of predictions) {
        // Filtrer par crypto sélectionnée
        if (pred.symbol !== config.selectedCrypto) continue;

        // Appliquer les seuils configurés
        if (pred.confidence > config.confidenceThreshold) {
          if (pred.action === 'BUY' && pred.strength > config.strengthThreshold) {
            await executeBuy(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
          } else if (pred.action === 'SELL') {
            await executeSell(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
          }
        }
      }
    };

    runAutoTrades();
  }, [predictions, autoTradeEnabled, config.confidenceThreshold, config.strengthThreshold, config.selectedCrypto]);

  // Update current prices for existing positions + vérifier stop loss / take profit
  useEffect(() => {
    if (predictions.length === 0) return;

    setPositions(prevPositions =>
      prevPositions.map(pos => {
        const pred = predictions.find(p => p.symbol === pos.symbol);
        if (pred) {
          const currentPrice = pred.currentPrice;
          const pnl = (currentPrice - pos.entryPrice) * pos.quantity;
          const pnlPercent = ((currentPrice - pos.entryPrice) / pos.entryPrice) * 100;
          const value = currentPrice * pos.quantity;

          // Vérifier stop loss / take profit
          if (autoTradeEnabled) {
            if (pnlPercent <= -config.stopLoss) {
              console.log(`🛑 Stop Loss hit for ${pos.symbol} at ${pnlPercent.toFixed(2)}%`);
              executeSell(pos.symbol, currentPrice, 1.0, `Stop Loss triggered at ${pnlPercent.toFixed(2)}%`);
            } else if (pnlPercent >= config.takeProfit) {
              console.log(`🎯 Take Profit hit for ${pos.symbol} at ${pnlPercent.toFixed(2)}%`);
              executeSell(pos.symbol, currentPrice, 1.0, `Take Profit triggered at ${pnlPercent.toFixed(2)}%`);
            }
          }

          return {
            ...pos,
            currentPrice,
            pnl,
            pnlPercent,
            value
          };
        }
        return pos;
      })
    );
  }, [predictions, autoTradeEnabled, config.stopLoss, config.takeProfit]);

  // Calculate portfolio stats
  useEffect(() => {
    const totalInvested = positions.reduce((sum, pos) => sum + pos.value, 0);
    const totalValue = cash + totalInvested;
    const totalPnL = totalValue - initialCapital;
    const totalPnLPercent = (totalPnL / initialCapital) * 100;

    const winningTrades = tradeHistory.filter(t => {
      if (t.action === 'SELL') {
        const buyTrade = tradeHistory.find(bt =>
          bt.symbol === t.symbol && bt.action === 'BUY' && bt.timestamp < t.timestamp
        );
        return buyTrade && t.price > buyTrade.price;
      }
      return false;
    }).length;

    const losingTrades = tradeHistory.filter(t => {
      if (t.action === 'SELL') {
        const buyTrade = tradeHistory.find(bt =>
          bt.symbol === t.symbol && bt.action === 'BUY' && bt.timestamp < t.timestamp
        );
        return buyTrade && t.price < buyTrade.price;
      }
      return false;
    }).length;

    const winRate = (winningTrades + losingTrades) > 0
      ? (winningTrades / (winningTrades + losingTrades)) * 100
      : 0;

    const returns = portfolioHistory.map((h, i) =>
      i > 0 ? (h.value - portfolioHistory[i-1].value) / portfolioHistory[i-1].value : 0
    );

    const avgReturn = returns.length > 0 ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
    const stdReturn = returns.length > 0
      ? Math.sqrt(returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) / returns.length)
      : 0;
    const sharpeRatio = stdReturn !== 0 ? (avgReturn / stdReturn) * Math.sqrt(252) : 0;

    const maxDrawdown = calculateMaxDrawdown(portfolioHistory);

    setStats({
      totalValue,
      cash,
      invested: totalInvested,
      totalPnL,
      totalPnLPercent,
      totalTrades: tradeHistory.length,
      winningTrades,
      losingTrades,
      winRate,
      bestTrade: Math.max(...tradeHistory.map(t => t.total), 0),
      worstTrade: Math.min(...tradeHistory.map(t => -t.total), 0),
      sharpeRatio,
      maxDrawdown
    });

    // Update portfolio history (removed from dependencies to prevent infinite loop)
    if (portfolioHistory.length === 0 ||
        Date.now() - portfolioHistory[portfolioHistory.length - 1].time.getTime() > 30000) {
      setPortfolioHistory(prev => [...prev, { time: new Date(), value: totalValue }]);
    }
  }, [cash, positions, tradeHistory, initialCapital]); // Removed portfolioHistory from dependencies

  // Auto-save to cache whenever important data changes
  useEffect(() => {
    const saveToCache = () => {
      PortfolioCache.save({
        initialCapital,
        cash,
        positions,
        tradeHistory,
        portfolioHistory,
        predictions,
        config
      });
    };

    // Debounce: sauvegarder après 1 seconde d'inactivité
    const timeoutId = setTimeout(saveToCache, 1000);
    return () => clearTimeout(timeoutId);
  }, [initialCapital, cash, positions, tradeHistory, portfolioHistory, predictions, config]);

  const calculateMaxDrawdown = (history: {time: Date, value: number}[]): number => {
    if (history.length < 2) return 0;

    let maxDrawdown = 0;
    let peak = history[0].value;

    for (let i = 1; i < history.length; i++) {
      if (history[i].value > peak) {
        peak = history[i].value;
      }
      const drawdown = ((peak - history[i].value) / peak) * 100;
      maxDrawdown = Math.max(maxDrawdown, drawdown);
    }

    return maxDrawdown;
  };

  const executeTrade = async (
    action: 'BUY' | 'SELL',
    symbol: string,
    price: number,
    confidence: number,
    reason: string
  ) => {
    try {
      const response = await fetch('http://localhost:8000/portfolio/trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, symbol, price, confidence, reason })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Trade failed');
      }
      applyPortfolioState(data);
    } catch (error) {
      console.error('Trade failed', error);
    }
  };

  const executeBuy = (symbol: string, price: number, confidence: number, reason: string) =>
    executeTrade('BUY', symbol, price, confidence, reason);

  const executeSell = (symbol: string, price: number, confidence: number, reason: string) =>
    executeTrade('SELL', symbol, price, confidence, reason);

  const resetPortfolio = async () => {
    if (!window.confirm('Reset portfolio to initial capital? This will close all positions and clear cache.')) return;

    try {
      const response = await fetch('http://localhost:8000/portfolio/reset', { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Reset failed');
      applyPortfolioState(data);

      // Effacer le cache
      PortfolioCache.clear();
    } catch (error) {
      console.error('Reset failed', error);
      setCash(initialCapital);
      setPositions([]);
      setTradeHistory([]);
      setPortfolioHistory([]);

      // Effacer le cache même en cas d'erreur
      PortfolioCache.clear();
    }
  };

  const getPerformanceChartOptions = () => {
    if (portfolioHistory.length === 0) return {};

    const labels = portfolioHistory.map(h => h.time.toLocaleTimeString());
    const values = portfolioHistory.map(h => h.value);
    const base = initialCapital || values[0] || 1;
    const pnlPercentSeries = portfolioHistory.map(h => ((h.value - base) / base) * 100);

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value: number) => `$${value.toFixed(2)}`
      },
      legend: {
        data: ['Valeur', 'PnL %'],
        textStyle: { color: '#d1d5db' }
      },
      grid: { left: '4%', right: '4%', top: '10%', bottom: '12%' },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { color: '#9ca3af' },
        axisLine: { lineStyle: { color: '#374151' } }
      },
      yAxis: [
        {
          type: 'value',
          name: 'Valeur ($)',
          axisLabel: { color: '#9ca3af' },
          axisLine: { lineStyle: { color: '#374151' } },
          splitLine: { lineStyle: { color: '#1f2937' } }
        },
        {
          type: 'value',
          name: 'PnL %',
          axisLabel: { color: '#9ca3af', formatter: '{value}%' },
          axisLine: { lineStyle: { color: '#374151' } },
          splitLine: { show: false }
        }
      ],
      series: [
        {
          name: 'Valeur',
          type: 'line',
          data: values,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#60a5fa', width: 3 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(96, 165, 250, 0.25)' },
                { offset: 1, color: 'rgba(96, 165, 250, 0)' }
              ]
            }
          }
        },
        {
          name: 'PnL %',
          type: 'line',
          yAxisIndex: 1,
          data: pnlPercentSeries,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#34d399', width: 2, type: 'dashed' }
        }
      ]
    };
  };

  // Indicateur de cache
  const cacheAge = PortfolioCache.getCacheAge();
  const cacheIndicator = cacheAge !== null
    ? `💾 Cached (${Math.round(cacheAge / 1000 / 60)}m ago)`
    : '📭 No cache';

  return (
    <div className="portfolio-tracker">
      {/* Header with overall stats */}
      <div className="portfolio-header">
        <div className="portfolio-title">
          <h2>💼 Portfolio Trading Simulator</h2>
          <div className="header-controls">
            <button
              className={`config-toggle-btn ${showConfig ? 'active' : ''}`}
              onClick={() => setShowConfig(!showConfig)}
            >
              ⚙️ Configuration
            </button>
            <div className="auto-trade-toggle">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={autoTradeEnabled}
                  onChange={(e) => setAutoTradeEnabled(e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className={`auto-trade-pill ${autoTradeEnabled ? 'active' : 'inactive'}`}>
                {autoTradeEnabled ? '🤖 Auto-Trading ON' : '⏸️ Auto-Trading OFF'}
              </span>
              <span style={{ marginLeft: '10px', fontSize: '0.85em', color: '#9ca3af' }}>
                {cacheIndicator}
              </span>
            </div>
          </div>
        </div>

        {/* Configuration Panel */}
        {showConfig && (
          <div className="config-panel">
            <div className="config-section">
              <h3>🎯 Modèle & Crypto</h3>
              <div className="config-row">
                <div className="config-item">
                  <label>Modèle IA:</label>
                  <select
                    value={config.selectedModel}
                    onChange={(e) => setConfig({...config, selectedModel: e.target.value})}
                    className="config-select"
                  >
                    {AVAILABLE_MODELS.map(model => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="config-item">
                  <label>Cryptomonnaie:</label>
                  <select
                    value={config.selectedCrypto}
                    onChange={(e) => setConfig({...config, selectedCrypto: e.target.value})}
                    className="config-select"
                  >
                    {CRYPTO_LIST.map(crypto => (
                      <option key={crypto.symbol} value={crypto.symbol}>
                        {crypto.emoji} {crypto.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="config-section">
              <h3>📊 Hyperparamètres de Trading</h3>
              <div className="config-row">
                <div className="config-item">
                  <label>
                    Confiance Min: {(config.confidenceThreshold * 100).toFixed(0)}%
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={config.confidenceThreshold}
                    onChange={(e) => setConfig({...config, confidenceThreshold: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>Seuil de confiance minimum pour exécuter un trade</small>
                </div>
                <div className="config-item">
                  <label>
                    Force Signal: {(config.strengthThreshold * 100).toFixed(0)}%
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={config.strengthThreshold}
                    onChange={(e) => setConfig({...config, strengthThreshold: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>Force minimum du signal pour les achats</small>
                </div>
              </div>

              <div className="config-row">
                <div className="config-item">
                  <label>
                    Stop Loss: {config.stopLoss.toFixed(1)}%
                  </label>
                  <input
                    type="range"
                    min="0.5"
                    max="10"
                    step="0.5"
                    value={config.stopLoss}
                    onChange={(e) => setConfig({...config, stopLoss: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>Perte maximale avant vente automatique</small>
                </div>
                <div className="config-item">
                  <label>
                    Take Profit: {config.takeProfit.toFixed(1)}%
                  </label>
                  <input
                    type="range"
                    min="1"
                    max="20"
                    step="0.5"
                    value={config.takeProfit}
                    onChange={(e) => setConfig({...config, takeProfit: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>Gain cible avant vente automatique</small>
                </div>
              </div>

              <div className="config-row">
                <div className="config-item">
                  <label>
                    Température: {config.temperature.toFixed(2)}
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={config.temperature}
                    onChange={(e) => setConfig({...config, temperature: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>Randomness des prédictions (0=déterministe, 1=aléatoire)</small>
                </div>
                <div className="config-item">
                  <label>
                    Taille Position Max: {(config.maxPositionSize * 100).toFixed(0)}%
                  </label>
                  <input
                    type="range"
                    min="0.05"
                    max="0.5"
                    step="0.05"
                    value={config.maxPositionSize}
                    onChange={(e) => setConfig({...config, maxPositionSize: parseFloat(e.target.value)})}
                    className="config-slider"
                  />
                  <small>% maximum du capital par position</small>
                </div>
              </div>
            </div>

            <div className="config-actions">
              <button
                className="config-save-btn"
                onClick={() => {
                  setShowConfig(false);
                  console.log('Configuration saved:', config);
                }}
              >
                ✅ Sauvegarder & Appliquer
              </button>
              <button
                className="config-reset-btn"
                onClick={() => {
                  setConfig({
                    selectedModel: 'production_v4_2_best_trading',
                    selectedCrypto: 'BTCUSDT',
                    confidenceThreshold: 0.7,
                    strengthThreshold: 0.6,
                    stopLoss: 2.0,
                    takeProfit: 5.0,
                    temperature: 0.5,
                    maxPositionSize: 0.2,
                  });
                }}
              >
                🔄 Réinitialiser
              </button>
            </div>
          </div>
        )}

        {stats && (
          <div className="portfolio-overview">
            <div className="overview-card main-value">
              <div className="card-label">Total Portfolio Value</div>
              <div className="card-value">${stats.totalValue.toFixed(2)}</div>
              <div className={`card-change ${stats.totalPnL >= 0 ? 'positive' : 'negative'}`}>
                {stats.totalPnL >= 0 ? '↑' : '↓'} ${Math.abs(stats.totalPnL).toFixed(2)} ({stats.totalPnLPercent.toFixed(2)}%)
              </div>
            </div>

            <div className="overview-card">
              <div className="card-label">Cash Available</div>
              <div className="card-value">${stats.cash.toFixed(2)}</div>
              <div className="card-subtitle">{((stats.cash / stats.totalValue) * 100).toFixed(1)}% of portfolio</div>
            </div>

            <div className="overview-card">
              <div className="card-label">Invested</div>
              <div className="card-value">${stats.invested.toFixed(2)}</div>
              <div className="card-subtitle">{((stats.invested / stats.totalValue) * 100).toFixed(1)}% of portfolio</div>
            </div>

            <div className="overview-card">
              <div className="card-label">Win Rate</div>
              <div className="card-value">{stats.winRate.toFixed(1)}%</div>
              <div className="card-subtitle">{stats.winningTrades}W / {stats.losingTrades}L</div>
            </div>

            <div className="overview-card">
              <div className="card-label">Sharpe Ratio</div>
              <div className="card-value">{stats.sharpeRatio.toFixed(2)}</div>
              <div className="card-subtitle">Risk-adjusted returns</div>
            </div>

            <div className="overview-card">
              <div className="card-label">Max Drawdown</div>
              <div className="card-value negative">{stats.maxDrawdown.toFixed(2)}%</div>
              <div className="card-subtitle">Largest decline</div>
            </div>
          </div>
        )}
      </div>

      <div className="portfolio-content">
        <div className="portfolio-section">
          <h3>📈 Évolution du portefeuille</h3>
          {portfolioHistory.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">⏳</div>
              <p>Aucune donnée encore</p>
              <small>Les trades auto seront enregistrés et sauvegardés dans MongoDB</small>
            </div>
          ) : (
            <ReactECharts
              option={getPerformanceChartOptions()}
              style={{ height: '280px', width: '100%' }}
            />
          )}
        </div>

        {/* Current Positions */}
        <div className="portfolio-section">
          <h3>📊 Current Positions ({positions.length})</h3>

          {positions.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">📭</div>
              <p>No open positions</p>
              <small>L’IA ouvre et ferme les positions automatiquement dès qu’un signal est détecté.</small>
            </div>
          ) : (
            <div className="positions-table">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Quantity</th>
                    <th>Entry Price</th>
                    <th>Current Price</th>
                    <th>Value</th>
                    <th>P&L</th>
                    <th>P&L %</th>
                    <th>Holding Time</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map(pos => {
                    const holdingTime = Math.floor((Date.now() - pos.entryTime.getTime()) / 1000 / 60);
                    return (
                      <tr key={pos.symbol} className={pos.pnl >= 0 ? 'positive-row' : 'negative-row'}>
                        <td className="symbol-cell">{pos.symbol}</td>
                        <td>{pos.quantity.toFixed(6)}</td>
                        <td>${pos.entryPrice.toFixed(2)}</td>
                        <td>${pos.currentPrice.toFixed(2)}</td>
                        <td>${pos.value.toFixed(2)}</td>
                        <td className={pos.pnl >= 0 ? 'positive' : 'negative'}>
                          ${pos.pnl.toFixed(2)}
                        </td>
                        <td className={pos.pnlPercent >= 0 ? 'positive' : 'negative'}>
                          {pos.pnl >= 0 ? '+' : ''}{pos.pnlPercent.toFixed(2)}%
                        </td>
                        <td>{holdingTime}m</td>
                        <td>
                          <button
                            className="sell-btn"
                            onClick={() => {
                              const pred = predictions.find(p => p.symbol === pos.symbol);
                              if (pred) {
                                executeSell(pos.symbol, pred.currentPrice, 1.0, 'Manual sell');
                              }
                            }}
                          >
                            Sell
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* AI Predictions & Signals */}
        <div className="portfolio-section">
          <h3>🤖 AI Trading Signals</h3>

          <div className="predictions-grid">
            {predictions.slice(0, 6).map(pred => (
              <div key={pred.symbol} className={`prediction-card ${pred.action.toLowerCase()}`}>
                <div className="pred-header">
                  <span className="pred-symbol">{pred.symbol}</span>
                  <span className={`pred-action ${pred.action.toLowerCase()}`}>
                    {pred.action}
                  </span>
                </div>

                <div className="pred-prices">
                  <div className="pred-price">
                    <div className="price-label">Current</div>
                    <div className="price-value">${pred.currentPrice.toFixed(2)}</div>
                  </div>
                  <div className="pred-arrow">→</div>
                  <div className="pred-price">
                    <div className="price-label">Predicted</div>
                    <div className="price-value">${pred.predictedPrice.toFixed(2)}</div>
                  </div>
                </div>

                <div className="pred-metrics">
                  <div className="pred-metric">
                    <span className="metric-label">Confidence:</span>
                    <div className="confidence-bar">
                      <div
                        className="confidence-fill"
                        style={{width: `${pred.confidence * 100}%`}}
                      />
                    </div>
                    <span className="metric-value">{(pred.confidence * 100).toFixed(0)}%</span>
                  </div>

                  <div className="pred-metric">
                    <span className="metric-label">Strength:</span>
                    <div className="strength-bar">
                      <div
                        className="strength-fill"
                        style={{width: `${pred.strength * 100}%`}}
                      />
                    </div>
                    <span className="metric-value">{(pred.strength * 100).toFixed(0)}%</span>
                  </div>
                </div>

                <div className="pred-reason">{pred.reason}</div>

                <button
                  className={`trade-btn ${pred.action.toLowerCase()}`}
                  onClick={() => {
                    if (pred.action === 'BUY') {
                      executeBuy(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
                    } else if (pred.action === 'SELL') {
                      executeSell(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
                    }
                  }}
                  disabled={pred.action === 'HOLD'}
                >
                  {pred.action === 'BUY' ? 'Execute Buy' : pred.action === 'SELL' ? 'Execute Sell' : 'Hold'}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Trade History */}
        <div className="portfolio-section">
          <h3>📜 Trade History ({tradeHistory.length})</h3>

          {tradeHistory.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">📋</div>
              <p>No trades executed yet</p>
            </div>
          ) : (
            <div className="trades-table">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Symbol</th>
                    <th>Action</th>
                    <th>Quantity</th>
                    <th>Price</th>
                    <th>Total</th>
                    <th>Confidence</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {tradeHistory.slice(0, 20).map(trade => (
                    <tr key={trade.id} className={trade.action === 'BUY' ? 'buy-row' : 'sell-row'}>
                      <td>{trade.timestamp.toLocaleTimeString()}</td>
                      <td className="symbol-cell">{trade.symbol}</td>
                      <td>
                        <span className={`action-badge ${trade.action.toLowerCase()}`}>
                          {trade.action}
                        </span>
                      </td>
                      <td>{trade.quantity.toFixed(6)}</td>
                      <td>${trade.price.toFixed(2)}</td>
                      <td>${trade.total.toFixed(2)}</td>
                      <td>
                        <div className="confidence-indicator" style={{
                          background: `linear-gradient(90deg,
                            ${trade.confidence > 0.7 ? '#10b981' : trade.confidence > 0.5 ? '#f59e0b' : '#ef4444'} ${trade.confidence * 100}%,
                            #374151 ${trade.confidence * 100}%)`
                        }}>
                          {(trade.confidence * 100).toFixed(0)}%
                        </div>
                      </td>
                      <td className="reason-cell">{trade.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="portfolio-actions">
        <button className="reset-btn" onClick={resetPortfolio}>
          🔄 Reset Portfolio
        </button>
      </div>
    </div>
  );
};

export default PortfolioTracker;
