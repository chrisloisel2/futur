import React, { useState, useEffect } from 'react';
import { DataService } from '../../services/DataService';
import { mlWebSocketService } from '../../services/MLWebSocketService';
import Level0Gating from './Level0Gating';
import Level1Context from './Level1Context';
import Level2Specialists from './Level2Specialists';
import Level3Aggregators from './Level3Aggregators';
import Level4MetaDecider from './Level4MetaDecider';
import ArchitectureFlow from './ArchitectureFlow';
import './MLArchitecture.css';

interface LevelData {
  level0?: any;
  level1?: any;
  level2?: any;
  level3?: any;
  level4?: any;
  [key: string]: any; // Allow dynamic level access
}

const MLArchitectureView: React.FC = () => {
  const [selectedLevel, setSelectedLevel] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<'flow' | 'detailed'>('flow');
  const [levelData, setLevelData] = useState<LevelData>({});
  const [loading, setLoading] = useState(true);
  const [realtime, setRealtime] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

  useEffect(() => {
    loadArchitectureData();

    if (realtime) {
      mlWebSocketService.connect();

      const handleConnection = (data: any) => {
        setWsConnected(data.status === 'connected');
      };

      const handleArchitectureUpdate = (data: any) => {
        setLevelData(prevData => ({ ...prevData, ...data }));
      };

      const handleLevel0Update = (data: any) => {
        setLevelData(prevData => ({ ...prevData, level0: data }));
      };

      const handleLevel1Update = (data: any) => {
        setLevelData(prevData => ({ ...prevData, level1: data }));
      };

      const handleLevel2Update = (data: any) => {
        setLevelData(prevData => ({ ...prevData, level2: data }));
      };

      const handleLevel3Update = (data: any) => {
        setLevelData(prevData => ({ ...prevData, level3: data }));
      };

      const handleLevel4Update = (data: any) => {
        setLevelData(prevData => ({ ...prevData, level4: data }));
      };

      mlWebSocketService.on('connection', handleConnection);
      mlWebSocketService.on('architecture', handleArchitectureUpdate);
      mlWebSocketService.on('level0', handleLevel0Update);
      mlWebSocketService.on('level1', handleLevel1Update);
      mlWebSocketService.on('level2', handleLevel2Update);
      mlWebSocketService.on('level3', handleLevel3Update);
      mlWebSocketService.on('level4', handleLevel4Update);

      return () => {
        mlWebSocketService.off('connection', handleConnection);
        mlWebSocketService.off('architecture', handleArchitectureUpdate);
        mlWebSocketService.off('level0', handleLevel0Update);
        mlWebSocketService.off('level1', handleLevel1Update);
        mlWebSocketService.off('level2', handleLevel2Update);
        mlWebSocketService.off('level3', handleLevel3Update);
        mlWebSocketService.off('level4', handleLevel4Update);
        mlWebSocketService.disconnect();
      };
    }
  }, [realtime]);

  const loadArchitectureData = async () => {
    try {
      const data = await DataService.getMLArchitectureData();
      setLevelData(data);
      setLoading(false);
    } catch (error) {
      console.error('Error loading ML architecture data:', error);
      setLoading(false);
    }
  };

  const levels = [
    {
      id: 0,
      name: 'Global Gating',
      description: 'Tradeability Filter',
      icon: '🚪',
      color: '#FF6B6B',
      component: Level0Gating
    },
    {
      id: 1,
      name: 'Context Detectors',
      description: '5 Orthogonal Patterns',
      icon: '🎯',
      color: '#4ECDC4',
      component: Level1Context
    },
    {
      id: 2,
      name: 'Conditional Specialists',
      description: '4 Expert Networks',
      icon: '🧠',
      color: '#45B7D1',
      component: Level2Specialists
    },
    {
      id: 3,
      name: 'Aggregators',
      description: 'Event & Pairwise Analysis',
      icon: '⚡',
      color: '#96CEB4',
      component: Level3Aggregators
    },
    {
      id: 4,
      name: 'Meta-Decider',
      description: 'PPO Policy Network',
      icon: '🎮',
      color: '#FFEAA7',
      component: Level4MetaDecider
    }
  ];

  if (loading) {
    return (
      <div className="ml-architecture-loading">
        <div className="spinner-large"></div>
        <p>Loading ML Architecture...</p>
      </div>
    );
  }

  return (
    <div className="ml-architecture-view">
      <header className="ml-architecture-header">
        <div className="header-content">
          <div className="header-left">
            <h1 className="architecture-title">
              <span className="title-icon">🏗️</span>
              5-Level ML Architecture
            </h1>
            <p className="architecture-subtitle">
              Hierarchical Multi-Level Trading Intelligence System
            </p>
          </div>

          <div className="header-controls">
            <button
              className={`view-mode-btn ${viewMode === 'flow' ? 'active' : ''}`}
              onClick={() => setViewMode('flow')}
            >
              <span>📊</span> Flow View
            </button>
            <button
              className={`view-mode-btn ${viewMode === 'detailed' ? 'active' : ''}`}
              onClick={() => setViewMode('detailed')}
            >
              <span>🔍</span> Detailed View
            </button>

            <button
              className={`realtime-toggle ${realtime ? 'active' : ''}`}
              onClick={() => setRealtime(!realtime)}
            >
              <span className={`status-indicator ${realtime ? 'live' : ''}`}></span>
              {realtime ? 'Live Mode' : 'Static Mode'}
            </button>
          </div>
        </div>

        <div className="architecture-stats">
          <div className="stat-item">
            <div className="stat-icon">📦</div>
            <div className="stat-content">
              <div className="stat-value">5</div>
              <div className="stat-label">Levels</div>
            </div>
          </div>

          <div className="stat-item">
            <div className="stat-icon">🤖</div>
            <div className="stat-content">
              <div className="stat-value">12</div>
              <div className="stat-label">Models</div>
            </div>
          </div>

          <div className="stat-item">
            <div className="stat-icon">📊</div>
            <div className="stat-content">
              <div className="stat-value">48</div>
              <div className="stat-label">Features</div>
            </div>
          </div>

          <div className="stat-item">
            <div className="stat-icon">⚡</div>
            <div className="stat-content">
              <div className="stat-value">256</div>
              <div className="stat-label">Lookback</div>
            </div>
          </div>
        </div>
      </header>

      {viewMode === 'flow' ? (
        <ArchitectureFlow
          levels={levels}
          onLevelClick={setSelectedLevel}
          selectedLevel={selectedLevel}
          levelData={levelData}
        />
      ) : (
        <div className="levels-grid">
          {levels.map((level) => {
            const LevelComponent = level.component;
            return (
              <div
                key={level.id}
                className={`level-card ${selectedLevel === level.id ? 'selected' : ''}`}
                onClick={() => setSelectedLevel(selectedLevel === level.id ? null : level.id)}
              >
                <div className="level-header" style={{ borderColor: level.color }}>
                  <div className="level-badge" style={{ backgroundColor: level.color }}>
                    <span className="level-icon">{level.icon}</span>
                    <span className="level-number">L{level.id}</span>
                  </div>
                  <div className="level-info">
                    <h3 className="level-name">{level.name}</h3>
                    <p className="level-description">{level.description}</p>
                  </div>
                  <button className="expand-btn">
                    {selectedLevel === level.id ? '−' : '+'}
                  </button>
                </div>

                {selectedLevel === level.id && (
                  <div className="level-content">
                    <LevelComponent data={levelData[`level${level.id}`]} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default MLArchitectureView;
