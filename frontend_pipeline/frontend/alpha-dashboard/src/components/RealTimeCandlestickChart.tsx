/**
 * Graphique Candlestick en temps réel utilisant nos données historiques
 */
import React, { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import './CryptoCandlestickModal.css';

interface RealTimeCandlestickChartProps {
  symbol: string;
  onClose: () => void;
}

const RealTimeCandlestickChart: React.FC<RealTimeCandlestickChartProps> = ({ symbol, onClose }) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const resizeObserver = useRef<ResizeObserver | null>(null);

  const [loading, setLoading] = useState(true);
  const [timeframe, setTimeframe] = useState<'1h' | '4h' | '1d'>('1h');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);

      resizeObserver.current = new ResizeObserver(() => {
        chartInstance.current?.resize();
      });

      resizeObserver.current.observe(chartRef.current);
    }

    loadChartData();

    const interval = setInterval(() => loadChartData(true), 30000);

    return () => {
      clearInterval(interval);
      resizeObserver.current?.disconnect();
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, [symbol, timeframe]);

  const loadChartData = async (isUpdate = false) => {
    try {
      if (!isUpdate) {
        setLoading(true);
        setError(null);
      }

      const url = `http://localhost:8000/api/historical/${symbol}?limit=500`;
      const response = await fetch(url);

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const result = await response.json();
      if (!result.success || !result.data?.length) throw new Error('No data available');

      const klines = result.data;

      const dates = klines.map((k: any) => {
        const d = new Date(k.timestamp);
        return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(
          d.getMinutes()
        ).padStart(2, '0')}`;
      });

      const candleData = klines.map((k: any) => [
        Number(k.open),
        Number(k.close),
        Number(k.low),
        Number(k.high)
      ]);

      const volumes = klines.map((k: any) => Number(k.volume));

      const calculateMA = (n: number) => {
        const out: (number | string)[] = [];
        for (let i = 0; i < candleData.length; i++) {
          if (i < n) out.push('-');
          else {
            let sum = 0;
            for (let j = 0; j < n; j++) sum += candleData[i - j][1];
            out.push(Number((sum / n).toFixed(2)));
          }
        }
        return out;
      };

      const option = {
        backgroundColor: '#0B0E11',
        title: {
          text: `${symbol} - Live Candlestick Chart`,
          left: 'center',
          top: 10,
          textStyle: { color: '#D1D4DC', fontSize: 18, fontWeight: 'bold' }
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          backgroundColor: 'rgba(19,23,34,0.95)',
          borderColor: '#2A2E39',
          textStyle: { color: '#D1D4DC' }
        },
        legend: {
          data: ['Candlestick', 'MA5', 'MA10', 'MA20', 'MA30', 'Volume'],
          top: 40,
          textStyle: { color: '#8E9098' },
          selected: { Volume: false }
        },
        grid: [
          { left: '10%', right: '8%', top: 80, height: '50%' },
          { left: '10%', right: '8%', top: '65%', height: '16%' }
        ],
        xAxis: [
          {
            type: 'category',
            data: dates,
            boundaryGap: false,
            axisLine: { lineStyle: { color: '#2A2E39' } },
            axisLabel: { color: '#8E9098', rotate: 45 }
          },
          {
            type: 'category',
            gridIndex: 1,
            data: dates,
            boundaryGap: false,
            axisLabel: { show: false }
          }
        ],
        yAxis: [
          {
            scale: true,
            axisLine: { lineStyle: { color: '#2A2E39' } },
            axisLabel: {
              color: '#8E9098',
              formatter: (v: number) => `$${v.toFixed(2)}`
            },
            splitLine: { lineStyle: { color: '#2A2E39' } }
          },
          {
            scale: true,
            gridIndex: 1,
            splitNumber: 2,
            axisLabel: { show: false },
            axisTick: { show: false },
            splitLine: { show: false }
          }
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1], start: 70, end: 100 },
          {
            show: true,
            type: 'slider',
            xAxisIndex: [0, 1],
            bottom: 10,
            start: 70,
            end: 100
          }
        ],
        series: [
          {
            name: 'Candlestick',
            type: 'candlestick',
            data: candleData,
            itemStyle: {
              color: '#26A69A',
              color0: '#EF5350',
              borderColor: '#26A69A',
              borderColor0: '#EF5350'
            }
          },
          { name: 'MA5', type: 'line', data: calculateMA(5), smooth: true, showSymbol: false },
          { name: 'MA10', type: 'line', data: calculateMA(10), smooth: true, showSymbol: false },
          { name: 'MA20', type: 'line', data: calculateMA(20), smooth: true, showSymbol: false },
          { name: 'MA30', type: 'line', data: calculateMA(30), smooth: true, showSymbol: false },
          {
            name: 'Volume',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes,
            itemStyle: {
              color: (p: any) =>
                candleData[p.dataIndex][1] >= candleData[p.dataIndex][0]
                  ? 'rgba(38,166,154,0.5)'
                  : 'rgba(239,83,80,0.5)'
            }
          }
        ]
      };

      chartInstance.current?.setOption(option);
      setLoading(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{symbol} - Live Candlestick Chart</h2>

          <div className="modal-controls">
            <div className="timeframe-selector">
              <button className={timeframe === '1h' ? 'active' : ''} onClick={() => setTimeframe('1h')}>1H</button>
              <button className={timeframe === '4h' ? 'active' : ''} onClick={() => setTimeframe('4h')}>4H</button>
              <button className={timeframe === '1d' ? 'active' : ''} onClick={() => setTimeframe('1d')}>1D</button>
              <button onClick={() => loadChartData()}>🔄</button>
            </div>

            <button className="close-btn" onClick={onClose}>✕</button>
          </div>
        </div>

        {error && (
          <div className="modal-error">
            <p style={{ color: '#EF5350' }}>❌ {error}</p>
          </div>
        )}

        <div
          ref={chartRef}
          className="chart-container"
          style={{ height: '600px', width: '100%' }}
        />

        <div className="modal-footer">
          <div className="chart-legend">
            <span className="legend-item"><span className="legend-color" style={{ background: '#26A69A' }}></span>Bullish</span>
            <span className="legend-item"><span className="legend-color" style={{ background: '#EF5350' }}></span>Bearish</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RealTimeCandlestickChart;
