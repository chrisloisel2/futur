import React, { useState, useEffect } from 'react';
import './App.css';
import Dashboard from './components/Dashboard';
import S3DataExplorer from './components/S3DataExplorer';
import DatasetExplorer from './components/DatasetExplorer';
import RealtimePredictions from './components/RealtimePredictions';
import PortfolioTracker from './components/PortfolioTracker';
import AIMetrics from './components/AIMetrics';
import TrainingDashboard from './components/TrainingDashboard';
import MLArchitectureView from './components/MLArchitecture/MLArchitectureView';
import { DataService } from './services/DataService';

type View = 'dashboard' | 's3-explorer' | 'dataset-explorer' | 'predictions' | 'portfolio' | 'ai-metrics' | 'training' | 'ml-architecture';

function App() {
  const [currentView, setCurrentView] = useState<View>('dashboard');
  const [dataLoaded, setDataLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Test API connection
    DataService.getSummary()
      .then(() => setDataLoaded(true))
      .catch((err) => setError(err.message));
  }, []);

  const renderView = () => {
    switch (currentView) {
      case 'dashboard':
        return <Dashboard />;
      case 'ml-architecture':
        return <MLArchitectureView />;
      case 's3-explorer':
        return <S3DataExplorer />;
      case 'dataset-explorer':
        return <DatasetExplorer />;
      case 'predictions':
        return <RealtimePredictions />;
      case 'portfolio':
        return <PortfolioTracker />;
      case 'ai-metrics':
        return <AIMetrics />;
      case 'training':
        return <TrainingDashboard />;
      default:
        return <Dashboard />;
    }
  };

  return (
    <div className="App">
      {error ? (
        <div className="error-container">
          <div className="error-icon">⚠️</div>
          <h2>API Connection Error</h2>
          <p className="error-message">{error}</p>
          <p className="error-hint">Make sure the API server is running</p>
          <code className="error-code">python api_server.py</code>
          <button className="retry-button" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      ) : !dataLoaded ? (
        <div className="loading-container">
          <div className="spinner"></div>
          <p>Loading Alpha Trading Platform...</p>
        </div>
      ) : (
        <>
          <nav className="top-nav">
            <div className="nav-brand">
              <div className="brand-icon">⚡</div>
              <div className="brand-text">
                <span className="brand-title">Alpha Trading</span>
                <span className="brand-subtitle">Professional Platform</span>
              </div>
            </div>

            <div className="nav-links">
              <button
                className={`nav-link ${currentView === 'dashboard' ? 'active' : ''}`}
                onClick={() => setCurrentView('dashboard')}
              >
                <span className="nav-icon">📊</span>
                <span>Dashboard</span>
              </button>

              <button
                className={`nav-link ${currentView === 'ml-architecture' ? 'active' : ''}`}
                onClick={() => setCurrentView('ml-architecture')}
              >
                <span className="nav-icon">🏗️</span>
                <span>ML Architecture</span>
              </button>

              <button
                className={`nav-link ${currentView === 'portfolio' ? 'active' : ''}`}
                onClick={() => setCurrentView('portfolio')}
              >
                <span className="nav-icon">💼</span>
                <span>Portfolio</span>
              </button>

              <button
                className={`nav-link ${currentView === 'ai-metrics' ? 'active' : ''}`}
                onClick={() => setCurrentView('ai-metrics')}
              >
                <span className="nav-icon">🧠</span>
                <span>AI Metrics</span>
              </button>

              <button
                className={`nav-link ${currentView === 'predictions' ? 'active' : ''}`}
                onClick={() => setCurrentView('predictions')}
              >
                <span className="nav-icon">🤖</span>
                <span>Predictions</span>
              </button>

              <button
                className={`nav-link ${currentView === 's3-explorer' ? 'active' : ''}`}
                onClick={() => setCurrentView('s3-explorer')}
              >
                <span className="nav-icon">🗂️</span>
                <span>S3 Data</span>
              </button>

              <button
                className={`nav-link ${currentView === 'dataset-explorer' ? 'active' : ''}`}
                onClick={() => setCurrentView('dataset-explorer')}
              >
                <span className="nav-icon">📊</span>
                <span>Dataset Integrity</span>
              </button>

              <button
                className={`nav-link ${currentView === 'training' ? 'active' : ''}`}
                onClick={() => setCurrentView('training')}
              >
                <span className="nav-icon">🎓</span>
                <span>Training</span>
              </button>
            </div>
          </nav>

          <main className="main-content">
            {renderView()}
          </main>
        </>
      )}
    </div>
  );
}

export default App;
