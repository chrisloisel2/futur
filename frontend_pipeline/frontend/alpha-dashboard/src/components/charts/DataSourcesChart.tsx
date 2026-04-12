import React from 'react';
import ReactECharts from 'echarts-for-react';

interface DataSourcesChartProps {
  data: any;
}

const DataSourcesChart: React.FC<DataSourcesChartProps> = ({ data }) => {
  const sources = Object.entries(data)
    .filter(([_, value]: [string, any]) => value.records)
    .map(([key, value]: [string, any]) => ({
      name: key.replace(/_/g, ' ').toUpperCase(),
      value: value.records,
      size: value.size_mb
    }))
    .sort((a, b) => b.value - a.value);

  const option = {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const source = sources.find(s => s.name === params.name);
        return `${params.name}<br/>Records: ${params.value}<br/>Size: ${source?.size.toFixed(2)} MB<br/>${params.percent}%`;
      }
    },
    legend: {
      orient: 'vertical',
      left: 'left',
      textStyle: { color: '#fff' }
    },
    series: [
      {
        name: 'Data Sources',
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 10,
          borderColor: '#1a1a2e',
          borderWidth: 2
        },
        label: {
          show: false,
          position: 'center'
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 20,
            fontWeight: 'bold',
            color: '#fff'
          }
        },
        labelLine: {
          show: false
        },
        data: sources.map(s => ({
          value: s.value,
          name: s.name
        }))
      }
    ],
    color: ['#00d4ff', '#00ff9d', '#ff6b9d', '#ffd700', '#9d00ff', '#ff4500', '#00bfff', '#32cd32']
  };

  return <ReactECharts option={option} style={{ height: '400px' }} theme="dark" />;
};

export default DataSourcesChart;
