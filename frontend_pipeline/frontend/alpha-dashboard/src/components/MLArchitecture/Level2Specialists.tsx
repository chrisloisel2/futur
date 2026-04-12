import React from 'react';
import ReactECharts from 'echarts-for-react';

interface ExpertOutput {
  predicted_return: number;
  predicted_volatility: number;
  confidence: number;
  active: boolean;
}

interface Level2Data {
  router: {
    mode: 'soft' | 'hard';
    weights: {
      impulse: number;
      reversal: number;
      breakout: number;
      squeeze: number;
    };
    selected_expert?: string;
  };
  experts: {
    impulse: ExpertOutput;
    reversal: ExpertOutput;
    breakout: ExpertOutput;
    squeeze: ExpertOutput;
  };
  predicted_return?: number;
  predicted_volatility?: number;
  active_expert?: string;
  status: string;
}

interface Level2SpecialistsProps {
  data?: Level2Data;
}

const Level2Specialists: React.FC<Level2SpecialistsProps> = ({ data }) => {
  if (!data) {
    return (
      <div className="level-no-data">
        <p>No data available for Level 2 - Conditional Specialists</p>
      </div>
    );
  }

  const getRouterWeightsOption = () => {
    const weights = data.router.weights;
    return {
      title: {
        text: `Router Weights (${data.router.mode.toUpperCase()} mode)`,
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      series: [
        {
          type: 'pie',
          radius: ['40%', '70%'],
          data: [
            { value: weights.impulse, name: 'Impulse', itemStyle: { color: '#4ECDC4' } },
            { value: weights.reversal, name: 'Reversal', itemStyle: { color: '#FF6B6B' } },
            { value: weights.breakout, name: 'Breakout', itemStyle: { color: '#FFD93D' } },
            { value: weights.squeeze, name: 'Squeeze', itemStyle: { color: '#9B59B6' } }
          ],
          label: {
            color: '#fff',
            formatter: '{b}: {d}%'
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: 'rgba(0, 0, 0, 0.5)'
            }
          }
        }
      ]
    };
  };

  const getExpertPredictionsOption = () => {
    const experts = data.experts;
    return {
      title: {
        text: 'Expert Predictions - Return & Volatility',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      legend: {
        data: ['Return', 'Volatility'],
        textStyle: { color: '#999' },
        top: '10%'
      },
      grid: {
        left: '10%',
        right: '5%',
        top: '25%',
        bottom: '10%'
      },
      xAxis: {
        type: 'category',
        data: ['Impulse', 'Reversal', 'Breakout', 'Squeeze'],
        axisLabel: { color: '#999' }
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: '#999',
          formatter: '{value}%'
        }
      },
      series: [
        {
          name: 'Return',
          type: 'bar',
          data: [
            experts.impulse.predicted_return * 100,
            experts.reversal.predicted_return * 100,
            experts.breakout.predicted_return * 100,
            experts.squeeze.predicted_return * 100
          ],
          itemStyle: {
            color: (params: any) => {
              const value = params.value;
              return value > 0 ? '#4ECDC4' : '#FF6B6B';
            }
          }
        },
        {
          name: 'Volatility',
          type: 'line',
          data: [
            experts.impulse.predicted_volatility * 100,
            experts.reversal.predicted_volatility * 100,
            experts.breakout.predicted_volatility * 100,
            experts.squeeze.predicted_volatility * 100
          ],
          lineStyle: { color: '#FFD93D', width: 2 },
          itemStyle: { color: '#FFD93D' }
        }
      ]
    };
  };

  const getExpertConfidenceOption = () => {
    const experts = data.experts;
    return {
      title: {
        text: 'Expert Confidence Scores',
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
        bottom: '10%'
      },
      xAxis: {
        type: 'category',
        data: ['Impulse', 'Reversal', 'Breakout', 'Squeeze'],
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
          type: 'bar',
          data: [
            {
              value: experts.impulse.confidence,
              itemStyle: {
                color: experts.impulse.active ? '#4ECDC4' : '#95A5A6'
              }
            },
            {
              value: experts.reversal.confidence,
              itemStyle: {
                color: experts.reversal.active ? '#FF6B6B' : '#95A5A6'
              }
            },
            {
              value: experts.breakout.confidence,
              itemStyle: {
                color: experts.breakout.active ? '#FFD93D' : '#95A5A6'
              }
            },
            {
              value: experts.squeeze.confidence,
              itemStyle: {
                color: experts.squeeze.active ? '#9B59B6' : '#95A5A6'
              }
            }
          ],
          barWidth: '60%'
        }
      ]
    };
  };

  const expertsList = [
    { key: 'impulse', name: 'Impulse Expert', icon: '🚀', color: '#4ECDC4' },
    { key: 'reversal', name: 'Reversal Expert', icon: '🔄', color: '#FF6B6B' },
    { key: 'breakout', name: 'Breakout Expert', icon: '💥', color: '#FFD93D' },
    { key: 'squeeze', name: 'Squeeze Expert', icon: '🎯', color: '#9B59B6' }
  ];

  return (
    <div className="level-detail-content">
      <div className="level-detail-header">
        <h2>Level 2: Conditional Specialists</h2>
        <p className="level-detail-description">
          Router network + 4 pattern-specific expert TCN models. Each expert specializes in
          a particular market pattern and outputs both return prediction and volatility estimate.
        </p>
      </div>

      <div className="router-panel">
        <div className="router-header">
          <h3>
            <span className="router-icon">🎛️</span>
            Router Network
          </h3>
          <div className={`router-mode-badge ${data.router.mode}`}>
            {data.router.mode.toUpperCase()} MODE
          </div>
        </div>

        <div className="router-info">
          {data.router.mode === 'soft' ? (
            <p>Soft routing: All experts contribute weighted by pattern scores</p>
          ) : (
            <p>Hard routing: Single expert selected based on threshold</p>
          )}
        </div>

        {data.router.selected_expert && (
          <div className="selected-expert">
            <span className="label">Selected Expert:</span>
            <span className="expert-name">{data.router.selected_expert.toUpperCase()}</span>
          </div>
        )}
      </div>

      <div className="experts-grid">
        {expertsList.map(expert => {
          const expertData = data.experts[expert.key as keyof typeof data.experts];
          const weight = data.router.weights[expert.key as keyof typeof data.router.weights];
          const isActive = expertData.active;

          return (
            <div key={expert.key} className={`expert-card ${isActive ? 'active' : ''}`}>
              <div className="expert-header">
                <div className="expert-icon" style={{ backgroundColor: expert.color }}>
                  {expert.icon}
                </div>
                <div className="expert-info">
                  <h4>{expert.name}</h4>
                  <div className={`expert-status ${isActive ? 'active' : 'inactive'}`}>
                    {isActive ? 'Active' : 'Inactive'}
                  </div>
                </div>
              </div>

              <div className="expert-weight">
                <span className="weight-label">Router Weight:</span>
                <div className="weight-bar-container">
                  <div
                    className="weight-bar"
                    style={{
                      width: `${weight * 100}%`,
                      backgroundColor: expert.color
                    }}
                  ></div>
                </div>
                <span className="weight-value">{(weight * 100).toFixed(1)}%</span>
              </div>

              <div className="expert-outputs">
                <div className="output-row">
                  <span className="output-label">Return:</span>
                  <span className={`output-value ${expertData.predicted_return > 0 ? 'positive' : 'negative'}`}>
                    {(expertData.predicted_return * 100).toFixed(2)}%
                  </span>
                </div>
                <div className="output-row">
                  <span className="output-label">Volatility:</span>
                  <span className="output-value">
                    {(expertData.predicted_volatility * 100).toFixed(2)}%
                  </span>
                </div>
                <div className="output-row">
                  <span className="output-label">Confidence:</span>
                  <span className="output-value">
                    {(expertData.confidence * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="aggregated-output">
        <h3>Aggregated Output</h3>
        <div className="aggregated-metrics">
          <div className="agg-metric">
            <span className="agg-label">Predicted Return:</span>
            <span className={`agg-value ${(data.predicted_return || 0) > 0 ? 'positive' : 'negative'}`}>
              {((data.predicted_return || 0) * 100).toFixed(3)}%
            </span>
          </div>
          <div className="agg-metric">
            <span className="agg-label">Predicted Volatility:</span>
            <span className="agg-value">
              {((data.predicted_volatility || 0) * 100).toFixed(3)}%
            </span>
          </div>
          {data.active_expert && (
            <div className="agg-metric">
              <span className="agg-label">Primary Expert:</span>
              <span className="agg-value expert-badge">
                {data.active_expert.toUpperCase()}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="charts-grid-3col">
        <div className="chart-container">
          <ReactECharts
            option={getRouterWeightsOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getExpertPredictionsOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getExpertConfidenceOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>
      </div>

      <div className="level-info-panel">
        <h3>Architecture Details</h3>
        <ul>
          <li><strong>Router:</strong> Soft (weighted average) or Hard (threshold-based) expert selection</li>
          <li><strong>Experts:</strong> 4 specialized TCN networks (Impulse, Reversal, Breakout, Squeeze)</li>
          <li><strong>Dual Heads:</strong> Each expert outputs [Return prediction (H=30), Volatility (1)]</li>
          <li><strong>Training:</strong> Independent with gradient stopping to prevent interference</li>
          <li><strong>Purpose:</strong> Pattern-specific predictions with uncertainty quantification</li>
        </ul>
      </div>
    </div>
  );
};

export default Level2Specialists;
