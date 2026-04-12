import React from 'react';
import ReactECharts from 'echarts-for-react';

interface Detector {
  name: string;
  type: string;
  output: any;
  confidence: number;
  active: boolean;
}

interface Level1Data {
  detectors: {
    tradeability: Detector;
    direction: Detector;
    pattern: Detector;
    event: Detector;
    pairwise: Detector;
  };
  direction?: string;
  active_patterns?: string[];
  regime?: string;
  status: string;
}

interface Level1ContextProps {
  data?: Level1Data;
}

const Level1Context: React.FC<Level1ContextProps> = ({ data }) => {
  if (!data) {
    return (
      <div className="level-no-data">
        <p>No data available for Level 1 - Context Detectors</p>
      </div>
    );
  }

  const getDirectionChartOption = () => {
    const directionData = data.detectors.direction?.output || { down: 0.33, flat: 0.34, up: 0.33 };
    return {
      title: {
        text: 'Direction Detector (3-class)',
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
            { value: directionData.down || 0, name: 'Down', itemStyle: { color: '#FF6B6B' } },
            { value: directionData.flat || 0, name: 'Flat', itemStyle: { color: '#95A5A6' } },
            { value: directionData.up || 0, name: 'Up', itemStyle: { color: '#4ECDC4' } }
          ],
          label: {
            color: '#fff'
          }
        }
      ]
    };
  };

  const getPatternActivationOption = () => {
    const patternData = data.detectors.pattern?.output || {};
    return {
      title: {
        text: 'Pattern Detector (Multi-label)',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'axis',
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
        data: ['Impulse', 'Reversal', 'Breakout', 'Squeeze'],
        axisLabel: { color: '#999', rotate: 0 }
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
              value: patternData.impulse || 0,
              itemStyle: { color: patternData.impulse > 0.5 ? '#4ECDC4' : '#95A5A6' }
            },
            {
              value: patternData.reversal || 0,
              itemStyle: { color: patternData.reversal > 0.5 ? '#FF6B6B' : '#95A5A6' }
            },
            {
              value: patternData.breakout || 0,
              itemStyle: { color: patternData.breakout > 0.5 ? '#FFD93D' : '#95A5A6' }
            },
            {
              value: patternData.squeeze || 0,
              itemStyle: { color: patternData.squeeze > 0.5 ? '#9B59B6' : '#95A5A6' }
            }
          ],
          barWidth: '60%'
        }
      ]
    };
  };

  const getRegimeChartOption = () => {
    const regimeData = data.detectors.pairwise?.output || {};
    return {
      title: {
        text: 'Pairwise Context (4-class Regime)',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      radar: {
        indicator: [
          { name: 'Trending', max: 1 },
          { name: 'Mean Reverting', max: 1 },
          { name: 'High Vol', max: 1 },
          { name: 'Low Vol', max: 1 }
        ],
        axisName: {
          color: '#999'
        },
        splitLine: {
          lineStyle: { color: 'rgba(255, 255, 255, 0.1)' }
        }
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: [
                regimeData.trending || 0,
                regimeData.mean_reverting || 0,
                regimeData.high_vol || 0,
                regimeData.low_vol || 0
              ],
              name: 'Regime',
              areaStyle: {
                color: 'rgba(78, 205, 196, 0.3)'
              },
              lineStyle: { color: '#4ECDC4' }
            }
          ]
        }
      ]
    };
  };

  const detectorsList = [
    {
      key: 'tradeability',
      icon: '🎯',
      name: 'Tradeability Detector',
      type: 'Binary',
      color: '#FF6B6B'
    },
    {
      key: 'direction',
      icon: '📈',
      name: 'Direction Detector',
      type: '3-class',
      color: '#4ECDC4'
    },
    {
      key: 'pattern',
      icon: '🔄',
      name: 'Pattern Detector',
      type: 'Multi-label',
      color: '#FFD93D'
    },
    {
      key: 'event',
      icon: '⚡',
      name: 'Event Detector',
      type: 'Rare Events',
      color: '#9B59B6'
    },
    {
      key: 'pairwise',
      icon: '🔀',
      name: 'Pairwise Context',
      type: '4-class',
      color: '#45B7D1'
    }
  ];

  return (
    <div className="level-detail-content">
      <div className="level-detail-header">
        <h2>Level 1: Context Detectors</h2>
        <p className="level-detail-description">
          5 orthogonal TCN (Temporal Convolutional Network) detectors identifying market patterns and contexts.
          Each detector specializes in a specific aspect of market behavior.
        </p>
      </div>

      <div className="detectors-grid">
        {detectorsList.map(detector => {
          const detectorData = data.detectors[detector.key as keyof typeof data.detectors];
          const isActive = detectorData?.active || false;
          const confidence = detectorData?.confidence || 0;

          return (
            <div key={detector.key} className={`detector-card ${isActive ? 'active' : ''}`}>
              <div className="detector-header">
                <div className="detector-icon" style={{ backgroundColor: detector.color }}>
                  {detector.icon}
                </div>
                <div className="detector-info">
                  <h4>{detector.name}</h4>
                  <span className="detector-type">{detector.type}</span>
                </div>
              </div>

              <div className="detector-status">
                <div className={`status-indicator ${isActive ? 'active' : 'inactive'}`}></div>
                <span>{isActive ? 'Active' : 'Inactive'}</span>
              </div>

              <div className="detector-confidence">
                <div className="confidence-bar-container">
                  <div
                    className="confidence-bar"
                    style={{
                      width: `${confidence * 100}%`,
                      backgroundColor: detector.color
                    }}
                  ></div>
                </div>
                <span className="confidence-value">{(confidence * 100).toFixed(1)}%</span>
              </div>

              {detector.key === 'tradeability' && detectorData?.output && (
                <div className="detector-output">
                  <span className={`output-badge ${detectorData.output.tradeable ? 'success' : 'error'}`}>
                    {detectorData.output.tradeable ? 'Tradeable' : 'Non-tradeable'}
                  </span>
                </div>
              )}

              {detector.key === 'direction' && detectorData?.output && (
                <div className="detector-output">
                  <span className="output-label">Predicted:</span>
                  <span className={`output-value direction-${detectorData.output.predicted}`}>
                    {detectorData.output.predicted?.toUpperCase() || 'N/A'}
                  </span>
                </div>
              )}

              {detector.key === 'pattern' && data.active_patterns && (
                <div className="detector-output">
                  <div className="active-patterns">
                    {data.active_patterns.map((pattern, idx) => (
                      <span key={idx} className="pattern-badge">{pattern}</span>
                    ))}
                    {data.active_patterns.length === 0 && <span className="no-pattern">None</span>}
                  </div>
                </div>
              )}

              {detector.key === 'event' && detectorData?.output && (
                <div className="detector-output">
                  <span className="output-label">Type:</span>
                  <span className="output-value">
                    {detectorData.output.type || 'None'}
                  </span>
                </div>
              )}

              {detector.key === 'pairwise' && data.regime && (
                <div className="detector-output">
                  <span className="regime-badge">{data.regime}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="charts-grid-3col">
        <div className="chart-container">
          <ReactECharts
            option={getDirectionChartOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getPatternActivationOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getRegimeChartOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>
      </div>

      <div className="level-info-panel">
        <h3>Architecture Details</h3>
        <ul>
          <li><strong>Model:</strong> TCN (Temporal Convolutional Network) with dilated causal convolutions</li>
          <li><strong>Hidden Dimensions:</strong> 128</li>
          <li><strong>Layers:</strong> 3</li>
          <li><strong>Dropout:</strong> 15%</li>
          <li><strong>Key Feature:</strong> Orthogonal contexts ensure diverse pattern detection</li>
          <li><strong>Purpose:</strong> Identify complementary market conditions for specialist routing</li>
        </ul>
      </div>
    </div>
  );
};

export default Level1Context;
