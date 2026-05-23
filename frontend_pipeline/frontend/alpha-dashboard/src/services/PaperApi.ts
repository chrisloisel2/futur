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
  symbol?: string;
  side?: string;
  entry?: number;
  exit?: number;
  pnl_pct?: number;
  hold_hours?: number;
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
  health:        () => get<{ status: string }>('/health'),
  market:        () => get<Market>('/api/market'),
  signalLatest:  () => get<Signal>('/api/signal/latest'),
  signalHistory: (n = 48) => get<Signal[]>(`/api/signal/history?n=${n}`),
  trades:        () => get<Trade[]>('/api/trades'),
  state:         () => get<PaperState>('/api/state'),
  runStatus:     () => get<RunStatus>('/api/signal/run-status'),
  triggerRun:    () => post<{ status: string }>('/api/signal/run'),
  triggerUpdate: () => post<{ status: string }>('/api/data/update'),
};
