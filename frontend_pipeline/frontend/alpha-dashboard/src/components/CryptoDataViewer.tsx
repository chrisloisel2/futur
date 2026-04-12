/**
 * Composant React pour afficher toutes les données crypto
 */
import React, { useState } from 'react';
import {
  useCryptos,
  useHistoricalData,
  useMetrics,
  useMarketOverview,
  useRealtimeData
} from '../hooks/useCryptoData';

export const CryptoDataViewer: React.FC = () => {
  const { cryptos, loading: cryptosLoading } = useCryptos();
  const { overview, loading: overviewLoading } = useMarketOverview();
  const { connected } = useRealtimeData();
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC/USDT');

  const { data: historicalData, loading: historicalLoading } = useHistoricalData(selectedSymbol, 168);
  const { metrics, loading: metricsLoading } = useMetrics(selectedSymbol);

  return (
    <div className="crypto-data-viewer" style={{ padding: '20px', fontFamily: 'monospace' }}>
      <h1>📊 Crypto Data Dashboard</h1>

      {/* WebSocket Status */}
      <div style={{ marginBottom: '20px', padding: '10px', background: connected ? '#d4edda' : '#f8d7da', borderRadius: '5px' }}>
        <strong>WebSocket: </strong>
        <span style={{ color: connected ? 'green' : 'red' }}>
          {connected ? '🟢 Connected' : '🔴 Disconnected'}
        </span>
      </div>

      {/* Market Overview */}
      {!overviewLoading && overview && (
        <div style={{ marginBottom: '30px' }}>
          <h2>🌍 Market Overview</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
            {/* Top Gainers */}
            <div style={{ background: '#e8f5e9', padding: '15px', borderRadius: '8px' }}>
              <h3>🚀 Top Gainers 24h</h3>
              {overview.top_gainers.slice(0, 5).map((item, idx) => (
                <div key={idx} style={{ padding: '5px 0', borderBottom: '1px solid #ddd' }}>
                  <strong>{item.symbol}</strong>: <span style={{ color: 'green' }}>+{item.change.toFixed(2)}%</span>
                  <br />
                  <small>${item.price.toFixed(4)}</small>
                </div>
              ))}
            </div>

            {/* Top Losers */}
            <div style={{ background: '#ffebee', padding: '15px', borderRadius: '8px' }}>
              <h3>📉 Top Losers 24h</h3>
              {overview.top_losers.slice(0, 5).map((item, idx) => (
                <div key={idx} style={{ padding: '5px 0', borderBottom: '1px solid #ddd' }}>
                  <strong>{item.symbol}</strong>: <span style={{ color: 'red' }}>{item.change.toFixed(2)}%</span>
                  <br />
                  <small>${item.price.toFixed(4)}</small>
                </div>
              ))}
            </div>

            {/* Highest Volume */}
            <div style={{ background: '#e3f2fd', padding: '15px', borderRadius: '8px' }}>
              <h3>💰 Highest Volume 24h</h3>
              {overview.highest_volume.slice(0, 5).map((item, idx) => (
                <div key={idx} style={{ padding: '5px 0', borderBottom: '1px solid #ddd' }}>
                  <strong>{item.symbol}</strong>
                  <br />
                  <small>Vol: {(item.volume / 1000000).toFixed(2)}M</small>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Crypto List */}
      <div style={{ marginBottom: '30px' }}>
        <h2>💎 All Cryptos ({cryptos.length})</h2>
        {cryptosLoading ? (
          <p>Loading cryptos...</p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
            {cryptos.map((crypto) => (
              <div
                key={crypto.symbol}
                onClick={() => setSelectedSymbol(crypto.symbol)}
                style={{
                  padding: '15px',
                  background: selectedSymbol === crypto.symbol ? '#fff3cd' : '#f8f9fa',
                  border: selectedSymbol === crypto.symbol ? '2px solid #ffc107' : '1px solid #ddd',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  transition: 'all 0.2s'
                }}
              >
                <strong>{crypto.name}</strong>
                <br />
                <span style={{ fontSize: '12px', color: '#6c757d' }}>{crypto.symbol}</span>
                <br />
                <strong>${crypto.current_price.toFixed(4)}</strong>
                <br />
                <span style={{ color: crypto.price_change_24h >= 0 ? 'green' : 'red', fontSize: '12px' }}>
                  {crypto.price_change_24h >= 0 ? '+' : ''}{crypto.price_change_24h.toFixed(2)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Selected Crypto Details */}
      {selectedSymbol && (
        <div style={{ marginBottom: '30px' }}>
          <h2>📈 {selectedSymbol} - Detailed Metrics</h2>

          {/* Metrics */}
          {metricsLoading ? (
            <p>Loading metrics...</p>
          ) : metrics && (
            <div style={{ background: '#fff', padding: '20px', borderRadius: '8px', border: '1px solid #ddd' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '15px' }}>
                <div>
                  <small>Current Price</small>
                  <h3>${metrics.current_price.toFixed(4)}</h3>
                </div>
                <div>
                  <small>24h Change</small>
                  <h3 style={{ color: metrics.price_change_24h >= 0 ? 'green' : 'red' }}>
                    {metrics.price_change_24h >= 0 ? '+' : ''}{metrics.price_change_24h.toFixed(2)}%
                  </h3>
                </div>
                <div>
                  <small>7d Change</small>
                  <h3 style={{ color: metrics.price_change_7d >= 0 ? 'green' : 'red' }}>
                    {metrics.price_change_7d >= 0 ? '+' : ''}{metrics.price_change_7d.toFixed(2)}%
                  </h3>
                </div>
                <div>
                  <small>1Y Change</small>
                  <h3 style={{ color: metrics.price_change_1y >= 0 ? 'green' : 'red' }}>
                    {metrics.price_change_1y >= 0 ? '+' : ''}{metrics.price_change_1y.toFixed(2)}%
                  </h3>
                </div>
                <div>
                  <small>24h High</small>
                  <h3>${metrics.high_24h.toFixed(4)}</h3>
                </div>
                <div>
                  <small>24h Low</small>
                  <h3>${metrics.low_24h.toFixed(4)}</h3>
                </div>
                <div>
                  <small>ATH</small>
                  <h3>${metrics.ath.toFixed(4)}</h3>
                  <small style={{ color: 'red' }}>{metrics.ath_change.toFixed(2)}% from ATH</small>
                </div>
                <div>
                  <small>24h Volume</small>
                  <h3>{(metrics.volume_24h / 1000000).toFixed(2)}M</h3>
                </div>
              </div>
            </div>
          )}

          {/* Historical Data Preview */}
          {historicalLoading ? (
            <p>Loading historical data...</p>
          ) : historicalData.length > 0 && (
            <div style={{ marginTop: '20px' }}>
              <h3>📊 Last 7 Days Data ({historicalData.length} points)</h3>
              <div style={{ maxHeight: '300px', overflow: 'auto', background: '#f8f9fa', padding: '10px', borderRadius: '5px' }}>
                <table style={{ width: '100%', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ background: '#e9ecef' }}>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Time</th>
                      <th style={{ padding: '8px', textAlign: 'right' }}>Open</th>
                      <th style={{ padding: '8px', textAlign: 'right' }}>High</th>
                      <th style={{ padding: '8px', textAlign: 'right' }}>Low</th>
                      <th style={{ padding: '8px', textAlign: 'right' }}>Close</th>
                      <th style={{ padding: '8px', textAlign: 'right' }}>Volume</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historicalData.slice(-20).reverse().map((point, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid #dee2e6' }}>
                        <td style={{ padding: '8px' }}>{point.timestamp}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>${point.open.toFixed(2)}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>${point.high.toFixed(2)}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>${point.low.toFixed(2)}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>${point.close.toFixed(2)}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>{point.volume.toFixed(0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default CryptoDataViewer;
