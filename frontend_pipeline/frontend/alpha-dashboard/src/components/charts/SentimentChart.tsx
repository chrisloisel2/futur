import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { DataService } from '../../services/DataService';

const SentimentChart: React.FC = () => {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    DataService.getSentiment()
      .then(response => setData(response))
      .catch(err => console.error('Error loading sentiment:', err));
  }, []);

  if (!data) {
    return <div style={{ textAlign: 'center', padding: '50px' }}>Loading...</div>;
  }

  const subreddits = data.by_subreddit.map((s: any) => s.subreddit);
  const scores = data.by_subreddit.map((s: any) => s.total_score);
  const comments = data.by_subreddit.map((s: any) => s.total_comments);

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      }
    },
    legend: {
      data: ['Upvotes', 'Comments'],
      textStyle: { color: '#fff' }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: subreddits,
      axisLabel: {
        color: '#aaa'
      }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#aaa'
      },
      splitLine: {
        lineStyle: {
          color: '#333'
        }
      }
    },
    series: [
      {
        name: 'Upvotes',
        type: 'bar',
        data: scores,
        itemStyle: {
          color: '#00d4ff'
        }
      },
      {
        name: 'Comments',
        type: 'bar',
        data: comments,
        itemStyle: {
          color: '#ff6b9d'
        }
      }
    ]
  };

  return (
    <div>
      <ReactECharts option={option} style={{ height: '300px' }} />
      <div style={{ color: '#aaa', fontSize: '12px', marginTop: '10px', textAlign: 'center' }}>
        Total Posts: {data.total_posts}
      </div>
    </div>
  );
};

export default SentimentChart;
