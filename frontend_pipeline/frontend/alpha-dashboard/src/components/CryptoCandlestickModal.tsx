import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { DataService } from '../services/DataService';
import './CryptoCandlestickModal.css';

interface CryptoCandlestickModalProps {
  symbol: string;
  onClose: () => void;
}

const CryptoCandlestickModal: React.FC<CryptoCandlestickModalProps> = ({ symbol, onClose }) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [timeframe, setTimeframe] = useState('1h');

  const loadChartData = useCallback(async () => {
    if (!chartRef.current) return;

    setLoading(true);
    try {
      // Convert symbol format: "DOGE/USDT" -> "DOGEUSDT"
      const binanceSymbol = symbol.replace('/', '');

      // Fetch klines data
      const klines = await DataService.getKlines(binanceSymbol, timeframe, 500);

      // Initialize ECharts
      const chart = echarts.init(chartRef.current);

      // Prepare data
      const dates = klines.map((k: any) => {
        const date = new Date(k.time * 1000);
        return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
      });

      const data = klines.map((k: any) => [k.open, k.close, k.low, k.high]);
      const volumes = klines.map((k: any) => k.volume);

      // Calculate MA (Moving Averages)
      const calculateMA = (dayCount: number) => {
        const result = [];
        for (let i = 0; i < data.length; i++) {
          if (i < dayCount) {
            result.push('-');
            continue;
          }
          let sum = 0;
          for (let j = 0; j < dayCount; j++) {
            sum += data[i - j][1]; // close price
          }
          result.push((sum / dayCount).toFixed(2));
        }
        return result;
      };

      const option = {
        backgroundColor: '#0B0E11',
        title: {
          text: `${symbol} - Candlestick Chart`,
          left: 'center',
          top: 10,
          textStyle: {
            color: '#D1D4DC',
            fontSize: 18,
            fontWeight: 'bold'
          }
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'cross'
          },
          backgroundColor: 'rgba(19, 23, 34, 0.95)',
          borderColor: '#2A2E39',
          textStyle: {
            color: '#D1D4DC'
          },
          formatter: function (params: any) {
            const param = params[0];
            const data = param.data;
            return `
              <div style="padding: 5px;">
                <div style="font-weight: bold; margin-bottom: 5px;">${param.name}</div>
                <div>Open: <span style="color: #26A69A;">${data[0]}</span></div>
                <div>Close: <span style="color: ${data[1] >= data[0] ? '#26A69A' : '#EF5350'};">${data[1]}</span></div>
                <div>Low: <span style="color: #8E9098;">${data[2]}</span></div>
                <div>High: <span style="color: #8E9098;">${data[3]}</span></div>
                ${params[5] ? `<div>Volume: <span style="color: #8E9098;">${params[5].data.toFixed(2)}</span></div>` : ''}
              </div>
            `;
          }
        },
        legend: {
          data: ['Candlestick', 'MA5', 'MA10', 'MA20', 'MA30'],
          top: 40,
          textStyle: {
            color: '#8E9098'
          }
        },
        grid: [
          {
            left: '10%',
            right: '8%',
            top: 80,
            height: '50%'
          },
          {
            left: '10%',
            right: '8%',
            top: '65%',
            height: '16%'
          }
        ],
        xAxis: [
          {
            type: 'category',
            data: dates,
            boundaryGap: false,
            axisLine: {
              lineStyle: { color: '#2A2E39' }
            },
            axisLabel: {
              color: '#8E9098',
              rotate: 45
            },
            splitLine: {
              show: false
            },
            min: 'dataMin',
            max: 'dataMax',
            axisPointer: {
              z: 100
            }
          },
          {
            type: 'category',
            gridIndex: 1,
            data: dates,
            boundaryGap: false,
            axisLine: {
              lineStyle: { color: '#2A2E39' }
            },
            axisLabel: {
              show: false
            },
            splitLine: {
              show: false
            },
            min: 'dataMin',
            max: 'dataMax'
          }
        ],
        yAxis: [
          {
            scale: true,
            splitArea: {
              show: true,
              areaStyle: {
                color: ['rgba(42, 46, 57, 0.1)', 'rgba(42, 46, 57, 0.05)']
              }
            },
            axisLine: {
              lineStyle: { color: '#2A2E39' }
            },
            axisLabel: {
              color: '#8E9098'
            },
            splitLine: {
              lineStyle: {
                color: '#2A2E39'
              }
            }
          },
          {
            scale: true,
            gridIndex: 1,
            splitNumber: 2,
            axisLabel: {
              show: false
            },
            axisLine: {
              show: false
            },
            axisTick: {
              show: false
            },
            splitLine: {
              show: false
            }
          }
        ],
        dataZoom: [
          {
            type: 'inside',
            xAxisIndex: [0, 1],
            start: 70,
            end: 100
          },
          {
            show: true,
            xAxisIndex: [0, 1],
            type: 'slider',
            bottom: 10,
            start: 70,
            end: 100,
            backgroundColor: '#1a1d29',
            borderColor: '#2A2E39',
            fillerColor: 'rgba(38, 166, 154, 0.2)',
            handleStyle: {
              color: '#26A69A'
            },
            textStyle: {
              color: '#8E9098'
            }
          }
        ],
        series: [
          {
            name: 'Candlestick',
            type: 'candlestick',
            data: data,
            itemStyle: {
              color: '#26A69A',
              color0: '#EF5350',
              borderColor: '#26A69A',
              borderColor0: '#EF5350'
            },
            emphasis: {
              itemStyle: {
                borderWidth: 2
              }
            }
          },
          {
            name: 'MA5',
            type: 'line',
            data: calculateMA(5),
            smooth: true,
            lineStyle: {
              width: 1.5,
              color: '#4FC3F7'
            },
            showSymbol: false
          },
          {
            name: 'MA10',
            type: 'line',
            data: calculateMA(10),
            smooth: true,
            lineStyle: {
              width: 1.5,
              color: '#FFA726'
            },
            showSymbol: false
          },
          {
            name: 'MA20',
            type: 'line',
            data: calculateMA(20),
            smooth: true,
            lineStyle: {
              width: 1.5,
              color: '#AB47BC'
            },
            showSymbol: false
          },
          {
            name: 'MA30',
            type: 'line',
            data: calculateMA(30),
            smooth: true,
            lineStyle: {
              width: 1.5,
              color: '#66BB6A'
            },
            showSymbol: false
          },
          {
            name: 'Volume',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes,
            itemStyle: {
              color: function (params: any) {
                const index = params.dataIndex;
                return data[index][1] >= data[index][0]
                  ? 'rgba(38, 166, 154, 0.5)'
                  : 'rgba(239, 83, 80, 0.5)';
              }
            }
          }
        ]
      };

      chart.setOption(option);

      // Resize handler
      const resizeObserver = new ResizeObserver(() => {
        chart.resize();
      });
      resizeObserver.observe(chartRef.current);

      setLoading(false);

      return () => {
        resizeObserver.disconnect();
        chart.dispose();
      };
    } catch (error) {
      console.error('Error loading chart data:', error);
      setLoading(false);
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    loadChartData();
  }, [loadChartData]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{symbol} - Candlestick Chart</h2>
          <div className="modal-controls">
            <div className="timeframe-selector">
              <button
                className={timeframe === '1m' ? 'active' : ''}
                onClick={() => setTimeframe('1m')}
              >
                1m
              </button>
              <button
                className={timeframe === '5m' ? 'active' : ''}
                onClick={() => setTimeframe('5m')}
              >
                5m
              </button>
              <button
                className={timeframe === '15m' ? 'active' : ''}
                onClick={() => setTimeframe('15m')}
              >
                15m
              </button>
              <button
                className={timeframe === '1h' ? 'active' : ''}
                onClick={() => setTimeframe('1h')}
              >
                1h
              </button>
              <button
                className={timeframe === '4h' ? 'active' : ''}
                onClick={() => setTimeframe('4h')}
              >
                4h
              </button>
              <button
                className={timeframe === '1d' ? 'active' : ''}
                onClick={() => setTimeframe('1d')}
              >
                1D
              </button>
            </div>
            <button className="close-btn" onClick={onClose}>✕</button>
          </div>
        </div>

        {loading ? (
          <div className="modal-loading">
            <div className="spinner"></div>
            <p>Loading chart...</p>
          </div>
        ) : (
          <div ref={chartRef} className="chart-container" />
        )}

        <div className="modal-footer">
          <div className="chart-legend">
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#26A69A' }}></span>
              Bullish Candle
            </span>
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#EF5350' }}></span>
              Bearish Candle
            </span>
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#4FC3F7' }}></span>
              MA5
            </span>
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#FFA726' }}></span>
              MA10
            </span>
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#AB47BC' }}></span>
              MA20
            </span>
            <span className="legend-item">
              <span className="legend-color" style={{ background: '#66BB6A' }}></span>
              MA30
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CryptoCandlestickModal;
