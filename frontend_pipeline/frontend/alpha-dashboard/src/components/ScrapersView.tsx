import React, { useState, useEffect, useCallback, useRef } from 'react';
import { API_BASE_URL } from '../config/api';

interface ScraperInfo {
  name: string;
  display: string;
  description: string;
  icon: string;
  category: string;
  status: 'running' | 'stopped' | 'error' | 'completed';
  pid: number | null;
  started_at: string | null;
  last_lines: string[];
}

const STATUS_COLORS: Record<string, string> = {
  running:   'badge-green',
  stopped:   'badge-dim',
  error:     'badge-red',
  completed: 'badge-blue',
};

const CATEGORIES: { id: string; label: string }[] = [
  { id: 'all',  label: 'Tous' },
  { id: 'data', label: 'Données' },
  { id: 'news', label: 'Actualités' },
  { id: 'api',  label: 'API' },
];

const relTime = (iso: string | null) => {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)   return `${diff.toFixed(0)}s`;
  if (diff < 3600) return `${(diff / 60).toFixed(0)}min`;
  return `${(diff / 3600).toFixed(1)}h`;
};

const colorLine = (line: string): string => {
  const l = line.toLowerCase();
  if (l.includes('error') || l.includes('exception') || l.includes('critical')) return 'err';
  if (l.includes('warn') || l.includes('warning')) return 'warn';
  if (l.includes('info') || l.includes('starting') || l.includes('loaded')) return 'info';
  if (l.includes('success') || l.includes('inserted') || l.includes('saved') || l.includes('done')) return 'ok';
  return '';
};

