import React, { useState, useEffect, useCallback } from 'react';
import { API_BASE_URL } from '../config/api';

interface CollectionStat {
  name: string;
  display: string;
  icon: string;
  count: number;
  last_update: string | null;
  status: string;
  error?: string;
}

interface MongoStats {
  mongo_connected: boolean;
  collections: CollectionStat[];
  timestamp: string;
  error?: string;
}

interface OHLCVInfo {
  total_records?: number;
  date_from?: string;
  date_to?: string;
  symbols?: string[];
}

const relTime = (iso: string | null): string => {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 0)   return 'à venir';
  if (diff < 60)  return `${diff.toFixed(0)}s`;
  if (diff < 3600) return `${(diff / 60).toFixed(0)}m`;
  if (diff < 86400) return `${(diff / 3600).toFixed(1)}h`;
  return `${(diff / 86400).toFixed(1)}j`;
};

const freshnessColor = (iso: string | null): string => {
  if (!iso) return 'badge-dim';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 300)   return 'badge-green';
  if (diff < 3600)  return 'badge-amber';
  if (diff < 86400) return 'badge-red';
  return 'badge-red';
};

const fmt = (n: number) =>
  n >= 1_000_000 ? (n / 1_000_000).toFixed(2) + 'M'
  : n >= 1_000   ? (n / 1_000).toFixed(1) + 'K'
  : n.toString();

