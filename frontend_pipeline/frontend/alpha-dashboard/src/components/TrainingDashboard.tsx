import React, { useState, useEffect, useCallback, useRef } from 'react';
import { API_BASE_URL } from '../config/api';

// ─── Types ────────────────────────────────────────────────────────────────────

interface ArchLevel {
  id: number; name: string; type: string;
  inputs: number; outputs: number; desc: string;
  params: Record<string, any>; color: string;
}

interface TRMSpecialist {
  name: string;
  status: 'trained' | 'pending' | string;
  horizon: string;
  horizon_hours: number | null;
  archetype: string;
  archetype_desc: string;
  train_quantile: number | null;
  auc: number | null;
  n_ctx: number | null;
}

interface TRMFleetMeta {
  n_total: number;
  n_trained: number;
  n_pending: number;
  fleet_run_year: number | null;
  fleet_auc_mean: number | null;
}

interface ArchData {
  run_id?: string;
  data: { bars_1h: number; bars_1m: number; features: number; period: string };
  levels: ArchLevel[];
  trm_fleet?: TRMSpecialist[];
  trm_fleet_meta?: TRMFleetMeta;
}

interface TrainingJob {
  job_id: string; status: string; config: string; symbol: string;
  mode?: 'long' | 'short' | 'combined';
  device: string; epochs: number; current_epoch: number; total_epochs: number;
  progress_pct: number; current_loss: number; current_val_loss: number;
  current_sharpe: number; start_time: string; end_time: string | null; error: string | null;
  data_path?: string; run_dir?: string;
  components?: TrainingComponent[];
  validation_summary?: ValidationSummary;
}

interface ModelVersion {
  filename: string; size_mb: number; created_at: string;
  is_production: boolean; metadata: any;
}

interface TrainingComponent {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'passed' | 'failed' | 'warning' | 'skipped' | string;
  required: boolean;
  message?: string;
  metrics?: Record<string, any>;
  started_at?: string | null;
  ended_at?: string | null;
}

interface ValidationSummary {
  status: 'running' | 'passed' | 'warning' | 'failed' | string;
  message?: string;
  required?: number;
  passed?: number;
  warnings?: number;
  failed?: number;
  run_dir?: string;
}

interface TrainConfig {
  symbol: string; device: string; mode: 'long' | 'short' | 'combined'; epochs: number;
  batch_size: number; learning_rate: number; debug_mode: boolean;
  data_path: string;
  test_from: number; auto_calibrate: boolean;
  skip_tcn: boolean; require_short_stability: boolean;
  tradeable_q: number; cost: number;
  filter_threshold_long: number; direction_threshold_long: number;
  filter_threshold_short: number; direction_threshold_short: number;
  risk_long: number; risk_short: number;
  max_losses_long: number; max_losses_short: number;
  cooldown_long: number; cooldown_short: number;
  grid: boolean; compare_models: boolean; regression: boolean;
  top_pct: number; margin: number;
}

