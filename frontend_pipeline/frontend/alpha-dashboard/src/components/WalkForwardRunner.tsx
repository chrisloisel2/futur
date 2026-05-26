import React, { useState, useEffect, useRef, useCallback } from 'react';
import { API_BASE_URL } from '../config/api';

// ─── Types ────────────────────────────────────────────────────────────────────

interface Asset { symbol: string; file: string; size_mb: number; }

interface WFJob {
  job_id: string;
  pipeline: 'long' | 'short';
  status: 'running' | 'completed' | 'failed' | 'stopped';
  start_time: string;
  end_time: string | null;
  config: Record<string, any>;
  error: string | null;
  result: WFResult | null;
}

interface FoldRow {
  year: number;
  // LONG
  long_n?: number;
  long_pf?: number;
  long_roi?: number;       // total_return_pct (position 0.2%)
  long_exp?: number;       // expectancy per trade
  long_ok?: boolean;
  long_cat?: boolean;
  long_auc?: number;
  // SHORT
  short_n?: number;
  short_pf?: number;
  short_roi?: number;      // total_return_pct (position 0.1%)
  short_exp?: number;
  short_ok?: boolean;
  short_cat?: boolean;
  short_auc?: number;
  short_skip?: boolean;
  // Combined
  combined_roi?: number;
}

interface WFResult {
  verdict?: string;
  deployable?: boolean;
  n_folds_ok?: number; n_ok?: number;
  n_catastrophic?: number;
  pf_median?: number; median_pf?: number;
  n_total_trades?: number; total_trades?: number;
  fold_results?: any[];
  folds?: any[];
  pf_stress_median?: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const ALL_YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
const SHORT_ONLY_YEARS = [2022, 2023, 2024, 2025, 2026];

const MONO: React.CSSProperties = { fontFamily: "'JetBrains Mono','Fira Code',monospace", fontSize: 12 };

const pfColor = (v: number) =>
  v >= 1.5 ? '#10b981' : v >= 1.2 ? '#60a5fa' : v >= 1.0 ? '#f59e0b' : '#ef4444';

const roiColor = (v: number) => v > 0 ? '#10b981' : '#ef4444';

const card = (extra?: React.CSSProperties): React.CSSProperties => ({
  background: '#0f172a', border: '1px solid #1e293b',
  borderRadius: 8, padding: '14px 18px', ...extra,
});

const lbl: React.CSSProperties = {
  fontSize: 10, color: '#475569', textTransform: 'uppercase',
  letterSpacing: '0.08em', marginBottom: 5, display: 'block',
};

const inp: React.CSSProperties = {
  ...MONO, background: '#1e293b', border: '1px solid #334155',
  borderRadius: 6, color: '#e2e8f0', padding: '5px 9px',
  width: '100%', boxSizing: 'border-box',
};

const chipStyle = (on: boolean, color = '#3b82f6'): React.CSSProperties => ({
  ...MONO, padding: '3px 9px', borderRadius: 4, cursor: 'pointer',
  background: on ? `${color}22` : '#1e293b',
  border: `1px solid ${on ? color : '#334155'}`,
  color: on ? color : '#64748b', userSelect: 'none', fontSize: 11,
});

const relTime = (iso: string | null) => {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  return `${(s / 3600).toFixed(1)}h`;
};

// ─── Normalize fold rows from LONG + SHORT results ────────────────────────────

function buildFoldRows(longResult: WFResult | null, shortResult: WFResult | null): FoldRow[] {
  const map: Record<number, FoldRow> = {};

  const longFolds = longResult?.folds || longResult?.fold_results || [];
  for (const f of longFolds) {
    const yr: number = f.year ?? f.fold_year;
    if (!yr) continue;
    map[yr] = map[yr] ?? { year: yr };
    map[yr].long_n   = f.n ?? f.n_trades;
    map[yr].long_pf  = f.pf;
    map[yr].long_roi = f.total_return_pct;
    map[yr].long_exp = f.expectancy;
    map[yr].long_ok  = f.ok ?? f.fold_ok;
    map[yr].long_cat = f.catastrophic ?? f.fold_catastrophic;
    map[yr].long_auc = f.fleet_auc_mean ?? f.ens_auc_val;
  }

  const shortFolds = shortResult?.fold_results || shortResult?.folds || [];
  for (const f of shortFolds) {
    const yr: number = f.fold_year ?? f.year;
    if (!yr) continue;
    map[yr] = map[yr] ?? { year: yr };
    const skip = f.status === 'SKIPPED' || f.fold_status === 'SKIPPED';
    map[yr].short_n    = skip ? undefined : (f.n_trades ?? f.n);
    map[yr].short_pf   = skip ? undefined : f.pf;
    map[yr].short_roi  = skip ? undefined : f.total_return_pct;
    map[yr].short_exp  = skip ? undefined : f.expectancy;
    map[yr].short_ok   = !skip && (f.fold_ok ?? f.ok);
    map[yr].short_cat  = !skip && (f.fold_catastrophic ?? f.catastrophic);
    map[yr].short_skip = skip;
  }

  return Object.values(map)
    .map(r => ({
      ...r,
      combined_roi: (r.long_roi ?? 0) + (r.short_roi ?? 0),
    }))
    .sort((a, b) => a.year - b.year);
}

// ─── ROI bar (CSS) ───────────────────────────────────────────────────────────

const RoiBar: React.FC<{ value: number; maxAbs: number; color: string; label: string }> = ({ value, maxAbs, color, label }) => {
  if (value === 0 || !maxAbs) return <span style={{ color: '#334155', fontSize: 11 }}>—</span>;
  const pct = Math.abs(value) / maxAbs * 100;
  const neg = value < 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {neg && <span style={{ color: '#ef4444', fontSize: 11, minWidth: 38, textAlign: 'right' }}>{value.toFixed(2)}%</span>}
      <div style={{
        height: 14, width: `${pct}%`, minWidth: 2, maxWidth: '100%',
        background: neg ? '#ef4444' : color,
        borderRadius: 2, opacity: 0.85,
        boxShadow: neg ? 'none' : `0 0 4px ${color}44`,
      }} />
      {!neg && <span style={{ color, fontSize: 11, minWidth: 38 }}>{value.toFixed(2)}%</span>}
    </div>
  );
};

