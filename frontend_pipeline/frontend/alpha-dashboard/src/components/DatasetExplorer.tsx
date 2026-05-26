import React, { useState, useEffect, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import './DatasetExplorer.css';
import { API_BASE_URL } from '../config/api';

interface CryptoIntegrity {
  crypto: string;
  status: string;
  total_rows: number;
  integrity: {
    overall_completeness: number;
    columns: {
      [key: string]: {
        present: boolean;
        missing_count: number;
        missing_pct: number;
        completeness: number;
      };
    };
  };
  stats: {
    price_min: number;
    price_max: number;
    price_mean: number;
    price_current: number;
    volume_mean: number;
  };
  indicators: {
    count: number;
    overall_completeness: number;
    indicators: {
      [key: string]: {
        completeness: number;
      };
    };
  };
  metadata: {
    count: number;
    overall_completeness: number;
    missing_critical: string[];
  };
  gaps: {
    gaps_detected: number;
    max_gap_minutes: number;
    total_gap_hours: number;
  };
  date_range: {
    start: string;
    end: string;
  };
}

interface IntegrityData {
  timestamp: string;
  total_cryptos: number;
  cryptos: {
    [key: string]: CryptoIntegrity;
  };
  global_stats: {
    total_data_points: number;
    avg_data_completeness: number;
    avg_indicator_completeness: number;
    avg_metadata_completeness: number;
  };
}

interface CryptoDataRow {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  [key: string]: any;
}

const DatasetExplorer: React.FC = () => {
  const [integrityData, setIntegrityData] = useState<IntegrityData | null>(null);
  const [selectedCrypto, setSelectedCrypto] = useState<string>('BTC');
  const [cryptoData, setCryptoData] = useState<CryptoDataRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'overview' | 'detailed'>('overview');

  // Chart controls
  const [timeRange, setTimeRange] = useState<string>('1M');
  const [chartZoomStart, setChartZoomStart] = useState<number>(0);
  const [chartZoomEnd, setChartZoomEnd] = useState<number>(100);
  const [customStartDate, setCustomStartDate] = useState<string>('');
  const [customEndDate, setCustomEndDate] = useState<string>('');

  useEffect(() => {
    fetchIntegrityData();
  }, []);

  useEffect(() => {
    if (selectedCrypto) {
      fetchCryptoData(selectedCrypto);
    }
  }, [selectedCrypto]);

  const fetchIntegrityData = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/data-integrity/all`);
      const data = await response.json();
      setIntegrityData(data);
      setLoading(false);
    } catch (err) {
      setError('Failed to load integrity data');
      setLoading(false);
    }
  };

  const fetchCryptoData = async (crypto: string) => {
    try {
      const response = await fetch(`${API_BASE_URL}/dataset/crypto-data/${crypto}?limit=500`);
      const result = await response.json();
      setCryptoData(result.data || []);
    } catch (err) {
      console.error(`Failed to load data for ${crypto}:`, err);
    }
  };

  const getCompletenessColor = (completeness: number): string => {
    if (completeness >= 90) return '#10b981'; // Green
    if (completeness >= 70) return '#f59e0b'; // Orange
    if (completeness >= 50) return '#ef4444'; // Red
    return '#991b1b'; // Dark red
  };

  const getCompletenessLabel = (completeness: number): string => {
    if (completeness >= 90) return 'Excellent';
    if (completeness >= 70) return 'Good';
    if (completeness >= 50) return 'Fair';
    return 'Poor';
  };

  // Filter and transform data for charts based on time range
  const filteredChartData = useMemo(() => {
    if (!cryptoData || cryptoData.length === 0) {
      return { candlestickData: [], volumeData: [] };
    }

    let filtered = [...cryptoData];

    // Calculate date range based on timeRange
    const now = new Date();
    let startDate: Date | null = null;

    if (timeRange !== 'ALL' && timeRange !== 'CUSTOM') {
      switch (timeRange) {
        case '1D':
          startDate = new Date(now.getTime() - 24 * 60 * 60 * 1000);
          break;
        case '1W':
          startDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
          break;
        case '1M':
          startDate = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
          break;
        case '3M':
          startDate = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);
          break;
        case '6M':
          startDate = new Date(now.getTime() - 180 * 24 * 60 * 60 * 1000);
          break;
        case '1Y':
          startDate = new Date(now.getTime() - 365 * 24 * 60 * 60 * 1000);
          break;
      }

      if (startDate) {
        filtered = filtered.filter(d => new Date(d.timestamp) >= startDate!);
      }
    } else if (timeRange === 'CUSTOM' && customStartDate && customEndDate) {
      const start = new Date(customStartDate);
      const end = new Date(customEndDate);
      filtered = filtered.filter(d => {
        const date = new Date(d.timestamp);
        return date >= start && date <= end;
      });
    }

    // Transform to ECharts format
    const candlestickData = filtered.map(d => [
      d.timestamp,
      parseFloat(String(d.open)),
      parseFloat(String(d.close)),
      parseFloat(String(d.low)),
      parseFloat(String(d.high))
    ]);

    const volumeData = filtered.map(d => [
      d.timestamp,
      parseFloat(String(d.volume))
    ]);

    return { candlestickData, volumeData };
  }, [cryptoData, timeRange, customStartDate, customEndDate]);

  // Handle time range button click
  const handleTimeRangeChange = (range: string) => {
    setTimeRange(range);
    setChartZoomStart(0);
    setChartZoomEnd(100);
  };

  // Handle custom date range apply
  const handleCustomDateApply = () => {
    if (customStartDate && customEndDate) {
      setTimeRange('CUSTOM');
      setChartZoomStart(0);
      setChartZoomEnd(100);
    }
  };

  // ECharts configuration
  const chartOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross'
      },
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: {
        color: '#e2e8f0'
      },
      formatter: (params: any) => {
        if (!params || params.length === 0) return '';

        const candleData = params.find((p: any) => p.seriesName === 'Price');
        const volumeData = params.find((p: any) => p.seriesName === 'Volume');

        let tooltip = `<div style="font-size: 12px;">`;

        if (candleData && candleData.value) {
          const date = new Date(candleData.value[0]).toLocaleString();
          const open = candleData.value[1];
          const close = candleData.value[2];
          const low = candleData.value[3];
          const high = candleData.value[4];
          const change = ((close - open) / open * 100).toFixed(2);

          tooltip += `<strong>${date}</strong><br/>`;
          tooltip += `Open: $${open.toLocaleString()}<br/>`;
          tooltip += `High: $${high.toLocaleString()}<br/>`;
          tooltip += `Low: $${low.toLocaleString()}<br/>`;
          tooltip += `Close: $${close.toLocaleString()}<br/>`;
          tooltip += `Change: <span style="color: ${parseFloat(change) >= 0 ? '#10b981' : '#ef4444'}">${change}%</span><br/>`;
        }

        if (volumeData && volumeData.value) {
          tooltip += `Volume: ${volumeData.value[1].toLocaleString()}`;
        }

        tooltip += `</div>`;
        return tooltip;
      }
    },
    grid: [
      {
        left: '10%',
        right: '8%',
        top: '8%',
        height: '55%'
      },
      {
        left: '10%',
        right: '8%',
        top: '70%',
        height: '18%'
      }
    ],
    xAxis: [
      {
        type: 'time',
        gridIndex: 0,
        axisLabel: {
          color: '#94a3b8',
          fontSize: 11
        },
        axisLine: {
          lineStyle: {
            color: '#334155'
          }
        }
      },
      {
        type: 'time',
        gridIndex: 1,
        axisLabel: {
          show: false
        },
        axisLine: {
          lineStyle: {
            color: '#334155'
          }
        }
      }
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: {
          lineStyle: {
            color: '#334155'
          }
        },
        axisLabel: {
          color: '#94a3b8',
          fontSize: 11,
          formatter: (value: number) => `$${value.toLocaleString()}`
        }
      },
      {
        scale: true,
        gridIndex: 1,
        splitLine: {
          show: false
        },
        axisLabel: {
          color: '#94a3b8',
          fontSize: 10,
          formatter: (value: number) => {
            if (value >= 1000000000) return `${(value / 1000000000).toFixed(1)}B`;
            if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
            if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
            return value.toString();
          }
        }
      }
    ],
    dataZoom: [
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        start: chartZoomStart,
        end: chartZoomEnd,
        height: 30,
        bottom: 10,
        handleSize: '100%',
        handleStyle: {
          color: '#3b82f6'
        },
        textStyle: {
          color: '#94a3b8'
        },
        borderColor: '#334155',
        fillerColor: 'rgba(59, 130, 246, 0.2)',
        dataBackground: {
          lineStyle: {
            color: '#3b82f6'
          },
          areaStyle: {
            color: 'rgba(59, 130, 246, 0.3)'
          }
        }
      },
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: chartZoomStart,
        end: chartZoomEnd
      }
    ],
    series: [
      {
        name: 'Price',
        type: 'candlestick',
        data: filteredChartData.candlestickData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#10b981',
          color0: '#ef4444',
          borderColor: '#10b981',
          borderColor0: '#ef4444'
        }
      },
      {
        name: 'Volume',
        type: 'bar',
        data: filteredChartData.volumeData,
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: {
          color: 'rgba(100, 116, 139, 0.5)'
        }
      }
    ]
  };

  if (loading) {
    return (
      <div className="dataset-explorer">
        <div className="loading">
          <div className="spinner"></div>
          <p>Analyzing dataset integrity...</p>
        </div>
      </div>
    );
  }

  if (error || !integrityData) {
    return (
      <div className="dataset-explorer">
        <div className="error-message">
          <h3>Error Loading Data</h3>
          <p>{error || 'No data available'}</p>
        </div>
      </div>
    );
  }

  const cryptos = Object.keys(integrityData.cryptos);
  const currentCryptoData = integrityData.cryptos[selectedCrypto];

  return (
    <div className="dataset-explorer">
      <div className="explorer-header">
        <h2>📊 Dataset Explorer & Integrity Monitor</h2>
        <div className="view-mode-toggle">
          <button
            className={viewMode === 'overview' ? 'active' : ''}
            onClick={() => setViewMode('overview')}
          >
            Overview
          </button>
          <button
            className={viewMode === 'detailed' ? 'active' : ''}
            onClick={() => setViewMode('detailed')}
          >
            Detailed Data
          </button>
        </div>
      </div>

      {/* Global Statistics */}
      <div className="global-stats">
        <h3>🌍 Global Statistics</h3>
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-label">Total Data Points</div>
            <div className="stat-value">
              {integrityData.global_stats.total_data_points.toLocaleString()}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Avg Data Completeness</div>
            <div className="stat-value" style={{ color: getCompletenessColor(integrityData.global_stats.avg_data_completeness) }}>
              {integrityData.global_stats.avg_data_completeness}%
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Avg Indicators</div>
            <div className="stat-value" style={{ color: getCompletenessColor(integrityData.global_stats.avg_indicator_completeness) }}>
              {integrityData.global_stats.avg_indicator_completeness}%
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Avg Metadata</div>
            <div className="stat-value" style={{ color: getCompletenessColor(integrityData.global_stats.avg_metadata_completeness) }}>
              {integrityData.global_stats.avg_metadata_completeness}%
            </div>
          </div>
        </div>
      </div>

      {/* Crypto Selector */}
      <div className="crypto-selector">
        <h3>Select Cryptocurrency</h3>
        {cryptos.length === 1 && (
          <div className="info-banner">
            <span className="info-icon">ℹ️</span>
            <span>Currently showing {cryptos.length} crypto. To add more cryptos, download additional data to <code>ai/cache/s3_data/</code></span>
          </div>
        )}
        <div className="crypto-buttons">
          {cryptos.map(crypto => (
            <button
              key={crypto}
              className={`crypto-btn ${selectedCrypto === crypto ? 'active' : ''}`}
              onClick={() => setSelectedCrypto(crypto)}
            >
              {crypto}
            </button>
          ))}
        </div>
      </div>

      {currentCryptoData && currentCryptoData.status === 'ok' && (
        <>
          {viewMode === 'overview' ? (
            <>
              {/* Crypto Overview */}
              <div className="crypto-overview">
                <h3>📈 {selectedCrypto} Overview</h3>
                <div className="overview-grid">
                  <div className="overview-card">
                    <h4>Data Volume</h4>
                    <p className="big-number">{currentCryptoData.total_rows.toLocaleString()}</p>
                    <p className="card-subtitle">Total Rows</p>
                  </div>
                  <div className="overview-card">
                    <h4>Price Range</h4>
                    <p className="big-number">${currentCryptoData.stats.price_current?.toLocaleString()}</p>
                    <p className="card-subtitle">
                      ${currentCryptoData.stats.price_min.toLocaleString()} - ${currentCryptoData.stats.price_max.toLocaleString()}
                    </p>
                  </div>
                  <div className="overview-card">
                    <h4>Date Range</h4>
                    <p className="small-text">{new Date(currentCryptoData.date_range.start).toLocaleDateString()}</p>
                    <p className="small-text">to {new Date(currentCryptoData.date_range.end).toLocaleDateString()}</p>
                  </div>
                </div>
              </div>

              {/* Interactive Price & Volume Chart */}
              <div className="interactive-chart-section">
                <h3>📊 Interactive Price & Volume Chart</h3>

                {/* Time Range Controls */}
                <div className="time-range-controls">
                  <div className="preset-buttons">
                    <button
                      onClick={() => handleTimeRangeChange('1D')}
                      className={timeRange === '1D' ? 'active' : ''}
                    >
                      1D
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('1W')}
                      className={timeRange === '1W' ? 'active' : ''}
                    >
                      1W
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('1M')}
                      className={timeRange === '1M' ? 'active' : ''}
                    >
                      1M
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('3M')}
                      className={timeRange === '3M' ? 'active' : ''}
                    >
                      3M
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('6M')}
                      className={timeRange === '6M' ? 'active' : ''}
                    >
                      6M
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('1Y')}
                      className={timeRange === '1Y' ? 'active' : ''}
                    >
                      1Y
                    </button>
                    <button
                      onClick={() => handleTimeRangeChange('ALL')}
                      className={timeRange === 'ALL' ? 'active' : ''}
                    >
                      ALL
                    </button>
                  </div>

                  <div className="custom-range">
                    <input
                      type="date"
                      value={customStartDate}
                      onChange={(e) => setCustomStartDate(e.target.value)}
                      placeholder="Start date"
                    />
                    <input
                      type="date"
                      value={customEndDate}
                      onChange={(e) => setCustomEndDate(e.target.value)}
                      placeholder="End date"
                    />
                    <button onClick={handleCustomDateApply} className="apply-btn">
                      Apply
                    </button>
                  </div>
                </div>

                {/* Chart Container */}
                <div className="chart-container">
                  {cryptoData.length > 0 ? (
                    <ReactECharts
                      option={chartOption}
                      style={{ height: '600px', width: '100%' }}
                      opts={{ renderer: 'canvas' }}
                    />
                  ) : (
                    <div className="no-data-message">
                      <p>No data available for the selected time range</p>
                    </div>
                  )}
                </div>

                <div className="chart-info">
                  <p>
                    <strong>Showing {filteredChartData.candlestickData.length} data points</strong>
                    {timeRange !== 'ALL' && timeRange !== 'CUSTOM' && ` (${timeRange})`}
                  </p>
                  <p className="chart-instructions">
                    💡 <strong>Tip:</strong> Use mouse wheel to zoom, drag slider to navigate, or select a time range above
                  </p>
                </div>
              </div>

              {/* Completeness Charts */}
              <div className="completeness-section">
                <h3>📊 Data Completeness Analysis</h3>
                <div className="completeness-charts">
                  {/* Overall Completeness */}
                  <div className="completeness-card">
                    <h4>Overall Data Quality</h4>
                    <div className="circular-progress" style={{
                      background: `conic-gradient(${getCompletenessColor(currentCryptoData.integrity.overall_completeness)} ${currentCryptoData.integrity.overall_completeness * 3.6}deg, #1e293b ${currentCryptoData.integrity.overall_completeness * 3.6}deg)`
                    }}>
                      <div className="progress-inner">
                        <span className="progress-value">{currentCryptoData.integrity.overall_completeness}%</span>
                        <span className="progress-label">{getCompletenessLabel(currentCryptoData.integrity.overall_completeness)}</span>
                      </div>
                    </div>
                  </div>

                  {/* Indicators Completeness */}
                  <div className="completeness-card">
                    <h4>Technical Indicators</h4>
                    <div className="circular-progress" style={{
                      background: `conic-gradient(${getCompletenessColor(currentCryptoData.indicators.overall_completeness)} ${currentCryptoData.indicators.overall_completeness * 3.6}deg, #1e293b ${currentCryptoData.indicators.overall_completeness * 3.6}deg)`
                    }}>
                      <div className="progress-inner">
                        <span className="progress-value">{currentCryptoData.indicators.overall_completeness}%</span>
                        <span className="progress-count">{currentCryptoData.indicators.count} indicators</span>
                      </div>
                    </div>
                  </div>

                  {/* Metadata Completeness */}
                  <div className="completeness-card">
                    <h4>Scraped Metadata</h4>
                    <div className="circular-progress" style={{
                      background: `conic-gradient(${getCompletenessColor(currentCryptoData.metadata.overall_completeness)} ${currentCryptoData.metadata.overall_completeness * 3.6}deg, #1e293b ${currentCryptoData.metadata.overall_completeness * 3.6}deg)`
                    }}>
                      <div className="progress-inner">
                        <span className="progress-value">{currentCryptoData.metadata.overall_completeness}%</span>
                        <span className="progress-count">{currentCryptoData.metadata.count} fields</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Column Completeness Bars */}
              <div className="columns-section">
                <h3>🔍 Column-by-Column Analysis</h3>
                <div className="columns-list">
                  {Object.entries(currentCryptoData.integrity.columns).map(([colName, colData]) => (
                    <div key={colName} className="column-item">
                      <div className="column-header">
                        <span className="column-name">{colName}</span>
                        <span className="column-completeness">{colData.completeness}%</span>
                      </div>
                      <div className="column-bar-bg">
                        <div
                          className="column-bar-fill"
                          style={{
                            width: `${colData.completeness}%`,
                            backgroundColor: getCompletenessColor(colData.completeness)
                          }}
                        ></div>
                      </div>
                      {colData.missing_count > 0 && (
                        <span className="column-missing">
                          {colData.missing_count.toLocaleString()} missing values
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Missing Critical Metadata Warning */}
              {currentCryptoData.metadata.missing_critical.length > 0 && (
                <div className="warning-section">
                  <h3>⚠️ Missing Critical Metadata</h3>
                  <p>The following critical metadata fields are missing and should be collected for optimal AI training:</p>
                  <ul className="missing-list">
                    {currentCryptoData.metadata.missing_critical.map(field => (
                      <li key={field}>{field}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Time Gaps Analysis */}
              {currentCryptoData.gaps.gaps_detected > 0 && (
                <div className="gaps-section">
                  <h3>⏱️ Time Gaps Detected</h3>
                  <div className="gaps-stats">
                    <div className="gap-stat">
                      <span className="gap-label">Total Gaps:</span>
                      <span className="gap-value">{currentCryptoData.gaps.gaps_detected}</span>
                    </div>
                    <div className="gap-stat">
                      <span className="gap-label">Max Gap:</span>
                      <span className="gap-value">{currentCryptoData.gaps.max_gap_minutes} minutes</span>
                    </div>
                    <div className="gap-stat">
                      <span className="gap-label">Total Gap Time:</span>
                      <span className="gap-value">{currentCryptoData.gaps.total_gap_hours.toFixed(2)} hours</span>
                    </div>
                  </div>
                </div>
              )}
            </>
          ) : (
            /* Detailed Data View */
            <div className="detailed-data-section">
              <h3>📋 Detailed Data: {selectedCrypto}</h3>
              <p className="data-info">Showing {cryptoData.length} sample rows</p>

              <div className="data-table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>Open</th>
                      <th>High</th>
                      <th>Low</th>
                      <th>Close</th>
                      <th>Volume</th>
                      {cryptoData.length > 0 && Object.keys(cryptoData[0])
                        .filter(key => !['timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol'].includes(key))
                        .map(key => <th key={key}>{key}</th>)
                      }
                    </tr>
                  </thead>
                  <tbody>
                    {cryptoData.map((row, idx) => (
                      <tr key={idx}>
                        <td>{new Date(row.timestamp).toLocaleString()}</td>
                        <td>${row.open.toLocaleString()}</td>
                        <td>${row.high.toLocaleString()}</td>
                        <td>${row.low.toLocaleString()}</td>
                        <td>${row.close.toLocaleString()}</td>
                        <td>{row.volume.toLocaleString()}</td>
                        {Object.entries(row)
                          .filter(([key]) => !['timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol'].includes(key))
                          .map(([key, value]) => (
                            <td key={key}>
                              {value === null || value === undefined ? (
                                <span className="missing-value">-</span>
                              ) : (
                                String(value)
                              )}
                            </td>
                          ))
                        }
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default DatasetExplorer;
