import React from 'react';
import ReactECharts from 'echarts-for-react';

interface Level3Data {
  event_classifier: {
    predicted_class: string;
    probabilities: {
      NORMAL: number;
      EVENT_UP: number;
      EVENT_DOWN: number;
      VOL_SHOCK: number;
    };
  };
  pairwise_comparator: {
    predicted_class: string;
    probabilities: {
      CONSISTENT: number;
      WEAKENING: number;
      CONTRADICTION: number;
    };
    consensus_score: number;
  };
  decision: string;
  event_type?: string;
  status: string;
  history?: Array<{
    timestamp: string;
    event: string;
    decision: string;
  }>;
}

interface Level3AggregatorsProps {
  data?: Level3Data;
}

const Level3Aggregators: React.FC<Level3AggregatorsProps> = ({ data }) => {
  if (!data) {
    return (
      <div className="level-no-data">
        <p>No data available for Level 3 - Aggregators</p>
      </div>
    );
  }

  const getEventClassifierOption = () => {
    const probs = data.event_classifier.probabilities;
    return {
      title: {
        text: 'Event Classifier (4-class)',
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
        data: ['NORMAL', 'EVENT_UP', 'EVENT_DOWN', 'VOL_SHOCK'],
        axisLabel: { color: '#999', rotate: 15 }
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
              value: probs.NORMAL,
              itemStyle: { color: probs.NORMAL === Math.max(...Object.values(probs)) ? '#4ECDC4' : '#95A5A6' }
            },
            {
              value: probs.EVENT_UP,
              itemStyle: { color: probs.EVENT_UP === Math.max(...Object.values(probs)) ? '#4ECDC4' : '#95A5A6' }
            },
            {
              value: probs.EVENT_DOWN,
              itemStyle: { color: probs.EVENT_DOWN === Math.max(...Object.values(probs)) ? '#FF6B6B' : '#95A5A6' }
            },
            {
              value: probs.VOL_SHOCK,
              itemStyle: { color: probs.VOL_SHOCK === Math.max(...Object.values(probs)) ? '#FFD93D' : '#95A5A6' }
            }
          ],
          barWidth: '50%'
        }
      ]
    };
  };

  const getPairwiseComparatorOption = () => {
    const probs = data.pairwise_comparator.probabilities;
    return {
      title: {
        text: 'Pairwise Comparator (3-class)',
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
            { value: probs.CONSISTENT, name: 'Consistent', itemStyle: { color: '#4ECDC4' } },
            { value: probs.WEAKENING, name: 'Weakening', itemStyle: { color: '#FFD93D' } },
            { value: probs.CONTRADICTION, name: 'Contradiction', itemStyle: { color: '#FF6B6B' } }
          ],
          label: {
            color: '#fff',
            formatter: '{b}: {d}%'
          }
        }
      ]
    };
  };

  const getDecisionFlowOption = () => {
    return {
      title: {
        text: 'Decision Flow',
        textStyle: { color: '#fff', fontSize: 14 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        textStyle: { color: '#fff' }
      },
      series: [
        {
          type: 'sankey',
          layout: 'none',
          emphasis: {
            focus: 'adjacency'
          },
          data: [
            { name: 'Event Classifier' },
            { name: 'Pairwise Comparator' },
            { name: 'CONFIRM' },
            { name: 'INVALIDATE' },
            { name: 'DELAY' }
          ],
          links: [
            {
              source: 'Event Classifier',
              target: data.decision,
              value: 5
            },
            {
              source: 'Pairwise Comparator',
              target: data.decision,
              value: 5
            }
          ],
          lineStyle: {
            color: 'gradient',
            curveness: 0.5
          }
        }
      ]
    };
  };

  const getConsensusGauge = () => {
    const consensus = data.pairwise_comparator.consensus_score;
    return {
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 1,
          splitNumber: 10,
          itemStyle: {
            color: consensus > 0.7 ? '#4ECDC4' : consensus > 0.4 ? '#FFD93D' : '#FF6B6B'
          },
          progress: {
            show: true,
            width: 18
          },
          pointer: {
            show: false
          },
          axisLine: {
            lineStyle: {
              width: 18,
              color: [[1, 'rgba(255,255,255,0.1)']]
            }
          },
          axisTick: {
            show: false
          },
          splitLine: {
            show: false
          },
          axisLabel: {
            distance: 25,
            color: '#999',
            fontSize: 10
          },
          detail: {
            valueAnimation: true,
            formatter: '{value}',
            color: '#fff',
            fontSize: 24,
            offsetCenter: [0, '0%']
          },
          data: [
            {
              value: consensus,
              name: 'Consensus Score'
            }
          ],
          title: {
            offsetCenter: [0, '80%'],
            fontSize: 12,
            color: '#999'
          }
        }
      ]
    };
  };

  const getDecisionColor = (decision: string) => {
    switch (decision) {
      case 'CONFIRM': return '#4ECDC4';
      case 'INVALIDATE': return '#FF6B6B';
      case 'DELAY': return '#FFD93D';
      default: return '#95A5A6';
    }
  };

  return (
    <div className="level-detail-content">
      <div className="level-detail-header">
        <h2>Level 3: Aggregators</h2>
        <p className="level-detail-description">
          Event classification and pairwise comparison to validate Level 2 predictions.
          Rule-based decision logic determines whether to CONFIRM, INVALIDATE, or DELAY.
        </p>
      </div>

      <div className="aggregators-summary">
        <div className="aggregator-card event-classifier">
          <div className="aggregator-header">
            <span className="aggregator-icon">⚡</span>
            <h3>Event Classifier</h3>
          </div>
          <div className={`aggregator-result ${data.event_classifier.predicted_class}`}>
            {data.event_classifier.predicted_class}
          </div>
          <div className="aggregator-confidence">
            {(data.event_classifier.probabilities[data.event_classifier.predicted_class as keyof typeof data.event_classifier.probabilities] * 100).toFixed(1)}% confidence
          </div>
        </div>

        <div className="aggregator-card pairwise-comparator">
          <div className="aggregator-header">
            <span className="aggregator-icon">🔀</span>
            <h3>Pairwise Comparator</h3>
          </div>
          <div className={`aggregator-result ${data.pairwise_comparator.predicted_class}`}>
            {data.pairwise_comparator.predicted_class}
          </div>
          <div className="aggregator-confidence">
            {(data.pairwise_comparator.probabilities[data.pairwise_comparator.predicted_class as keyof typeof data.pairwise_comparator.probabilities] * 100).toFixed(1)}% confidence
          </div>
        </div>

        <div className="aggregator-card decision">
          <div className="aggregator-header">
            <span className="aggregator-icon">🎯</span>
            <h3>Final Decision</h3>
          </div>
          <div className="aggregator-result decision-badge" style={{ backgroundColor: getDecisionColor(data.decision) }}>
            {data.decision}
          </div>
          <div className="aggregator-description">
            {data.decision === 'CONFIRM' && 'Predictions validated, proceed to Level 4'}
            {data.decision === 'INVALIDATE' && 'Predictions rejected, do not trade'}
            {data.decision === 'DELAY' && 'Uncertain, wait for more data'}
          </div>
        </div>
      </div>

      <div className="charts-grid-2col">
        <div className="chart-container">
          <ReactECharts
            option={getEventClassifierOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getPairwiseComparatorOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>
      </div>

      <div className="charts-grid-2col">
        <div className="chart-container">
          <h4 className="chart-title">Consensus Gauge</h4>
          <ReactECharts
            option={getConsensusGauge()}
            style={{ height: '250px' }}
            theme="dark"
          />
        </div>

        <div className="decision-flow-panel">
          <h4>Decision Logic</h4>
          <div className="decision-tree">
            <div className="decision-node">
              <div className="node-header">Event Classifier</div>
              <div className="node-value">{data.event_classifier.predicted_class}</div>
            </div>
            <div className="decision-operator">+</div>
            <div className="decision-node">
              <div className="node-header">Pairwise Comparator</div>
              <div className="node-value">{data.pairwise_comparator.predicted_class}</div>
            </div>
            <div className="decision-operator">→</div>
            <div className="decision-node final" style={{ borderColor: getDecisionColor(data.decision) }}>
              <div className="node-header">Decision</div>
              <div className="node-value" style={{ color: getDecisionColor(data.decision) }}>
                {data.decision}
              </div>
            </div>
          </div>

          <div className="decision-rules">
            <h5>Rules:</h5>
            <ul>
              <li>CONSISTENT + NORMAL/EVENT → CONFIRM</li>
              <li>CONTRADICTION + any → INVALIDATE</li>
              <li>WEAKENING + VOL_SHOCK → DELAY</li>
            </ul>
          </div>
        </div>
      </div>

      {data.history && data.history.length > 0 && (
        <div className="event-timeline">
          <h4>Recent Events</h4>
          <div className="timeline">
            {data.history.slice(-10).map((event, idx) => (
              <div key={idx} className="timeline-item">
                <div className="timeline-time">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </div>
                <div className="timeline-content">
                  <span className="timeline-event">{event.event}</span>
                  <span className={`timeline-decision ${event.decision}`}>
                    {event.decision}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="level-info-panel">
        <h3>Architecture Details</h3>
        <ul>
          <li><strong>Event Classifier:</strong> 4-class (NORMAL, EVENT_UP, EVENT_DOWN, VOL_SHOCK)</li>
          <li><strong>Pairwise Comparator:</strong> 3-class (CONSISTENT, WEAKENING, CONTRADICTION)</li>
          <li><strong>Decision Logic:</strong> Rule-based combining both outputs</li>
          <li><strong>Purpose:</strong> Validate Level 2 predictions before trading decision</li>
          <li><strong>Safety:</strong> INVALIDATE/DELAY states prevent bad trades</li>
        </ul>
      </div>
    </div>
  );
};

export default Level3Aggregators;