interface SymbolAvailability {
  symbol: string;
  ready: boolean;
  data_path?: string | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + 'M' :
  n >= 1_000     ? (n / 1_000).toFixed(0) + 'K' : String(n);

const relTime = (iso: string | null) => {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s/60).toFixed(0)}m`;
  return `${(s/3600).toFixed(1)}h`;
};

const statusColor = (status: string) => {
  switch (status) {
    case 'running': return '#10b981';
    case 'completed':
    case 'passed': return '#60a5fa';
    case 'warning': return '#f59e0b';
    case 'failed': return '#ef4444';
    case 'stopped':
    case 'skipped': return '#64748b';
    default: return '#94a3b8';
  }
};

const statusLabel = (status: string) => {
  switch (status) {
    case 'running': return 'En cours';
    case 'completed': return 'Terminé';
    case 'passed': return 'Validé';
    case 'warning': return 'À revoir';
    case 'failed': return 'Échec';
    case 'stopped': return 'Arrêté';
    case 'skipped': return 'Ignoré';
    case 'pending': return 'Attente';
    default: return status || '—';
  }
};

// ─── Architecture SVG node graph ──────────────────────────────────────────────

interface NodePos { x: number; y: number; w: number; h: number; }

const ARCH_NODES: Array<{
  id: string; label: string; sublabel: string; icon: string;
  color: string; detail: string; glow: string;
}> = [
  {
    id: 'data', label: 'DATA', sublabel: '76K bars · 88 features',
    icon: '⬡', color: '#334155', detail: '1h 2017→2026', glow: '#475569',
  },
  {
    id: 'l0', label: 'L0 GATE', sublabel: 'Quantile Cal.',
    icon: '◈', color: '#4338ca', detail: '24 features → tradeable?', glow: '#6366f1',
  },
  {
    id: 'l1', label: 'L1 EVENT', sublabel: 'TCN d=64 · 3L',
    icon: '⬢', color: '#6d28d9', detail: 'CHOP/UP/DOWN · tradeability · entropy', glow: '#8b5cf6',
  },
  {
    id: 'l2', label: 'L2 EDGE', sublabel: 'TCN d=96 · 3L',
    icon: '▲', color: '#0e7490', detail: 'edge score + volatility head', glow: '#06b6d4',
  },
  {
    id: 'l3', label: 'L3 ROUTER', sublabel: 'XGBoost × 6',
    icon: '◉', color: '#065f46', detail: 'TREND · MR · BREAKOUT · HIGHVOL', glow: '#10b981',
  },
  {
    id: 'l7', label: 'L7 RISK', sublabel: 'Kelly + ATR',
    icon: '◆', color: '#92400e', detail: '0.2% risk · 2.5×ATR stop · -2% daily KS', glow: '#f59e0b',
  },
  {
    id: 'out', label: 'SIGNAL', sublabel: 'LONG · SHORT · WAIT',
    icon: '★', color: '#1e3a5f', detail: 'entry · stop · TP1 · TP2 · size', glow: '#3b82f6',
  },
];

// Animation particle positions along path
function usePulse(active: boolean) {
  const [offset, setOffset] = useState(0);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (!active) return;
    let t = 0;
    const tick = () => {
      t = (t + 0.4) % 100;
      setOffset(t);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [active]);
  return offset;
}

const ArchGraph: React.FC<{ arch: ArchData | null; activeJob: boolean }> = ({ arch, activeJob }) => {
  const pulse = usePulse(activeJob);

  const W = 900, H = 280;
  const cols = ARCH_NODES.length;
  const colW = W / cols;

  const cx = (i: number) => colW * i + colW / 2;
  const cy = H / 2;
  const nodeH = 100, nodeW = 108;

  // Get live data counts for nodes
  const d = arch?.data;
  const fleet = arch?.trm_fleet ?? [];
  const meta  = arch?.trm_fleet_meta;
  const nTrained = meta?.n_trained ?? fleet.filter(t => t.status === 'trained').length;
  const nTotal   = meta?.n_total   ?? fleet.length;
  const liveLabels: Record<string, string> = {
    data: d ? `${fmt(d.bars_1h)} bars · ${d.features} ft` : '',
    l0:   '24 features in',
    l1:   'd=64 · 3 layers · dilation 1-2-4',
    l2:   'd=96 · 3 layers · dual head',
    l3:   nTotal > 0 ? `${nTrained}/${nTotal} entraînés · top-k=4` : '73 TRM · 9h × 8arch',
    l7:   '0.2% equity · 2.5×ATR',
    out:  'LONG / SHORT / WAIT',
  };

  return (
    <div style={{ position: 'relative', width: '100%', overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', minWidth: 700, height: H }}>
        <defs>
          {ARCH_NODES.map(n => (
            <filter key={n.id} id={`glow-${n.id}`} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation={activeJob ? "4" : "2"} result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          ))}
          <filter id="glow-edge" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* ── Connection edges ── */}
        {ARCH_NODES.slice(0, -1).map((n, i) => {
          const x1 = cx(i) + nodeW / 2;
          const x2 = cx(i + 1) - nodeW / 2;
          const y  = cy;
          const nxt = ARCH_NODES[i + 1];
          const pct = pulse;
          const px  = x1 + (x2 - x1) * (pct / 100);

          return (
            <g key={n.id}>
              {/* base line */}
              <line x1={x1} y1={y} x2={x2} y2={y}
                stroke={`${nxt.glow}44`} strokeWidth={1.5}
                strokeDasharray="4 3"
              />
              {/* animated glow line */}
              {activeJob && (
                <line x1={x1} y1={y} x2={x2} y2={y}
                  stroke={nxt.glow}
                  strokeWidth={2}
                  strokeDasharray={`${(x2 - x1) * 0.3} ${(x2 - x1) * 0.7}`}
                  strokeDashoffset={-pct / 100 * (x2 - x1)}
                  filter="url(#glow-edge)"
                  opacity={0.7}
                />
              )}
              {/* particle */}
              {activeJob && (
                <circle
                  cx={px} cy={y} r={4}
                  fill={nxt.glow}
                  filter={`url(#glow-${nxt.id})`}
                  opacity={0.9}
                />
              )}
            </g>
          );
        })}

        {/* ── Nodes ── */}
        {ARCH_NODES.map((n, i) => {
          const x = cx(i) - nodeW / 2;
          const y = cy - nodeH / 2;

          return (
            <g key={n.id}>
              {/* Outer glow rect */}
              {activeJob && (
                <rect x={x - 4} y={y - 4} width={nodeW + 8} height={nodeH + 8}
                  rx={12} fill="none"
                  stroke={n.glow} strokeWidth={1}
                  opacity={0.3}
                  filter={`url(#glow-${n.id})`}
                />
              )}
              {/* Main card */}
              <rect x={x} y={y} width={nodeW} height={nodeH}
                rx={8}
                fill={n.color}
                stroke={activeJob ? n.glow : `${n.glow}55`}
                strokeWidth={activeJob ? 1.5 : 1}
              />
              {/* Top accent bar */}
              <rect x={x} y={y} width={nodeW} height={3}
                rx={8}
                fill={n.glow}
                opacity={0.9}
              />

              {/* Icon */}
              <text x={cx(i)} y={y + 22}
                textAnchor="middle" dominantBaseline="middle"
                fill={n.glow} fontSize={16} fontFamily="monospace"
              >{n.icon}</text>

              {/* Main label */}
              <text x={cx(i)} y={y + 42}
                textAnchor="middle" dominantBaseline="middle"
                fill="#f1f5f9" fontSize={10} fontWeight="bold"
                fontFamily="'Inter', sans-serif" letterSpacing="0.04em"
              >{n.label}</text>

              {/* Sublabel */}
              <text x={cx(i)} y={y + 57}
                textAnchor="middle" dominantBaseline="middle"
                fill={n.glow} fontSize={8} fontFamily="'Inter', sans-serif"
              >{n.sublabel}</text>

              {/* Live info */}
              <foreignObject x={x + 4} y={y + 68} width={nodeW - 8} height={28}>
                <div style={{
                  fontSize: 7.5, color: '#94a3b8', textAlign: 'center',
                  lineHeight: 1.4, wordBreak: 'break-word',
                }}>
                  {liveLabels[n.id]}
                </div>
              </foreignObject>
            </g>
          );
        })}

        {/* ── Labels en dessous ── */}
        {ARCH_NODES.map((n, i) => {
          const level = arch?.levels.find(l => `l${l.id}` === n.id);
          if (!level) return null;
          return (
            <text key={`lbl-${n.id}`}
              x={cx(i)} y={cy + nodeH / 2 + 18}
              textAnchor="middle" fill="#475569" fontSize={8}
              fontFamily="'Inter', sans-serif"
            >
              {level.type}
            </text>
          );
        })}
      </svg>
    </div>
  );
};

