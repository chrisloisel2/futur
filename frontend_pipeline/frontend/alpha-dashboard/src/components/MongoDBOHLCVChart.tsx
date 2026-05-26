import React, { useState, useEffect, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { API_BASE_URL } from '../config/api';

const UP_COLOR        = '#ec0000';
const UP_BORDER_COLOR = '#8A0000';
const DN_COLOR        = '#00da3c';
const DN_BORDER_COLOR = '#008F28';

interface OHLCVRow {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

function calculateMA(values: number[][], period: number): (number | string)[] {
  return values.map((_, i) => {
    if (i < period) return '-';
    const sum = values.slice(i - period, i).reduce((acc, v) => acc + v[1], 0); // v[1] = close
    return +(sum / period).toFixed(2);
  });
}

const MongoDBOHLCVChart: React.FC = () => {
  const [rows, setRows]     = useState<OHLCVRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [limit, setLimit]   = useState(500);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${API_BASE_URL}/api/ohlcv1m?symbol=${symbol}&limit=${limit}`)
      .then(r => r.json())
      .then(json => {
        if (json.success) setRows(json.data);
        else setError(json.detail || 'Erreur API');
        setLoading(false);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [symbol, limit]);

  // [open, close, low, high] — format ECharts candlestick
  const { categoryData, values, volumes } = useMemo(() => {
    const categoryData: string[] = [];
    const values: number[][] = [];
    const volumes: number[] = [];
    rows.forEach(r => {
      categoryData.push(r.timestamp);
      values.push([r.open, r.close, r.low, r.high]);
      volumes.push(r.volume);
    });
    return { categoryData, values, volumes };
  }, [rows]);

  const option = useMemo(() => {
    if (values.length === 0) return {};

    const ma5  = calculateMA(values, 5);
    const ma10 = calculateMA(values, 10);
    const ma20 = calculateMA(values, 20);
    const ma30 = calculateMA(values, 30);

    return {
      backgroundColor: '#1a1a2e',
      title: {
        text: `${symbol} — 1m (MongoDB)`,
        left: 0,
        textStyle: { color: '#e0e0e0', fontSize: 16 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(26,26,46,0.95)',
        borderColor: '#3b82f6',
        textStyle: { color: '#fff' },
      },
      legend: {
        data: ['Candles', 'MA5', 'MA10', 'MA20', 'MA30'],
        top: 30,
        textStyle: { color: '#a0aec0' },
      },
      grid: [
        { left: '10%', right: '10%', top: 70, bottom: '25%' },
        { left: '10%', right: '10%', height: '10%', bottom: '12%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: categoryData,
          boundaryGap: false,
          axisLine: { onZero: false, lineStyle: { color: '#4a5568' } },
          splitLine: { show: false },
          axisLabel: { color: '#a0aec0', fontSize: 10 },
          min: 'dataMin',
          max: 'dataMax',
        },
        {
          type: 'category',
          gridIndex: 1,
          data: categoryData,
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#4a5568' } },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          splitArea: {
            show: true,
            areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(0,0,0,0.05)'] },
          },
          axisLabel: {
            color: '#a0aec0',
            formatter: (v: number) => `$${v.toLocaleString()}`,
          },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { color: '#a0aec0', fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 80, end: 100 },
        {
          show: true,
          type: 'slider',
          xAxisIndex: [0, 1],
          top: '88%',
          start: 80,
          end: 100,
          backgroundColor: 'rgba(255,255,255,0.05)',
          borderColor: '#3b82f6',
          fillerColor: 'rgba(59,130,246,0.2)',
          handleStyle: { color: '#3b82f6' },
          textStyle: { color: '#a0aec0' },
        },
      ],
      series: [
        {
          name: 'Candles',
          type: 'candlestick',
          data: values,
          itemStyle: {
            color: UP_COLOR,
            color0: DN_COLOR,
            borderColor: UP_BORDER_COLOR,
            borderColor0: DN_BORDER_COLOR,
          },
          markPoint: {
            label: {
              formatter: (param: any) =>
                param != null ? Math.round(param.value) + '' : '',
            },
            data: [
              { name: 'highest value',            type: 'max',     valueDim: 'highest' },
              { name: 'lowest value',             type: 'min',     valueDim: 'lowest'  },
              { name: 'average value on close',   type: 'average', valueDim: 'close'   },
            ],
            tooltip: {
              formatter: (param: any) => param.name + '<br>' + (param.data.coord || ''),
            },
          },
          markLine: {
            symbol: ['none', 'none'],
            data: [
              [
                {
                  name: 'from lowest to highest',
                  type: 'min', valueDim: 'lowest',
                  symbol: 'circle', symbolSize: 10,
                  label: { show: false }, emphasis: { label: { show: false } },
                },
                {
                  type: 'max', valueDim: 'highest',
                  symbol: 'circle', symbolSize: 10,
                  label: { show: false }, emphasis: { label: { show: false } },
                },
              ],
              { name: 'min line on close', type: 'min', valueDim: 'close' },
              { name: 'max line on close', type: 'max', valueDim: 'close' },
            ],
          },
        },
        {
          name: 'Volume',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          itemStyle: {
            color: (params: any) => {
              const v = values[params.dataIndex];
              return v && v[1] >= v[0]
                ? 'rgba(236,0,0,0.4)'
                : 'rgba(0,218,60,0.4)';
            },
          },
        },
        { name: 'MA5',  type: 'line', data: ma5,  smooth: true, lineStyle: { opacity: 0.7 } },
        { name: 'MA10', type: 'line', data: ma10, smooth: true, lineStyle: { opacity: 0.7 } },
        { name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { opacity: 0.7 } },
        { name: 'MA30', type: 'line', data: ma30, smooth: true, lineStyle: { opacity: 0.7 } },
      ],
    };
  }, [categoryData, values, volumes, symbol]);

  return (
    <div style={{ padding: 24, background: '#1a1a2e', minHeight: '100vh' }}>
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          style={selectStyle}
        >
          <option value="BTCUSDT">BTCUSDT</option>
        </select>
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          style={selectStyle}
        >
          <option value={200}>200 bougies</option>
          <option value={500}>500 bougies</option>
          <option value={1000}>1000 bougies</option>
          <option value={2000}>2000 bougies</option>
          <option value={5000}>5000 bougies</option>
        </select>
        <span style={{ color: '#718096', fontSize: 13 }}>
          {loading ? 'Chargement…' : error ? `Erreur: ${error}` : `${rows.length} candles`}
        </span>
      </div>

      {!loading && !error && values.length > 0 && (
        <ReactECharts
          option={option}
          style={{ height: 650, width: '100%' }}
          theme="dark"
          notMerge
          opts={{ renderer: 'canvas' }}
        />
      )}

      {!loading && error && (
        <div style={{ color: '#ef4444', padding: 40, textAlign: 'center' }}>
          {error}
        </div>
      )}

      {loading && (
        <div style={{ color: '#a0aec0', padding: 40, textAlign: 'center' }}>
          Chargement des données MongoDB…
        </div>
      )}
    </div>
  );
};

const selectStyle: React.CSSProperties = {
  background: '#2d3748',
  color: '#e0e0e0',
  border: '1px solid #4a5568',
  borderRadius: 4,
  padding: '4px 8px',
  cursor: 'pointer',
};

export default MongoDBOHLCVChart;
