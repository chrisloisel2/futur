import React, { useState, useEffect } from 'react';
import { DataService } from '../services/DataService';
import CryptoMarket from './CryptoMarket';
import CandlestickChart from './CandlestickChart';
import WebsocketStatus from './WebsocketStatus';
import FearGreedGauge from './charts/FearGreedGauge';
import './Dashboard.css';

const Dashboard: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);
  const [signals, setSignals] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [summaryData, signalsData] = await Promise.all([
        DataService.getSummary(),
        DataService.getSignals(),
      ]);
      setSummary(summaryData);
      setSignals(signalsData);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="dashboard-loading">
        <div className="spinner-large"></div>
        <p>Loading market data...</p>
      </div>
    );
  }

  return (
    <div className="dashboard-modern">
      <header className="dashboard-modern-header">
        <div className="header-top">
          <div>
            <h1 className="dashboard-modern-title">Market Overview</h1>
            <p className="dashboard-modern-subtitle">Real-time cryptocurrency market intelligence</p>
          </div>
        </div>

        {summary && signals && (
          <div className="metrics-row">
            <div className="metric-card">
              <div className="metric-icon">📊</div>
              <div className="metric-content">
                <div className="metric-value">{summary.total_records.toLocaleString()}</div>
                <div className="metric-label">Total Records</div>
              </div>
            </div>

            <div className="metric-card">
              <div className="metric-icon">⚡</div>
              <div className="metric-content">
                <div className="metric-value">{signals.stats.total}</div>
                <div className="metric-label">Alpha Signals</div>
              </div>
            </div>

            <div className="metric-card">
              <div className="metric-icon">🔗</div>
              <div className="metric-content">
                <div className="metric-value">{Object.keys(summary.data_sources).length}</div>
                <div className="metric-label">Data Sources</div>
              </div>
            </div>

            <div className="metric-card">
              <div className="metric-icon">📈</div>
              <div className="metric-content">
                <div className="metric-value">
                  {signals.stats.by_direction?.bullish || 0}
                </div>
                <div className="metric-label">Bullish Signals</div>
              </div>
            </div>
          </div>
        )}
      </header>

      <div className="dashboard-modern-grid">
        {/* Candlestick Chart with Zoom & Time Selection */}
        <div className="modern-card full-width">
          <div className="card-body-modern" style={{ padding: 0 }}>
            <CandlestickChart />
          </div>
        </div>

        {/* WebSocket Status */}
        <div className="modern-card full-width">
          <div className="card-header-modern">
            <h2 className="card-title-modern">Pipeline Status</h2>
          </div>
          <div className="card-body-modern">
            <WebsocketStatus />
          </div>
        </div>

        {/* Crypto Market (Original) */}
        <div className="modern-card full-width">
          <div className="card-header-modern">
            <h2 className="card-title-modern">Extended Market Data</h2>
          </div>
          <div className="card-body-modern">
            <CryptoMarket />
          </div>
        </div>

        {/* Fear & Greed Index */}
        <div className="modern-card">
          <div className="card-header-modern">
            <h2 className="card-title-modern">Fear & Greed Index</h2>
          </div>
          <div className="card-body-modern">
            <FearGreedGauge />
          </div>
        </div>

        {/* Signals Summary */}
        {signals && (
          <div className="modern-card">
            <div className="card-header-modern">
              <h2 className="card-title-modern">Signal Analytics</h2>
            </div>
            <div className="card-body-modern">
              <div className="signals-summary">
                <div className="signal-stat">
                  <div className="signal-stat-value success">
                    {signals.stats.by_direction?.bullish || 0}
                  </div>
                  <div className="signal-stat-label">Bullish</div>
                </div>
                <div className="signal-stat">
                  <div className="signal-stat-value error">
                    {signals.stats.by_direction?.bearish || 0}
                  </div>
                  <div className="signal-stat-label">Bearish</div>
                </div>
                <div className="signal-stat">
                  <div className="signal-stat-value neutral">
                    {signals.stats.by_direction?.neutral || 0}
                  </div>
                  <div className="signal-stat-label">Neutral</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Dashboard;