// ─── Training Controls ────────────────────────────────────────────────────────

const DEVICES   = ['auto', 'cpu', 'cuda'];
const SYMBOLS   = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'MATICUSDT'];
const MODES: Array<{ id: TrainConfig['mode']; label: string }> = [
  { id: 'combined', label: 'Pipeline' },
  { id: 'long', label: 'LONG' },
  { id: 'short', label: 'SHORT' },
];
const LR_OPTIONS = [0.001, 0.0005, 0.0001, 0.00005];
const EPOCH_OPT  = [50, 100, 150, 200, 300];
const BATCH_OPT  = [64, 128, 256, 512];

const fieldStyle: React.CSSProperties = {
  width: '100%',
  minWidth: 0,
  background: 'var(--bg3)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  color: 'var(--txt)',
  padding: '0.38rem 0.45rem',
  fontSize: '0.72rem',
};

const NumberControl: React.FC<{
  label: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}> = ({ label, value, step = 0.01, min, max, onChange }) => (
  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
    <span style={{ fontSize: '0.62rem', color: 'var(--txt-muted)' }}>{label}</span>
    <input
      type="number"
      value={value}
      step={step}
      min={min}
      max={max}
      onChange={e => onChange(Number(e.target.value))}
      style={fieldStyle}
    />
  </label>
);

