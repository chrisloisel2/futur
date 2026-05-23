import React, { useEffect, useState, useCallback } from 'react';
import { PaperApi, Market, Signal, PaperState, RunStatus, Trade } from '../services/PaperApi';
import './PaperDashboard.css';

const fmt = (v: number | null | undefined, dec = 2, suffix = '') =>
  v == null ? '–' : `${v.toFixed(dec)}${suffix}`;

const fmtPct = (v: number | null | undefined) => fmt(v, 2, '%');

function MarketHeader({ market }: { market: Market | null }) {
  if (!market) return <div className="market-header skeleton" />;
  const bull = market.regime === 'BULL';
  return (
    <div className={`market-header regime-${market.regime.toLowerCase()}`}>
      <div className="mh-price">
        <span className="mh-symbol">BTC/USDT</span>
        <span className="mh-val">${market.price?.toLocaleString('en-US', { maximumFractionDigits: 0 }) ?? '–'}</span>
      </div>
      <div className="mh-stats">
        <Stat label="24h" value={fmtPct(market.ret_24h)} color={market.ret_24h >= 0 ? 'green' : 'red'} />
        <Stat label="7j" value={fmtPct(market.ret_7d)} color={market.ret_7d >= 0 ? 'green' : 'red'} />
        <Stat label="vs EMA200" value={fmtPct(market.vs_ema200)} color={market.vs_ema200 >= 0 ? 'green' : 'red'} />
        <Stat label="vs EMA50" value={fmtPct(market.vs_ema50)} color={market.vs_ema50 >= 0 ? 'green' : 'red'} />
      </div>
      <div className={`regime-badge ${bull ? 'bull' : 'bear'}`}>{market.regime}</div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${color ?? ''}`}>{value}</span>
    </div>
  );
}

function SignalCard({ signal }: { signal: Signal | null }) {
  if (!signal) return <div className="card skeleton h120" />;
  const action = signal.action ?? 'NO_DATA';
  const isLong = action === 'LONG';
  const pLong = typeof signal.p_long === 'number' ? signal.p_long : null;
  const threshold = typeof signal.threshold === 'number' ? signal.threshold : null;
  const progress = pLong != null && threshold != null ? Math.min((pLong / threshold) * 100, 100) : 0;

  return (
    <div className={`card signal-card ${isLong ? 'signal-long' : 'signal-none'}`}>
      <div className="card-head">
        <span className="card-title">Signal courant</span>
        <span className={`action-badge ${isLong ? 'long' : 'neutral'}`}>{action}</span>
      </div>
      <div className="signal-body">
        <div className="prob-row">
          <span className="prob-label">p_long</span>
          <span className="prob-val">{pLong != null ? pLong.toFixed(4) : '–'}</span>
          <span className="prob-thresh">/ seuil {threshold != null ? threshold.toFixed(4) : '–'}</span>
        </div>
        <div className="prob-bar-bg">
          <div
            className={`prob-bar-fill ${isLong ? 'fill-long' : 'fill-neutral'}`}
            style={{ width: `${progress}%` }}
          />
          {threshold != null && pLong != null && (
            <div className="prob-bar-thresh" style={{ left: '100%' }} />
          )}
        </div>
        <div className="signal-meta">
          {signal.regime && <span className="meta-pill">{signal.regime}</span>}
          {signal.suppressor && <span className="meta-pill">{signal.suppressor}</span>}
          {typeof signal.sizer === 'number' && (
            <span className="meta-pill">size×{signal.sizer.toFixed(2)}</span>
          )}
          {signal.timestamp && (
            <span className="meta-ts">{new Date(signal.timestamp).toLocaleString('fr-FR')}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function GatesPanel({ state }: { state: PaperState | null }) {
  if (!state) return <div className="card skeleton h200" />;
  return (
    <div className="card gates-card">
      <div className="card-head">
        <span className="card-title">Gates paper trading</span>
        <span className={`verdict-badge ${state.all_gates_ok ? 'candidate' : 'paper'}`}>
          {state.verdict}
        </span>
      </div>
      <div className="gates-grid">
        {state.gates.map(g => {
          const pct = g.target > 0 && g.value != null
            ? Math.min((g.value / g.target) * 100, 100)
            : g.ok ? 100 : 0;
          return (
            <div key={g.name} className={`gate-row ${g.ok ? 'ok' : 'pending'}`}>
              <span className="gate-icon">{g.ok ? '✓' : '·'}</span>
              <span className="gate-name">{g.name}</span>
              <div className="gate-bar-bg">
                <div className="gate-bar-fill" style={{ width: `${pct}%` }} />
              </div>
              <span className="gate-value">
                {g.value != null ? g.value : '–'}{g.unit} / {g.target}{g.unit}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetricsRow({ state }: { state: PaperState | null }) {
  if (!state) return <div className="metrics-row skeleton" />;
  return (
    <div className="metrics-row">
      <MetricCard label="Trades" value={String(state.total_trades)} sub={`${state.total_wins} wins`} />
      <MetricCard label="WR" value={state.wr_live != null ? fmtPct(state.wr_live * 100) : '–'} />
      <MetricCard label="PF live" value={state.pf_live != null ? fmt(state.pf_live, 3) : 'N/A'} />
      <MetricCard label="Max DD" value={fmtPct(state.max_dd_pct)} color={state.max_dd_pct > 2 ? 'red' : 'green'} />
      <MetricCard label="PnL cumul" value={fmtPct(state.cumulative_pnl_pct)} color={state.cumulative_pnl_pct >= 0 ? 'green' : 'red'} />
      <MetricCard label="PnL 7j" value={fmtPct(state.weekly_pnl_pct)} color={state.weekly_pnl_pct >= 0 ? 'green' : 'red'} />
    </div>
  );
}

function MetricCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <span className={`metric-value ${color ?? ''}`}>{value}</span>
      {sub && <span className="metric-sub">{sub}</span>}
    </div>
  );
}

function RunControls({ status, onRun }: { status: RunStatus | null; onRun: () => void }) {
  const running = status?.signal_running || status?.data_update_running;
  return (
    <div className="card run-card">
      <div className="card-head">
        <span className="card-title">Exécution</span>
        {running && <span className="running-badge">EN COURS…</span>}
      </div>
      <button className="run-btn" onClick={onRun} disabled={!!running}>
        {running ? 'Signal en cours…' : '▶ Lancer signal'}
      </button>
      {status?.log && status.log.length > 0 && (
        <pre className="run-log">{status.log.join('\n')}</pre>
      )}
    </div>
  );
}

// ── View: Signal ──────────────────────────────────────────────────────────────

export function SignalView() {
  const [market, setMarket] = useState<Market | null>(null);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [state, setState] = useState<PaperState | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);

  const refresh = useCallback(async () => {
    try { setMarket(await PaperApi.market()); } catch {}
    try { setSignal(await PaperApi.signalLatest()); } catch {}
    try { setState(await PaperApi.state()); } catch {}
    try { setStatus(await PaperApi.runStatus()); } catch {}
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  }, [refresh]);

  // Poll faster when a run is active
  useEffect(() => {
    if (!status?.signal_running && !status?.data_update_running) return;
    const t = setInterval(async () => {
      try { setStatus(await PaperApi.runStatus()); } catch {}
      try { setSignal(await PaperApi.signalLatest()); } catch {}
    }, 5_000);
    return () => clearInterval(t);
  }, [status?.signal_running, status?.data_update_running]);

  const handleRun = async () => {
    try { await PaperApi.triggerRun(); setStatus(await PaperApi.runStatus()); } catch {}
  };

  return (
    <div className="paper-view">
      <MarketHeader market={market} />
      <MetricsRow state={state} />
      <div className="paper-grid">
        <div className="paper-col-left">
          <SignalCard signal={signal} />
          <RunControls status={status} onRun={handleRun} />
        </div>
        <div className="paper-col-right">
          <GatesPanel state={state} />
        </div>
      </div>
    </div>
  );
}

// ── View: Trades ──────────────────────────────────────────────────────────────

export function TradesView() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    PaperApi.trades()
      .then(t => setTrades([...t].reverse()))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="skeleton h300" />;
  if (!trades.length) return (
    <div className="empty-state">Aucun trade enregistré — paper trading en attente de signal.</div>
  );

  return (
    <div className="trades-view">
      <table className="trades-table">
        <thead>
          <tr>
            <th>Date</th><th>Symbole</th><th>Côté</th>
            <th>Entrée</th><th>Sortie</th><th>PnL %</th><th>Durée (h)</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const pnl = typeof t.pnl_pct === 'number' ? t.pnl_pct : null;
            return (
              <tr key={i} className={pnl != null ? (pnl >= 0 ? 'row-win' : 'row-loss') : ''}>
                <td>{t.timestamp ? new Date(t.timestamp).toLocaleString('fr-FR') : '–'}</td>
                <td>{t.symbol ?? '–'}</td>
                <td>{t.side ?? '–'}</td>
                <td>{t.entry != null ? `$${Number(t.entry).toLocaleString()}` : '–'}</td>
                <td>{t.exit != null ? `$${Number(t.exit).toLocaleString()}` : '–'}</td>
                <td className={pnl != null ? (pnl >= 0 ? 'green' : 'red') : ''}>
                  {fmtPct(pnl)}
                </td>
                <td>{t.hold_hours != null ? Number(t.hold_hours).toFixed(1) : '–'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── View: Modèle ─────────────────────────────────────────────────────────────

const WF_RESULTS = [
  { fold: '2020', trades: 0,  wr: null,   pf: null,  note: 'anecdotique (0 trade)' },
  { fold: '2021', trades: 5,  wr: 100,    pf: null,  note: 'non stat (5 trades)' },
  { fold: '2022', trades: 7,  wr: 100,    pf: null,  note: 'non stat (7 trades)' },
  { fold: '2023', trades: 14, wr: 100,    pf: null,  note: 'non stat (14 trades)' },
  { fold: '2024', trades: 86, wr: 98,     pf: 228,   note: '✓ valide' },
  { fold: '2025', trades: 80, wr: 96,     pf: 74,    note: '✓ valide' },
];

export function ModelView() {
  return (
    <div className="model-view">
      <div className="card">
        <div className="card-head"><span className="card-title">Walk-Forward BTC+ETH — 6 folds 2020-2025</span></div>
        <div className="wf-note">
          Alpha validé sur 2 folds statistiquement significatifs (n&gt;80). Les folds 2020-2023 sont anecdotiques (trop peu de trades).
        </div>
        <table className="wf-table">
          <thead><tr><th>Fold</th><th>Trades</th><th>WR</th><th>PF</th><th>Note</th></tr></thead>
          <tbody>
            {WF_RESULTS.map(r => (
              <tr key={r.fold} className={r.trades >= 50 ? 'row-valid' : 'row-weak'}>
                <td>{r.fold}</td>
                <td>{r.trades}</td>
                <td>{r.wr != null ? `${r.wr}%` : '–'}</td>
                <td>{r.pf != null ? r.pf : 'N/A'}</td>
                <td>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="model-meta-grid">
        <div className="card meta-card">
          <div className="card-head"><span className="card-title">Configuration</span></div>
          <ul className="meta-list">
            <li>Assets : <strong>BTC+ETH uniquement</strong></li>
            <li>TRM Fleet : 100 modèles (10 horizons × 10 archetypes)</li>
            <li>Fréquence : ~80 trades/an (~1.5/semaine)</li>
            <li>Threshold : 0.55</li>
            <li>SHORT : <strong>désactivé définitivement</strong></li>
            <li>Bear regime : sizing ×0.65</li>
          </ul>
        </div>
        <div className="card meta-card">
          <div className="card-head"><span className="card-title">Risque actif</span></div>
          <ul className="meta-list">
            <li>KillSwitch intraday : –1%</li>
            <li>KillSwitch hebdo : –5%</li>
            <li>KillSwitch crash BTC : –30% / 60j</li>
            <li>DynamicSizer : vol-target 15% ann.</li>
            <li>MetaSuppressor : vol spike / funding / momentum</li>
            <li>DD max paper : &lt;3%</li>
          </ul>
        </div>
        <div className="card meta-card">
          <div className="card-head"><span className="card-title">Gates live</span></div>
          <ul className="meta-list">
            <li>Durée : ≥90 jours</li>
            <li>Trades : ≥100</li>
            <li>PF live : &gt;1.30</li>
            <li>DD max : &lt;3%</li>
            <li>Erreurs comptables : 0</li>
            <li>Drift critique : 0</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