const DataPipelineView: React.FC = () => {
  const [stats, setStats]         = useState<MongoStats | null>(null);
  const [ohlcv, setOhlcv]         = useState<OHLCVInfo | null>(null);
  const [loading, setLoading]     = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/data/mongodb/stats`);
      if (res.ok) {
        const data = await res.json();
        setStats(data);

        // Compute OHLCV info depuis MongoDB (pas S3)
        const collections: CollectionStat[] = data.collections || [];
        const enrichedCandidates = collections.filter(c =>
          c.name === 'historical_ohlcv_enriched'
          || c.name.startsWith('historical_ohlcv_enriched_')
          || c.display.toLowerCase().includes('enrichi')
        );
        const dedicatedCollections = enrichedCandidates.filter(c => c.name.startsWith('historical_ohlcv_enriched_'));
        const enrichedCollections = dedicatedCollections.length ? dedicatedCollections : enrichedCandidates;
        const coll1h = enrichedCollections[0] || collections.find(c => c.name === 'historical_ohlcv');
        const coll1m = collections.find(c => c.name === 'ohlcv_1m');
        const dedicatedSymbols = dedicatedCollections
          .map(c => c.name.replace('historical_ohlcv_enriched_', '').toUpperCase());
        setOhlcv({
          total_records: (enrichedCollections.length
            ? enrichedCollections.reduce((sum, c) => sum + (c.count || 0), 0)
            : (coll1h?.count || 0)) + (coll1m?.count || 0),
          date_from:     '2017-08-17T00:00:00',
          date_to:       new Date().toISOString(),
          symbols:       dedicatedSymbols.length
            ? dedicatedSymbols
            : ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'],
        });
      }
    } catch (e) {
      console.error('DataPipeline load error:', e);
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);

  const totalRecords = stats?.collections.reduce((a, c) => a + (c.count || 0), 0) || 0;
  const okCount      = stats?.collections.filter(c => c.status === 'ok').length || 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }} className="animate-fadeIn">

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span className={`badge ${stats?.mongo_connected ? 'badge-green' : 'badge-red'}`}>
            ● {stats?.mongo_connected ? 'MongoDB OK' : 'MongoDB KO'}
          </span>
          {loading && <span className="spinner" />}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load}>↻ Refresh</button>
      </div>

      {/* Quick stats */}
      <div className="stat-grid">
        <div className="stat-tile">
          <div className="stat-label">Total records</div>
          <div className="stat-value blue">{fmt(totalRecords)}</div>
          <div className="stat-sub">toutes collections</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Collections OK</div>
          <div className="stat-value green">{okCount}</div>
          <div className="stat-sub">sur {stats?.collections.length || 0}</div>
        </div>
        {ohlcv && (
          <>
            <div className="stat-tile">
              <div className="stat-label">OHLCV bars</div>
              <div className="stat-value blue">{fmt(ohlcv.total_records || 0)}</div>
              <div className="stat-sub">1-minute BTC</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Période</div>
              <div className="stat-value" style={{ fontSize: '0.85rem' }}>
                {ohlcv.date_from ? new Date(ohlcv.date_from).getFullYear() : '?'}
                {' – '}
                {ohlcv.date_to   ? new Date(ohlcv.date_to).getFullYear()   : '?'}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Collections table */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">⬡ Collections MongoDB</div>
          <span style={{ fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
            Maj {lastRefresh.toLocaleTimeString()}
          </span>
        </div>

        {!stats ? (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--txt-muted)' }}>
            <div className="spinner" style={{ margin: '0 auto 0.75rem' }} />
            Connexion MongoDB…
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Collection</th>
                <th>Records</th>
                <th>Dernière MAJ</th>
                <th>Fraîcheur</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {stats.collections.map(c => (
                <tr key={c.name}>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span>{c.icon}</span>
                      <div>
                        <div style={{ fontWeight: 500, color: 'var(--txt)' }}>{c.display}</div>
                        <div style={{ fontSize: '0.65rem', color: 'var(--txt-muted)' }}>{c.name}</div>
                      </div>
                    </div>
                  </td>
                  <td style={{ fontWeight: 600, color: c.count > 0 ? 'var(--txt)' : 'var(--txt-muted)' }}>
                    {fmt(c.count)}
                  </td>
                  <td style={{ color: 'var(--txt-muted)', fontSize: '0.75rem' }}>
                    {c.last_update
                      ? new Date(c.last_update).toLocaleString()
                      : <span style={{ color: 'var(--txt-dim)' }}>—</span>
                    }
                  </td>
                  <td>
                    <span className={`badge ${freshnessColor(c.last_update)}`}>
                      {relTime(c.last_update)}
                    </span>
                  </td>
                  <td>
                    {c.status === 'ok' ? (
                      <span className="badge badge-green">OK</span>
                    ) : (
                      <span className="badge badge-red" title={c.error}>Erreur</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* OHLCV detail */}
      {ohlcv && ohlcv.symbols && (
        <div className="card">
          <div className="card-header">
            <div className="card-title">📊 Symbols OHLCV disponibles</div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
            {ohlcv.symbols.map(s => (
              <span key={s} className="badge badge-blue">{s}</span>
            ))}
          </div>
        </div>
      )}

      {/* Pipeline flow diagram */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">⬡ Flux de données</div>
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.25rem',
          flexWrap: 'wrap',
          fontSize: '0.75rem',
          padding: '0.5rem 0',
        }}>
          {[
            { label: 'Exchanges\n(Binance)', icon: '🏦', color: '#3b82f6' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'OHLCV\nCollector', icon: '📊', color: '#10b981' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'MongoDB', icon: '🗄️', color: '#f59e0b' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'Feature\nEngineering', icon: '⚙️', color: '#8b5cf6' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'ML Model\n(Transformer)', icon: '🤖', color: '#ec4899' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'Signal\nLive', icon: '▲', color: '#10b981' },
          ].map((step, i) =>
            step.label === '→' ? (
              <span key={i} style={{ color: step.color, fontSize: '1rem', padding: '0 0.25rem' }}>→</span>
            ) : (
              <div key={i} style={{
                background: 'var(--bg2)',
                border: `1px solid ${step.color}33`,
                borderRadius: 'var(--radius)',
                padding: '0.4rem 0.75rem',
                textAlign: 'center',
                color: step.color,
                fontSize: '0.7rem',
                lineHeight: 1.4,
                fontWeight: 500,
              }}>
                <div style={{ fontSize: '1rem', marginBottom: '0.15rem' }}>{step.icon}</div>
                {step.label.split('\n').map((l, j) => <div key={j}>{l}</div>)}
              </div>
            )
          )}
        </div>
      </div>

      {/* News pipeline */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">📰 Pipeline Actualités</div>
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.25rem',
          flexWrap: 'wrap',
          fontSize: '0.75rem',
          padding: '0.5rem 0',
        }}>
          {[
            { label: 'CoinDesk\nCointelegraph\nDecrypt', icon: '🌐', color: '#3b82f6' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'Scrapy\nSpiders', icon: '🕷️', color: '#f59e0b' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'market_intel\n(MongoDB)', icon: '🗄️', color: '#10b981' },
            { label: '→', icon: '', color: 'var(--txt-dim)' },
            { label: 'Sentiment\nAnalysis', icon: '🧠', color: '#8b5cf6' },
          ].map((step, i) =>
            step.label === '→' ? (
              <span key={i} style={{ color: step.color, fontSize: '1rem', padding: '0 0.25rem' }}>→</span>
            ) : (
              <div key={i} style={{
                background: 'var(--bg2)',
                border: `1px solid ${step.color}33`,
                borderRadius: 'var(--radius)',
                padding: '0.4rem 0.75rem',
                textAlign: 'center',
                color: step.color,
                fontSize: '0.7rem',
                lineHeight: 1.4,
                fontWeight: 500,
              }}>
                <div style={{ fontSize: '1rem', marginBottom: '0.15rem' }}>{step.icon}</div>
                {step.label.split('\n').map((l, j) => <div key={j}>{l}</div>)}
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
};

export default DataPipelineView;
