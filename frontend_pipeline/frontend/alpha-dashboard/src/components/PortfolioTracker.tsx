import React, { useState, useEffect, useCallback, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import { API_BASE_URL } from '../config/api';
import './PortfolioTracker.css';

// ─── Types ───────────────────────────────────────────────────────────────────

interface Position {
  symbol: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  value: number;
  pnl: number;
  pnl_percent: number;
  entry_time: string;
}

interface Trade {
  id: string;
  timestamp: string;
  symbol: string;
  action: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  total: number;
  reason: string;
  confidence: number;
}

interface HistoryPoint {
  timestamp: string;
  total_value: number;
  cash: number;
  invested: number;
  pnl: number;
  pnl_percent: number;
  signal?: string;
  action_taken?: string;
}

interface PortfolioStats {
  total_value: number;
  cash: number;
  invested: number;
  total_pnl: number;
  total_pnl_percent: number;
}

interface PortfolioState {
  initial_capital: number;
  cash: number;
  positions: Position[];
  trades: Trade[];
  history: HistoryPoint[];
  stats: PortfolioStats;
  updated_at: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtUSD(v: number) {
  return v.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString('fr-FR', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function signalColor(s?: string) {
  if (!s) return '#9ca3af';
  if (s === 'LONG') return '#10b981';
  if (s === 'SHORT') return '#ef4444';
  return '#9ca3af';
}

function actionBadge(a?: string) {
  if (a === 'BUY')         return { label: 'BUY',         color: '#10b981' };
  if (a === 'SELL')        return { label: 'SELL',        color: '#ef4444' };
  if (a === 'STOP_LOSS')   return { label: 'STOP',        color: '#f97316' };
  if (a === 'TAKE_PROFIT') return { label: 'TP',          color: '#a78bfa' };
  if (a === 'RESET')       return { label: 'RESET',       color: '#6b7280' };
  return { label: 'HOLD', color: '#4b5563' };
}

// ─── Main Component ───────────────────────────────────────────────────────────

const PortfolioTracker: React.FC = () => {
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [replayIdx, setReplayIdx] = useState<number | null>(null);
  const [replayMode, setReplayMode] = useState(false);
  const [activeTab, setActiveTab] = useState<'chart' | 'positions' | 'trades'>('chart');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/portfolio/state`);
      if (!res.ok) return;
      const data: PortfolioState = await res.json();
      setPortfolio(data);
      setLastUpdate(new Date());
      setLoading(false);
    } catch {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPortfolio();
    intervalRef.current = setInterval(fetchPortfolio, 30_000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [fetchPortfolio]);

  // Pause live updates in replay mode
  useEffect(() => {
    if (replayMode) {
      if (intervalRef.current) clearInterval(intervalRef.current);
    } else {
      setReplayIdx(null);
      intervalRef.current = setInterval(fetchPortfolio, 30_000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [replayMode, fetchPortfolio]);

  const handleReset = async () => {
    if (!window.confirm('Remettre le portfolio à 100 000 $ ? Toutes les positions et l\'historique seront effacés.')) return;
    try {
      const res = await fetch(`${API_BASE_URL}/portfolio/reset`, { method: 'POST' });
      if (!res.ok) return;
      const data: PortfolioState = await res.json();
      setPortfolio(data);
      setReplayMode(false);
      setReplayIdx(null);
    } catch (e) {
      console.error('Reset failed', e);
    }
  };

  // ── Stats to display (live or replay snapshot) ───────────────────────────
  const history = portfolio?.history ?? [];
  const replayPoint = replayMode && replayIdx !== null ? history[replayIdx] : null;

  const displayStats: PortfolioStats = replayPoint
    ? {
        total_value: replayPoint.total_value,
        cash: replayPoint.cash,
        invested: replayPoint.invested,
        total_pnl: replayPoint.pnl,
        total_pnl_percent: replayPoint.pnl_percent,
      }
    : portfolio?.stats ?? { total_value: 0, cash: 0, invested: 0, total_pnl: 0, total_pnl_percent: 0 };

  const initialCapital = portfolio?.initial_capital ?? 100_000;

  // ── Derived metrics ─────────────────────────────────────────────────────
  const trades = portfolio?.trades ?? [];
  const sellTrades = trades.filter(t => t.action === 'SELL');
  const buyMap = new Map<string, Trade>();
  for (const t of [...trades].reverse()) {
    if (t.action === 'BUY') buyMap.set(t.symbol, t);
  }
  const winCount = sellTrades.filter(s => {
    const b = buyMap.get(s.symbol);
    return b && s.price > b.price;
  }).length;
  const winRate = sellTrades.length > 0 ? (winCount / sellTrades.length) * 100 : 0;

  const maxDrawdown = (() => {
    if (history.length < 2) return 0;
    let peak = history[0].total_value, md = 0;
    for (const h of history) {
      if (h.total_value > peak) peak = h.total_value;
      const dd = ((peak - h.total_value) / peak) * 100;
      if (dd > md) md = dd;
    }
    return md;
  })();

  // Latest AI signal
  const lastSignal = history.length > 0 ? history[history.length - 1].signal : undefined;

  // ── Chart ─────────────────────────────────────────────────────────────────
  const getChartOptions = () => {
    if (history.length === 0) return {};

    const times = history.map(h => fmtTime(h.timestamp));
    const values = history.map(h => h.total_value);

    // Trade markers mapped to closest history index
    const tradeMarkers = trades.map(t => {
      const tMs = new Date(t.timestamp).getTime();
      let best = 0, bestDiff = Infinity;
      history.forEach((h, i) => {
        const diff = Math.abs(new Date(h.timestamp).getTime() - tMs);
        if (diff < bestDiff) { bestDiff = diff; best = i; }
      });
      const isBuy = t.action === 'BUY';
      return {
        coord: [best, values[best]],
        symbol: isBuy ? 'triangle' : 'triangle',
        symbolRotate: isBuy ? 0 : 180,
        symbolSize: 14,
        itemStyle: { color: isBuy ? '#10b981' : '#ef4444' },
        label: {
          show: true,
          formatter: isBuy ? '▲' : '▼',
          color: isBuy ? '#10b981' : '#ef4444',
          fontSize: 11,
          offset: isBuy ? [0, -16] : [0, 10],
        },
      };
    });

    // Replay cursor line
    const markLineData = replayMode && replayIdx !== null
      ? [{ xAxis: replayIdx, lineStyle: { color: '#f59e0b', type: 'dashed', width: 2 } }]
      : [];

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1f2937',
        borderColor: '#374151',
        textStyle: { color: '#f9fafb' },
        formatter: (params: any) => {
          const p = params[0];
          const h = history[p.dataIndex];
          const sig = h?.signal ?? 'HOLD';
          const act = h?.action_taken ?? 'NONE';
          return `
            <div style="font-size:12px">
              <b>${times[p.dataIndex]}</b><br/>
              Valeur : <b>$${fmtUSD(p.value)}</b><br/>
              P&L : <span style="color:${h?.pnl >= 0 ? '#10b981' : '#ef4444'}">${h?.pnl >= 0 ? '+' : ''}${h?.pnl_percent?.toFixed(2)}%</span><br/>
              Signal : <span style="color:${signalColor(sig)}">${sig}</span> → ${act}
            </div>`;
        },
      },
      grid: { left: '5%', right: '3%', top: '8%', bottom: '18%' },
      dataZoom: [
        { type: 'inside', throttle: 50 },
        { type: 'slider', height: 20, bottom: 8, borderColor: '#374151',
          fillerColor: 'rgba(96,165,250,0.15)', handleStyle: { color: '#60a5fa' },
          textStyle: { color: '#9ca3af', fontSize: 10 } },
      ],
      xAxis: {
        type: 'category',
        data: times,
        axisLabel: { color: '#9ca3af', fontSize: 10, interval: Math.max(0, Math.floor(history.length / 8)) },
        axisLine: { lineStyle: { color: '#374151' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: '#9ca3af', fontSize: 11,
          formatter: (v: number) => `$${(v / 1000).toFixed(0)}k`,
        },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: '#1f2937' } },
      },
      series: [
        {
          name: 'Portfolio',
          type: 'line',
          data: values,
          smooth: 0.3,
          showSymbol: false,
          lineStyle: { color: '#60a5fa', width: 2 },
          areaStyle: {
            color: {
              type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(96,165,250,0.25)' },
                { offset: 1, color: 'rgba(96,165,250,0)' },
              ],
            },
          },
          markPoint: {
            data: tradeMarkers,
            animation: false,
          },
          markLine: {
            silent: true,
            symbol: 'none',
            data: markLineData,
          },
        },
      ],
    };
  };

  // ── Replay slider ────────────────────────────────────────────────────────
  const handleChartClick = (params: any) => {
    if (!replayMode) return;
    setReplayIdx(params.dataIndex ?? 0);
  };

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="pt-loading">
        <div className="pt-spinner" />
        <span>Chargement du portfolio…</span>
      </div>
    );
  }

  const pnlPos = displayStats.total_pnl >= 0;

  return (
    <div className="pt-root">
      {/* ── Header ── */}
      <div className="pt-header">
        <div className="pt-title-row">
          <div>
            <h2 className="pt-title">Portfolio Trading Simulator</h2>
            <p className="pt-subtitle">
              Mode autonome · IA tradant BTCUSDT · Stop {3}% / TP {6}%
            </p>
          </div>
          <div className="pt-header-right">
            {/* Live signal pill */}
            <div className="pt-signal-pill" style={{ borderColor: signalColor(lastSignal), color: signalColor(lastSignal) }}>
              Signal IA : <b>{lastSignal ?? '—'}</b>
            </div>
            {lastUpdate && !replayMode && (
              <span className="pt-last-update">
                Mis à jour {lastUpdate.toLocaleTimeString('fr-FR')}
              </span>
            )}
            <button className="pt-reset-btn" onClick={handleReset}>
              Réinitialiser
            </button>
          </div>
        </div>

        {/* ── KPI cards ── */}
        <div className="pt-kpi-row">
          <div className="pt-kpi main">
            <div className="pt-kpi-label">Valeur totale</div>
            <div className="pt-kpi-value">${fmtUSD(displayStats.total_value)}</div>
            <div className={`pt-kpi-sub ${pnlPos ? 'pos' : 'neg'}`}>
              {pnlPos ? '+' : ''}{fmtUSD(displayStats.total_pnl)} ({displayStats.total_pnl_percent.toFixed(2)}%)
            </div>
          </div>
          <div className="pt-kpi">
            <div className="pt-kpi-label">Cash disponible</div>
            <div className="pt-kpi-value">${fmtUSD(displayStats.cash)}</div>
            <div className="pt-kpi-sub neutral">
              {displayStats.total_value > 0
                ? ((displayStats.cash / displayStats.total_value) * 100).toFixed(1)
                : '0'}% du portfolio
            </div>
          </div>
          <div className="pt-kpi">
            <div className="pt-kpi-label">Investi</div>
            <div className="pt-kpi-value">${fmtUSD(displayStats.invested)}</div>
            <div className="pt-kpi-sub neutral">
              {portfolio?.positions.length ?? 0} position(s)
            </div>
          </div>
          <div className="pt-kpi">
            <div className="pt-kpi-label">Win Rate</div>
            <div className="pt-kpi-value">{winRate.toFixed(1)}%</div>
            <div className="pt-kpi-sub neutral">{winCount}W / {sellTrades.length - winCount}L</div>
          </div>
          <div className="pt-kpi">
            <div className="pt-kpi-label">Max Drawdown</div>
            <div className="pt-kpi-value neg">{maxDrawdown.toFixed(2)}%</div>
            <div className="pt-kpi-sub neutral">{trades.length} trades</div>
          </div>
        </div>
      </div>

      {/* ── Replay banner ── */}
      {replayMode && replayPoint && (
        <div className="pt-replay-banner">
          <span className="pt-replay-icon">⏪</span>
          <span>
            Replay — {fmtTime(replayPoint.timestamp)} ·
            Valeur <b>${fmtUSD(replayPoint.total_value)}</b> ·
            Signal <span style={{ color: signalColor(replayPoint.signal) }}>{replayPoint.signal}</span> ·
            Action <b>{replayPoint.action_taken}</b>
          </span>
        </div>
      )}

      {/* ── Tabs ── */}
      <div className="pt-tabs">
        <button className={`pt-tab ${activeTab === 'chart' ? 'active' : ''}`} onClick={() => setActiveTab('chart')}>
          Évolution
        </button>
        <button className={`pt-tab ${activeTab === 'positions' ? 'active' : ''}`} onClick={() => setActiveTab('positions')}>
          Positions ({portfolio?.positions.length ?? 0})
        </button>
        <button className={`pt-tab ${activeTab === 'trades' ? 'active' : ''}`} onClick={() => setActiveTab('trades')}>
          Historique ({trades.length})
        </button>
        <div className="pt-tab-spacer" />
        <label className="pt-replay-toggle">
          <input type="checkbox" checked={replayMode} onChange={e => setReplayMode(e.target.checked)} />
          <span>Mode Replay</span>
        </label>
      </div>

      {/* ── Chart tab ── */}
      {activeTab === 'chart' && (
        <div className="pt-panel">
          {history.length === 0 ? (
            <div className="pt-empty">
              <div className="pt-empty-icon">⏳</div>
              <p>En attente du premier snapshot…</p>
              <small>L'IA enregistre un point par minute dès le premier signal.</small>
            </div>
          ) : (
            <>
              <ReactECharts
                option={getChartOptions()}
                style={{ height: 340, width: '100%' }}
                onEvents={replayMode ? { click: handleChartClick } : {}}
              />
              {replayMode && history.length > 0 && (
                <div className="pt-replay-slider-row">
                  <span className="pt-slider-label">{fmtTime(history[0].timestamp)}</span>
                  <input
                    type="range"
                    min={0}
                    max={history.length - 1}
                    value={replayIdx ?? history.length - 1}
                    onChange={e => setReplayIdx(Number(e.target.value))}
                    className="pt-slider"
                  />
                  <span className="pt-slider-label">{fmtTime(history[history.length - 1].timestamp)}</span>
                </div>
              )}
              {/* Legend */}
              <div className="pt-chart-legend">
                <span><span className="pt-dot" style={{ background: '#10b981' }} />BUY</span>
                <span><span className="pt-dot" style={{ background: '#ef4444' }} />SELL</span>
                <span><span className="pt-dot" style={{ background: '#f59e0b' }} />Replay cursor</span>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Positions tab ── */}
      {activeTab === 'positions' && (
        <div className="pt-panel">
          {(portfolio?.positions.length ?? 0) === 0 ? (
            <div className="pt-empty">
              <div className="pt-empty-icon">📭</div>
              <p>Aucune position ouverte</p>
              <small>L'IA ouvrira une position dès le prochain signal LONG.</small>
            </div>
          ) : (
            <div className="pt-table-wrap">
              <table className="pt-table">
                <thead>
                  <tr>
                    <th>Symbole</th>
                    <th>Quantité</th>
                    <th>Prix entrée</th>
                    <th>Prix actuel</th>
                    <th>Valeur</th>
                    <th>P&L ($)</th>
                    <th>P&L (%)</th>
                    <th>Durée</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolio!.positions.map(pos => {
                    const mins = Math.floor((Date.now() - new Date(pos.entry_time).getTime()) / 60_000);
                    return (
                      <tr key={pos.symbol} className={pos.pnl >= 0 ? 'row-pos' : 'row-neg'}>
                        <td className="cell-symbol">{pos.symbol}</td>
                        <td>{pos.quantity.toFixed(6)}</td>
                        <td>${fmtUSD(pos.entry_price)}</td>
                        <td>${fmtUSD(pos.current_price)}</td>
                        <td>${fmtUSD(pos.value)}</td>
                        <td className={pos.pnl >= 0 ? 'pos' : 'neg'}>${fmtUSD(pos.pnl)}</td>
                        <td className={pos.pnl_percent >= 0 ? 'pos' : 'neg'}>
                          {pos.pnl_percent >= 0 ? '+' : ''}{pos.pnl_percent.toFixed(2)}%
                        </td>
                        <td>{mins >= 60 ? `${Math.floor(mins / 60)}h${mins % 60}m` : `${mins}m`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Trade history tab ── */}
      {activeTab === 'trades' && (
        <div className="pt-panel">
          {trades.length === 0 ? (
            <div className="pt-empty">
              <div className="pt-empty-icon">📋</div>
              <p>Aucun trade exécuté</p>
            </div>
          ) : (
            <div className="pt-table-wrap">
              <table className="pt-table">
                <thead>
                  <tr>
                    <th>Heure</th>
                    <th>Symbole</th>
                    <th>Action</th>
                    <th>Quantité</th>
                    <th>Prix</th>
                    <th>Total</th>
                    <th>Confiance</th>
                    <th>Raison</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map(t => {
                    const badge = actionBadge(t.action);
                    return (
                      <tr key={t.id} className={t.action === 'BUY' ? 'row-buy' : 'row-sell'}>
                        <td className="cell-time">{fmtTime(t.timestamp)}</td>
                        <td className="cell-symbol">{t.symbol}</td>
                        <td>
                          <span className="pt-badge" style={{ background: badge.color + '22', color: badge.color, border: `1px solid ${badge.color}44` }}>
                            {badge.label}
                          </span>
                        </td>
                        <td>{t.quantity.toFixed(6)}</td>
                        <td>${fmtUSD(t.price)}</td>
                        <td>${fmtUSD(t.total)}</td>
                        <td>
                          <div className="pt-conf-bar">
                            <div className="pt-conf-fill" style={{ width: `${t.confidence * 100}%`, background: t.confidence > 0.7 ? '#10b981' : t.confidence > 0.5 ? '#f59e0b' : '#ef4444' }} />
                            <span>{(t.confidence * 100).toFixed(0)}%</span>
                          </div>
                        </td>
                        <td className="cell-reason">{t.reason}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default PortfolioTracker;