// ─── Log viewer ──────────────────────────────────────────────────────────────

const LogViewer: React.FC<{ lines: string[]; running: boolean }> = ({ lines, running }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines]);
  return (
    <div ref={ref} style={{
      ...MONO, background: '#020617', border: '1px solid #0f172a',
      borderRadius: 6, padding: 12, height: 320, overflowY: 'auto',
      color: '#94a3b8', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
    }}>
      {lines.length === 0
        ? <span style={{ color: '#334155' }}>{running ? 'Démarrage…' : 'Aucun log'}</span>
        : lines.map((line, i) => {
            const c =
              /ERROR|ERREUR/.test(line) ? '#ef4444' :
              /WARN/.test(line) ? '#f59e0b' :
              /✓|OK|CANDIDATE|DEPLOYABLE/.test(line) ? '#10b981' :
              /FOLD|fold_year/.test(line) ? '#60a5fa' :
              /lgbm|fleet|TRM/.test(line) ? '#a78bfa' :
              /SMOTE|AUC|adaptive/.test(line) ? '#34d399' :
              '#94a3b8';
            return <div key={i} style={{ color: c }}>{line || ' '}</div>;
          })}
      {running && <span style={{ color: '#10b981' }}>▋</span>}
    </div>
  );
};

// ─── Asset picker ─────────────────────────────────────────────────────────────

const AssetPicker: React.FC<{ assets: Asset[]; selected: string[]; onChange: (s: string[]) => void }> = ({ assets, selected, onChange }) => {
  const toggle = (sym: string) =>
    onChange(selected.includes(sym) ? selected.filter(s => s !== sym) : [...selected, sym]);
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
        <button onClick={() => onChange(assets.map(a => a.symbol))} style={{ ...chipStyle(false), color: '#94a3b8' }}>Tout</button>
        <button onClick={() => onChange([])} style={{ ...chipStyle(false), color: '#94a3b8' }}>Aucun</button>
        <span style={{ marginLeft: 'auto', color: '#60a5fa', fontSize: 11 }}>{selected.length}/{assets.length}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, maxHeight: 140, overflowY: 'auto' }}>
        {assets.map(a => (
          <button key={a.symbol} onClick={() => toggle(a.symbol)} style={{ ...chipStyle(selected.includes(a.symbol)), fontSize: 10, padding: '2px 7px' }}>
            {a.symbol}
          </button>
        ))}
      </div>
    </div>
  );
};

// ─── Main component ───────────────────────────────────────────────────────────

type Pipe = 'long' | 'short';
type TabId = 'config' | 'logs_long' | 'logs_short' | 'results' | 'roi' | 'canonical';

interface CanonicalData {
  found: boolean;
  source: string | null;
  pipeline: string;
  result: WFResult | null;
}

// Presets temporels — fenêtres d'analyse prédéfinies
const TEMPORAL_PRESETS: { label: string; years: number[]; desc: string }[] = [
  { label: 'Tout', years: ALL_YEARS, desc: '7 folds — 2020→2026' },
  { label: 'Bear', years: [2022, 2023], desc: 'Marché baissier 2022-23' },
  { label: 'Récent', years: [2024, 2025, 2026], desc: 'Dernières 3 années' },
  { label: 'Hors 2021', years: [2020, 2022, 2023, 2024, 2025, 2026], desc: 'Sans le bull extreme' },
];