const ScrapersView: React.FC = () => {
  const [scrapers, setScrapers]     = useState<ScraperInfo[]>([]);
  const [loading, setLoading]       = useState(true);
  const [category, setCategory]     = useState('all');
  const [selected, setSelected]     = useState<string | null>(null);
  const [logs, setLogs]             = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [busy, setBusy]             = useState<Record<string, boolean>>({});
  const logRef = useRef<HTMLDivElement>(null);

  const loadScrapers = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/scrapers/list`);
      if (r.ok) {
        const data = await r.json();
        setScrapers(data.scrapers || []);
      }
    } catch {}
    setLoading(false);
  }, []);

  const loadLogs = useCallback(async (name: string) => {
    setLogsLoading(true);
    try {
      const r = await fetch(`${API_BASE_URL}/scrapers/${name}/logs?lines=200`);
      if (r.ok) {
        const data = await r.json();
        setLogs(data.logs || []);
        setTimeout(() => { logRef.current?.scrollTo({ top: 99999, behavior: 'smooth' }); }, 50);
      }
    } catch {}
    setLogsLoading(false);
  }, []);

  useEffect(() => {
    loadScrapers();
    const t = setInterval(loadScrapers, 10_000);
    return () => clearInterval(t);
  }, [loadScrapers]);

  useEffect(() => {
    if (selected) {
      loadLogs(selected);
      const t = setInterval(() => loadLogs(selected), 5_000);
      return () => clearInterval(t);
    }
  }, [selected, loadLogs]);

  const startScraper = async (name: string) => {
    setBusy(b => ({ ...b, [name]: true }));
    try {
      const r = await fetch(`${API_BASE_URL}/scrapers/${name}/start`, { method: 'POST' });
      const data = await r.json();
      if (!data.success) alert(data.message);
      await loadScrapers();
    } catch (e) {
      alert(`Erreur: ${e}`);
    } finally {
      setBusy(b => ({ ...b, [name]: false }));
    }
  };

  const stopScraper = async (name: string) => {
    if (!window.confirm(`Arrêter "${name}" ?`)) return;
    setBusy(b => ({ ...b, [name]: true }));
    try {
      await fetch(`${API_BASE_URL}/scrapers/${name}/stop`, { method: 'POST' });
      await loadScrapers();
    } catch (e) {
      alert(`Erreur: ${e}`);
    } finally {
      setBusy(b => ({ ...b, [name]: false }));
    }
  };

  const filtered = scrapers.filter(s => category === 'all' || s.category === category);
  const runningCount = scrapers.filter(s => s.status === 'running').length;

  return (
    <div style={{ display: 'flex', gap: '1rem', height: 'calc(100vh - 140px)' }} className="animate-fadeIn">

      {/* Left panel */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', width: '360px', minWidth: '300px', overflowY: 'auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span className={`badge ${runningCount > 0 ? 'badge-green' : 'badge-dim'}`}>
              {runningCount} actif{runningCount > 1 ? 's' : ''}
            </span>
            {loading && <span className="spinner" />}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={loadScrapers}>↻</button>
        </div>

        {/* Category filter */}
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
          {CATEGORIES.map(cat => (
            <button
              key={cat.id}
              className={`btn btn-xs ${category === cat.id ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setCategory(cat.id)}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {/* Scraper cards */}
        {filtered.map(s => (
          <div
            key={s.name}
            className="card"
            style={{
              cursor: 'pointer',
              border: selected === s.name ? '1px solid var(--blue)' : '1px solid var(--border)',
              transition: 'border-color 0.15s',
            }}
            onClick={() => { setSelected(s.name === selected ? null : s.name); }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.5rem' }}>
              <div style={{ display: 'flex', gap: '0.625rem', alignItems: 'flex-start', flex: 1 }}>
                <span style={{ fontSize: '1.25rem', lineHeight: 1 }}>{s.icon}</span>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.8rem', color: 'var(--txt)', marginBottom: '0.15rem' }}>
                    {s.display}
                  </div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {s.description}
                  </div>
                  {s.pid && (
                    <div style={{ fontSize: '0.65rem', color: 'var(--txt-dim)', marginTop: '0.2rem' }}>
                      PID {s.pid} · {relTime(s.started_at)}
                    </div>
                  )}
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.35rem' }}>
                <span className={`badge ${STATUS_COLORS[s.status] || 'badge-dim'}`}>
                  {s.status === 'running' && <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', marginRight: 3, animation: 'pulse 1.5s infinite' }} />}
                  {s.status}
                </span>
                <div style={{ display: 'flex', gap: '0.25rem' }} onClick={e => e.stopPropagation()}>
                  {s.status !== 'running' ? (
                    <button
                      className="btn btn-success btn-xs"
                      disabled={busy[s.name]}
                      onClick={() => startScraper(s.name)}
                    >
                      {busy[s.name] ? <span className="spinner" style={{ width: 10, height: 10 }} /> : '▶ Start'}
                    </button>
                  ) : (
                    <button
                      className="btn btn-danger btn-xs"
                      disabled={busy[s.name]}
                      onClick={() => stopScraper(s.name)}
                    >
                      {busy[s.name] ? <span className="spinner" style={{ width: 10, height: 10 }} /> : '■ Stop'}
                    </button>
                  )}
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() => { setSelected(s.name); loadLogs(s.name); }}
                  >
                    Logs
                  </button>
                </div>
              </div>
            </div>

            {/* Mini log preview */}
            {s.last_lines.length > 0 && selected !== s.name && (
              <div style={{
                marginTop: '0.6rem',
                padding: '0.4rem 0.5rem',
                background: '#060810',
                borderRadius: 'var(--radius)',
                fontSize: '0.62rem',
                fontFamily: 'monospace',
                color: '#64748b',
                overflow: 'hidden',
                maxHeight: '48px',
                lineHeight: 1.5,
              }}>
                {s.last_lines.slice(-3).map((l, i) => (
                  <div key={i} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l}</div>
                ))}
              </div>
            )}
          </div>
        ))}

        {filtered.length === 0 && !loading && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--txt-muted)' }}>
            Aucun scraper dans cette catégorie
          </div>
        )}
      </div>

      {/* Right panel — logs */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.75rem', minWidth: 0 }}>
        {selected ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                <span style={{ fontSize: '1rem' }}>
                  {scrapers.find(s => s.name === selected)?.icon}
                </span>
                <span style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--txt)' }}>
                  {scrapers.find(s => s.name === selected)?.display} — Logs
                </span>
                {logsLoading && <span className="spinner" />}
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button className="btn btn-ghost btn-sm" onClick={() => loadLogs(selected)}>↻ Refresh</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>✕ Fermer</button>
              </div>
            </div>

            <div
              ref={logRef}
              className="log-terminal"
              style={{ flex: 1, maxHeight: 'none', overflow: 'auto' }}
            >
              {logs.length === 0 ? (
                <div style={{ color: 'var(--txt-dim)', textAlign: 'center', padding: '1rem' }}>
                  {logsLoading ? 'Chargement…' : 'Aucun log disponible. Démarrez le scraper.'}
                </div>
              ) : (
                logs.map((line, i) => (
                  <div key={i} className={`log-line ${colorLine(line)}`}>
                    {line}
                  </div>
                ))
              )}
            </div>

            {/* Controls in log panel */}
            {(() => {
              const s = scrapers.find(x => x.name === selected);
              if (!s) return null;
              return (
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  {s.status !== 'running' ? (
                    <button className="btn btn-success btn-sm" disabled={busy[s.name]} onClick={() => startScraper(s.name)}>
                      ▶ Démarrer
                    </button>
                  ) : (
                    <button className="btn btn-danger btn-sm" disabled={busy[s.name]} onClick={() => stopScraper(s.name)}>
                      ■ Arrêter
                    </button>
                  )}
                  <span className={`badge ${STATUS_COLORS[s.status] || 'badge-dim'}`}>{s.status}</span>
                  {s.pid && <span style={{ fontSize: '0.68rem', color: 'var(--txt-muted)' }}>PID {s.pid}</span>}
                  {s.started_at && <span style={{ fontSize: '0.68rem', color: 'var(--txt-muted)' }}>Démarré il y a {relTime(s.started_at)}</span>}
                </div>
              );
            })()}
          </>
        ) : (
          <div style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexDirection: 'column',
            gap: '0.75rem',
            color: 'var(--txt-muted)',
          }}>
            <div style={{ fontSize: '2.5rem', opacity: 0.3 }}>◉</div>
            <div style={{ fontSize: '0.875rem' }}>Sélectionnez un scraper pour voir ses logs</div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ScrapersView;
