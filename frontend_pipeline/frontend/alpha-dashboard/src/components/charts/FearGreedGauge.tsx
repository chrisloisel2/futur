import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { DataService } from '../../services/DataService';

const FearGreedGauge: React.FC = () => {
  const [value, setValue] = useState(50);
  const [classification, setClassification] = useState('Neutral');

  useEffect(() => {
    DataService.getFearGreed()
      .then(data => {
        if (data.latest) {
          setValue(parseInt(data.latest.value));
          setClassification(data.latest.classification || getClassification(parseInt(data.latest.value)));
        }
      })
      .catch(err => console.error('Error loading Fear & Greed:', err));
  }, []);

  const getClassification = (val: number): string => {
    if (val < 25) return 'Extreme Fear';
    if (val < 45) return 'Fear';
    if (val < 55) return 'Neutral';
    if (val < 75) return 'Greed';
    return 'Extreme Greed';
  };

  const getColor = (val: number): string => {
    if (val < 25) return '#ff4444';
    if (val < 45) return '#ff8844';
    if (val < 55) return '#ffcc44';
    if (val < 75) return '#88ff44';
    return '#44ff44';
  };

  const option = {
    series: [
      {
        type: 'gauge',
        startAngle: 180,
        endAngle: 0,
        min: 0,
        max: 100,
        splitNumber: 10,
        itemStyle: {
          color: getColor(value)
        },
        progress: {
          show: true,
          width: 30
        },
        pointer: {
          show: true,
          length: '60%',
          width: 8
        },
        axisLine: {
          lineStyle: {
            width: 30,
            color: [
              [0.25, '#ff4444'],
              [0.45, '#ff8844'],
              [0.55, '#ffcc44'],
              [0.75, '#88ff44'],
              [1, '#44ff44']
            ]
          }
        },
        axisTick: {
          distance: -45,
          splitNumber: 5,
          lineStyle: {
            width: 2,
            color: '#999'
          }
        },
        splitLine: {
          distance: -52,
          length: 14,
          lineStyle: {
            width: 3,
            color: '#999'
          }
        },
        axisLabel: {
          distance: -20,
          color: '#999',
          fontSize: 12
        },
        anchor: {
          show: false
        },
        title: {
          show: false
        },
        detail: {
          valueAnimation: true,
          width: '60%',
          lineHeight: 40,
          borderRadius: 8,
          offsetCenter: [0, '-15%'],
          fontSize: 40,
          fontWeight: 'bolder',
          formatter: '{value}',
          color: 'inherit'
        },
        data: [
          {
            value: value,
            name: classification
          }
        ]
      }
    ]
  };

  return (
    <div style={{ textAlign: 'center' }}>
      <ReactECharts option={option} style={{ height: '300px' }} />
      <div style={{ color: getColor(value), fontSize: '24px', fontWeight: 'bold', marginTop: '-20px' }}>
        {classification}
      </div>
    </div>
  );
};

export default FearGreedGauge;
