import React from 'react';
import ReactECharts from 'echarts-for-react';

interface Level4Data {
  actor: {
    action_probabilities: {
      BUY: number;
      SELL: number;
      WAIT: number;
    };
    selected_action: string;
  };
  critic: {
    value_estimate: number;
    advantage: number;
  };
  reward_components: {
    pnl_proxy: number;
    error_cost: number;
    drawdown_penalty: number;
    turnover_penalty: number;
    total_reward: number;
  };
  action?: string;
  confidence?: number;
  status: string;
  trade_history?: Array<{
    timestamp: string;
    action: string;
    price: number;
    pnl: number;
  }>;
  performance?: {
    total_pnl: number;
    sharpe_ratio: number;
    win_rate: number;
    max_drawdown: number;
  };
}

interface Level4MetaDeciderProps {
  data?: Level4Data;
}

const Level4MetaDecider: React.FC<Level4MetaDeciderProps> = ({ data }) => {
  if (!data) {
    return (
      <div className="level-no-data">
        <p>No data available for Level 4 - Meta-Decider</p>
      </div>
    );
  }

  const getActionProbabilitiesOption = () => {
    const probs = data.actor.action_probabilities;
    return {
      title: {
        text: 'Actor: Action Probabilities',
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
        data: ['BUY', 'SELL', 'WAIT'],
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
              value: probs.BUY,
              itemStyle: {
                color: probs.BUY === Math.max(...Object.values(probs)) ? '#4ECDC4' : '#95A5A6'
              }
            },
            {
              value: probs.SELL,
              itemStyle: {
                color: probs.SELL === Math.max(...Object.values(probs)) ? '#FF6B6B' : '#95A5A6'
              }
            },
            {
              value: probs.WAIT,
              itemStyle: {
                color: probs.WAIT === Math.max(...Object.values(probs)) ? '#FFD93D' : '#95A5A6'
              }
            }
          ],
          barWidth: '50%',
          label: {
            show: true,
            position: 'top',
            formatter: '{c}',
            color: '#fff'
          }
        }
      ]
    };
  };

  const getRewardDecompositionOption = () => {
    const rewards = data.reward_components;
    return {
      title: {
        text: 'Reward Components Breakdown',
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
        data: ['PnL Proxy', 'Error Cost', 'Drawdown', 'Turnover', 'Total'],
        axisLabel: { color: '#999', rotate: 15 }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#999' }
      },
      series: [
        {
          type: 'bar',
          data: [
            { value: rewards.pnl_proxy, itemStyle: { color: '#4ECDC4' } },
            { value: rewards.error_cost, itemStyle: { color: '#FF6B6B' } },
            { value: rewards.drawdown_penalty, itemStyle: { color: '#E74C3C' } },
            { value: rewards.turnover_penalty, itemStyle: { color: '#FFD93D' } },
            { value: rewards.total_reward, itemStyle: { color: '#9B59B6' } }
          ],
          barWidth: '60%'
        }
      ]
    };
  };

  const getPnLHistoryOption = () => {
    const history = data.trade_history || [];
    const cumulativePnL = history.reduce((acc: number[], trade, idx) => {
      const prev = idx > 0 ? acc[idx - 1] : 0;
      acc.push(prev + trade.pnl);
      return acc;
    }, []);

    return {
      title: {
        text: 'Cumulative PnL',
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
        axisLabel: { color: '#999' }
      },
      series: [
        {
          type: 'line',
          data: cumulativePnL,
          smooth: true,
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(78, 205, 196, 0.5)' },
                { offset: 1, color: 'rgba(78, 205, 196, 0.1)' }
              ]
            }
          },
          lineStyle: { color: '#4ECDC4', width: 2 }
        }
      ]
    };
  };

  const getValueEstimateGauge = () => {
    const value = data.critic.value_estimate;
    return {
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: -1,
          max: 1,
          splitNumber: 10,
          itemStyle: {
            color: value > 0 ? '#4ECDC4' : '#FF6B6B'
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
              value: value,
              name: 'Value Estimate'
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

  const getActionColor = (action: string) => {
    switch (action) {
      case 'BUY': return '#4ECDC4';
      case 'SELL': return '#FF6B6B';
      case 'WAIT': return '#FFD93D';
      default: return '#95A5A6';
    }
  };

  return (
    <div className="level-detail-content">
      <div className="level-detail-header">
        <h2>Level 4: Meta-Decider (PPO Policy Network)</h2>
        <p className="level-detail-description">
          Proximal Policy Optimization (PPO) agent for final trading decisions.
          Actor-Critic architecture with multi-component reward function.
        </p>
      </div>

      <div className="ppo-summary">
        <div className="ppo-card actor">
          <div className="ppo-header">
            <span className="ppo-icon">🎭</span>
            <h3>Actor Network</h3>
          </div>
          <div className="ppo-action" style={{ backgroundColor: getActionColor(data.actor.selected_action) }}>
            {data.actor.selected_action}
          </div>
          <div className="ppo-confidence">
            {(data.actor.action_probabilities[data.actor.selected_action as keyof typeof data.actor.action_probabilities] * 100).toFixed(1)}% probability
          </div>
        </div>

        <div className="ppo-card critic">
          <div className="ppo-header">
            <span className="ppo-icon">📊</span>
            <h3>Critic Network</h3>
          </div>
          <div className="ppo-metrics">
            <div className="ppo-metric">
              <span className="ppo-metric-label">Value Estimate:</span>
              <span className={`ppo-metric-value ${data.critic.value_estimate > 0 ? 'positive' : 'negative'}`}>
                {data.critic.value_estimate.toFixed(4)}
              </span>
            </div>
            <div className="ppo-metric">
              <span className="ppo-metric-label">Advantage:</span>
              <span className={`ppo-metric-value ${data.critic.advantage > 0 ? 'positive' : 'negative'}`}>
                {data.critic.advantage.toFixed(4)}
              </span>
            </div>
          </div>
        </div>

        <div className="ppo-card rewards">
          <div className="ppo-header">
            <span className="ppo-icon">🎁</span>
            <h3>Total Reward</h3>
          </div>
          <div className={`ppo-reward ${data.reward_components.total_reward > 0 ? 'positive' : 'negative'}`}>
            {data.reward_components.total_reward.toFixed(4)}
          </div>
          <div className="ppo-reward-breakdown">
            <div className="reward-item">
              <span>PnL:</span>
              <span>{data.reward_components.pnl_proxy.toFixed(3)}</span>
            </div>
            <div className="reward-item">
              <span>Error:</span>
              <span>{data.reward_components.error_cost.toFixed(3)}</span>
            </div>
            <div className="reward-item">
              <span>DD:</span>
              <span>{data.reward_components.drawdown_penalty.toFixed(3)}</span>
            </div>
            <div className="reward-item">
              <span>Turnover:</span>
              <span>{data.reward_components.turnover_penalty.toFixed(3)}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="charts-grid-2col">
        <div className="chart-container">
          <ReactECharts
            option={getActionProbabilitiesOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          <ReactECharts
            option={getRewardDecompositionOption()}
            style={{ height: '300px' }}
            theme="dark"
          />
        </div>
      </div>

      <div className="charts-grid-2col">
        <div className="chart-container">
          <h4 className="chart-title">Critic Value Function</h4>
          <ReactECharts
            option={getValueEstimateGauge()}
            style={{ height: '250px' }}
            theme="dark"
          />
        </div>

        <div className="chart-container">
          {data.trade_history && data.trade_history.length > 0 && (
            <ReactECharts
              option={getPnLHistoryOption()}
              style={{ height: '250px' }}
              theme="dark"
            />
          )}
        </div>
      </div>

      {data.performance && (
        <div className="performance-panel">
          <h3>Performance Metrics</h3>
          <div className="performance-grid">
            <div className="perf-metric">
              <span className="perf-label">Total PnL:</span>
              <span className={`perf-value ${data.performance.total_pnl > 0 ? 'positive' : 'negative'}`}>
                {data.performance.total_pnl.toFixed(2)}%
              </span>
            </div>
            <div className="perf-metric">
              <span className="perf-label">Sharpe Ratio:</span>
              <span className={`perf-value ${data.performance.sharpe_ratio > 1 ? 'positive' : 'neutral'}`}>
                {data.performance.sharpe_ratio.toFixed(2)}
              </span>
            </div>
            <div className="perf-metric">
              <span className="perf-label">Win Rate:</span>
              <span className="perf-value">
                {(data.performance.win_rate * 100).toFixed(1)}%
              </span>
            </div>
            <div className="perf-metric">
              <span className="perf-label">Max Drawdown:</span>
              <span className="perf-value negative">
                {(data.performance.max_drawdown * 100).toFixed(2)}%
              </span>
            </div>
          </div>
        </div>
      )}

      {data.trade_history && data.trade_history.length > 0 && (
        <div className="trade-history">
          <h4>Recent Trades</h4>
          <div className="trades-table">
            <div className="trades-header">
              <span>Time</span>
              <span>Action</span>
              <span>Price</span>
              <span>PnL</span>
            </div>
            {data.trade_history.slice(-10).map((trade, idx) => (
              <div key={idx} className="trade-row">
                <span>{new Date(trade.timestamp).toLocaleTimeString()}</span>
                <span className={`trade-action ${trade.action.toLowerCase()}`}>
                  {trade.action}
                </span>
                <span>{trade.price.toFixed(2)}</span>
                <span className={`trade-pnl ${trade.pnl > 0 ? 'positive' : 'negative'}`}>
                  {trade.pnl.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="level-info-panel">
        <h3>Architecture Details</h3>
        <ul>
          <li><strong>Algorithm:</strong> PPO (Proximal Policy Optimization)</li>
          <li><strong>Actor:</strong> 3-layer MLP (128 hidden units) → 3 actions (BUY, SELL, WAIT)</li>
          <li><strong>Critic:</strong> Value function for advantage estimation (GAE)</li>
          <li><strong>Input:</strong> Concatenated outputs from Levels 0-3 + recent performance</li>
          <li><strong>Reward:</strong> PnL proxy - error cost - drawdown penalty - turnover penalty</li>
          <li><strong>Purpose:</strong> Learn optimal trading policy through reinforcement learning</li>
        </ul>
      </div>
    </div>
  );
};

export default Level4MetaDecider;
