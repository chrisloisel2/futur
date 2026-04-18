import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactECharts from 'echarts-for-react';
import './RealtimePredictions.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PipelineSignal {
  symbol: string;
  run_id: string;
  timestamp: string;
  refreshed_at: string;
  current_price: number;
  action: 'LONG' | 'SHORT' | 'HOLD';
  reason: string;
  // Level 0
  p_filter: number;
  filter_thr_long: number;
  filter_thr_short: number;
  filter_passed_long: boolean;
  filter_passed_short: boolean;
  // Level 1
  regime: string;
  dist_ema50: number;
  rsi: number;
  // Level 2
  p_long: number;
  p_short: number;
  thr_edge_long: number;
  thr_edge_short: number;
  long_signal: boolean;
  short_signal: boolean;
  // Level 7
  qty: number;
  stop_price: number;
  take_profit: number;
  // History
  history?: HistoryPoint[];
}

interface HistoryPoint {
  timestamp: string;
  action: string;
  p_long: number;
  p_short: number;
  p_filter: number;
  price: number;
}

// ── Constantes visuelles ──────────────────────────────────────────────────────

const ACTION_STYLE: Record<string, { bg: string; border: string; text: string; label: string }> = {
  LONG:  { bg: 'rgba(16,185,129,0.12)', border: '#10B981', text: '#10B981',  label: 'LONG ↑' },
  SHORT: { bg: 'rgba(239,68,68,0.12)',  border: '#EF4444', text: '#EF4444',  label: 'SHORT ↓' },
  HOLD:  { bg: 'rgba(107,114,128,0.12)',border: '#6B7280', text: '#9CA3AF',  label: 'HOLD —' },
};

