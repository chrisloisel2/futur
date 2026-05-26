import React, { useState, useEffect, useCallback } from 'react';
import { API_BASE_URL } from '../config/api';

type View = 'overview' | 'pipeline' | 'spiders' | 'signal' | 'training' | 'paper';

interface Props { onNavigate: (v: View) => void; }

interface SummaryCard {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  onClick?: () => void;
}

interface CollectionStat {
  name: string; display: string; icon: string; count: number;
  last_update: string | null; status: string;
}

interface ScraperInfo {
  name: string; display: string; icon: string;
  category: string; status: string;
}

interface Signal {
  action: string; current_price: number; confidence?: number; regime?: string;
}

const fmt = (n: number) =>
  n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + 'M'
  : n >= 1_000   ? (n / 1_000).toFixed(0) + 'K'
  : n.toString();

const relTime = (iso: string | null) => {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)   return `${diff.toFixed(0)}s`;
  if (diff < 3600) return `${(diff / 60).toFixed(0)}m`;
  if (diff < 86400) return `${(diff / 3600).toFixed(1)}h`;
  return `${(diff / 86400).toFixed(1)}d`;
};

const OverviewDashboard: React.FC<Props> = ({ onNavigate }) => {
  const [signal, setSignal]         = useState<Signal | null>(null);
  const [collections, setCollections] = useState<CollectionStat[]>([]);
  const [scrapers, setScrapers]     = useState<ScraperInfo[]>([]);
  const [trainingJobs, setTrainingJobs] = useState<any[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const load = useCallback(async () => {
    const safe = async (p: Promise<Response>) => {
      try { const r = await p; return r.ok ? r.json() : null; } catch { return null; }
    };

    const [sig, mongo, sc, jobs] = await Promise.all([
      safe(fetch(`${API_BASE_URL}/pipeline/signal`)),
      safe(fetch(`${API_BASE_URL}/data/mongodb/stats`)),
      safe(fetch(`${API_BASE_URL}/scrapers/list`)),
      safe(fetch(`${API_BASE_URL}/training/jobs`)),
    ]);

    if (sig)   setSignal(sig);
    if (mongo) setCollections(mongo.collections || []);
    if (sc)    setScrapers(sc.scrapers || []);
    if (jobs)  setTrainingJobs(jobs.jobs || []);
    setLastRefresh(new Date());
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);

  const runningScrapers = scrapers.filter(s => s.status === 'running').length;
  const activeJobs      = trainingJobs.filter((j: any) => j.status === 'running').length;
  const totalRecords    = collections.reduce((a, c) => a + (c.count || 0), 0);
  const mongoOk         = collections.length > 0 && collections.some(c => c.status === 'ok');

  const quickStats: SummaryCard[] = [
    {
      label: 'Signal',
      value: signal ? signal.action : '—',
      sub: signal ? `$${signal.current_price?.toLocaleString()}` : 'Loading…',
      color: signal?.action === 'LONG' ? 'green' : signal?.action === 'SHORT' ? 'red' : undefined,
      onClick: () => onNavigate('signal'),
    },
    {
      label: 'Records MongoDB',
      value: fmt(totalRecords),
      sub: mongoOk ? 'Connecté' : 'Déconnecté',
      color: mongoOk ? 'green' : 'red',
      onClick: () => onNavigate('pipeline'),
    },
    {
      label: 'Scrapers actifs',
      value: `${runningScrapers} / ${scrapers.length}`,
      sub: 'en cours',
      color: runningScrapers > 0 ? 'green' : undefined,
      onClick: () => onNavigate('spiders'),
    },
    {
      label: 'Training jobs',
      value: activeJobs > 0 ? `${activeJobs} actif` : `${trainingJobs.length} total`,
      sub: activeJobs > 0 ? 'En cours…' : 'Inactif',
      color: activeJobs > 0 ? 'blue' : undefined,
      onClick: () => onNavigate('training'),
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }} className="animate-fadeIn">

      {/* Quick stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: '0.75rem' }}>
        {quickStats.map(s => (
          <div
            key={s.label}
            className="stat-tile"
            style={{ cursor: s.onClick ? 'pointer' : 'default', transition: 'border-color 0.15s' }}
            onClick={s.onClick}
            onMouseEnter={e => s.onClick && ((e.currentTarget as HTMLElement).style.borderColor = 'rgba(59,130,246,0.4)')}
            onMouseLeave={e => s.onClick && ((e.currentTarget as HTMLElement).style.borderColor = '')}
          >
            <div className="stat-label">{s.label}</div>
            <div className={`stat-value ${s.color || ''}`}>{s.value}</div>
            {s.sub && <div className="stat-sub">{s.sub}</div>}
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>

        {/* Data collections */}
        <div className="card">
          <div className="card-header">
            <div className="card-title">⬡ Collections MongoDB</div>
            <button className="btn btn-ghost btn-xs" onClick={() => onNavigate('pipeline')}>Détails →</button>
          </div>
          {collections.length === 0 ? (
            <div style={{ color: 'var(--txt-muted)', fontSize: '0.8rem' }}>Connexion…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {collections.map(c => (
                <div key={c.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.4rem 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span>{c.icon}</span>
                    <span style={{ fontSize: '0.78rem', color: 'var(--txt)' }}>{c.display}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--txt-muted)', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(c.count)}
                    </span>
                    <span className={`badge ${c.status === 'ok' ? 'badge-green' : 'badge-red'}`}>
                      {c.last_update ? relTime(c.last_update) : c.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Scrapers */}
        <div className="card">
          <div className="card-header">
            <div className="card-title">◉ Scrapers</div>
            <button className="btn btn-ghost btn-xs" onClick={() => onNavigate('spiders')}>Gérer →</button>
          </div>
          {scrapers.length === 0 ? (
            <div style={{ color: 'var(--txt-muted)', fontSize: '0.8rem' }}>Chargement…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {scrapers.map(s => (
                <div key={s.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.4rem 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span>{s.icon}</span>
                    <span style={{ fontSize: '0.78rem', color: 'var(--txt)' }}>{s.display}</span>
                  </div>
                  <span className={`badge ${
                    s.status === 'running'   ? 'badge-green' :
                    s.status === 'error'     ? 'badge-red' :
                    s.status === 'completed' ? 'badge-blue' : 'badge-dim'
                  }`}>
                    {s.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Signal detail */}
      {signal && (
        <div className="card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('signal')}>
          <div className="card-header">
            <div className="card-title">▲ Signal Live · BTC/USDT</div>
            <button className="btn btn-ghost btn-xs">Voir signal →</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.75rem' }}>
            {[
              { label: 'Action',    value: signal.action, color: signal.action === 'LONG' ? 'green' : signal.action === 'SHORT' ? 'red' : undefined },
              { label: 'Prix',      value: `$${signal.current_price?.toLocaleString()}` },
              { label: 'Confiance', value: signal.confidence ? `${(signal.confidence * 100).toFixed(1)}%` : '—' },
              { label: 'Régime',    value: signal.regime || '—' },
            ].map(item => (
              <div key={item.label} className="stat-tile">
                <div className="stat-label">{item.label}</div>
                <div className={`stat-value ${(item as any).color || ''}`} style={{ fontSize: '1rem' }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Training jobs */}
      {trainingJobs.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div className="card-title">⬢ Jobs d'entraînement</div>
            <button className="btn btn-ghost btn-xs" onClick={() => onNavigate('training')}>Gérer →</button>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Job ID</th><th>Config</th><th>Statut</th><th>Progrès</th><th>Loss</th>
              </tr>
            </thead>
            <tbody>
              {trainingJobs.slice(0, 5).map((j: any) => (
                <tr key={j.job_id}>
                  <td style={{ color: 'var(--txt-muted)', fontSize: '0.68rem' }}>{j.job_id?.slice(0, 12)}…</td>
                  <td>{j.config || '—'}</td>
                  <td>
                    <span className={`badge ${
                      j.status === 'running'   ? 'badge-green' :
                      j.status === 'completed' ? 'badge-blue' :
                      j.status === 'failed'    ? 'badge-red' : 'badge-dim'
                    }`}>{j.status}</span>
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <div className="progress-bar" style={{ flex: 1 }}>
                        <div className="progress-fill" style={{ width: `${j.progress_pct || 0}%` }} />
                      </div>
                      <span style={{ fontSize: '0.68rem', color: 'var(--txt-muted)' }}>
                        {(j.progress_pct || 0).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                  <td style={{ color: 'var(--txt-muted)' }}>{j.current_loss?.toFixed(4) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ fontSize: '0.65rem', color: 'var(--txt-dim)', textAlign: 'right' }}>
        Mis à jour {lastRefresh.toLocaleTimeString()}
      </div>
    </div>
  );
};

export default OverviewDashboard;
