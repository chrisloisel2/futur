import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { DataService } from '../../services/DataService';

interface PriceChartProps {
  symbol: string;
}

const PriceChart: React.FC<PriceChartProps> = ({ symbol }) => {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    DataService.getOHLCV(symbol, 200)
      .then(response => {
        setData(response.data);
        setLoading(false);
      })
      .catch(err => {
        console.error('Error loading OHLCV:', err);
        setLoading(false);
      });
  }, [symbol]);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '50px' }}>Loading...</div>;
  }

  const candlestickData = data.map(d => [
    d.timestamp,
    parseFloat(d.open),
    parseFloat(d.close),
    parseFloat(d.low),
    parseFloat(d.high)
  ]);

  const volumeData = data.map(d => [
    d.timestamp,
    parseFloat(d.volume)
  ]);

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross'
      },
      formatter: (params: any) => {
        const candle = params[0];
        if (!candle) return '';
        const date = new Date(candle.value[0]).toLocaleString();
        return `${date}<br/>
                Open: $${candle.value[1]}<br/>
                High: $${candle.value[4]}<br/>
                Low: $${candle.value[3]}<br/>
                Close: $${candle.value[2]}`;
      }
    },
    grid: [
      {
        left: '10%',
        right: '8%',
        height: '50%'
      },
      {
        left: '10%',
        right: '8%',
        top: '70%',
        height: '16%'
      }
    ],
    xAxis: [
      {
        type: 'time',
        gridIndex: 0,
        axisLabel: {
          color: '#aaa'
        }
      },
      {
        type: 'time',
        gridIndex: 1,
        axisLabel: {
          show: false
        }
      }
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        axisLabel: {
          color: '#aaa',
          formatter: (value: number) => `$${value}`
        }
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: {
          show: false
        }
      }
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: 50,
        end: 100
      },
      {
        show: true,
        xAxisIndex: [0, 1],
        type: 'slider',
        top: '90%',
        start: 50,
        end: 100
      }
    ],
    series: [
      {
        name: 'Candlestick',
        type: 'candlestick',
        data: candlestickData,
        itemStyle: {
          color: '#00ff9d',
          color0: '#ff6b9d',
          borderColor: '#00ff9d',
          borderColor0: '#ff6b9d'
        },
        xAxisIndex: 0,
        yAxisIndex: 0
      },
      {
        name: 'Volume',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumeData,
        itemStyle: {
          color: '#00d4ff',
          opacity: 0.5
        }
      }
    ]
  };

  return <ReactECharts option={option} style={{ height: '500px' }} />;
};

export default PriceChart;
