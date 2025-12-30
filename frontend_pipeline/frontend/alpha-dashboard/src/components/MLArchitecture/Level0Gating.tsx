import React from 'react';
import ReactECharts from 'echarts-for-react';

interface Level0Data {
  tradeability_score: number;
  is_tradeable: boolean;
  threshold: number;
  features: {
    realized_return: number;
    realized_volatility: number;
    max_drawdown: number;
  };
  quantiles: {
    p10: number;
    p50: number;
    p90: number;
  };
  window_size: number;
  horizon: number;
  status: string;
  history?: Array<{
    timestamp: string;
    score: number;
    tradeable: boolean;
  }>;
}

interface Level0GatingProps {
  data?: Level0Data;
}

const Level0Gating: React.FC<Level0GatingProps> = ({ data }) => {
  if (!data) {
    return (
      <div className="level-no-data">
        <p>No data available for Level 0 - Global Gating</p>
      </div>
    );
  }

  const getTradeabilityChartOption = () => {
    const history = data.history || [];
    return {
      title: {
        text: 'Tradeability Score Evolution',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      grid: {
        left: '10%',
        right: '5%',
        top: '20%',
        bottom: '15%'
      },
      xAxis: {
        type: 'category',
        data: history.map(h => new Date(h.timestamp).toLocaleTimeString()),
        axisLabel: { color: '#999' }
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 1,
        axisLabel: { color: '#999' }
      },
      series: [
        {
          name: 'Score',
          type: 'line',
          data: history.map(h => h.score),
          smooth: true,
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(255, 107, 107, 0.5)' },
                { offset: 1, color: 'rgba(255, 107, 107, 0.1)' }
              ]
            }
          },
          lineStyle: { color: '#FF6B6B', width: 2 }
        },
        {
          name: 'Threshold',
          type: 'line',
          data: history.map(() => data.threshold),
          lineStyle: { color: '#FFD93D', type: 'dashed', width: 2 }
        }
      ]
    };
  };

  const getQuantilesChartOption = () => {
    return {
      title: {
        text: 'P² Quantile Distribution',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      grid: {
        left: '15%',
        right: '5%',
        top: '20%',
        bottom: '10%'
      },
      xAxis: {
        type: 'category',
        data: ['P10', 'P50 (Median)', 'P90'],
        axisLabel: { color: '#999' }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#999' }
      },
      series: [
        {
          type: 'bar',
          data: [
            { value: data.quantiles.p10, itemStyle: { color: '#4ECDC4' } },
            { value: data.quantiles.p50, itemStyle: { color: '#FF6B6B' } },
            { value: data.quantiles.p90, itemStyle: { color: '#FFD93D' } }
          ],
          barWidth: '60%'
        }
      ]
    };
  };

  const getFeaturesChartOption = () => {
    return {
      title: {
        text: 'Feature Values',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      radar: {
        indicator: [
          { name: 'Realized Return', max: 1 },
          { name: 'Realized Volatility', max: 1 },
          { name: 'Max Drawdown', max: 1 }
        ],
        axisName: {
          color: '#999'
        },
        splitLine: {
          lineStyle: { color: 'rgba(255, 255, 255, 0.1)' }
        },
        splitArea: {
          show: false
        }
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: [
                Math.abs(data.features.realized_return),
                data.features.realized_volatility,
                Math.abs(data.features.max_drawdown)
              ],
              name: 'Features',
              areaStyle: {
                color: 'rgba(255, 107, 107, 0.3)'
              },
              lineStyle: { color: '#FF6B6B' }
            }
          ]
        }
      ]
    };
  };

  return (
    <div className="level-detail-content">
      <div className="level-detail-header">
        <h2>Level 0: Global Gating & Tradeability Filter</h2>
        <p className="level-detail-description">
          Online P² quantile tracking for causal threshold estimation.
          Filters tradeable vs non-tradeable market conditions based on realized metrics.
        </p>
      </div>

      <div className="level-metrics-grid">
        <div className="metric-card-detail">
          <div className="metric-header">
            <span className="metric-icon">📊</span>
            <span className="metric-title">Tradeability Score</span>
          </div>
          <div className="metric-value-large" style={{
            color: data.tradeability_score > data.threshold ? '#4ECDC4' : '#FF6B6B'
          }}>
            {(data.tradeability_score * 100).toFixed(2)}%
          </div>
          <div className="metric-sublabel">
            Threshold: {(data.threshold * 100).toFixed(2)}%
          </div>
        </div>

        <div className="metric-card-detail">
          <div className="metric-header">
            <span className="metric-icon">🎯</span>
            <span className="metric-title">Tradeable Status</span>
          </div>
          <div className={`status-badge-large ${data.is_tradeable ? 'success' : 'error'}`}>
            {data.is_tradeable ? 'TRADEABLE' : 'NON-TRADEABLE'}
          </div>
          <div className="metric-sublabel">
            Based on {data.window_size} bars lookback
          </div>
        </div>

        <div className="metric-card-detail">
          <div className="metric-header">
            <span className="metric-icon">📏</span>
            <span className="metric-title">Configuration</span>
          </div>
          <div className="config-values">
            <div className="config-item">
              <span className="config-label">Lookback:</span>
              <span className="config-value">{data.window_size} bars</span>
            </div>
            <div className="config-item">
              <span className="config-label">Horizon:</span>
              <span className="config-value">{data.horizon} bars</span>
            </div>
          </div>
        </div>

        <div className="metric-card-detail">
          <div className="metric-header">
            <span className="metric-icon">📈</span>
            <span className="metric-title">Features</span>
          </div>
          <div className="features-values">
            <div className="feature-item">
              <span className="feature-label">Return (R):</span>
              <span className={`feature-value ${data.features.realized_return > 0 ? 'positive' : 'negative'}`}>
                {(data.features.realized_return * 100).toFixed(3)}%
              </span>
            </div>
            <div className="feature-item">
              <span className="feature-label">Volatility (RV):</span>
              <span className="feature-value">
                {(data.features.realized_volatility * 100).toFixed(3)}%
              </span>
            </div>
            <div className="feature-item">
              <span className="feature-label">Drawdown (DD):</span>
              <span className="feature-value negative">
                {(data.features.max_drawdown * 100).toFixed(3)}%
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="charts-grid-2col">
        <div className="chart-container">
          <ReactECharts
            option={getTradeabilityChartOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getQuantilesChartOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>
      </div>

      <div className="chart-container-full">
        <ReactECharts
          option={getFeaturesChartOption()}
          style={{ height: '300px' }}
          theme="dark"
        />
      </div>

      <div className="level-info-panel">
        <h3>Algorithm Details</h3>
        <ul>
          <li><strong>Method:</strong> Online P² (Piecewise-Parabolic) quantile estimation</li>
          <li><strong>Normalization:</strong> Robust scaling using median/MAD (Median Absolute Deviation)</li>
          <li><strong>Decision Rule:</strong> score &gt; threshold → Tradeable, else Non-tradeable</li>
          <li><strong>Purpose:</strong> Filter out low-quality market conditions to reduce false signals</li>
        </ul>
      </div>
    </div>
  );
};

export default Level0Gating;
