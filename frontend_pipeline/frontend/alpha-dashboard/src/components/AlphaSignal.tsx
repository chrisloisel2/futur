import React, { useState, useEffect, useRef } from 'react';
import ReactECharts from 'echarts-for-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Signal {
  action: 'LONG' | 'SHORT' | 'HOLD';
  current_price: number;
  p_filter: number;
  filter_thr_long: number;
  p_long: number;
  p_short: number;
  thr_edge_long: number;
  thr_edge_short: number;
  regime: string;
  rsi: number;
  stop_price: number;
  take_profit: number;
  reason: string;
  refreshed_at: string;
  run_id: string;
}

interface Trade {
  bar: number;
  date: string;
  year: number;
  side: string;
  pnl_abs: number;
  pnl_net_pct: number;
  equity: number;
  p_side: number;
  p_tradeable: number;
}

interface BacktestSide {
  summary: {
    n_trades: number;
    profit_factor: number;
    win_rate: number;
    sharpe_annualized: number;
    total_return_pct: number;
    max_drawdown: number;
    final_equity: number;
  };
  trades: Trade[];
  equity: number[];
}

interface History {
  run_id: string;
  long: BacktestSide | null;
  short: BacktestSide | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt$ = (n: number) =>
  '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });

const fmtPct = (n: number, digits = 1) =>
  (n >= 0 ? '+' : '') + n.toFixed(digits) + '%';

const API = 'http://localhost:8000';

// ── Hook signal live ──────────────────────────────────────────────────────────

function useSignal() {
  const [signal, setSignal] = useState<Signal | null>(null);
  const [age, setAge]       = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const r = await fetch(`${API}/pipeline/signal`);
        if (r.ok) { setSignal(await r.json()); setAge(0); }
      } catch { /* server not running */ }
    };
    fetch_();
    timer.current = setInterval(fetch_, 60_000);
    const tick = setInterval(() => setAge(a => a + 1), 1000);
    return () => { clearInterval(timer.current!); clearInterval(tick); };
  }, []);

  return { signal, age };
}

// ── Hook historique backtest ──────────────────────────────────────────────────

function useHistory() {
  const [history, setHistory] = useState<History | null>(null);
  useEffect(() => {
    fetch(`${API}/backtest/history`)
      .then(r => r.json())
      .then(setHistory)
      .catch(() => {});
  }, []);
  return history;
}

// ── Composant ─────────────────────────────────────────────────────────────────

