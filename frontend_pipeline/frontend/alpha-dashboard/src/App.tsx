import React, { useState } from 'react';
import './App.css';
import AlphaSignal from './components/AlphaSignal';
import TrainingDashboard from './components/TrainingDashboard';

type View = 'signal' | 'training';

function App() {
  const [currentView, setCurrentView] = useState<View>('signal');

  const renderView = () => {
    switch (currentView) {
      case 'signal':
        return <AlphaSignal />;
      case 'training':
        return <TrainingDashboard />;
      default:
        return <AlphaSignal />;
    }
  };

  return (
    <div className="App">
      <nav className="top-nav">
        <div className="nav-brand">
          <div className="brand-icon">⚡</div>
          <div className="brand-text">
            <span className="brand-title">Alpha Trading</span>
            <span className="brand-subtitle">BTC/USDT · 1h</span>
          </div>
        </div>

        <div className="nav-links">
          <button
            className={`nav-link ${currentView === 'signal' ? 'active' : ''}`}
            onClick={() => setCurrentView('signal')}
          >
            <span className="nav-icon">⚡</span>
            <span>Signal</span>
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
    </div>
  );
}

export default App;
