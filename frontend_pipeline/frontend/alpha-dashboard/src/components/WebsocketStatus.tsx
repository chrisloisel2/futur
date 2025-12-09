import React, { useEffect, useState } from 'react';
import { DataService } from '../services/DataService';

type CollectorStatus = {
  status: string;
  symbols?: number;
  last_message?: string;
  messages?: number;
  error?: string;
};

type PipelineStatus = {
  status: string;
  runtime_seconds?: number;
  collectors?: Record<string, CollectorStatus>;
  message?: string;
};

const statusBadgeClass = (status: string) => {
  switch (status) {
    case 'connected':
      return 'status-badge status-ok';
    case 'running':
      return 'status-badge status-ok';
    case 'stopped':
    case 'closed':
      return 'status-badge status-neutral';
    default:
      return 'status-badge status-error';
  }
};

const formatAgo = (iso?: string) => {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  if (diffMs < 0) return '—';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
};

const WebsocketStatus: React.FC = () => {
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const loadStatus = async () => {
    try {
      const data = await DataService.getPipelineStatus();
      setPipelineStatus(data);
      setError(null);
    } catch (err) {
      console.error('Error fetching pipeline status', err);
      setError('Impossible de joindre le backend');
    } finally {
      setLoading(false);
    }
  };

  const startPipeline = async () => {
    try {
      setStarting(true);
      await DataService.startPipeline();
      await loadStatus();
    } catch (err) {
      console.error('Error starting pipeline', err);
      setError("Impossible de démarrer la pipeline (vérifie l'API).");
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="ws-status-message">Chargement du statut des WebSockets...</div>;
  if (error) return <div className="ws-status-message error">{error}</div>;
  if (!pipelineStatus) return null;

  const collectors = pipelineStatus.collectors || {};
  const collectorEntries = Object.entries(collectors);

  if (pipelineStatus.status !== 'running') {
    return (
      <div className="ws-status-message">
        <div>Pipeline arrêtée. Lance-la pour voir l&apos;état des WebSockets.</div>
        <button className="ws-start-btn" onClick={startPipeline} disabled={starting}>
          {starting ? 'Démarrage...' : '🚀 Démarrer la pipeline'}
        </button>
      </div>
    );
  }

  return (
    <div className="ws-status">
      <div className="ws-status-header">
        <div>
          <div className="ws-status-title">Pings WebSocket</div>
          <div className="ws-status-subtitle">Flux temps réel des collecteurs</div>
        </div>
        <div className="ws-runtime">
          ⏱️ {pipelineStatus.runtime_seconds ? `${Math.floor(pipelineStatus.runtime_seconds / 60)}m` : '—'}
        </div>
      </div>
      <div className="ws-status-grid">
        {collectorEntries.length === 0 && (
          <div className="ws-status-message">Aucun collecteur actif. Configurez-en dans pipeline_config.json.</div>
        )}
        {collectorEntries.map(([name, info]) => (
          <div key={name} className="ws-card">
            <div className="ws-card-header">
              <span className={statusBadgeClass(info.status)}>{info.status || 'unknown'}</span>
              <div className="ws-card-name">{name}</div>
            </div>
            <div className="ws-card-body">
              <div className="ws-row">
                <span>Dernier ping</span>
                <strong>{formatAgo(info.last_message)}</strong>
              </div>
              <div className="ws-row">
                <span>Messages</span>
                <strong>{info.messages ?? 0}</strong>
              </div>
              <div className="ws-row">
                <span>Symboles</span>
                <strong>{info.symbols ?? '—'}</strong>
              </div>
              {info.error && <div className="ws-error">⚠️ {info.error}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default WebsocketStatus;
