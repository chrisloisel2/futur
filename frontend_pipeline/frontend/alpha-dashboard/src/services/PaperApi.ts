import { API_BASE_URL } from '../config/api';

export interface Market {
  price: number | null;
  vs_ema200: number;
  vs_ema50: number;
  ret_7d: number;
  ret_24h: number;
  regime: 'BULL' | 'BEAR' | 'UNKNOWN';
  timestamp: string;
  error?: string;
}

export interface Signal {
  timestamp?: string;
  action?: string;
  p_long?: number;
  threshold?: number;
  regime?: string;
  suppressor?: string;
  sizer?: number;
  [key: string]: unknown;
}

export interface Trade {
  timestamp?: string;
  entry_time?: string;
  symbol?: string;
  side?: string;
  entry?: number;
  close_entry?: number;
  exit?: number;
  pnl_pct?: number;
  hold_hours?: number;
  p_long?: number;
  outcome?: string;
  [key: string]: unknown;
}

export interface Gate {
  name: string;
  ok: boolean;
  value: number | null;
  target: number;
  unit: string;
}

export interface PaperState {
  total_trades: number;
  total_wins: number;
  pf_live: number | null;
  wr_live: number | null;
  max_dd_pct: number;
  cumulative_pnl_pct: number;
  consecutive_losses: number;
  weekly_pnl_pct: number;
  crash_halt_until: string | null;
  start_date: string | null;
  drift_alerts: number;
  accounting_errors: number;
  gates: Gate[];
  all_gates_ok: boolean;
  verdict: 'LIVE_CANDIDATE' | 'PAPER_ONLY';
}

export interface RunStatus {
  signal_running: boolean;
  data_update_running: boolean;
  log: string[];
}

// ── Fleet types ───────────────────────────────────────────────────────────────

export type AssetAction = 'LONG' | 'WATCH' | 'NO_SIGNAL' | 'PENDING' | 'NO_DATA' | 'BLOCKED' | 'ERROR';

export interface AssetSummary {
  symbol: string;
  available: boolean;
  action?: AssetAction;
  p_long?: number | null;
  threshold?: number | null;
  timestamp?: string | null;
  total_trades?: number;
  pf_live?: number | null;
  wr_live?: number | null;
  max_dd_pct?: number;
  cumulative_pnl_pct?: number;
  all_gates_ok?: boolean;
  verdict?: string;
  btc_regime?: string;
  sup_level?: string;
  size_mult?: number;
  close?: number | null;
  start_date?: string | null;
}

export interface FleetSummary {
  timestamp: string;
  btc_regime: 'BULL' | 'BEAR' | 'UNKNOWN';
  btc_price: number | null;
  btc_vs_ema200: number | null;
  val_stats?: { n: number; pf: number; wr: number; features: number };
  assets: AssetSummary[];
  n_assets?: number;
  n_long_signals?: number;
  n_watch?: number;
  elapsed_s?: number;
}

export interface AssetDetail extends PaperState {
  symbol: string;
  available: boolean;
  latest_signal: Signal;
}

export interface FleetRunStatus {
  fleet_running: boolean;
  log: string[];
}

// ── API helpers ───────────────────────────────────────────────────────────────

const get = async <T>(path: string): Promise<T> => {
  const r = await fetch(`${API_BASE_URL}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};

const post = async <T>(path: string): Promise<T> => {
  const r = await fetch(`${API_BASE_URL}${path}`, { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};

export const PaperApi = {
  // Single-asset BTC
  health:        () => get<{ status: string }>('/health'),
  market:        () => get<Market>('/api/market'),
  signalLatest:  () => get<Signal>('/api/signal/latest'),
  signalHistory: (n = 48) => get<Signal[]>(`/api/signal/history?n=${n}`),
  trades:        () => get<Trade[]>('/api/trades'),
  state:         () => get<PaperState>('/api/state'),
  runStatus:     () => get<RunStatus>('/api/signal/run-status'),
  triggerRun:    () => post<{ status: string }>('/api/signal/run'),
  triggerUpdate: () => post<{ status: string }>('/api/data/update'),

  // Fleet multi-asset
  fleet:              () => get<FleetSummary>('/api/fleet'),
  fleetRunStatus:     () => get<FleetRunStatus>('/api/fleet/run-status'),
  fleetAsset:         (symbol: string) => get<AssetDetail>(`/api/fleet/${symbol}`),
  fleetAssetSignals:  (symbol: string, n = 100) => get<Signal[]>(`/api/fleet/${symbol}/signals?n=${n}`),
  fleetAssetTrades:   (symbol: string) => get<Trade[]>(`/api/fleet/${symbol}/trades`),
  triggerFleetRun:    () => post<{ status: string }>('/api/fleet/run'),
};