export default function WalkForwardRunner() {
  // Pipeline multi-select
  const [pipes, setPipes] = useState<Set<Pipe>>(new Set<Pipe>(['short']));

  // Shared folds
  const [folds, setFolds] = useState<number[]>(SHORT_ONLY_YEARS);

  // SHORT params
  const [maxAssets, setMaxAssets] = useState(50);
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [useLgbm, setUseLgbm] = useState(true);
  const [useTransformer, setUseTransformer] = useState(false);
  const [maxEpochs, setMaxEpochs] = useState(40);

  // Data
  const [assets, setAssets] = useState<Asset[]>([]);

  // Jobs
  const [jobLong, setJobLong] = useState<WFJob | null>(null);
  const [jobShort, setJobShort] = useState<WFJob | null>(null);
  const [logsLong, setLogsLong] = useState<string[]>([]);
  const [logsShort, setLogsShort] = useState<string[]>([]);
  const [history, setHistory] = useState<WFJob[]>([]);

  // Résultat canonique validé (walk_forward_4h.json)
  const [canonical, setCanonical] = useState<CanonicalData | null>(null);

  // UI
  const [tab, setTab] = useState<TabId>('canonical');
  const [error, setError] = useState<string | null>(null);
  const pollLong = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollShort = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Toggle pipeline ────────────────────────────────────────────────────────
  const togglePipe = (p: Pipe) => {
    setPipes(prev => {
      const next = new Set(prev);
      if (next.has(p)) { if (next.size > 1) next.delete(p); }
      else next.add(p);
      // Ajuster les folds selon la sélection
      return next;
    });
  };

  // Adapter les folds proposés : si LONG sans SHORT → tous les ans; si SHORT → SHORT_ONLY_YEARS
  const availableYears = pipes.has('long') && !pipes.has('short') ? ALL_YEARS : SHORT_ONLY_YEARS;

  // ── Load assets ────────────────────────────────────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE_URL}/wf/assets`)
      .then(r => r.json())
      .then(d => { setAssets(d.assets || []); setSelectedAssets((d.assets || []).map((a: Asset) => a.symbol)); })
      .catch(() => {});
  }, []);

  const loadHistory = useCallback(async () => {
    const d = await fetch(`${API_BASE_URL}/wf/jobs`).then(r => r.json()).catch(() => ({ jobs: [] }));
    setHistory(d.jobs || []);
  }, []);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  // ── Charger le résultat canonique validé au montage ───────────────────────
  useEffect(() => {
    fetch(`${API_BASE_URL}/wf/canonical`)
      .then(r => r.json())
      .then((d: CanonicalData) => setCanonical(d))
      .catch(() => {});
  }, []);

  // ── Polling helpers ────────────────────────────────────────────────────────
  const stopPoll = (ref: React.MutableRefObject<ReturnType<typeof setInterval> | null>) => {
    if (ref.current) { clearInterval(ref.current); ref.current = null; }
  };

  const startPoll = useCallback((jobId: string, pipe: Pipe) => {
    const ref = pipe === 'long' ? pollLong : pollShort;
    stopPoll(ref);
    ref.current = setInterval(async () => {
      try {
        const [lr, rr] = await Promise.all([
          fetch(`${API_BASE_URL}/wf/logs/${jobId}?lines=500`).then(r => r.json()),
          fetch(`${API_BASE_URL}/wf/results/${jobId}`).then(r => r.json()),
        ]);
        if (pipe === 'long') setLogsLong(lr.lines || []);
        else setLogsShort(lr.lines || []);
        if (rr.status !== 'running') {
          const setter = pipe === 'long' ? setJobLong : setJobShort;
          setter(prev => prev ? { ...prev, status: rr.status, end_time: rr.end_time, result: rr.result } : prev);
          stopPoll(ref);
          loadHistory();
        }
      } catch { /* ignore */ }
    }, 2000);
  }, [loadHistory]);

  useEffect(() => () => { stopPoll(pollLong); stopPoll(pollShort); }, []);

  // ── Start ─────────────────────────────────────────────────────────────────
  const handleStart = async () => {
    setError(null);
    setLogsLong([]); setLogsShort([]);
    setTab('logs_long');

    const startPipe = async (pipe: Pipe) => {
      const body: Record<string, any> = {
        pipeline: pipe,
        folds,
        max_assets: Math.min(maxAssets, selectedAssets.length),
        use_transformer: useTransformer,
        use_lgbm: useLgbm,
        max_epochs: maxEpochs,
      };
      const d = await fetch(`${API_BASE_URL}/wf/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(r => r.json());
      if (!d.success) throw new Error(d.detail || `Erreur démarrage ${pipe}`);
      const job: WFJob = {
        job_id: d.job_id, pipeline: pipe, status: 'running',
        start_time: new Date().toISOString(), end_time: null,
        config: body, error: null, result: null,
      };
      if (pipe === 'long') setJobLong(job);
      else setJobShort(job);
      startPoll(d.job_id, pipe);
      return job;
    };

    try {
      const launches = Array.from(pipes).map(p => startPipe(p));
      await Promise.all(launches);
      loadHistory();
      // Ouvrir le log du premier pipeline sélectionné
      setTab(pipes.has('long') ? 'logs_long' : 'logs_short');
    } catch (e: any) {
      setError(e.message || 'Erreur inconnue');
    }
  };

  // ── Stop ──────────────────────────────────────────────────────────────────
  const handleStop = async (pipe: Pipe) => {
    const job = pipe === 'long' ? jobLong : jobShort;
    if (!job) return;
    await fetch(`${API_BASE_URL}/wf/stop/${job.job_id}`, { method: 'POST' }).catch(() => {});
    stopPoll(pipe === 'long' ? pollLong : pollShort);
    const setter = pipe === 'long' ? setJobLong : setJobShort;
    setter(prev => prev ? { ...prev, status: 'stopped', end_time: new Date().toISOString() } : null);
    loadHistory();
  };

  // ── Resume from history ───────────────────────────────────────────────────
  const handleResume = async (job: WFJob) => {
    const setter = job.pipeline === 'long' ? setJobLong : setJobShort;
    setter(job);
    if (job.pipeline === 'long') setLogsLong([]);
    else setLogsShort([]);
    const lr = await fetch(`${API_BASE_URL}/wf/logs/${job.job_id}?lines=500`).then(r => r.json()).catch(() => ({ lines: [] }));
    if (job.pipeline === 'long') setLogsLong(lr.lines || []);
    else setLogsShort(lr.lines || []);
    if (job.status === 'running') startPoll(job.job_id, job.pipeline);
    setTab(job.pipeline === 'long' ? 'logs_long' : 'logs_short');
  };

  // ── Derived ───────────────────────────────────────────────────────────────
  const isRunning = (p: Pipe) => (p === 'long' ? jobLong : jobShort)?.status === 'running';
  const anyRunning = isRunning('long') || isRunning('short');

  const longResult = jobLong?.result ?? null;
  const shortResult = jobShort?.result ?? null;
  const hasResults = longResult !== null || shortResult !== null;

  const foldRows = buildFoldRows(
    (pipes.has('long') ? longResult : null),
    (pipes.has('short') ? shortResult : null),
  );

  const maxRoi = Math.max(
    ...foldRows.map(r => Math.abs(r.combined_roi ?? 0)),
    ...foldRows.map(r => Math.abs(r.long_roi ?? 0)),
    ...foldRows.map(r => Math.abs(r.short_roi ?? 0)),
    0.1,
  );

  const totalLongRoi = foldRows.reduce((s, r) => s + (r.long_roi ?? 0), 0);
  const totalShortRoi = foldRows.reduce((s, r) => s + (r.short_roi ?? 0), 0);
  const totalCombinedRoi = totalLongRoi + totalShortRoi;

  // Equity curve (multiplicative compounding per fold)
  const equityCurve = foldRows.reduce<{ year: number; equity: number }[]>((acc, r) => {
    const prev = acc.length > 0 ? acc[acc.length - 1].equity : 1.0;
    const combined_pct = (r.combined_roi ?? 0) / 100;
    acc.push({ year: r.year, equity: prev * (1 + combined_pct) });
    return acc;
  }, []);

  const maxEquity = Math.max(...equityCurve.map(e => e.equity), 1.0);

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: 16, maxWidth: 1280, margin: '0 auto', ...MONO }}>
      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 14 }}>

        {/* ── LEFT ──────────────────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Pipeline multi-select */}
          <div style={card()}>
            <span style={lbl}>Pipelines</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(['long', 'short'] as Pipe[]).map(p => {
                const on = pipes.has(p);
                const job = p === 'long' ? jobLong : jobShort;
                const st = job?.status;
                return (
                  <button
                    key={p}
                    onClick={() => togglePipe(p)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '9px 12px', borderRadius: 7, cursor: 'pointer',
                      background: on ? `rgba(${p === 'long' ? '16,185,129' : '96,165,250'},0.08)` : '#1e293b',
                      border: `1px solid ${on ? (p === 'long' ? '#10b981' : '#60a5fa') : '#334155'}`,
                      color: on ? (p === 'long' ? '#10b981' : '#60a5fa') : '#475569',
                      textAlign: 'left',
                    }}
                  >
                    <span style={{ fontSize: 16 }}>{p === 'long' ? '▲' : '▼'}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{p.toUpperCase()}</div>
                      <div style={{ fontSize: 10, opacity: 0.7, marginTop: 1 }}>
                        {p === 'long' ? 'TRM Fleet v2 · 50 actifs · 4h' : 'LightGBM+TRM · bear-gate · 4h'}
                      </div>
                    </div>
                    {st && (
                      <span style={{
                        fontSize: 10, padding: '2px 6px', borderRadius: 3,
                        background: st === 'running' ? '#10b98133' : st === 'completed' ? '#60a5fa33' : '#ef444433',
                        color: st === 'running' ? '#10b981' : st === 'completed' ? '#60a5fa' : '#ef4444',
                      }}>{st === 'running' ? '●' : st === 'completed' ? '✓' : '✗'} {st}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Folds */}
          <div style={card()}>
            <span style={lbl}>Fenêtre temporelle</span>
            {/* Presets */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
              {TEMPORAL_PRESETS.filter(p => pipes.has('long') || p.years.every(y => SHORT_ONLY_YEARS.includes(y))).map(preset => {
                const active = preset.years.length === folds.length && preset.years.every(y => folds.includes(y));
                return (
                  <button
                    key={preset.label}
                    onClick={() => setFolds([...preset.years].sort())}
                    title={preset.desc}
                    style={{ ...chipStyle(active, '#a78bfa'), fontSize: 10 }}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>
            {/* Sélection individuelle */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {availableYears.map(y => (
                <button key={y} onClick={() => setFolds(prev => prev.includes(y) ? prev.filter(f => f !== y) : [...prev, y].sort())}
                  style={chipStyle(folds.includes(y))}>
                  {y}
                </button>
              ))}
            </div>
            {folds.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 10, color: '#475569' }}>
                {folds.length} fold{folds.length > 1 ? 's' : ''} sélectionné{folds.length > 1 ? 's' : ''} : {folds.join(', ')}
              </div>
            )}
          </div>

          {/* SHORT params (affiché si SHORT sélectionné) */}
          {pipes.has('short') && (
            <>
              <div style={card()}>
                <span style={lbl}>SHORT — Actifs</span>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 11, color: '#64748b' }}>Max :</span>
                  <input type="number" min={1} max={52} value={maxAssets}
                    onChange={e => setMaxAssets(+e.target.value)}
                    style={{ ...inp, width: 60 }} />
                </div>
                <AssetPicker assets={assets} selected={selectedAssets} onChange={setSelectedAssets} />
              </div>
              <div style={card()}>
                <span style={lbl}>SHORT — Modèles</span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: useLgbm ? '#10b981' : '#64748b', fontSize: 12 }}>
                    <input type="checkbox" checked={useLgbm} onChange={e => setUseLgbm(e.target.checked)} />
                    LightGBM · w=0.65 (primary)
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: useTransformer ? '#a78bfa' : '#64748b', fontSize: 12 }}>
                    <input type="checkbox" checked={useTransformer} onChange={e => setUseTransformer(e.target.checked)} />
                    Transformer · w=0.00 (lent)
                  </label>
                  {useTransformer && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>Epochs :</span>
                      <input type="number" min={5} max={200} value={maxEpochs}
                        onChange={e => setMaxEpochs(+e.target.value)}
                        style={{ ...inp, width: 60 }} />
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleStart}
              disabled={anyRunning || folds.length === 0 || pipes.size === 0}
              style={{
                flex: 1, padding: '10px 0', borderRadius: 6, fontWeight: 700, ...MONO, fontSize: 13,
                background: anyRunning ? '#1e293b' : 'rgba(16,185,129,0.12)',
                border: `1px solid ${anyRunning ? '#334155' : '#10b981'}`,
                color: anyRunning ? '#475569' : '#10b981',
                cursor: anyRunning ? 'not-allowed' : 'pointer',
              }}
            >
              {anyRunning ? '● En cours…' : `▶  Lancer ${Array.from(pipes).map(p => p.toUpperCase()).join(' + ')}`}
            </button>
          </div>
          {(isRunning('long') || isRunning('short')) && (
            <div style={{ display: 'flex', gap: 8 }}>
              {isRunning('long') && (
                <button onClick={() => handleStop('long')} style={{ flex: 1, padding: '7px 0', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid #ef4444', color: '#ef4444', cursor: 'pointer', ...MONO, fontSize: 12 }}>
                  ■ Stop LONG
                </button>
              )}
              {isRunning('short') && (
                <button onClick={() => handleStop('short')} style={{ flex: 1, padding: '7px 0', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid #ef4444', color: '#ef4444', cursor: 'pointer', ...MONO, fontSize: 12 }}>
                  ■ Stop SHORT
                </button>
              )}
            </div>
          )}
          {error && <div style={{ ...card({ borderColor: '#ef4444' }), color: '#ef4444', fontSize: 12 }}>{error}</div>}

          {/* History */}
          {history.length > 0 && (
            <div style={card()}>
              <span style={lbl}>Historique</span>
              <div style={{ maxHeight: 200, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 5 }}>
                {history.slice(0, 12).map(j => (
                  <button key={j.job_id} onClick={() => handleResume(j)} style={{
                    background: 'transparent', border: '1px solid #1e293b', borderRadius: 5,
                    padding: '5px 9px', cursor: 'pointer', textAlign: 'left', ...MONO, fontSize: 11,
                    color: '#64748b',
                  }}>
                    <span style={{ color: j.status === 'completed' ? '#10b981' : j.status === 'failed' ? '#ef4444' : j.status === 'running' ? '#60a5fa' : '#475569', marginRight: 6 }}>●</span>
                    <span style={{ color: j.pipeline === 'long' ? '#10b981' : '#60a5fa', fontWeight: 700 }}>{j.pipeline.toUpperCase()}</span>
                    {' · '}{new Date(j.start_time).toLocaleString('fr-FR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    {j.result && <span style={{ marginLeft: 6, color: '#94a3b8' }}>
                      PF {((j.result.pf_median ?? j.result.median_pf) ?? 0).toFixed(2)}
                    </span>}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── RIGHT ─────────────────────────────────────────────────────── */}
        <div>
          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 12, flexWrap: 'wrap' }}>
            {[
              { id: 'canonical' as TabId, label: `★ Validé${canonical?.found ? '' : ' …'}` },
              { id: 'config' as TabId, label: '⚙ État' },
              ...(pipes.has('long') ? [{ id: 'logs_long' as TabId, label: `▲ Logs LONG${isRunning('long') ? ' ●' : ''}` }] : []),
              ...(pipes.has('short') ? [{ id: 'logs_short' as TabId, label: `▼ Logs SHORT${isRunning('short') ? ' ●' : ''}` }] : []),
              ...(hasResults ? [{ id: 'results' as TabId, label: '📊 Résultats' }] : []),
              ...(hasResults && foldRows.length > 0 ? [{ id: 'roi' as TabId, label: '💰 ROI' }] : []),
            ].map(t => (
              <button key={t.id} onClick={() => setTab(t.id)} style={{
                padding: '6px 13px', borderRadius: 5,
                background: tab === t.id ? '#1e293b' : 'transparent',
                border: `1px solid ${tab === t.id ? '#334155' : 'transparent'}`,
                color: tab === t.id ? '#e2e8f0' : '#64748b', cursor: 'pointer', ...MONO, fontSize: 12,
              }}>{t.label}</button>
            ))}
          </div>

          {/* ── Status tab ────────────────────────────────────────────── */}
          {tab === 'config' && (
            <div style={card()}>
              {(pipes.has('long') || pipes.has('short')) ? (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {(['long', 'short'] as Pipe[]).filter(p => pipes.has(p)).map(p => {
                    const job = p === 'long' ? jobLong : jobShort;
                    const col = p === 'long' ? '#10b981' : '#60a5fa';
                    return (
                      <div key={p} style={{ flex: 1, minWidth: 220 }}>
                        <div style={{ color: col, fontWeight: 700, marginBottom: 8 }}>{p.toUpperCase()} pipeline</div>
                        {!job ? (
                          <div style={{ color: '#334155', fontSize: 11 }}>Non lancé</div>
                        ) : (
                          <>
                            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                              <span style={{ ...chipStyle(job.status === 'running', col), cursor: 'default' }}>{job.status}</span>
                              <span style={{ ...chipStyle(false), cursor: 'default', color: '#94a3b8' }}>{relTime(job.start_time)}</span>
                            </div>
                            <pre style={{ margin: 0, color: '#475569', fontSize: 10, overflow: 'auto', maxHeight: 200 }}>
                              {JSON.stringify(job.config, null, 2)}
                            </pre>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ color: '#334155', textAlign: 'center', padding: '20px 0' }}>
                  Sélectionnez un pipeline et cliquez ▶ Lancer
                </div>
              )}
            </div>
          )}

          {/* ── Résultat canonique validé ─────────────────────────── */}
          {tab === 'canonical' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {!canonical ? (
                <div style={{ ...card(), color: '#475569', fontSize: 12 }}>Chargement…</div>
              ) : !canonical.found || !canonical.result ? (
                <div style={{ ...card({ borderColor: '#334155' }), color: '#475569', fontSize: 12 }}>
                  Aucun résultat validé trouvé dans reports/walk_forward_4h/walk_forward_4h.json
                </div>
              ) : (() => {
                const r = canonical.result!;
                const nOk = r.n_ok ?? r.n_folds_ok ?? 0;
                const pfMed = r.median_pf ?? r.pf_median ?? 0;
                const nTrades = r.total_trades ?? r.n_total_trades ?? 0;
                const nCat = r.n_catastrophic ?? 0;
                const deployable = r.deployable ?? false;
                const folds = r.folds || r.fold_results || [];
                return (
                  <>
                    {/* Header */}
                    <div style={card({ borderColor: deployable ? '#10b98133' : '#ef444433' })}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <div>
                          <div style={{ color: '#10b981', fontWeight: 700, fontSize: 14 }}>LONG · TRM Fleet v2 · 50 actifs · 4h</div>
                          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
                            Source : {canonical.source} · Validation scientifique (non modifiable)
                          </div>
                        </div>
                        <span style={{
                          fontSize: 12, fontWeight: 700,
                          color: deployable ? '#10b981' : '#ef4444',
                          padding: '3px 10px', borderRadius: 4,
                          background: deployable ? '#10b98122' : '#ef444422',
                          border: `1px solid ${deployable ? '#10b98144' : '#ef444444'}`,
                        }}>
                          {deployable ? '✓ DEPLOYABLE' : '✗ NOT DEPLOYABLE'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                        {[
                          { label: 'Folds OK', val: `${nOk}/7`, color: nOk >= 5 ? '#10b981' : nOk >= 3 ? '#f59e0b' : '#ef4444' },
                          { label: 'PF médian', val: pfMed.toFixed(3), color: pfColor(pfMed) },
                          { label: 'Trades', val: nTrades, color: '#60a5fa' },
                          { label: 'Catastrophiques', val: nCat, color: nCat > 0 ? '#ef4444' : '#334155' },
                        ].map(m => (
                          <div key={m.label}>
                            <span style={lbl}>{m.label}</span>
                            <span style={{ fontWeight: 700, fontSize: 20, color: m.color }}>{m.val}</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Fold table */}
                    {folds.length > 0 && (
                      <div style={card()}>
                        <span style={lbl}>Résultats par fold</span>
                        <div style={{ overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
                            <thead>
                              <tr style={{ color: '#475569', fontSize: 10 }}>
                                {['Année', 'n', 'PF', 'ROI%', 'Exp%', 'WR%', 'AUC', 'OK'].map(h => (
                                  <th key={h} style={{ padding: '4px 8px', textAlign: h === 'Année' ? 'left' : 'right', borderBottom: '1px solid #1e293b' }}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {folds.map((f: any) => {
                                const yr = f.year ?? f.fold_year;
                                const ok = f.ok ?? f.fold_ok;
                                const cat = f.catastrophic ?? f.fold_catastrophic;
                                const pf = f.pf ?? 0;
                                const roi = f.total_return_pct ?? 0;
                                const exp = f.expectancy ?? 0;
                                const wr = f.win_rate ?? 0;
                                const auc = f.fleet_auc_mean ?? f.ens_auc_val ?? null;
                                return (
                                  <tr key={yr} style={{ borderBottom: '1px solid #0f172a' }}>
                                    <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 700 }}>{yr}</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: '#64748b' }}>{f.n ?? '—'}</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: pfColor(pf) }}>{pf.toFixed(3)}</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: roiColor(roi) }}>{roi.toFixed(2)}%</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: roiColor(exp) }}>{exp.toFixed(2)}%</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: wr >= 0.55 ? '#10b981' : wr >= 0.45 ? '#f59e0b' : '#ef4444' }}>{(wr * 100).toFixed(0)}%</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', color: auc !== null ? (auc >= 0.70 ? '#10b981' : auc >= 0.60 ? '#f59e0b' : '#94a3b8') : '#334155' }}>{auc !== null ? auc.toFixed(3) : '—'}</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right' }}>
                                      {cat ? <span style={{ color: '#ef4444', fontWeight: 700 }}>✗CAT</span>
                                           : ok  ? <span style={{ color: '#10b981' }}>✓</span>
                                                 : <span style={{ color: '#f59e0b' }}>~</span>}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {/* ROI bar chart */}
                    {folds.length > 0 && (() => {
                      const maxRoi = Math.max(...folds.map((f: any) => Math.abs(f.total_return_pct ?? 0)), 0.1);
                      return (
                        <div style={card()}>
                          <span style={lbl}>ROI par fold (position 0.2% / trade)</span>
                          {folds.map((f: any) => {
                            const yr = f.year ?? f.fold_year;
                            const roi = f.total_return_pct ?? 0;
                            const pct = Math.abs(roi) / maxRoi * 100;
                            const neg = roi < 0;
                            return (
                              <div key={yr} style={{ display: 'grid', gridTemplateColumns: '44px 1fr', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                                <span style={{ color: '#64748b', fontSize: 12, fontWeight: 700 }}>{yr}</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                  {neg && <span style={{ color: '#ef4444', fontSize: 11, minWidth: 42, textAlign: 'right' }}>{roi.toFixed(2)}%</span>}
                                  <div style={{
                                    height: 14, width: `${pct}%`, minWidth: 2, maxWidth: '100%',
                                    background: neg ? '#ef4444' : '#10b981',
                                    borderRadius: 2, opacity: 0.85,
                                    boxShadow: neg ? 'none' : '0 0 4px #10b98144',
                                  }} />
                                  {!neg && <span style={{ color: '#10b981', fontSize: 11, minWidth: 42 }}>{roi.toFixed(2)}%</span>}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()}

                    <div style={{ fontSize: 10, color: '#334155', padding: '6px 10px', background: '#0a0e1a', borderRadius: 4 }}>
                      Ce résultat est figé — il représente le walk-forward scientifiquement validé.
                      Pour tester de nouvelles configurations, utilisez le panneau gauche pour lancer un nouveau run.
                    </div>
                  </>
                );
              })()}
            </div>
          )}

          {/* ── Logs ──────────────────────────────────────────────────── */}
          {tab === 'logs_long' && (
            <div>
              <div style={{ fontSize: 11, color: '#475569', marginBottom: 6 }}>
                {logsLong.length} lignes {isRunning('long') && <span style={{ color: '#10b981' }}>● live</span>}
              </div>
              <LogViewer lines={logsLong} running={isRunning('long')} />
            </div>
          )}
          {tab === 'logs_short' && (
            <div>
              <div style={{ fontSize: 11, color: '#475569', marginBottom: 6 }}>
                {logsShort.length} lignes {isRunning('short') && <span style={{ color: '#60a5fa' }}>● live</span>}
              </div>
              <LogViewer lines={logsShort} running={isRunning('short')} />
            </div>
          )}

          {/* ── Results tab ───────────────────────────────────────────── */}
          {tab === 'results' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {/* Summary cards per pipeline */}
              {(['long', 'short'] as Pipe[]).filter(p => pipes.has(p) && (p === 'long' ? longResult : shortResult)).map(p => {
                const r = (p === 'long' ? longResult : shortResult)!;
                const col = p === 'long' ? '#10b981' : '#60a5fa';
                const nOk = r.n_folds_ok ?? r.n_ok ?? 0;
                const pfMed = (r.pf_median ?? r.median_pf ?? 0);
                const nTrades = r.n_total_trades ?? r.total_trades ?? 0;
                const verdict = r.verdict || (r.deployable ? 'DEPLOYABLE' : r.deployable === false ? 'NOT_DEPLOYABLE' : '');
                return (
                  <div key={p} style={card({ borderColor: col + '44' })}>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
                      <div style={{ color: col, fontWeight: 700, fontSize: 14, flex: 1 }}>{p.toUpperCase()}</div>
                      {verdict && <span style={{ fontSize: 12, fontWeight: 700, color: col }}>{verdict}</span>}
                    </div>
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                      {[
                        { label: 'Folds OK', val: nOk, color: nOk >= 2 ? col : '#ef4444' },
                        { label: 'PF médian', val: pfMed.toFixed(3), color: pfColor(pfMed) },
                        { label: 'Trades', val: nTrades, color: col },
                        { label: 'Cat.', val: r.n_catastrophic ?? 0, color: (r.n_catastrophic ?? 0) > 0 ? '#ef4444' : '#334155' },
                      ].map(m => (
                        <div key={m.label}>
                          <span style={lbl}>{m.label}</span>
                          <span style={{ fontWeight: 700, fontSize: 18, color: m.color }}>{m.val}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {/* Fold table */}
              {foldRows.length > 0 && (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
                    <thead>
                      <tr style={{ color: '#475569', fontSize: 10 }}>
                        <th style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #1e293b' }}>Fold</th>
                        {pipes.has('long') && <>
                          <th style={{ padding: '4px 8px', color: '#10b981', borderBottom: '1px solid #1e293b' }}>▲ n</th>
                          <th style={{ padding: '4px 8px', color: '#10b981', borderBottom: '1px solid #1e293b' }}>▲ PF</th>
                          <th style={{ padding: '4px 8px', color: '#10b981', borderBottom: '1px solid #1e293b' }}>▲ ROI%</th>
                        </>}
                        {pipes.has('short') && <>
                          <th style={{ padding: '4px 8px', color: '#60a5fa', borderBottom: '1px solid #1e293b' }}>▼ n</th>
                          <th style={{ padding: '4px 8px', color: '#60a5fa', borderBottom: '1px solid #1e293b' }}>▼ PF</th>
                          <th style={{ padding: '4px 8px', color: '#60a5fa', borderBottom: '1px solid #1e293b' }}>▼ ROI%</th>
                        </>}
                        {pipes.size === 2 && <th style={{ padding: '4px 8px', color: '#e2e8f0', borderBottom: '1px solid #1e293b' }}>Σ ROI%</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {foldRows.map(r => (
                        <tr key={r.year}>
                          <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 700 }}>{r.year}</td>
                          {pipes.has('long') && <>
                            <td style={{ padding: '5px 8px', color: '#64748b' }}>{r.long_n ?? '—'}</td>
                            <td style={{ padding: '5px 8px', color: pfColor(r.long_pf ?? 0) }}>{r.long_pf?.toFixed(3) ?? '—'}</td>
                            <td style={{ padding: '5px 8px', color: roiColor(r.long_roi ?? 0) }}>{r.long_roi != null ? `${r.long_roi.toFixed(2)}%` : '—'}</td>
                          </>}
                          {pipes.has('short') && <>
                            <td style={{ padding: '5px 8px', color: '#64748b', opacity: r.short_skip ? 0.3 : 1 }}>{r.short_skip ? '—' : (r.short_n ?? '—')}</td>
                            <td style={{ padding: '5px 8px', color: pfColor(r.short_pf ?? 0), opacity: r.short_skip ? 0.3 : 1 }}>{r.short_skip ? '—' : (r.short_pf?.toFixed(3) ?? '—')}</td>
                            <td style={{ padding: '5px 8px', color: roiColor(r.short_roi ?? 0), opacity: r.short_skip ? 0.3 : 1 }}>{r.short_skip ? '—' : (r.short_roi != null ? `${r.short_roi.toFixed(2)}%` : '—')}</td>
                          </>}
                          {pipes.size === 2 && (
                            <td style={{ padding: '5px 8px', fontWeight: 700, color: roiColor(r.combined_roi ?? 0) }}>
                              {r.combined_roi ? `${r.combined_roi.toFixed(2)}%` : '—'}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ── ROI tab ───────────────────────────────────────────────── */}
          {tab === 'roi' && foldRows.length > 0 && (
            <div style={card()}>
              {/* Summary totals */}
              <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
                {pipes.has('long') && totalLongRoi !== 0 && (
                  <div style={{ ...card({ background: 'rgba(16,185,129,0.05)', borderColor: '#10b98133' }), flex: 1, minWidth: 120 }}>
                    <span style={lbl}>LONG total</span>
                    <span style={{ fontSize: 22, fontWeight: 800, color: roiColor(totalLongRoi) }}>{totalLongRoi.toFixed(2)}%</span>
                    <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>pos. 0.2% / trade</div>
                  </div>
                )}
                {pipes.has('short') && totalShortRoi !== 0 && (
                  <div style={{ ...card({ background: 'rgba(96,165,250,0.05)', borderColor: '#60a5fa33' }), flex: 1, minWidth: 120 }}>
                    <span style={lbl}>SHORT total</span>
                    <span style={{ fontSize: 22, fontWeight: 800, color: roiColor(totalShortRoi) }}>{totalShortRoi.toFixed(2)}%</span>
                    <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>pos. 0.1% / trade</div>
                  </div>
                )}
                {pipes.size === 2 && (
                  <div style={{ ...card({ background: 'rgba(255,255,255,0.03)', borderColor: '#33415555' }), flex: 1, minWidth: 120 }}>
                    <span style={lbl}>COMBINÉ</span>
                    <span style={{ fontSize: 22, fontWeight: 800, color: roiColor(totalCombinedRoi) }}>{totalCombinedRoi.toFixed(2)}%</span>
                    <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>sur {foldRows.filter(r => r.combined_roi !== 0).length} années</div>
                  </div>
                )}
              </div>

              {/* Bar chart */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: '#475569', marginBottom: 10 }}>ROI par année (position sizing réel)</div>
                {foldRows.map(r => (
                  <div key={r.year} style={{ display: 'grid', gridTemplateColumns: '44px 1fr', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                    <span style={{ color: '#64748b', fontSize: 12, fontWeight: 700 }}>{r.year}</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {pipes.has('long') && r.long_roi != null && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ color: '#10b981', fontSize: 10, width: 40 }}>LONG</span>
                          <div style={{ flex: 1 }}>
                            <RoiBar value={r.long_roi} maxAbs={maxRoi} color="#10b981" label={`${r.long_roi.toFixed(2)}%`} />
                          </div>
                        </div>
                      )}
                      {pipes.has('short') && !r.short_skip && r.short_roi != null && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ color: '#60a5fa', fontSize: 10, width: 40 }}>SHORT</span>
                          <div style={{ flex: 1 }}>
                            <RoiBar value={r.short_roi} maxAbs={maxRoi} color="#60a5fa" label={`${r.short_roi.toFixed(2)}%`} />
                          </div>
                        </div>
                      )}
                      {pipes.has('short') && r.short_skip && (
                        <div style={{ fontSize: 10, color: '#334155' }}>SHORT · bull year — inactif</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Equity curve */}
              {equityCurve.length > 1 && (
                <div style={{ marginTop: 16, borderTop: '1px solid #1e293b', paddingTop: 14 }}>
                  <div style={{ fontSize: 11, color: '#475569', marginBottom: 10 }}>
                    Equity cumulée (départ 1.00 · LONG + SHORT additifs)
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 80 }}>
                    {equityCurve.map((e, i) => {
                      const prev = i > 0 ? equityCurve[i - 1].equity : 1.0;
                      const gain = e.equity > prev;
                      const heightPct = (e.equity / maxEquity) * 100;
                      return (
                        <div key={e.year} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                          <span style={{ fontSize: 9, color: roiColor(e.equity - 1) }}>
                            {((e.equity - 1) * 100).toFixed(1)}%
                          </span>
                          <div style={{
                            width: '100%', background: gain ? '#10b98166' : '#ef444455',
                            height: `${heightPct}%`, minHeight: 4, borderRadius: '3px 3px 0 0',
                            border: `1px solid ${gain ? '#10b981' : '#ef4444'}`,
                          }} />
                          <span style={{ fontSize: 9, color: '#475569' }}>{e.year}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div style={{ marginTop: 14, padding: '8px 12px', background: '#0a0e1a', borderRadius: 5, fontSize: 10, color: '#334155', lineHeight: 1.6 }}>
                💡 ROI calculé avec position sizing réel : LONG 0.2%/trade, SHORT 0.1%/trade.
                Pour multiplier le ROI × N, ajuster le sizing dans les scripts (POSITION_PCT).
                Les années bull (2021, 2023, 2024) : SHORT inactif → ROI LONG seulement.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
