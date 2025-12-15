import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import './PortfolioTracker.css';

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

const PortfolioTracker: React.FC = () => {
  const [initialCapital, setInitialCapital] = useState<number>(10000); // $10,000 initial capital
  const [cash, setCash] = useState<number>(10000);
  const [positions, setPositions] = useState<Position[]>([]);
  const [tradeHistory, setTradeHistory] = useState<Trade[]>([]);
  const [stats, setStats] = useState<PortfolioStats | null>(null);
  const [predictions, setPredictions] = useState<AIPrediction[]>([]);
  const [portfolioHistory, setPortfolioHistory] = useState<{time: Date, value: number}[]>([]);

  const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

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

  const normalizePredictionAction = (
    rawSignal?: string,
    fallback: 'BUY' | 'SELL' | 'HOLD' = 'HOLD'
  ): 'BUY' | 'SELL' | 'HOLD' => {
    const signal = (rawSignal || '').toString().toUpperCase();
    if (['BUY', 'LONG', 'BULLISH'].includes(signal)) return 'BUY';
    if (['SELL', 'SHORT', 'BEARISH'].includes(signal)) return 'SELL';
    return fallback;
  };

  const buildReason = (pred: any): string => {
    if (pred.reason) return pred.reason;
    if (pred.indicators && typeof pred.indicators === 'object') {
      const keys = Object.keys(pred.indicators);
      if (keys.length > 0) {
        return `Signal basé sur ${keys.slice(0, 2).join(', ')}`;
      }
    }
    return 'AI model prediction';
  };

  const determineAction = (pred: any): 'BUY' | 'SELL' | 'HOLD' => {
    const predicted = pred.predicted_price ?? pred.predictedPrice ?? pred.price ?? 0;
    const current = pred.current_price ?? pred.currentPrice ?? pred.price ?? 0;
    if (!current) return 'HOLD';

    const changePercent = ((predicted - current) / current) * 100;

    if (changePercent > 1) return 'BUY';
    if (changePercent < -1) return 'SELL';
    return 'HOLD';
  };

  const normalizePredictions = (data: any): AIPrediction[] => {
    const rawPredictions = data?.predictions;
    if (!rawPredictions) return [];

    const arrayPredictions = Array.isArray(rawPredictions)
      ? rawPredictions
      : typeof rawPredictions === 'object'
        ? Object.entries(rawPredictions).map(([symbol, pred]) => {
            const predObj = pred && typeof pred === 'object'
              ? pred
              : { price: Number(pred) };
            return { symbol, ...predObj };
          })
        : [];

    return arrayPredictions
      .map((pred: any) => {
        const symbol = pred.symbol || pred.ticker || pred.pair;
        if (!symbol) return null;

        const currentPrice = Number(pred.current_price ?? pred.price ?? pred.last_price ?? 0);
        const predictedPrice = Number(pred.predicted_price ?? pred.target_price ?? currentPrice);
        const confidence = clamp01(
          Number(pred.confidence ?? pred.score ?? pred.probability ?? 0.5)
        );
        const changePctRaw = pred.change_pct ??
          ((predictedPrice - currentPrice) / (currentPrice || 1)) * 100;
        const changePct = isNaN(changePctRaw) ? 0 : changePctRaw;
        const strengthFromMove = Math.abs(changePct) / 5; // 5% move → full strength
        const strength = clamp01(Math.max(strengthFromMove, confidence));

        return {
          symbol,
          predictedPrice: isNaN(predictedPrice) ? 0 : predictedPrice,
          currentPrice: isNaN(currentPrice) ? 0 : currentPrice,
          confidence: isNaN(confidence) ? 0.5 : confidence,
          action: normalizePredictionAction(
            pred.signal || pred.action || pred.direction,
            determineAction({ predicted_price: predictedPrice, current_price: currentPrice })
          ),
          reason: buildReason(pred),
          strength: isNaN(strength) ? 0.5 : strength
        };
      })
      .filter((p): p is AIPrediction => Boolean(p));
  };

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

  // Fetch AI predictions
  useEffect(() => {
    const fetchPredictions = async () => {
      try {
        const response = await fetch('http://localhost:8000/pipeline/predictions');
        const data = await response.json();

        const formattedPredictions = normalizePredictions(data);
        if (formattedPredictions.length > 0) {
          setPredictions(formattedPredictions);
        } else {
          setPredictions(generateSimulatedPredictions());
        }
      } catch (error) {
        console.log('Pipeline not available, using simulated predictions');
        setPredictions(generateSimulatedPredictions());
      }
    };

    fetchPredictions();
    const interval = setInterval(fetchPredictions, 5000); // Update every 5s
    return () => clearInterval(interval);
  }, []);

  // Auto trading based on AI predictions
  useEffect(() => {
    if (predictions.length === 0) return;

    const runAutoTrades = async () => {
      for (const pred of predictions) {
        if (pred.confidence > 0.7) { // Only trade with high confidence
          if (pred.action === 'BUY' && pred.strength > 0.6) {
            await executeBuy(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
          } else if (pred.action === 'SELL') {
            await executeSell(pred.symbol, pred.currentPrice, pred.confidence, pred.reason);
          }
        }
      }
    };

    runAutoTrades();
  }, [predictions]);

  // Update current prices for existing positions
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
  }, [predictions]);

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

    // Update portfolio history
    if (portfolioHistory.length === 0 ||
        Date.now() - portfolioHistory[portfolioHistory.length - 1].time.getTime() > 5000) {
      setPortfolioHistory(prev => [...prev, { time: new Date(), value: totalValue }]);
    }
  }, [cash, positions, tradeHistory, initialCapital, portfolioHistory]);

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
    if (!window.confirm('Reset portfolio to initial capital? This will close all positions.')) return;

    try {
      const response = await fetch('http://localhost:8000/portfolio/reset', { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Reset failed');
      applyPortfolioState(data);
    } catch (error) {
      console.error('Reset failed', error);
      setCash(initialCapital);
      setPositions([]);
      setTradeHistory([]);
      setPortfolioHistory([]);
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

  return (
    <div className="portfolio-tracker">
      {/* Header with overall stats */}
      <div className="portfolio-header">
        <div className="portfolio-title">
          <h2>💼 Portfolio Trading Simulator</h2>
          <div className="auto-trade-toggle">
            <span className="auto-trade-pill active">Auto-Trading IA actif</span>
          </div>
        </div>

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
