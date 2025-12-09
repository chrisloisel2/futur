import React, { useState, useEffect } from 'react';
import { DataService } from '../services/DataService';
import DataSourcesChart from './charts/DataSourcesChart';
import SignalsChart from './charts/SignalsChart';
import FearGreedGauge from './charts/FearGreedGauge';
import FundingRatesChart from './charts/FundingRatesChart';
import SentimentChart from './charts/SentimentChart';
import PriceChart from './charts/PriceChart';
import CryptoMarket from './CryptoMarket';
import WebsocketStatus from './WebsocketStatus';
import './Dashboard.css';

const Dashboard: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);
  const [signals, setSignals] = useState<any>(null);
  const [selectedSymbol, setSelectedSymbol] = useState('BTC/USDT');

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
    }
  };

  if (!summary || !signals) {
    return <div className="loading">Loading dashboard...</div>;
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>🚀 Alpha Trading Dashboard</h1>
        <div className="header-stats">
          <div className="stat">
            <span className="stat-label">Total Records</span>
            <span className="stat-value">{summary.total_records.toLocaleString()}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Signals Detected</span>
            <span className="stat-value">{signals.stats.total}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Data Sources</span>
            <span className="stat-value">{Object.keys(summary.data_sources).length}</span>
          </div>
        </div>
      </header>

      <div className="dashboard-grid">
        <div className="chart-card full-width">
          <h2>🔌 Statut WebSocket</h2>
          <WebsocketStatus />
        </div>

        {/* Crypto Market - Full Width */}
        <div className="chart-card full-width">
          <h2>💰 Marché Crypto</h2>
          <CryptoMarket />
        </div>

        <div className="chart-card full-width">
          <h2>📊 Data Sources Overview</h2>
          <DataSourcesChart data={summary.data_sources} />
        </div>

        <div className="chart-card">
          <h2>⚡ Alpha Signals</h2>
          <SignalsChart signals={signals} />
        </div>

        <div className="chart-card">
          <h2>😊 Fear & Greed Index</h2>
          <FearGreedGauge />
        </div>

        <div className="chart-card full-width">
          <h2>💹 Price Chart</h2>
          <div className="symbol-selector">
            <select value={selectedSymbol} onChange={(e) => setSelectedSymbol(e.target.value)}>
              <option value="BTC/USDT">BTC/USDT</option>
              <option value="ETH/USDT">ETH/USDT</option>
              <option value="SOL/USDT">SOL/USDT</option>
              <option value="BNB/USDT">BNB/USDT</option>
              <option value="XRP/USDT">XRP/USDT</option>
            </select>
          </div>
          <PriceChart symbol={selectedSymbol} />
        </div>

        <div className="chart-card">
          <h2>📈 Funding Rates</h2>
          <FundingRatesChart />
        </div>

        <div className="chart-card">
          <h2>🗨️ Social Sentiment</h2>
          <SentimentChart />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