const REGIME_STYLE: Record<string, React.CSSProperties> = {
  SHORTABLE: { background: 'rgba(239,68,68,0.15)',   color: '#EF4444' },
  NEUTRAL:   { background: 'rgba(245,158,11,0.15)',  color: '#F59E0B' },
  NO_SHORT:  { background: 'rgba(59,130,246,0.15)',  color: '#3B82F6' },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtPrice = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const fmtPct = (n: number) => `${(n * 100).toFixed(1)}%`;

const Bar: React.FC<{
  value: number;
  threshold?: number;
  color: string;
  label: string;
  sublabel?: string;
}> = ({ value, threshold, color, label, sublabel }) => (
  <div className="rp-bar-row">
    <div className="rp-bar-labels">
      <span className="rp-bar-label">{label}</span>
      {sublabel && <span className="rp-bar-sublabel">{sublabel}</span>}
    </div>
    <div className="rp-bar-track">
      <div className="rp-bar-fill" style={{ width: `${Math.min(100, value * 100)}%`, background: color }} />
      {threshold !== undefined && (
        <div className="rp-bar-threshold" style={{ left: `${threshold * 100}%` }} />
      )}
    </div>
    <span className="rp-bar-value" style={{ color }}>{fmtPct(value)}</span>
  </div>
);

const LevelCard: React.FC<{
  level: string;
  title: string;
  passed?: boolean | null;
  children: React.ReactNode;
}> = ({ level, title, passed, children }) => (
  <div className={`rp-level-card ${passed === true ? 'passed' : passed === false ? 'blocked' : ''}`}>
    <div className="rp-level-header">
      <span className="rp-level-badge">{level}</span>
      <span className="rp-level-title">{title}</span>
      {passed !== null && passed !== undefined && (
        <span className={`rp-level-status ${passed ? 'ok' : 'ko'}`}>
          {passed ? '✓' : '✗'}
        </span>
      )}
    </div>
    <div className="rp-level-body">{children}</div>
  </div>
);

// ── Composant principal ───────────────────────────────────────────────────────

const RealtimePredictions: React.FC = () => {
  const [signal, setSignal] = useState<PipelineSignal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchSignal = useCallback(async () => {
    try {
      const res  = await fetch('http://localhost:8000/pipeline/signal');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: PipelineSignal = await res.json();
      setSignal(data);
      setLastUpdate(new Date());
      setError(null);
    } catch (e: any) {
      setError(e.message ?? 'Erreur réseau');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignal();
    intervalRef.current = setInterval(fetchSignal, 60_000); // 1 bar = 1h → refresh toutes les 60s
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [fetchSignal]);

  // ── Chart historique ────────────────────────────────────────────────────────
  const chartOption = signal?.history?.length
    ? {
        backgroundColor: 'transparent',
        grid: { left: '5%', right: '5%', top: '15%', bottom: '15%' },
        legend: {
          data: ['p_long', 'p_short', 'p_filter'],
          textStyle: { color: '#9CA3AF', fontSize: 11 },
          top: 0,
        },
        xAxis: {
          type: 'category',
          data: signal.history.map(h =>
            new Date(h.timestamp).toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit' })
          ),
          axisLabel: { color: '#6B7280', fontSize: 10 },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
        },
        yAxis: {
          type: 'value',
          min: 0,
          max: 1,
          axisLabel: { color: '#6B7280', fontSize: 10, formatter: (v: number) => `${(v*100).toFixed(0)}%` },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(17,24,39,0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          textStyle: { color: '#F9FAFB', fontSize: 12 },
          formatter: (params: any[]) =>
            params.map((p: any) => `${p.marker}${p.seriesName}: ${(p.value*100).toFixed(1)}%`).join('<br/>'),
        },
        series: [
          {
            name: 'p_long',
            type: 'line',
            data: signal.history.map(h => h.p_long),
            smooth: true,
            lineStyle: { color: '#10B981', width: 2 },
            itemStyle: { color: '#10B981' },
            areaStyle: { color: 'rgba(16,185,129,0.08)' },
          },
          {
            name: 'p_short',
            type: 'line',
            data: signal.history.map(h => h.p_short),
            smooth: true,
            lineStyle: { color: '#EF4444', width: 2 },
            itemStyle: { color: '#EF4444' },
            areaStyle: { color: 'rgba(239,68,68,0.06)' },
          },
          {
            name: 'p_filter',
            type: 'line',
            data: signal.history.map(h => h.p_filter),
            smooth: true,
            lineStyle: { color: '#6B7280', width: 1, type: 'dashed' },
            itemStyle: { color: '#6B7280' },
          },
        ],
      }
    : null;

  // ── Rendu ──────────────────────────────────────────────────────────────────
  const actionStyle = signal ? ACTION_STYLE[signal.action] : ACTION_STYLE.HOLD;
  const regimeStyle = signal ? (REGIME_STYLE[signal.regime] ?? REGIME_STYLE.NEUTRAL) : REGIME_STYLE.NEUTRAL;

  return (
    <div className="rp-root">
      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="rp-header">
        <div>
          <h1 className="rp-title">Live Signal</h1>
          <p className="rp-subtitle">Modèles ML en production · barre 1h · BTCUSDT</p>
        </div>
        <div className="rp-header-right">
          {signal && (
            <div className="rp-run-badge">run: {signal.run_id}</div>
          )}
          <div className={`rp-live-dot ${loading ? 'loading' : error ? 'error' : 'live'}`} />
          {lastUpdate && (
            <span className="rp-last-update">
              {lastUpdate.toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          <button className="rp-refresh-btn" onClick={fetchSignal} title="Forcer refresh">
            ↻
          </button>
        </div>
      </div>

      {/* ── Error / Loading ────────────────────────────────────────────────── */}
      {error && (
        <div className="rp-error">
          Serveur non disponible — <strong>{error}</strong>
          <br /><small>Vérifier que api_server.py tourne sur :8000</small>
        </div>
      )}

      {loading && !signal && (
        <div className="rp-loading">Initialisation du moteur d'inférence…</div>
      )}

      {signal && (
        <div className="rp-content">
          {/* ── Carte principale ──────────────────────────────────────────── */}
          <div className="rp-main-card" style={{ borderColor: actionStyle.border, background: actionStyle.bg }}>
            {/* Action */}
            <div className="rp-action-row">
              <span className="rp-action-badge" style={{ color: actionStyle.text, borderColor: actionStyle.border }}>
                {actionStyle.label}
              </span>
              <span className="rp-price">{fmtPrice(signal.current_price)}</span>
            </div>

            {/* Reason */}
            <p className="rp-reason">{signal.reason}</p>

            {/* Stop / TP / Qty */}
            {signal.action !== 'HOLD' && (
              <div className="rp-risk-row">
                <div className="rp-risk-item">
                  <span className="rp-risk-label">Stop</span>
                  <span className="rp-risk-value red">{fmtPrice(signal.stop_price)}</span>
                </div>
                <div className="rp-risk-sep">→</div>
                <div className="rp-risk-item">
                  <span className="rp-risk-label">Take Profit</span>
                  <span className="rp-risk-value green">{fmtPrice(signal.take_profit)}</span>
                </div>
                <div className="rp-risk-sep">·</div>
                <div className="rp-risk-item">
                  <span className="rp-risk-label">Qty</span>
                  <span className="rp-risk-value">{signal.qty.toFixed(5)} BTC</span>
                </div>
              </div>
            )}
          </div>

          {/* ── Cascade pipeline ──────────────────────────────────────────── */}
          <div className="rp-pipeline">

            {/* Level 0 */}
            <LevelCard
              level="L0"
              title="Filtre Tradeable"
              passed={signal.filter_passed_long || signal.filter_passed_short}
            >
              <Bar
                value={signal.p_filter}
                threshold={signal.filter_thr_long}
                color="#3B82F6"
                label="p_filter"
                sublabel={`seuil long ${fmtPct(signal.filter_thr_long)} · court ${fmtPct(signal.filter_thr_short)}`}
              />
              <div className="rp-level-tags">
                <span className={`rp-tag ${signal.filter_passed_long ? 'ok' : 'ko'}`}>
                  Long {signal.filter_passed_long ? '✓' : '✗'}
                </span>
                <span className={`rp-tag ${signal.filter_passed_short ? 'ok' : 'ko'}`}>
                  Short {signal.filter_passed_short ? '✓' : '✗'}
                </span>
              </div>
            </LevelCard>

            <div className="rp-arrow">↓</div>

            {/* Level 1 */}
            <LevelCard
              level="L1"
              title="Régime de Marché"
              passed={signal.regime !== 'NO_SHORT' || signal.long_signal}
            >
              <div className="rp-regime-row">
                <span className="rp-regime-badge" style={regimeStyle}>
                  {signal.regime}
                </span>
                <div className="rp-regime-details">
                  <span>RSI {signal.rsi.toFixed(1)}</span>
                  <span>EMA50 {(signal.dist_ema50 * 100).toFixed(2)}%</span>
                </div>
              </div>
            </LevelCard>

            <div className="rp-arrow">↓</div>

            {/* Level 2 Long */}
            <LevelCard
              level="L2"
              title="Edge Scorer — Long"
              passed={signal.long_signal}
            >
              <Bar
                value={signal.p_long}
                threshold={signal.thr_edge_long}
                color="#10B981"
                label="p_long"
                sublabel={`seuil ${fmtPct(signal.thr_edge_long)}`}
              />
            </LevelCard>

            <div className="rp-arrow">↓</div>

            {/* Level 2 Short */}
            <LevelCard
              level="L2"
              title="Edge Scorer — Short"
              passed={signal.short_signal}
            >
              <Bar
                value={signal.p_short}
                threshold={signal.thr_edge_short}
                color="#EF4444"
                label="p_short (calibré)"
                sublabel={`seuil ${fmtPct(signal.thr_edge_short)}`}
              />
              {signal.regime === 'NO_SHORT' && (
                <p className="rp-blocked-note">Bloqué — régime NO_SHORT</p>
              )}
            </LevelCard>

            <div className="rp-arrow">↓</div>

            {/* Level 7 */}
            <LevelCard level="L7" title="Risk Controller" passed={signal.action !== 'HOLD'}>
              {signal.action !== 'HOLD' ? (
                <div className="rp-risk-grid">
                  <div>
                    <span className="rp-risk-label">Stop Loss</span>
                    <span className="rp-risk-value red">{fmtPrice(signal.stop_price)}</span>
                  </div>
                  <div>
                    <span className="rp-risk-label">Take Profit</span>
                    <span className="rp-risk-value green">{fmtPrice(signal.take_profit)}</span>
                  </div>
                  <div>
                    <span className="rp-risk-label">Quantité</span>
                    <span className="rp-risk-value">{signal.qty.toFixed(5)} BTC</span>
                  </div>
                </div>
              ) : (
                <p className="rp-blocked-note">Pas de position — signal HOLD</p>
              )}
            </LevelCard>
          </div>

          {/* ── Historique probabilities ───────────────────────────────────── */}
          {chartOption && (signal.history?.length ?? 0) >= 3 && (
            <div className="rp-chart-section">
              <h3 className="rp-chart-title">Historique des probabilités</h3>
              <ReactECharts option={chartOption} style={{ height: '260px' }} />
            </div>
          )}

          {/* ── Footer ────────────────────────────────────────────────────── */}
          <p className="rp-footer">
            Dernière barre : {new Date(signal.timestamp).toLocaleString('fr')}
            {' · '}Run : {signal.run_id}
          </p>
        </div>
      )}
    </div>
  );
};

export default RealtimePredictions;
