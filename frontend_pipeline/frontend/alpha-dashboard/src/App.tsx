import React, { useState, useEffect } from 'react';
import './App.css';
import { SignalView, TradesView, ModelView } from './components/PaperDashboard';
import { FleetView } from './components/FleetView';
import { API_BASE_URL } from './config/api';

type View = 'fleet' | 'signal' | 'trades' | 'model';

const NAV: { id: View; label: string; icon: string }[] = [
  { id: 'fleet',  label: 'Fleet TOP 10',  icon: '◈' },
  { id: 'signal', label: 'Signal BTC',    icon: '▲' },
  { id: 'trades', label: 'Trades',        icon: '◆' },
  { id: 'model',  label: 'Modèle',        icon: '⊞' },
];

function App() {
  const [view, setView] = useState<View>('fleet');
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [clock, setClock] = useState('');

  useEffect(() => {
    const tick = () =>
      setClock(new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch(`${API_BASE_URL}/health`);
        setApiOnline(r.ok);
      } catch {
        setApiOnline(false);
      }
    };
    check();
    const t = setInterval(check, 30_000);
    return () => clearInterval(t);
  }, []);

  const renderView = () => {
    switch (view) {
      case 'fleet':  return <FleetView />;
      case 'signal': return <SignalView />;
      case 'trades': return <TradesView />;
      case 'model':  return <ModelView />;
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-logo">⚡</div>
          <div className="brand-info">
            <span className="brand-name">Alpha Trading</span>
            <span className="brand-tag">TOP 10 · LONG · Paper</span>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map(item => (
            <button
              key={item.id}
              className={`nav-item ${view === item.id ? 'active' : ''}`}
              onClick={() => setView(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div
            className={`api-status ${
              apiOnline === true ? 'online' : apiOnline === false ? 'offline' : 'checking'
            }`}
          >
            <span className="status-dot" />
            <span className="status-text">
              {apiOnline === true
                ? 'API Online'
                : apiOnline === false
                ? 'API Offline'
                : 'Connecting…'}
            </span>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <div className="page-header">
          <div className="page-title-row">
            <span className="page-icon">{NAV.find(n => n.id === view)?.icon}</span>
            <h1 className="page-title">{NAV.find(n => n.id === view)?.label}</h1>
          </div>
          <div className="header-right">
            <span className="paper-mode-tag">PAPER</span>
            <span className="header-clock">{clock}</span>
          </div>
        </div>

        <div className="page-content">
          {renderView()}
        </div>
      </main>
    </div>
  );
}

export default App;
