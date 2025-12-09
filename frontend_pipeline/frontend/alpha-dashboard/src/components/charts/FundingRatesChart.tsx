import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { DataService } from '../../services/DataService';

const FundingRatesChart: React.FC = () => {
  const [data, setData] = useState<any[]>([]);

  useEffect(() => {
    DataService.getFundingRates()
      .then(response => setData(response.data.slice(0, 15)))
      .catch(err => console.error('Error loading funding rates:', err));
  }, []);

  const symbols = data.map(d => d.symbol.replace('/USDT', ''));
  const rates = data.map(d => (parseFloat(d.funding_rate) * 100).toFixed(4));

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      },
      formatter: (params: any) => {
        const rate = parseFloat(params[0].value);
        const color = rate > 0 ? '#00ff9d' : '#ff6b9d';
        return `${params[0].name}<br/>
                <span style="color:${color}">Funding Rate: ${params[0].value}%</span><br/>
                ${rate > 0.01 ? '⚠️ High long leverage' : rate < -0.01 ? '⚠️ High short leverage' : '✓ Normal'}`;
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: symbols,
      axisLabel: {
        color: '#aaa',
        rotate: 45
      }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#aaa',
        formatter: '{value}%'
      },
      splitLine: {
        lineStyle: {
          color: '#333'
        }
      }
    },
    series: [
      {
        name: 'Funding Rate',
        type: 'bar',
        data: rates,
        itemStyle: {
          color: (params: any) => {
            return parseFloat(params.value) > 0 ? '#00ff9d' : '#ff6b9d';
          }
        },
        markLine: {
          data: [
            { yAxis: 0.01, lineStyle: { color: '#ff4444', type: 'dashed' }, label: { formatter: 'High' } },
            { yAxis: -0.01, lineStyle: { color: '#ff4444', type: 'dashed' }, label: { formatter: 'Low' } },
            { yAxis: 0, lineStyle: { color: '#666' } }
          ]
        }
      }
    ]
  };

  return <ReactECharts option={option} style={{ height: '350px' }} />;
};

export default FundingRatesChart;
