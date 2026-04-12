import React, { useState, useEffect, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import { designSystem } from '../styles/designSystem';
import './RealtimePredictions.css';

interface Prediction {
  symbol: string;
  timestamp: string;
  predicted_price: number;
  current_price: number;
  confidence: number;
  direction: 'up' | 'down' | 'neutral';
  change_pct: number;
}

interface PipelineStatus {
  status: string;
  uptime: number;
  symbols: string[];
  predictions_count: number;
}

const RealtimePredictions: React.FC = () => {
  const [predictions, setPredictions] = useState<Map<string, Prediction>>(new Map());
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [predictionHistory, setPredictionHistory] = useState<Map<string, Prediction[]>>(new Map());
  const [isStarting, setIsStarting] = useState(false);
  const updateInterval = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    checkPipelineStatus();
    return () => {
      if (updateInterval.current) {
        clearInterval(updateInterval.current);
      }
    };
  }, []);

  useEffect(() => {
    if (pipelineStatus?.status === 'running') {
      startPolling();
    } else {
      stopPolling();
    }
  }, [pipelineStatus?.status]);

  const checkPipelineStatus = async () => {
    try {
      const response = await fetch('http://localhost:8000/pipeline/status');
      const data = await response.json();
      setPipelineStatus(data);
    } catch (error) {
      console.error('Error checking pipeline status:', error);
    }
  };

  const startPipeline = async () => {
    setIsStarting(true);
    try {
      const response = await fetch('http://localhost:8000/pipeline/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await response.json();
      if (data.status === 'started') {
        setTimeout(checkPipelineStatus, 2000);
      }
    } catch (error) {
      console.error('Error starting pipeline:', error);
    } finally {
      setIsStarting(false);
    }
  };

  const fetchPredictions = async () => {
    try {
      const response = await fetch('http://localhost:8000/pipeline/predictions');
      const data = await response.json();

      const newPredictions = new Map<string, Prediction>();
      data.predictions.forEach((pred: any) => {
        const prediction: Prediction = {
          symbol: pred.symbol,
          timestamp: pred.timestamp || new Date().toISOString(),
          predicted_price: pred.predicted_price || 0,
          current_price: pred.current_price || 0,
          confidence: pred.confidence || 0,
          direction: pred.direction || 'neutral',
          change_pct: pred.change_pct || 0,
        };
        newPredictions.set(pred.symbol, prediction);

        // Update history
        setPredictionHistory(prev => {
          const history = prev.get(pred.symbol) || [];
          const updated = [...history, prediction].slice(-100); // Keep last 100
          const newMap = new Map(prev);
          newMap.set(pred.symbol, updated);
          return newMap;
        });
      });

      setPredictions(newPredictions);
    } catch (error) {
      console.error('Error fetching predictions:', error);
    }
  };

  const startPolling = () => {
    if (!updateInterval.current) {
      fetchPredictions(); // Initial fetch
      updateInterval.current = setInterval(fetchPredictions, 1000); // Update every second
    }
  };

  const stopPolling = () => {
    if (updateInterval.current) {
      clearInterval(updateInterval.current);
      updateInterval.current = null;
    }
  };

  const getChartOptions = (symbol: string) => {
    const history = predictionHistory.get(symbol) || [];
    if (history.length === 0) return {};

    return {
      backgroundColor: 'transparent',
      grid: {
        left: '5%',
        right: '5%',
        top: '10%',
        bottom: '15%',
      },
      xAxis: {
        type: 'category',
        data: history.map(h => new Date(h.timestamp).toLocaleTimeString()),
        axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
        axisLabel: {
          color: designSystem.colors.text.tertiary,
          fontSize: 11,
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
        axisLabel: {
          color: designSystem.colors.text.tertiary,
          fontSize: 11,
          formatter: (value: number) => `$${value.toFixed(2)}`,
        },
        splitLine: {
          lineStyle: { color: designSystem.colors.border.light }
        },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(17, 24, 39, 0.95)',
        borderColor: designSystem.colors.border.medium,
        textStyle: {
          color: designSystem.colors.text.primary,
          fontSize: 12,
        },
      },
      legend: {
        data: ['Current Price', 'Predicted Price'],
        textStyle: {
          color: designSystem.colors.text.secondary,
        },
        top: 0,
      },
      series: [
        {
          name: 'Current Price',
          type: 'line',
          data: history.map(h => h.current_price),
          smooth: true,
          lineStyle: {
            color: designSystem.colors.accent.info,
            width: 2,
          },
          itemStyle: {
            color: designSystem.colors.accent.info,
          },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(6, 182, 212, 0.3)' },
                { offset: 1, color: 'rgba(6, 182, 212, 0.0)' }
              ],
            },
          },
        },
        {
          name: 'Predicted Price',
          type: 'line',
          data: history.map(h => h.predicted_price),
          smooth: true,
          lineStyle: {
            color: designSystem.colors.accent.primary,
            width: 2,
            type: 'dashed',
          },
          itemStyle: {
            color: designSystem.colors.accent.primary,
          },
        }
      ]
    };
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.7) return designSystem.colors.accent.success;
    if (confidence >= 0.4) return designSystem.colors.accent.warning;
    return designSystem.colors.accent.error;
  };

  const getDirectionIcon = (direction: string) => {
    if (direction === 'up') return '↗';
    if (direction === 'down') return '↘';
    return '→';
  };

  const predictionsList = Array.from(predictions.values()).sort((a, b) =>
    Math.abs(b.change_pct) - Math.abs(a.change_pct)
  );

  return (
    <div className="realtime-predictions">
      <div className="predictions-header">
        <div className="header-content">
          <h1 className="predictions-title">Real-Time Predictions</h1>
          <p className="predictions-subtitle">AI-powered cryptocurrency price predictions updated every second</p>
        </div>

        <div className="pipeline-controls">
          {pipelineStatus?.status === 'running' ? (
            <div className="status-indicator running">
              <div className="pulse-dot"></div>
              <span>Live</span>
            </div>
          ) : (
            <button
              className="start-button"
              onClick={startPipeline}
              disabled={isStarting}
            >
              {isStarting ? 'Starting...' : 'Start Pipeline'}
            </button>
          )}
        </div>
      </div>

      {pipelineStatus?.status === 'running' ? (
        <div className="predictions-content">
          <div className="predictions-grid">
            {predictionsList.map(prediction => (
              <div
                key={prediction.symbol}
                className={`prediction-card ${selectedSymbol === prediction.symbol ? 'selected' : ''}`}
                onClick={() => setSelectedSymbol(prediction.symbol)}
              >
                <div className="card-header">
                  <div className="symbol-info">
                    <h3 className="symbol-name">{prediction.symbol.replace('USDT', '')}</h3>
                    <span className="symbol-pair">/USDT</span>
                  </div>
                  <div
                    className={`direction-badge ${prediction.direction}`}
                  >
                    {getDirectionIcon(prediction.direction)}
                  </div>
                </div>

                <div className="card-body">
                  <div className="price-row">
                    <span className="price-label">Current</span>
                    <span className="price-value">${prediction.current_price.toFixed(2)}</span>
                  </div>
                  <div className="price-row">
                    <span className="price-label">Predicted</span>
                    <span className="price-value predicted">${prediction.predicted_price.toFixed(2)}</span>
                  </div>
                  <div className="price-row">
                    <span className="price-label">Change</span>
                    <span className={`change-value ${prediction.direction}`}>
                      {prediction.change_pct > 0 ? '+' : ''}{prediction.change_pct.toFixed(2)}%
                    </span>
                  </div>
                </div>

                <div className="card-footer">
                  <div className="confidence-bar">
                    <div className="confidence-label">Confidence</div>
                    <div className="confidence-track">
                      <div
                        className="confidence-fill"
                        style={{
                          width: `${prediction.confidence * 100}%`,
                          backgroundColor: getConfidenceColor(prediction.confidence),
                        }}
                      ></div>
                    </div>
                    <div className="confidence-value">
                      {(prediction.confidence * 100).toFixed(0)}%
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {selectedSymbol && (
            <div className="chart-panel">
              <div className="chart-header">
                <h2 className="chart-title">
                  {selectedSymbol} - Prediction History
                </h2>
                <button
                  className="close-button"
                  onClick={() => setSelectedSymbol(null)}
                >
                  ×
                </button>
              </div>
              <div className="chart-container">
                <ReactECharts
                  option={getChartOptions(selectedSymbol)}
                  style={{ height: '400px' }}
                />
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="empty-state">
          <div className="empty-icon">🤖</div>
          <h3>Pipeline Non Configuré</h3>
          <p>Le module de prédictions nécessite la configuration du pipeline_api_connector</p>
          <br />
          <p style={{ fontSize: '0.9rem', color: '#9CA3AF' }}>
            💡 En attendant, utilisez le <strong>Dataset Explorer</strong> pour visualiser<br />
            toutes vos données S3 avec des graphiques professionnels !
          </p>
        </div>
      )}
    </div>
  );
};

export default RealtimePredictions;
