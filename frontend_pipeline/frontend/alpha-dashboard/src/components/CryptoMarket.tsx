import React, { useState, useEffect } from 'react';
import { DataService } from '../services/DataService';
import RealTimeCandlestickChart from './RealTimeCandlestickChart';
import './CryptoMarket.css';

interface Crypto {
  symbol: string;
  name: string;
  current_price: number;
  previous_price: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  price_change: number;
  price_change_pct: number;
  h24_high: number;
  h24_low: number;
  h24_volume: number;
  h24_change: number;
  h24_change_pct: number;
  timestamp: string;
  is_positive: boolean;
}

interface CryptoMarketData {
  cryptos: Crypto[];
  count: number;
  stats: {
    total_cryptos: number;
    gainers: number;
    losers: number;
    neutral: number;
    top_gainer: Crypto | null;
    top_loser: Crypto | null;
    highest_volume: Crypto | null;
  };
  timestamp: string;
}

const CryptoMarket: React.FC = () => {
  const [marketData, setMarketData] = useState<CryptoMarketData | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [filter, setFilter] = useState<'all' | 'gainers' | 'losers' | 'top-volume'>('all');
  const [selectedCrypto, setSelectedCrypto] = useState<string | null>(null);

  useEffect(() => {
    loadMarketData();
    const interval = setInterval(loadMarketData, 30000); // Refresh every 30 seconds
    return () => clearInterval(interval);
  }, []);

  const loadMarketData = async () => {
    try {
      const data = await DataService.getAllCryptos();
      setMarketData(data);
      setLoading(false);
    } catch (error) {
      console.error('Error loading crypto market data:', error);
      setLoading(false);
    }
  };

  const formatVolume = (volume: number): string => {
    if (volume >= 1000000000) return `${(volume / 1000000000).toFixed(1)}B`;
    if (volume >= 1000000) return `${(volume / 1000000).toFixed(1)}M`;
    if (volume >= 1000) return `${(volume / 1000).toFixed(1)}K`;
    return volume.toFixed(0);
  };

  const getFilteredCryptos = (): Crypto[] => {
    if (!marketData) return [];

    let filtered = [...marketData.cryptos];

    // Apply search filter
    if (searchTerm) {
      filtered = filtered.filter(crypto =>
        crypto.symbol.toLowerCase().includes(searchTerm.toLowerCase()) ||
        crypto.name.toLowerCase().includes(searchTerm.toLowerCase())
      );
    }

    // Apply category filter
    switch (filter) {
      case 'gainers':
        filtered = filtered.filter(c => c.h24_change_pct > 0)
                         .sort((a, b) => b.h24_change_pct - a.h24_change_pct);
        break;
      case 'losers':
        filtered = filtered.filter(c => c.h24_change_pct < 0)
                         .sort((a, b) => a.h24_change_pct - b.h24_change_pct);
        break;
      case 'top-volume':
        filtered = filtered.sort((a, b) => b.h24_volume - a.h24_volume);
        break;
    }

    return filtered;
  };

  if (loading || !marketData) {
    return (
      <div className="crypto-market-loading">
        <div className="spinner"></div>
        <p>Chargement du marché crypto...</p>
      </div>
    );
  }

  const filteredCryptos = getFilteredCryptos();

  return (
    <div className="crypto-market">
      {/* Statistics Cards */}
      <div className="market-stats">
        <div className="stat-card">
          <div className="stat-label">Total Cryptos</div>
          <div className="stat-value">{marketData?.stats?.total_cryptos || 0}</div>
        </div>
        <div className="stat-card stat-positive">
          <div className="stat-label">Gainers 24h</div>
          <div className="stat-value">📈 {marketData?.stats?.gainers || 0}</div>
        </div>
        <div className="stat-card stat-negative">
          <div className="stat-label">Losers 24h</div>
          <div className="stat-value">📉 {marketData?.stats?.losers || 0}</div>
        </div>
        {marketData?.stats?.top_gainer && (
          <div className="stat-card stat-positive">
            <div className="stat-label">Top Gainer</div>
            <div className="stat-value">{marketData.stats.top_gainer.name}</div>
            <div className="stat-sublabel">+{marketData.stats.top_gainer.h24_change_pct.toFixed(2)}%</div>
          </div>
        )}
        {marketData?.stats?.top_loser && (
          <div className="stat-card stat-negative">
            <div className="stat-label">Top Loser</div>
            <div className="stat-value">{marketData.stats.top_loser.name}</div>
            <div className="stat-sublabel">{marketData.stats.top_loser.h24_change_pct.toFixed(2)}%</div>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="market-filters">
        <div className="search-box">
          <input
            type="text"
            placeholder="🔍 Rechercher une crypto..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="filter-buttons">
          <button
            className={filter === 'all' ? 'active' : ''}
            onClick={() => setFilter('all')}
          >
            Toutes
          </button>
          <button
            className={filter === 'gainers' ? 'active' : ''}
            onClick={() => setFilter('gainers')}
          >
            📈 Gainers
          </button>
          <button
            className={filter === 'losers' ? 'active' : ''}
            onClick={() => setFilter('losers')}
          >
            📉 Losers
          </button>
          <button
            className={filter === 'top-volume' ? 'active' : ''}
            onClick={() => setFilter('top-volume')}
          >
            💰 Top Volume
          </button>
        </div>
        <button className="refresh-btn" onClick={loadMarketData} title="Actualiser">
          🔄
        </button>
      </div>

      {/* Crypto Grid */}
      <div className="crypto-grid">
        {filteredCryptos.map((crypto) => (
          <div
            key={crypto.symbol}
            className="crypto-card"
            onClick={() => setSelectedCrypto(crypto.symbol)}
          >
            <div className="crypto-header">
              <div className="crypto-symbol">{crypto.name}</div>
              <span className={`badge ${crypto.is_positive ? 'badge-positive' : 'badge-negative'}`}>
                {crypto.is_positive ? '↗' : '↘'} {crypto.price_change_pct.toFixed(2)}%
              </span>
            </div>

            <div className={`crypto-price ${crypto.is_positive ? 'positive' : 'negative'}`}>
              ${crypto.current_price.toFixed(crypto.current_price >= 1 ? 2 : 6)}
            </div>

            <div className="crypto-changes">
              <div className="change-item">
                <div className="change-label">Prix précédent</div>
                <div className="change-value">
                  ${crypto.previous_price.toFixed(crypto.previous_price >= 1 ? 2 : 6)}
                </div>
              </div>
              <div className="change-item">
                <div className="change-label">Change 24h</div>
                <div className={`change-value ${crypto.h24_change >= 0 ? 'positive' : 'negative'}`}>
                  {crypto.h24_change >= 0 ? '+' : ''}{crypto.h24_change_pct.toFixed(2)}%
                </div>
              </div>
            </div>

            <div className="crypto-stats">
              <div>
                <div className="stat-item">High: ${crypto.h24_high.toFixed(crypto.h24_high >= 1 ? 2 : 6)}</div>
                <div className="stat-item">Low: ${crypto.h24_low.toFixed(crypto.h24_low >= 1 ? 2 : 6)}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="stat-item">Vol: {formatVolume(crypto.h24_volume)}</div>
                <div className="stat-item stat-timestamp">{crypto.timestamp.split('T')[0]}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filteredCryptos.length === 0 && (
        <div className="no-results">
          <p>Aucune crypto trouvée</p>
        </div>
      )}

      {/* Real-Time Candlestick Chart Modal */}
      {selectedCrypto && (
        <RealTimeCandlestickChart
          symbol={selectedCrypto}
          onClose={() => setSelectedCrypto(null)}
        />
      )}
    </div>
  );
};

export default CryptoMarket;
