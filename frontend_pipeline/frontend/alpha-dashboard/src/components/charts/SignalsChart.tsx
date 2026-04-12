import React from 'react';
import ReactECharts from 'echarts-for-react';

interface SignalsChartProps {
  signals: any;
}

const SignalsChart: React.FC<SignalsChartProps> = ({ signals }) => {
  const byType = signals.stats.by_type || {};
  const byDirection = signals.stats.by_direction || {};

  const typeData = Object.entries(byType).map(([key, value]) => ({
    name: key.replace(/_/g, ' '),
    value: value
  }));

  const directionData = Object.entries(byDirection).map(([key, value]) => ({
    name: key,
    value: value
  }));

  const option = {
    tooltip: {
      trigger: 'item'
    },
    legend: {
      top: '5%',
      left: 'center',
      textStyle: { color: '#fff' }
    },
    series: [
      {
        name: 'Signals by Direction',
        type: 'pie',
        radius: ['15%', '35%'],
        center: ['25%', '60%'],
        data: directionData,
        label: {
          color: '#fff',
          formatter: '{b}: {c}'
        },
        itemStyle: {
          borderRadius: 5
        }
      },
      {
        name: 'Signals by Type',
        type: 'pie',
        radius: ['15%', '35%'],
        center: ['75%', '60%'],
        data: typeData,
        label: {
          color: '#fff',
          fontSize: 10,
          formatter: '{b}'
        },
        itemStyle: {
          borderRadius: 5
        }
      }
    ],
    color: {
      type: 'linear',
      x: 0,
      y: 0,
      x2: 1,
      y2: 1,
      colorStops: [
        { offset: 0, color: '#00d4ff' },
        { offset: 1, color: '#9d00ff' }
      ]
    }
  };

  return <ReactECharts option={option} style={{ height: '350px' }} />;
};

export default SignalsChart;