const TrainingControls: React.FC<{
  onStart: (cfg: TrainConfig) => void;
  running: boolean;
}> = ({ onStart, running }) => {
  const [cfg, setCfg] = useState<TrainConfig>({
    symbol: 'BTCUSDT', device: 'auto', mode: 'combined', epochs: 100,
    batch_size: 128, learning_rate: 0.001, debug_mode: false,
    data_path: '',
    test_from: 2024, auto_calibrate: true,
    skip_tcn: false, require_short_stability: true,
    tradeable_q: 0.70, cost: 0.001,
    filter_threshold_long: 0.40, direction_threshold_long: 0.52,
    filter_threshold_short: 0.45, direction_threshold_short: 0.55,
    risk_long: 0.002, risk_short: 0.001,
    max_losses_long: 3, max_losses_short: 2,
    cooldown_long: 2, cooldown_short: 3,
    grid: false, compare_models: false, regression: false,
    top_pct: 0.01, margin: 0.001,
  });
  const [symbols, setSymbols] = useState<Record<string, SymbolAvailability>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);

  const set = (k: keyof TrainConfig, v: any) => setCfg(c => ({ ...c, [k]: v }));

  useEffect(() => {
    let mounted = true;
    fetch(`${API_BASE_URL}/training/symbols`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!mounted || !d?.symbols) return;
        const next: Record<string, SymbolAvailability> = {};
        d.symbols.forEach((s: SymbolAvailability) => { next[s.symbol] = s; });
        setSymbols(next);
      })
      .catch(() => {});
    return () => { mounted = false; };
  }, []);

  const selectedReady = symbols[cfg.symbol]?.ready;
  const hasDatasetOverride = cfg.data_path.trim().length > 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>

      <div style={{ fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--txt-muted)' }}>
        Configuration
      </div>

      {/* Mode */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>Mode</label>
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
          {MODES.map(m => (
            <button key={m.id} className={`btn btn-xs ${cfg.mode === m.id ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => set('mode', m.id)}>{m.label}</button>
          ))}
        </div>
      </div>

      {/* Symbol */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>Symbole</label>
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
          {SYMBOLS.map(s => {
            const ready = symbols[s]?.ready;
            return (
              <button key={s}
                className={`btn btn-xs ${cfg.symbol === s ? 'btn-primary' : 'btn-ghost'}`}
                title={ready === false ? 'Dataset local requis ou override avancé' : symbols[s]?.data_path || s}
                style={ready === false ? { opacity: 0.65 } : undefined}
                onClick={() => set('symbol', s)}
              >
                {s.replace('USDT','')}
              </button>
            );
          })}
        </div>
      </div>

      {/* Device */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>Device</label>
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          {DEVICES.map(d => (
            <button key={d} className={`btn btn-xs ${cfg.device === d ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => set('device', d)}>
              {d === 'auto' ? '⚡ Auto' : d === 'cuda' ? '🖥 GPU' : '💻 CPU'}
            </button>
          ))}
        </div>
      </div>

      {/* Epochs */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>
          Époques: <strong style={{ color: 'var(--txt)' }}>{cfg.epochs}</strong>
        </label>
        <input type="range" min={50} max={500} step={50} value={cfg.epochs}
          onChange={e => set('epochs', Number(e.target.value))}
          style={{ width: '100%', accentColor: 'var(--blue)' }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.6rem', color: 'var(--txt-dim)' }}>
          <span>50</span><span>500</span>
        </div>
      </div>

      {/* Batch size */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>Batch Size</label>
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          {BATCH_OPT.map(b => (
            <button key={b} className={`btn btn-xs ${cfg.batch_size === b ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => set('batch_size', b)}>{b}</button>
          ))}
        </div>
      </div>

      {/* Learning rate */}
      <div>
        <label style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', display: 'block', marginBottom: 4 }}>Learning Rate</label>
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
          {LR_OPTIONS.map(lr => (
            <button key={lr} className={`btn btn-xs ${cfg.learning_rate === lr ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => set('learning_rate', lr)}>{lr}</button>
          ))}
        </div>
      </div>

      {/* Debug mode */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <input type="checkbox" id="debug" checked={cfg.debug_mode}
          onChange={e => set('debug_mode', e.target.checked)}
          style={{ accentColor: 'var(--amber)' }}
        />
        <label htmlFor="debug" style={{ fontSize: '0.75rem', color: 'var(--txt-muted)', cursor: 'pointer' }}>
          Mode debug (logs verbeux)
        </label>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <input type="checkbox" id="short-stability" checked={cfg.require_short_stability}
          onChange={e => set('require_short_stability', e.target.checked)}
          style={{ accentColor: 'var(--amber)' }}
        />
        <label htmlFor="short-stability" style={{ fontSize: '0.75rem', color: 'var(--txt-muted)', cursor: 'pointer' }}>
          Gate robustesse SHORT
        </label>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <input type="checkbox" id="skip-tcn" checked={cfg.skip_tcn}
          onChange={e => set('skip_tcn', e.target.checked)}
          style={{ accentColor: 'var(--amber)' }}
        />
        <label htmlFor="skip-tcn" style={{ fontSize: '0.75rem', color: 'var(--txt-muted)', cursor: 'pointer' }}>
          Ignorer TCN long
        </label>
      </div>

      <button
        className="btn btn-ghost btn-xs"
        style={{ justifyContent: 'center' }}
        onClick={() => setShowAdvanced(v => !v)}
      >
        {showAdvanced ? 'Masquer pilotage avancé' : 'Pilotage avancé'}
      </button>

      {showAdvanced && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.75rem',
          background: 'var(--bg2)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: '0.75rem',
        }}>
          <div style={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--txt-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Dataset
          </div>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: '0.62rem', color: 'var(--txt-muted)' }}>Chemin dataset override</span>
            <input
              value={cfg.data_path}
              onChange={e => set('data_path', e.target.value)}
              placeholder="auto"
              style={fieldStyle}
            />
          </label>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.5rem' }}>
            <NumberControl label="Test depuis" value={cfg.test_from} step={1} min={2024} max={2026} onChange={v => set('test_from', Math.max(2024, v))} />
            <NumberControl label="Tradeable q" value={cfg.tradeable_q} step={0.01} min={0.5} max={0.95} onChange={v => set('tradeable_q', v)} />
            <NumberControl label="Coût" value={cfg.cost} step={0.0001} min={0} max={0.01} onChange={v => set('cost', v)} />
            <NumberControl label="Top pct reg" value={cfg.top_pct} step={0.001} min={0.001} max={0.2} onChange={v => set('top_pct', v)} />
          </div>

          <div style={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--txt-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Seuils
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.5rem' }}>
            <NumberControl label="Filtre LONG" value={cfg.filter_threshold_long} step={0.01} min={0.01} max={0.99} onChange={v => set('filter_threshold_long', v)} />
            <NumberControl label="Direction LONG" value={cfg.direction_threshold_long} step={0.01} min={0.01} max={0.99} onChange={v => set('direction_threshold_long', v)} />
            <NumberControl label="Filtre SHORT" value={cfg.filter_threshold_short} step={0.01} min={0.01} max={0.99} onChange={v => set('filter_threshold_short', v)} />
            <NumberControl label="Direction SHORT" value={cfg.direction_threshold_short} step={0.01} min={0.01} max={0.99} onChange={v => set('direction_threshold_short', v)} />
          </div>

          <div style={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--txt-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Risque
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.5rem' }}>
            <NumberControl label="Risk LONG" value={cfg.risk_long} step={0.0005} min={0.0001} max={0.02} onChange={v => set('risk_long', v)} />
            <NumberControl label="Risk SHORT" value={cfg.risk_short} step={0.0005} min={0.0001} max={0.02} onChange={v => set('risk_short', v)} />
            <NumberControl label="Pertes LONG" value={cfg.max_losses_long} step={1} min={1} max={10} onChange={v => set('max_losses_long', v)} />
            <NumberControl label="Pertes SHORT" value={cfg.max_losses_short} step={1} min={1} max={10} onChange={v => set('max_losses_short', v)} />
            <NumberControl label="Cooldown LONG" value={cfg.cooldown_long} step={1} min={0} max={48} onChange={v => set('cooldown_long', v)} />
            <NumberControl label="Cooldown SHORT" value={cfg.cooldown_short} step={1} min={0} max={48} onChange={v => set('cooldown_short', v)} />
            <NumberControl label="Margin reg" value={cfg.margin} step={0.0005} min={0} max={0.05} onChange={v => set('margin', v)} />
          </div>

          {[
            ['auto_calibrate', 'Calibration filtre auto'],
            ['compare_models', 'Comparer modèles'],
            ['grid', 'Grid sweep'],
            ['regression', 'Mode régression PnL'],
          ].map(([key, label]) => (
            <label key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.72rem', color: 'var(--txt-muted)' }}>
              <input
                type="checkbox"
                checked={Boolean(cfg[key as keyof TrainConfig])}
                onChange={e => set(key as keyof TrainConfig, e.target.checked)}
                style={{ accentColor: 'var(--amber)' }}
              />
              {label}
            </label>
          ))}
        </div>
      )}

      {/* Summary */}
      <div style={{
        background: 'var(--bg3)', borderRadius: 8, padding: '0.625rem',
        fontSize: '0.68rem', color: 'var(--txt-muted)', lineHeight: 1.8,
        fontFamily: 'monospace',
      }}>
        <div>Symbol:  <strong style={{ color: 'var(--txt)' }}>{cfg.symbol}</strong></div>
        <div>Mode:    <strong style={{ color: 'var(--txt)' }}>{cfg.mode}</strong></div>
        <div>Device:  <strong style={{ color: 'var(--txt)' }}>{cfg.device}</strong></div>
        <div>Epochs:  <strong style={{ color: 'var(--txt)' }}>{cfg.epochs}</strong></div>
        <div>Batch:   <strong style={{ color: 'var(--txt)' }}>{cfg.batch_size}</strong></div>
        <div>LR:      <strong style={{ color: 'var(--txt)' }}>{cfg.learning_rate}</strong></div>
        <div>Test:    <strong style={{ color: 'var(--txt)' }}>{cfg.test_from}</strong></div>
        <div>Calib:   <strong style={{ color: 'var(--txt)' }}>{cfg.auto_calibrate ? 'auto' : 'manual'}</strong></div>
      </div>

      <button
        className={`btn ${running ? 'btn-ghost' : 'btn-success'}`}
        style={{ padding: '0.625rem', fontWeight: 700, fontSize: '0.875rem', justifyContent: 'center' }}
        disabled={running || (selectedReady === false && !hasDatasetOverride)}
        onClick={() => onStart(cfg)}
      >
        {running
          ? <><span className="spinner" />&nbsp; Entraînement en cours…</>
          : selectedReady === false && !hasDatasetOverride
            ? 'Dataset local requis'
            : '▶ Lancer l\'entraînement'}
      </button>

      {running && (
        <div style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', textAlign: 'center' }}>
          Local · GPU si disponible · Logs en temps réel →
        </div>
      )}
    </div>
  );
};

// ─── Training Monitor ─────────────────────────────────────────────────────────

const formatMetric = (value: any) => {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return String(value);
    return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(3);
  }
  return String(value);
};

const ComponentChecklist: React.FC<{ components: TrainingComponent[] }> = ({ components }) => {
  if (!components || components.length === 0) return null;
  const visible = components.filter(c => c.required || c.status !== 'skipped');

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
      gap: '0.5rem',
    }}>
      {visible.map(c => {
        const color = statusColor(c.status);
        const metrics = Object.entries(c.metrics || {})
          .filter(([, v]) => formatMetric(v) !== null)
          .slice(0, 3);

        return (
          <div key={c.id} style={{
            background: 'var(--bg2)',
            border: `1px solid ${color}55`,
            borderLeft: `3px solid ${color}`,
            borderRadius: 8,
            padding: '0.55rem 0.65rem',
            minHeight: 86,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'center', marginBottom: 5 }}>
              <span style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--txt)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {c.name}
              </span>
              <span style={{
                fontSize: '0.6rem',
                color,
                border: `1px solid ${color}55`,
                borderRadius: 99,
                padding: '1px 6px',
                whiteSpace: 'nowrap',
              }}>
                {statusLabel(c.status)}
              </span>
            </div>
            <div style={{ fontSize: '0.64rem', color: 'var(--txt-muted)', minHeight: 28, lineHeight: 1.45 }}>
              {c.message || '—'}
            </div>
            {metrics.length > 0 && (
              <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap', marginTop: 6, fontSize: '0.6rem', color: 'var(--txt-dim)' }}>
                {metrics.map(([k, v]) => (
                  <span key={k}>
                    {k}: <strong style={{ color: 'var(--txt-muted)' }}>{formatMetric(v)}</strong>
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

const TrainingMonitor: React.FC<{
  jobs: TrainingJob[];
  activeJobId: string | null;
  onStop: (id: string) => void;
  onRefresh: () => void;
}> = ({ jobs, activeJobId, onStop, onRefresh }) => {
  const [logs, setLogs]     = useState<string[]>([]);
  const [selJob, setSelJob] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const jobToShow = selJob || activeJobId || jobs[0]?.job_id;

  useEffect(() => {
    if (!jobToShow) return;
    const loadLogs = async () => {
      try {
        const r = await fetch(`${API_BASE_URL}/training/logs/${jobToShow}`);
        if (r.ok) {
          const d = await r.json();
          setLogs(d.logs || []);
          setTimeout(() => logRef.current?.scrollTo({ top: 99999 }), 30);
        }
      } catch {}
    };
    loadLogs();
    const t = setInterval(loadLogs, 3_000);
    return () => clearInterval(t);
  }, [jobToShow]);

  if (jobs.length === 0) {
    return (
      <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '0.75rem', color: 'var(--txt-muted)' }}>
        <span style={{ fontSize: '2rem', opacity: 0.3 }}>⬢</span>
        <span style={{ fontSize: '0.875rem' }}>Aucun job — Lance un entraînement</span>
      </div>
    );
  }

  const job = jobs.find(j => j.job_id === jobToShow);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

      {/* Job selector */}
      <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {jobs.slice(0, 5).map(j => (
          <button key={j.job_id}
            className={`btn btn-xs ${jobToShow === j.job_id ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setSelJob(j.job_id)}
          >
            <span style={{
              display: 'inline-block', width: 6, height: 6, borderRadius: '50%', marginRight: 4,
              background: statusColor(j.validation_summary?.status || j.status)
            }} />
            {j.symbol || 'BTC'} {j.job_id.slice(-6)}
          </button>
        ))}
        <button className="btn btn-ghost btn-xs" onClick={onRefresh} style={{ marginLeft: 'auto' }}>↻</button>
      </div>

      {/* Active job stats */}
      {job && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.5rem' }}>
            {[
              { label: 'Statut',  value: statusLabel(job.status), color: statusColor(job.status) },
              { label: 'Validation', value: statusLabel(job.validation_summary?.status || 'pending'), color: statusColor(job.validation_summary?.status || 'pending') },
              { label: 'Train Loss', value: job.current_loss ? job.current_loss.toFixed(4) : '—', color: 'var(--amber)' },
              { label: 'Sharpe', value: job.current_sharpe ? job.current_sharpe.toFixed(3) : '—', color: '#10b981' },
            ].map(s => (
              <div key={s.label} className="stat-tile" style={{ padding: '0.5rem' }}>
                <div className="stat-label">{s.label}</div>
                <div style={{ fontSize: '0.875rem', fontWeight: 700, color: s.color }}>{s.value}</div>
              </div>
            ))}
          </div>

          {job.validation_summary?.message && (
            <div style={{
              border: `1px solid ${statusColor(job.validation_summary?.status || 'pending')}55`,
              background: `${statusColor(job.validation_summary?.status || 'pending')}12`,
              color: 'var(--txt-muted)',
              borderRadius: 8,
              padding: '0.55rem 0.65rem',
              fontSize: '0.72rem',
              lineHeight: 1.5,
            }}>
              {job.validation_summary?.message}
            </div>
          )}

          {/* Progress bar */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
              <span>Progression</span>
              <span>{(job.progress_pct || 0).toFixed(0)}%</span>
            </div>
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${job.progress_pct || 0}%` }} />
            </div>
          </div>

          {/* Stop button */}
          {job.status === 'running' && (
            <button className="btn btn-danger btn-sm" onClick={() => onStop(job.job_id)}>
              ■ Arrêter
            </button>
          )}

          <ComponentChecklist components={job.components || []} />
        </>
      )}

      {/* Log terminal */}
      <div className="log-terminal" ref={logRef} style={{ height: 200, maxHeight: 200 }}>
        {logs.length === 0
          ? <div style={{ color: 'var(--txt-dim)' }}>En attente des logs…</div>
          : logs.map((l, i) => {
              const cl = l.toLowerCase().includes('error') ? 'err'
                       : l.toLowerCase().includes('failed') ? 'err'
                       : l.toLowerCase().includes('warn')  ? 'warn'
                       : l.toLowerCase().includes('rejet')  ? 'warn'
                       : l.includes('✓') || l.includes('sharpe') || l.includes('Epoch') ? 'info'
                       : '';
              return <div key={i} className={`log-line ${cl}`}>{l}</div>;
            })
        }
      </div>
    </div>
  );
};

// ─── Model Registry ───────────────────────────────────────────────────────────

const ModelRegistry: React.FC = () => {
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/training/models`);
      if (r.ok) setModels((await r.json()).models || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const setProd = async (filename: string) => {
    if (!window.confirm(`Activer ${filename} en production ?`)) return;
    await fetch(`${API_BASE_URL}/training/models/${filename}/set-production`, { method: 'POST' });
    load();
  };

  if (loading) return <div style={{ color: 'var(--txt-muted)', fontSize: '0.8rem' }}>Chargement…</div>;

  if (models.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '1.5rem', color: 'var(--txt-muted)' }}>
        <div style={{ fontSize: '1.5rem', opacity: 0.3, marginBottom: '0.5rem' }}>🤖</div>
        <div style={{ fontSize: '0.8rem' }}>Aucun modèle entraîné</div>
        <div style={{ fontSize: '0.7rem', marginTop: 4, color: 'var(--txt-dim)' }}>Lance un entraînement pour créer le premier modèle</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {models.map(m => {
        const meta = m.metadata || {};
        return (
          <div key={m.filename} style={{
            padding: '0.75rem',
            background: m.is_production ? 'rgba(59,130,246,0.08)' : 'var(--bg2)',
            border: `1px solid ${m.is_production ? '#3b82f644' : 'var(--border)'}`,
            borderRadius: 8,
            display: 'flex', alignItems: 'center', gap: '0.75rem',
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: 2 }}>
                {m.is_production && <span style={{ fontSize: '0.65rem', padding: '1px 6px', borderRadius: 99, background: '#3b82f622', color: '#60a5fa', border: '1px solid #3b82f644', fontWeight: 700 }}>● PROD</span>}
                <span style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--txt)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {m.filename}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '0.75rem', fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
                <span>{m.size_mb} MB</span>
                <span>Créé il y a {relTime(m.created_at)}</span>
                {meta.val_sharpe && <span style={{ color: '#10b981' }}>Sharpe: {meta.val_sharpe?.toFixed(3)}</span>}
                {meta.config && <span>{meta.config}</span>}
              </div>
            </div>
            {!m.is_production && (
              <button className="btn btn-ghost btn-xs" onClick={() => setProd(m.filename)}>
                Activer
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── Main component ───────────────────────────────────────────────────────────

const TrainingDashboard: React.FC = () => {
  const [arch, setArch]           = useState<ArchData | null>(null);
  const [jobs, setJobs]           = useState<TrainingJob[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [tab, setTab]             = useState<'monitor' | 'models'>('monitor');

  const activeJob = jobs.find(j => j.status === 'running') || null;

  const loadArch = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/training/architecture`);
      if (r.ok) setArch(await r.json());
    } catch {}
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/training/jobs`);
      if (r.ok) {
        const d = await r.json();
        const jobList: TrainingJob[] = d.jobs || [];
        setJobs(jobList);
        const running = jobList.find(j => j.status === 'running');
        if (running) setActiveJobId(running.job_id);
      }
    } catch {}
  }, []);

  useEffect(() => {
    loadArch();
    loadJobs();
    const t = setInterval(loadJobs, 5_000);
    return () => clearInterval(t);
  }, [loadArch, loadJobs]);

  const startTraining = async (cfg: TrainConfig) => {
    try {
      const r = await fetch(`${API_BASE_URL}/training/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...cfg, config: 'pipeline' }),
      });
      if (r.ok) {
        const d = await r.json();
        setActiveJobId(d.job_id);
        setTab('monitor');
        setTimeout(loadJobs, 1000);
      }
    } catch (e) {
      alert(`Erreur: ${e}`);
    }
  };

  const stopJob = async (jobId: string) => {
    if (!window.confirm('Arrêter ce job ?')) return;
    await fetch(`${API_BASE_URL}/training/stop/${jobId}`, { method: 'POST' });
    loadJobs();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }} className="animate-fadeIn">

      {/* ─ Architecture node graph ──────────────────────────────────────────── */}
      <div className="card" style={{ padding: '1.25rem' }}>
        <div className="card-header" style={{ marginBottom: '1rem' }}>
          <div className="card-title" style={{ fontSize: '0.8rem' }}>
            ◈ Architecture IA — Pipeline de décision
          </div>
          <div style={{ fontSize: '0.68rem', color: 'var(--txt-muted)' }}>
            {arch?.data.bars_1h?.toLocaleString()} bars 1h · {arch?.data.bars_1m?.toLocaleString()} bars 1m · {arch?.data.features} features · {arch?.data.period}
            {activeJob && <span style={{ marginLeft: '0.75rem', color: '#10b981' }}>● Entraînement actif</span>}
          </div>
        </div>
        <ArchGraph arch={arch} activeJob={!!activeJob} />

        {/* Level detail cards */}
        {arch?.levels && (
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', flexWrap: 'wrap' }}>
            {arch.levels.map(lvl => (
              <div key={lvl.id} style={{
                flex: '1 1 150px', padding: '0.625rem',
                background: 'var(--bg2)',
                border: `1px solid ${lvl.color}33`,
                borderRadius: 8,
                borderTop: `2px solid ${lvl.color}`,
              }}>
                <div style={{ fontSize: '0.65rem', fontWeight: 700, color: lvl.color, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>
                  Level {lvl.id} — {lvl.name}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--txt)', marginBottom: 2 }}>
                  {lvl.type}
                </div>
                <div style={{ fontSize: '0.62rem', color: 'var(--txt-muted)', lineHeight: 1.5 }}>
                  {Object.entries(lvl.params).slice(0, 3).map(([k, v]) => (
                    <span key={k} style={{ marginRight: '0.5rem' }}>
                      {k}: <strong style={{ color: 'var(--txt)' }}>{Array.isArray(v) ? v.join('·') : String(v)}</strong>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* TRM Fleet — 73 spécialistes multi-horizon */}
        {arch?.trm_fleet && arch.trm_fleet.length > 0 && (() => {
          const meta = arch.trm_fleet_meta;
          const nTr  = meta?.n_trained ?? arch.trm_fleet.filter(t => t.status === 'trained').length;
          const nTo  = meta?.n_total   ?? arch.trm_fleet.length;
          const pending = nTr === 0;

          // Group by horizon
          const byHorizon: Record<string, TRMSpecialist[]> = {};
          for (const sp of arch.trm_fleet) {
            const h = sp.horizon ?? 'all';
            if (!byHorizon[h]) byHorizon[h] = [];
            byHorizon[h].push(sp);
          }

          return (
            <div style={{ marginTop: '1rem' }}>
              {/* Header */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                marginBottom: '0.6rem',
              }}>
                <span style={{ fontSize: '0.65rem', fontWeight: 700, color: '#10b981', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  ◉ L3 TRM Fleet
                </span>
                <span style={{ fontSize: '0.62rem', color: 'var(--txt-muted)' }}>
                  {nTr}/{nTo} entraînés · 9 horizons × 8 archétypes + général
                  {meta?.fleet_auc_mean != null && ` · AUC moy ${meta.fleet_auc_mean.toFixed(3)}`}
                  {meta?.fleet_run_year != null && ` · fold ${meta.fleet_run_year}`}
                </span>
                {pending && (
                  <span style={{ fontSize: '0.6rem', padding: '1px 6px', borderRadius: 4, background: '#f59e0b22', color: '#f59e0b' }}>
                    ⚠ run walk_forward pour activer
                  </span>
                )}
              </div>

              {/* Grid grouped by horizon */}
              {Object.entries(byHorizon).map(([horizon, specialists]) => (
                <div key={horizon} style={{ marginBottom: '0.5rem' }}>
                  <div style={{ fontSize: '0.6rem', color: '#64748b', fontWeight: 600, marginBottom: '0.25rem', letterSpacing: '0.04em' }}>
                    {horizon.toUpperCase()}
                  </div>
                  <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                    {specialists.map(sp => {
                      const trained = sp.status === 'trained';
                      const aucColor = sp.auc == null ? '#64748b' : sp.auc >= 0.65 ? '#10b981' : sp.auc >= 0.55 ? '#f59e0b' : '#ef4444';
                      return (
                        <div key={sp.name} title={`${sp.archetype_desc} · top-${sp.train_quantile != null ? Math.round((1 - sp.train_quantile) * 100) : '?'}%`} style={{
                          padding: '0.3rem 0.45rem',
                          background: trained ? 'rgba(16,185,129,0.07)' : 'var(--bg2)',
                          border: `1px solid ${trained ? '#10b98133' : '#1e293b'}`,
                          borderRadius: 6,
                          minWidth: 90,
                          opacity: trained ? 1 : 0.5,
                        }}>
                          <div style={{ fontSize: '0.6rem', fontWeight: 600, color: trained ? '#10b981' : '#475569', marginBottom: 2 }}>
                            {sp.archetype}
                          </div>
                          {trained && sp.auc != null ? (
                            <div style={{ fontSize: '0.65rem', color: aucColor, fontWeight: 700 }}>
                              {sp.auc.toFixed(3)}
                              {sp.n_ctx != null && (
                                <span style={{ fontSize: '0.56rem', color: '#64748b', fontWeight: 400, marginLeft: 4 }}>
                                  n={sp.n_ctx.toLocaleString()}
                                </span>
                              )}
                            </div>
                          ) : (
                            <div style={{ fontSize: '0.58rem', color: '#334155' }}>—</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      {/* ─ Bottom row: Controls + Monitor ──────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: '1rem', alignItems: 'start' }}>

        {/* Controls */}
        <div className="card">
          <div className="card-header" style={{ marginBottom: '1rem' }}>
            <div className="card-title">▶ Entraînement Local</div>
          </div>
          <TrainingControls onStart={startTraining} running={!!activeJob} />
        </div>

        {/* Monitor + Registry */}
        <div className="card">
          <div className="card-header" style={{ marginBottom: '1rem' }}>
            <div style={{ display: 'flex', gap: '0.35rem' }}>
              {(['monitor', 'models'] as const).map(t => (
                <button key={t} className={`btn btn-xs ${tab === t ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setTab(t)}>
                  {t === 'monitor' ? '📊 Monitor' : '🤖 Modèles'}
                  {t === 'monitor' && jobs.filter(j => j.status === 'running').length > 0 && (
                    <span style={{
                      marginLeft: 4, width: 6, height: 6, borderRadius: '50%',
                      background: '#10b981', display: 'inline-block',
                      animation: 'pulse 1.5s infinite',
                    }} />
                  )}
                </button>
              ))}
            </div>
          </div>

          {tab === 'monitor' ? (
            <TrainingMonitor
              jobs={jobs}
              activeJobId={activeJobId}
              onStop={stopJob}
              onRefresh={loadJobs}
            />
          ) : (
            <ModelRegistry />
          )}
        </div>
      </div>
    </div>
  );
};

export default TrainingDashboard;
