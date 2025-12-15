import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { designSystem } from '../styles/designSystem';
import './S3DataExplorer.css';

interface S3Overview {
  years: number[];
  total_symbols: number;
  symbols_by_year: {
    [key: string]: {
      count: number;
      symbols: string[];
    };
  };
}

interface SymbolData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  symbol: string;
}

interface SymbolStats {
  min_price: number;
  max_price: number;
  avg_price: number;
  total_volume: number;
  start_date: string;
  end_date: string;
}

const S3DataExplorer: React.FC = () => {
  const [overview, setOverview] = useState<S3Overview | null>(null);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [symbolData, setSymbolData] = useState<SymbolData[]>([]);
  const [symbolStats, setSymbolStats] = useState<SymbolStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    loadOverview();
  }, []);

  const loadOverview = async () => {
    try {
      const response = await fetch('http://localhost:8000/s3/overview');
      const data = await response.json();
      setOverview(data);
      if (data.years && data.years.length > 0) {
        setSelectedYear(data.years[data.years.length - 1]);
      }
    } catch (error) {
      console.error('Error loading S3 overview:', error);
    }
  };

  const loadSymbolData = async (symbol: string, year: number) => {
    setLoading(true);
    try {
      const response = await fetch(`http://localhost:8000/s3/data/${symbol}/${year}?limit=5000`);
      const data = await response.json();
      setSymbolData(data.data || []);
      setSymbolStats(data.stats || null);
      setSelectedSymbol(symbol);
    } catch (error) {
      console.error('Error loading symbol data:', error);
    } finally {
      setLoading(false);
    }
  };

  const getChartOptions = () => {
    if (!symbolData || symbolData.length === 0) return {};

    const chartData = symbolData.map(d => [
      d.timestamp,
      d.open,
      d.close,
      d.low,
      d.high,
      d.volume
    ]);

    return {
      backgroundColor: 'transparent',
      grid: [
        { left: '5%', right: '5%', top: '8%', height: '65%' },
        { left: '5%', right: '5%', top: '78%', height: '15%' }
      ],
      xAxis: [
        {
          type: 'category',
          data: symbolData.map(d => d.timestamp),
          axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
          axisLabel: {
            color: designSystem.colors.text.tertiary,
            fontSize: 11,
            formatter: (value: string) => {
              const date = new Date(value);
              return `${date.getMonth() + 1}/${date.getDate()}`;
            }
          },
          gridIndex: 0,
        },
        {
          type: 'category',
          data: symbolData.map(d => d.timestamp),
          axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
          axisLabel: { show: false },
          gridIndex: 1,
        }
      ],
      yAxis: [
        {
          scale: true,
          axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
          axisLabel: {
            color: designSystem.colors.text.tertiary,
            fontSize: 11,
          },
          splitLine: {
            lineStyle: { color: designSystem.colors.border.light }
          },
          gridIndex: 0,
        },
        {
          scale: true,
          axisLine: { lineStyle: { color: designSystem.colors.border.medium } },
          axisLabel: {
            color: designSystem.colors.text.tertiary,
            fontSize: 11,
          },
          splitLine: { show: false },
          gridIndex: 1,
        }
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'cross',
          lineStyle: {
            color: designSystem.colors.accent.primary,
            opacity: 0.5,
          }
        },
        backgroundColor: 'rgba(17, 24, 39, 0.95)',
        borderColor: designSystem.colors.border.medium,
        textStyle: {
          color: designSystem.colors.text.primary,
          fontSize: 12,
        },
        formatter: (params: any) => {
          const data = params[0];
          if (!data) return '';
          const values = data.data;

          // Ensure all values are numbers
          const timestamp = values[0];
          const open = Number(values[1]) || 0;
          const close = Number(values[2]) || 0;
          const low = Number(values[3]) || 0;
          const high = Number(values[4]) || 0;
          const volume = Number(values[5]) || 0;

          return `
            <div style="padding: 8px;">
              <div style="color: ${designSystem.colors.text.secondary}; margin-bottom: 4px;">${timestamp}</div>
              <div>Open: <span style="color: ${designSystem.colors.accent.info};">${open.toFixed(2)}</span></div>
              <div>Close: <span style="color: ${close >= open ? designSystem.colors.accent.success : designSystem.colors.accent.error};">${close.toFixed(2)}</span></div>
              <div>High: <span style="color: ${designSystem.colors.text.primary};">${high.toFixed(2)}</span></div>
              <div>Low: <span style="color: ${designSystem.colors.text.primary};">${low.toFixed(2)}</span></div>
              <div>Volume: <span style="color: ${designSystem.colors.accent.warning};">${volume.toFixed(2)}</span></div>
            </div>
          `;
        }
      },
      series: [
        {
          name: 'Price',
          type: 'candlestick',
          data: chartData,
          itemStyle: {
            color: designSystem.colors.accent.success,
            color0: designSystem.colors.accent.error,
            borderColor: designSystem.colors.accent.success,
            borderColor0: designSystem.colors.accent.error,
          },
          xAxisIndex: 0,
          yAxisIndex: 0,
        },
        {
          name: 'Volume',
          type: 'bar',
          data: symbolData.map(d => d.volume),
          itemStyle: {
            color: designSystem.colors.accent.primary,
            opacity: 0.5,
          },
          xAxisIndex: 1,
          yAxisIndex: 1,
        }
      ]
    };
  };

  const filteredSymbols = selectedYear && overview?.symbols_by_year[selectedYear]
    ? overview.symbols_by_year[selectedYear].symbols.filter(s =>
        s.toLowerCase().includes(searchTerm.toLowerCase())
      )
    : [];

  return (
    <div className="s3-explorer">
      <div className="s3-explorer-header">
        <div className="header-content">
          <h1 className="explorer-title">Dataset Explorer</h1>
          <p className="explorer-subtitle">Complete S3 cryptocurrency dataset visualization</p>
        </div>

        {overview && (
          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-value">{overview.years.length}</div>
              <div className="stat-label">Years</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{overview.total_symbols}</div>
              <div className="stat-label">Total Symbols</div>
            </div>
            {selectedYear && overview.symbols_by_year[selectedYear] && (
              <div className="stat-card">
                <div className="stat-value">{overview.symbols_by_year[selectedYear].count}</div>
                <div className="stat-label">{selectedYear} Symbols</div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="explorer-content">
        <div className="sidebar">
          {/* Year Selector */}
          <div className="control-section">
            <label className="control-label">Select Year</label>
            <div className="year-selector">
              {overview?.years.map(year => (
                <button
                  key={year}
                  className={`year-button ${selectedYear === year ? 'active' : ''}`}
                  onClick={() => setSelectedYear(year)}
                >
                  {year}
                </button>
              ))}
            </div>
          </div>

          {/* Symbol Search */}
          <div className="control-section">
            <label className="control-label">Search Symbol</label>
            <input
              type="text"
              className="search-input"
              placeholder="e.g. BTC, ETH..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          {/* Symbol List */}
          <div className="control-section">
            <label className="control-label">
              Symbols ({filteredSymbols.length})
            </label>
            <div className="symbol-list">
              {filteredSymbols.map(symbol => (
                <button
                  key={symbol}
                  className={`symbol-button ${selectedSymbol === symbol ? 'active' : ''}`}
                  onClick={() => selectedYear && loadSymbolData(symbol, selectedYear)}
                >
                  <span className="symbol-name">{symbol.replace('USDT', '')}</span>
                  <span className="symbol-pair">/USDT</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="main-content">
          {loading ? (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading data...</p>
            </div>
          ) : selectedSymbol && symbolData.length > 0 ? (
            <>
              <div className="chart-header">
                <div>
                  <h2 className="chart-title">{selectedSymbol}</h2>
                  <p className="chart-subtitle">
                    {symbolStats?.start_date} - {symbolStats?.end_date}
                  </p>
                </div>
                {symbolStats && (
                  <div className="stats-inline">
                    <div className="stat-inline">
                      <span className="stat-inline-label">Min</span>
                      <span className="stat-inline-value">${symbolStats.min_price.toFixed(2)}</span>
                    </div>
                    <div className="stat-inline">
                      <span className="stat-inline-label">Max</span>
                      <span className="stat-inline-value">${symbolStats.max_price.toFixed(2)}</span>
                    </div>
                    <div className="stat-inline">
                      <span className="stat-inline-label">Avg</span>
                      <span className="stat-inline-value">${symbolStats.avg_price.toFixed(2)}</span>
                    </div>
                  </div>
                )}
              </div>
              <div className="chart-container">
                <ReactECharts option={getChartOptions()} style={{ height: '600px' }} />
              </div>
            </>
          ) : (
            <div className="empty-state">
              <div className="empty-icon">📊</div>
              <h3>Select a symbol to visualize</h3>
              <p>Choose a year and symbol from the sidebar to explore the data</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default S3DataExplorer;