export default function AlphaSignal() {
  const { signal, age } = useSignal();
  const history = useHistory();
  const [side, setSide] = useState<'long' | 'short'>('long');

  const bk   = history?.[side] ?? null;
  const other = side === 'long' ? 'short' : 'long';
  const hasOther = !!history?.[other];

  // ── Equity chart ─────────────────────────────────────────────────────────

  const equityOption = React.useMemo(() => {
    if (!bk?.trades.length) return null;

    const trades = bk.trades;
    const xs     = trades.map(t => t.date);
    const ys     = trades.map(t => t.equity);
    const wins   = trades.filter(t => t.pnl_abs > 0);
    const losses = trades.filter(t => t.pnl_abs <= 0);

    const dot = (arr: Trade[], color: string, symbol: string) =>
      arr.map(t => ({ coord: [t.date, t.equity], itemStyle: { color }, symbol, symbolSize: 7 }));

    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 60, right: 20, top: 20, bottom: 40 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1a1f2e',
        borderColor: '#2d3748',
        textStyle: { color: '#d1d5db', fontSize: 12 },
        formatter: (p: any) => {
          const t = trades[p[0]?.dataIndex ?? 0];
          if (!t) return '';
          const pnl = t.pnl_abs >= 0
            ? `<span style="color:#10b981">+$${t.pnl_abs.toFixed(2)}</span>`
            : `<span style="color:#ef4444">$${t.pnl_abs.toFixed(2)}</span>`;
          return `${t.date}<br/>Equity ${fmt$(t.equity)}<br/>PnL ${pnl}<br/>p_side ${(t.p_side * 100).toFixed(1)}%`;
        },
      },
      xAxis: {
        type: 'category',
        data: xs,
        axisLabel: { color: '#6b7280', fontSize: 11 },
        axisLine: { lineStyle: { color: '#2d3748' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#6b7280', fontSize: 11, formatter: (v: number) => fmt$(v) },
        splitLine: { lineStyle: { color: '#1f2937' } },
      },
      series: [{
        type: 'line',
        data: ys,
        smooth: false,
        lineStyle: { color: side === 'long' ? '#3b82f6' : '#a78bfa', width: 2 },
        itemStyle: { color: 'transparent' },
        areaStyle: {
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: side === 'long' ? 'rgba(59,130,246,0.18)' : 'rgba(167,139,250,0.18)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ]
          },
        },
        markPoint: {
          data: [
            ...dot(wins,   '#10b981', 'triangle'),
            ...dot(losses, '#ef4444', 'triangle'),
          ],
          silent: true,
        },
      }],
    };
  }, [bk, side]);

  // ── Render ────────────────────────────────────────────────────────────────

  const action = signal?.action ?? 'HOLD';
  const actionColor = action === 'LONG' ? '#10b981' : action === 'SHORT' ? '#ef4444' : '#6b7280';

  return (
    <div style={S.root}>

      {/* ── Ligne supérieure : signal temps réel ─────────────────────────── */}
      <div style={S.topBar}>

        <div style={S.signalBlock}>
          <span style={{ ...S.actionBadge, color: actionColor, borderColor: actionColor }}>
            {action === 'LONG' ? '↑ LONG' : action === 'SHORT' ? '↓ SHORT' : '— HOLD'}
          </span>
          <div>
            <div style={S.price}>
              {signal ? fmt$(signal.current_price) : '—'}
              <span style={S.symbol}>BTCUSDT</span>
            </div>
            <div style={S.reason}>{signal?.reason ?? 'Connexion au serveur…'}</div>
          </div>
        </div>

        {signal && (
          <div style={S.probeRow}>
            <Probe label="Filtre"  value={signal.p_filter}  thr={signal.filter_thr_long} color="#3b82f6" />
            <Probe label="Long"    value={signal.p_long}    thr={signal.thr_edge_long}   color="#10b981" />
            <Probe label="Short"   value={signal.p_short}   thr={signal.thr_edge_short}  color="#ef4444" />
            <div style={S.regimePill}>{signal.regime}</div>
          </div>
        )}

        {signal && action !== 'HOLD' && (
          <div style={S.riskBlock}>
            <RiskLine label="Stop"  value={fmt$(signal.stop_price)}  color="#ef4444" />
            <RiskLine label="TP"    value={fmt$(signal.take_profit)} color="#10b981" />
          </div>
        )}

        <div style={S.meta}>
          {signal && <span style={S.metaText}>run {signal.run_id.slice(-13)}</span>}
          <span style={{ ...S.metaText, color: age > 90 ? '#6b7280' : '#4ade80' }}>
            {age}s
          </span>
        </div>
      </div>

      {/* ── Stats ─────────────────────────────────────────────────────────── */}
      {bk && (
        <div style={S.statsRow}>
          <Stat label="Trades"  value={String(bk.summary.n_trades)} />
          <Stat label="Win"     value={(bk.summary.win_rate * 100).toFixed(0) + '%'} />
          <Stat label="PF"      value={bk.summary.profit_factor.toFixed(2)}
                color={bk.summary.profit_factor >= 1 ? '#10b981' : '#ef4444'} />
          <Stat label="Sharpe"  value={bk.summary.sharpe_annualized.toFixed(1)}
                color={bk.summary.sharpe_annualized >= 0 ? '#10b981' : '#ef4444'} />
          <Stat label="Retour"  value={fmtPct(bk.summary.total_return_pct)}
                color={bk.summary.total_return_pct >= 0 ? '#10b981' : '#ef4444'} />
          <Stat label="Max DD"  value={fmtPct(-bk.summary.max_drawdown * 100)} color="#f59e0b" />
          <Stat label="Equity"  value={fmt$(bk.summary.final_equity)} />

          {/* Switcher Long / Short */}
          <div style={S.sideSwitcher}>
            {(['long', 'short'] as const).map(s => (
              <button
                key={s}
                onClick={() => setSide(s)}
                disabled={!history?.[s]}
                style={{
                  ...S.sideBtn,
                  background: side === s ? (s === 'long' ? 'rgba(59,130,246,0.2)' : 'rgba(167,139,250,0.2)') : 'transparent',
                  color:      side === s ? (s === 'long' ? '#3b82f6' : '#a78bfa') : '#6b7280',
                  borderColor: side === s ? (s === 'long' ? '#3b82f6' : '#a78bfa') : 'transparent',
                  opacity: history?.[s] ? 1 : 0.3,
                  cursor: history?.[s] ? 'pointer' : 'not-allowed',
                }}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Equity curve ──────────────────────────────────────────────────── */}
      <div style={S.chartBox}>
        {equityOption ? (
          <ReactECharts option={equityOption} style={{ height: '100%' }} notMerge />
        ) : (
          <div style={S.placeholder}>
            {history === null ? 'Chargement…' : 'Pas de données backtest pour ce côté'}
          </div>
        )}
      </div>

      {/* ── Tableau des derniers trades ────────────────────────────────────── */}
      {bk && bk.trades.length > 0 && (
        <div style={S.tableWrap}>
          <table style={S.table}>
            <thead>
              <tr>
                {['Date', 'PnL $', 'PnL %', 'p_side', 'p_filter', 'Equity'].map(h => (
                  <th key={h} style={S.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...bk.trades].reverse().slice(0, 30).map((t, i) => {
                const win = t.pnl_abs > 0;
                return (
                  <tr key={i} style={{ borderBottom: '1px solid #1f2937' }}>
                    <td style={S.td}>{t.date}</td>
                    <td style={{ ...S.td, color: win ? '#10b981' : '#ef4444', fontWeight: 600 }}>
                      {win ? '+' : ''}{t.pnl_abs.toFixed(2)}
                    </td>
                    <td style={{ ...S.td, color: win ? '#10b981' : '#ef4444' }}>
                      {fmtPct(t.pnl_net_pct * 100, 3)}
                    </td>
                    <td style={S.td}>{(t.p_side * 100).toFixed(1)}%</td>
                    <td style={S.td}>{(t.p_tradeable * 100).toFixed(1)}%</td>
                    <td style={S.td}>{fmt$(t.equity)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

    </div>
  );
}

// ── Sous-composants ───────────────────────────────────────────────────────────

function Probe({ label, value, thr, color }: { label: string; value: number; thr: number; color: string }) {
  const pct = Math.min(100, value * 100);
  const pass = value >= thr;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 80 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 11, color: '#6b7280' }}>{label}</span>
        <span style={{ fontSize: 11, color: pass ? color : '#6b7280', fontWeight: 600 }}>
          {(value * 100).toFixed(1)}%
        </span>
      </div>
      <div style={{ height: 4, background: '#1f2937', borderRadius: 2, position: 'relative', overflow: 'visible' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${pct}%`,
                      background: pass ? color : '#374151', borderRadius: 2, transition: 'width 0.3s' }} />
        <div style={{ position: 'absolute', top: -2, left: `${thr * 100}%`,
                      width: 1, height: 8, background: 'rgba(255,255,255,0.35)' }} />
      </div>
    </div>
  );
}

function RiskLine({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: 600, color, fontFamily: 'monospace' }}>{value}</span>
    </div>
  );
}

function Stat({ label, value, color = '#d1d5db' }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ fontSize: 15, fontWeight: 700, color, fontFamily: 'monospace' }}>{value}</span>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const S: Record<string, React.CSSProperties> = {
  root: {
    minHeight: '100vh',
    background: '#0a0e1a',
    color: '#f9fafb',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  topBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 32,
    padding: '16px 24px',
    background: '#111827',
    borderBottom: '1px solid #1f2937',
    flexWrap: 'wrap',
  },
  signalBlock: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
  },
  actionBadge: {
    fontFamily: 'monospace',
    fontSize: 20,
    fontWeight: 800,
    border: '2px solid',
    borderRadius: 8,
    padding: '4px 14px',
    letterSpacing: '0.04em',
    whiteSpace: 'nowrap',
  },
  price: {
    fontSize: 22,
    fontWeight: 700,
    fontFamily: 'monospace',
    display: 'flex',
    alignItems: 'baseline',
    gap: 6,
  },
  symbol: {
    fontSize: 13,
    color: '#6b7280',
    fontWeight: 400,
  },
  reason: {
    fontSize: 12,
    color: '#6b7280',
    fontFamily: 'monospace',
    marginTop: 2,
    maxWidth: 300,
  },
  probeRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 20,
    flex: 1,
    minWidth: 280,
  },
  regimePill: {
    fontSize: 11,
    fontFamily: 'monospace',
    padding: '3px 10px',
    borderRadius: 4,
    background: 'rgba(255,255,255,0.05)',
    color: '#9ca3af',
    border: '1px solid #2d3748',
    whiteSpace: 'nowrap',
  },
  riskBlock: {
    display: 'flex',
    gap: 24,
  },
  meta: {
    marginLeft: 'auto',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    gap: 2,
  },
  metaText: {
    fontSize: 11,
    color: '#4b5563',
    fontFamily: 'monospace',
  },
  statsRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 32,
    padding: '12px 24px',
    background: '#0d1117',
    borderBottom: '1px solid #1f2937',
    flexWrap: 'wrap',
  },
  sideSwitcher: {
    marginLeft: 'auto',
    display: 'flex',
    gap: 4,
  },
  sideBtn: {
    fontSize: 11,
    fontWeight: 700,
    fontFamily: 'monospace',
    padding: '3px 12px',
    border: '1px solid',
    borderRadius: 4,
    transition: 'all 0.15s',
  },
  chartBox: {
    flex: 1,
    minHeight: 320,
    padding: '0 8px',
    background: '#0a0e1a',
  },
  placeholder: {
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#4b5563',
    fontSize: 14,
  },
  tableWrap: {
    overflowX: 'auto',
    borderTop: '1px solid #1f2937',
    maxHeight: 320,
    overflowY: 'auto',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 12,
    fontFamily: 'monospace',
  },
  th: {
    padding: '8px 16px',
    textAlign: 'left' as const,
    color: '#6b7280',
    fontWeight: 500,
    fontSize: 11,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    borderBottom: '1px solid #1f2937',
    background: '#0d1117',
    position: 'sticky' as const,
    top: 0,
  },
  td: {
    padding: '7px 16px',
    color: '#9ca3af',
  },
};
