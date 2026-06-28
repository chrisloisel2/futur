import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  PaperApi,
  PortfolioLive,
  AgentLive,
  Trade,
} from '../services/PaperApi';
import { API_BASE_URL } from '../config/api';
import './LiveDashboard.css';

// ── Constants ─────────────────────────────────────────────────────────────────

const TICKER: Record<string, string> = {
  BTCUSDT: 'BTC', ETHUSDT: 'ETH', BNBUSDT: 'BNB', SOLUSDT: 'SOL',
  XRPUSDT: 'XRP', DOGEUSDT: 'DOGE', ADAUSDT: 'ADA', AVAXUSDT: 'AVAX',
  DOTUSDT: 'DOT', LINKUSDT: 'LINK',
};

const COLORS: Record<string, string> = {
  BTCUSDT: '#f7931a', ETHUSDT: '#627eea', BNBUSDT: '#f3ba2f', SOLUSDT: '#9945ff',
  XRPUSDT: '#00aae4', DOGEUSDT: '#c2a633', ADAUSDT: '#0033ad', AVAXUSDT: '#e84142',
  DOTUSDT: '#e6007a', LINKUSDT: '#2a5ada',
};


// ── Formatters ────────────────────────────────────────────────────────────────

const fmtPrice = (v: number | null | undefined): string => {
  if (v == null) return '–';
  if (v >= 10000) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  if (v >= 100)   return `$${v.toFixed(2)}`;
  if (v >= 1)     return `$${v.toFixed(3)}`;
  return `$${v.toFixed(5)}`;
};

const fmtUSD = (v: number): string => {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1000) return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
  return `${sign}$${abs.toFixed(2)}`;
};

const fmtPct = (v: number | null | undefined, forceSign = false): string => {
  if (v == null) return '–';
  const sign = forceSign && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
};

const fmtVol = (v: number | null | undefined): string => {
  if (v == null || v === 0) return '–';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${(v / 1e3).toFixed(0)}K`;
};

const tsShort = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString('fr-FR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–';

const duration = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 60) return `${mins}m`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h${mins % 60 > 0 ? `${mins % 60}m` : ''}`;
  return `${Math.floor(mins / 1440)}j`;
};

// ── Portfolio Header ──────────────────────────────────────────────────────────

function PortfolioHeader({
  data,
  lastUpdate,
  refreshing,
}: {
  data: PortfolioLive | null;
  lastUpdate: Date | null;
  refreshing: boolean;
}) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const pnl = data?.total_pnl_usd ?? 0;
  const pnlPct = data?.total_pnl_pct ?? 0;
  const pnlPos = pnl >= 0;
  const realized = data?.total_realized_pnl_usd ?? 0;
  const unrealized = data?.total_unrealized_pnl_usd ?? 0;

  const n_long = data?.agents.filter(a => a.action === 'LONG').length ?? 0;
  const n_watch = data?.agents.filter(a => a.action === 'WATCH').length ?? 0;
  const n_open = data?.agents.reduce((s, a) => s + a.open_positions.length, 0) ?? 0;

  return (
    <div className={`ld-header ${pnlPos ? 'header-pos' : 'header-neg'}`}>
      {/* Left: portfolio value */}
      <div className="ld-header-main">
        <div className="ld-header-label">Portefeuille Paper</div>
        <div className="ld-header-value">
          {data ? fmtUSD(data.total_value_usd) : '—'}
        </div>
        <div className={`ld-header-pnl ${pnlPos ? 'pos' : 'neg'}`}>
          {pnlPos ? '▲' : '▼'}&nbsp;
          {data ? `${fmtUSD(pnl)} (${fmtPct(pnlPct, true)})` : '—'}
        </div>
      </div>

      {/* Middle: breakdown */}
      <div className="ld-header-breakdown">
        <div className="ld-hb-item">
          <span className="ld-hb-label">Réalisé</span>
          <span className={`ld-hb-val ${realized >= 0 ? 'pos' : 'neg'}`}>
            {data ? fmtUSD(realized) : '—'}
          </span>
        </div>
        <div className="ld-hb-sep" />
        <div className="ld-hb-item">
          <span className="ld-hb-label">Non réalisé</span>
          <span className={`ld-hb-val ${unrealized >= 0 ? 'pos' : 'neg'}`}>
            {data ? fmtUSD(unrealized) : '—'}
          </span>
        </div>
        <div className="ld-hb-sep" />
        <div className="ld-hb-item">
          <span className="ld-hb-label">Capital initial</span>
          <span className="ld-hb-val">{data ? fmtUSD(data.initial_capital_usd) : '—'}</span>
        </div>
      </div>

      {/* Right: market state */}
      <div className="ld-header-right">
        <div className="ld-header-signals">
          <span className="ld-sig-pill long">{n_long} LONG</span>
          <span className="ld-sig-pill watch">{n_watch} WATCH</span>
          <span className="ld-sig-pill open">{n_open} POS</span>
        </div>
        {data?.btc_price != null && (
          <div className="ld-header-btc">
            <span className="ld-btc-label">BTC</span>
            <span className="ld-btc-price">{fmtPrice(data.btc_price)}</span>
          </div>
        )}
        <div className="ld-header-clock">
          <span className={`ld-live-dot ${refreshing ? 'pulsing' : ''}`} />
          {now.toLocaleTimeString('fr-FR')}
          {lastUpdate && (
            <span className="ld-last-update">
              · maj {lastUpdate.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Agent Card ────────────────────────────────────────────────────────────────

function AgentCard({
  agent,
  selected,
  onClick,
  prevPrice,
}: {
  agent: AgentLive;
  selected: boolean;
  onClick: () => void;
  prevPrice: number | null;
}) {
  const ticker  = TICKER[agent.symbol] ?? agent.symbol.replace('USDT', '');
  const color   = COLORS[agent.symbol] ?? '#6b7280';
  const action  = agent.action ?? 'NO_DATA';
  const isLong  = action === 'LONG';
  const isWatch = action === 'WATCH';
  const isBlocked = action.startsWith('BLOCKED') || action === 'CRASH_HALT';
  const hasPos  = agent.open_positions.length > 0;

  const priceUp = (agent.price_change_24h_pct ?? 0) >= 0;
  const pnlPos  = agent.cumulative_pnl_pct >= 0;

  // Flash class when price changes
  const flashClass = prevPrice != null && agent.live_price != null
    ? agent.live_price > prevPrice ? 'price-flash-up'
    : agent.live_price < prevPrice ? 'price-flash-down'
    : ''
    : '';

  const progress = agent.p_long != null && agent.threshold != null
    ? Math.min((agent.p_long / agent.threshold) * 100, 100)
    : 0;

  const openPos = hasPos ? agent.open_positions[0] : null;

  return (
    <button
      className={[
        'ld-agent-card',
        isLong ? 'card-long' : isWatch ? 'card-watch' : isBlocked ? 'card-blocked' : 'card-neutral',
        selected ? 'card-selected' : '',
        hasPos ? 'card-has-pos' : '',
      ].join(' ')}
      onClick={onClick}
      style={{ '--color': color } as React.CSSProperties}
    >
      {/* ── Top: icon + action + open indicator ─── */}
      <div className="ld-card-top">
        <div className="ld-card-icon" style={{ background: `${color}22`, borderColor: `${color}55` }}>
          <span style={{ color }}>{ticker}</span>
        </div>
        <div className="ld-card-action-wrap">
          <span className={`ld-card-action ${isLong ? 'long' : isWatch ? 'watch' : isBlocked ? 'blocked' : 'neutral'}`}>
            {isLong ? '▲ LONG' : isWatch ? '◉ WATCH' : isBlocked ? '✕ HALT' : '● HOLD'}
          </span>
          {hasPos && <span className="ld-card-pos-dot" title="Position ouverte" />}
        </div>
      </div>

      {/* ── Live price + 24h change ─── */}
      <div className="ld-card-price-row">
        <span className={`ld-card-price ${flashClass}`}>
          {fmtPrice(agent.live_price)}
        </span>
        {agent.price_change_24h_pct != null && (
          <span className={`ld-card-24h ${priceUp ? 'pos' : 'neg'}`}>
            {fmtPct(agent.price_change_24h_pct, true)}
          </span>
        )}
      </div>

      {/* ── Signal probability bar ─── */}
      {agent.p_long != null && agent.threshold != null && (
        <div className="ld-card-sigbar">
          <div className="ld-card-sigbar-bg">
            <div
              className={`ld-card-sigbar-fill ${isLong ? 'fill-long' : isWatch ? 'fill-watch' : 'fill-neutral'}`}
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="ld-card-sigbar-label">
            p {agent.p_long.toFixed(3)}&thinsp;/&thinsp;{agent.threshold.toFixed(3)}
          </span>
        </div>
      )}

      {/* ── Open position P&L ─── */}
      {openPos && (
        <div className={`ld-card-pos ${openPos.unrealized_pnl_pct >= 0 ? 'pos' : 'neg'}`}>
          <span className="ld-card-pos-label">◎ POS</span>
          <span className="ld-card-pos-pct">
            {openPos.unrealized_pnl_pct >= 0 ? '+' : ''}{openPos.unrealized_pnl_pct.toFixed(2)}%
          </span>
          <span className="ld-card-pos-usd">
            ({openPos.unrealized_pnl_usd >= 0 ? '+' : ''}{fmtUSD(openPos.unrealized_pnl_usd)})
          </span>
        </div>
      )}

      {/* ── Stats row: cum PnL + trades ─── */}
      <div className="ld-card-stats">
        <span className={`ld-card-stat ${pnlPos ? 'pos' : 'neg'}`}>
          {fmtPct(agent.cumulative_pnl_pct, true)}
        </span>
        {agent.total_trades > 0 && (
          <span className="ld-card-stat muted">{agent.total_trades}t</span>
        )}
        {agent.max_dd_pct > 0 && (
          <span className={`ld-card-stat ${agent.max_dd_pct > 2 ? 'neg' : 'muted'}`}>
            DD {agent.max_dd_pct.toFixed(1)}%
          </span>
        )}
      </div>

      {/* ── Timestamp ─── */}
      {agent.signal_timestamp && (
        <div className="ld-card-ts">{tsShort(agent.signal_timestamp)}</div>
      )}
    </button>
  );
}

// ── Agent Detail Panel ────────────────────────────────────────────────────────

function AgentDetailPanel({
  agent,
  onClose,
}: {
  agent: AgentLive;
  onClose: () => void;
}) {
  const ticker = TICKER[agent.symbol] ?? agent.symbol.replace('USDT', '');
  const color  = COLORS[agent.symbol] ?? '#6b7280';
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loadingTrades, setLoadingTrades] = useState(true);

  useEffect(() => {
    setLoadingTrades(true);
    PaperApi.fleetAssetTrades(agent.symbol)
      .then(t => setTrades([...t].reverse().slice(0, 15)))
      .catch(() => {})
      .finally(() => setLoadingTrades(false));
  }, [agent.symbol]);

  const priceUp = (agent.price_change_24h_pct ?? 0) >= 0;
  const totalPnl = agent.realized_pnl_usd + agent.unrealized_pnl_usd;

  return (
    <div className="ld-detail">
      {/* Header */}
      <div className="ld-det-header">
        <div className="ld-det-title">
          <span className="ld-det-dot" style={{ background: color }} />
          <span className="ld-det-ticker">{ticker}</span>
          <span className="ld-det-sym">{agent.symbol}</span>
        </div>
        <button className="ld-det-close" onClick={onClose}>✕</button>
      </div>

      {/* Price + 24h */}
      <div className="ld-det-price-block">
        <span className="ld-det-price">{fmtPrice(agent.live_price)}</span>
        {agent.price_change_24h_pct != null && (
          <span className={`ld-det-change ${priceUp ? 'pos' : 'neg'}`}>
            {fmtPct(agent.price_change_24h_pct, true)} 24h
          </span>
        )}
      </div>

      {agent.high_24h != null && (
        <div className="ld-det-range">
          <span>H&thinsp;{fmtPrice(agent.high_24h)}</span>
          <span>·</span>
          <span>L&thinsp;{fmtPrice(agent.low_24h)}</span>
          {agent.volume_24h_usdt != null && (
            <><span>·</span><span>Vol&thinsp;{fmtVol(agent.volume_24h_usdt)}</span></>
          )}
        </div>
      )}

      {/* Signal courant */}
      <div className="ld-det-section">
        <div className="ld-det-section-title">Signal agent</div>
        <div className="ld-det-signal-row">
          <span className={`ld-det-action ${agent.action === 'LONG' ? 'long' : agent.action === 'WATCH' ? 'watch' : 'neutral'}`}>
            {agent.action}
          </span>
          {agent.p_long != null && (
            <span className="ld-det-sig-p">p&thinsp;=&thinsp;{agent.p_long.toFixed(4)}</span>
          )}
          {agent.threshold != null && (
            <span className="ld-det-sig-thr">seuil {agent.threshold.toFixed(4)}</span>
          )}
          {agent.sup_level && (
            <span className="ld-det-sig-sup">sup&thinsp;{agent.sup_level}</span>
          )}
        </div>
        <div className="ld-det-sig-ts">{tsShort(agent.signal_timestamp)}</div>
      </div>

      {/* Position ouverte */}
      {agent.open_positions.length > 0 && (
        <div className="ld-det-section">
          <div className="ld-det-section-title">
            Position ouverte&ensp;
            <span className="ld-det-dur">{duration(agent.open_positions[0].entry_time)}</span>
          </div>
          {agent.open_positions.map((pos, i) => (
            <div key={i} className="ld-det-pos-card">
              <div className="ld-det-pos-grid">
                <div><span className="ld-det-pl">Entrée</span><strong>{fmtPrice(pos.entry_price)}</strong></div>
                <div><span className="ld-det-pl">Actuel</span><strong>{fmtPrice(pos.current_price)}</strong></div>
                <div><span className="ld-det-pl">Taille</span><strong>{fmtUSD(pos.position_usd)}</strong></div>
                <div><span className="ld-det-pl">Qté</span><strong>{pos.quantity.toFixed(6)}</strong></div>
              </div>
              <div className={`ld-det-pos-pnl ${pos.unrealized_pnl_pct >= 0 ? 'pos' : 'neg'}`}>
                <span>P&amp;L non réalisé</span>
                <strong>
                  {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(3)}%
                  &ensp;({pos.unrealized_pnl_usd >= 0 ? '+' : ''}{fmtUSD(pos.unrealized_pnl_usd)})
                </strong>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Performance */}
      <div className="ld-det-section">
        <div className="ld-det-section-title">Performance paper</div>
        <div className="ld-det-metrics">
          <div className="ld-det-metric">
            <span>Capital agent</span>
            <strong>{fmtUSD(agent.agent_value_usd)}</strong>
          </div>
          <div className="ld-det-metric">
            <span>P&amp;L total</span>
            <strong className={totalPnl >= 0 ? 'pos' : 'neg'}>
              {totalPnl >= 0 ? '+' : ''}{fmtUSD(totalPnl)}
            </strong>
          </div>
          <div className="ld-det-metric">
            <span>Réalisé</span>
            <strong className={agent.realized_pnl_usd >= 0 ? 'pos' : 'neg'}>
              {agent.realized_pnl_usd >= 0 ? '+' : ''}{fmtUSD(agent.realized_pnl_usd)}
            </strong>
          </div>
          <div className="ld-det-metric">
            <span>Trades</span>
            <strong>{agent.total_trades} ({agent.total_wins}W)</strong>
          </div>
          <div className="ld-det-metric">
            <span>DD max</span>
            <strong className={agent.max_dd_pct > 2 ? 'neg' : ''}>
              {agent.max_dd_pct.toFixed(2)}%
            </strong>
          </div>
          {agent.start_date && (
            <div className="ld-det-metric">
              <span>Départ</span>
              <strong>{tsShort(agent.start_date)}</strong>
            </div>
          )}
        </div>
      </div>

      {/* Modèle */}
      {agent.model_trained && (agent.val_pf != null || agent.val_n != null) && (
        <div className="ld-det-section">
          <div className="ld-det-section-title">Qualité modèle (val 2025)</div>
          <div className="ld-det-metrics">
            {agent.val_pf != null && (
              <div className="ld-det-metric">
                <span>PF val</span>
                <strong className={agent.val_pf >= 1.3 ? 'pos' : agent.val_pf < 1 ? 'neg' : ''}>
                  {agent.val_pf.toFixed(2)}
                </strong>
              </div>
            )}
            {agent.val_wr != null && (
              <div className="ld-det-metric">
                <span>WR val</span>
                <strong>{(agent.val_wr * 100).toFixed(0)}%</strong>
              </div>
            )}
            {agent.val_n != null && (
              <div className="ld-det-metric">
                <span>Trades val</span>
                <strong className={agent.val_n < 30 ? 'neg' : 'pos'}>{agent.val_n}</strong>
              </div>
            )}
            {agent.n_features != null && (
              <div className="ld-det-metric">
                <span>Features</span>
                <strong>{agent.n_features}</strong>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Trades récents */}
      <div className="ld-det-section">
        <div className="ld-det-section-title">Derniers trades ({agent.total_trades})</div>
        {loadingTrades ? (
          <div className="ld-det-loading">Chargement…</div>
        ) : trades.length === 0 ? (
          <div className="ld-det-empty">Aucun trade enregistré</div>
        ) : (
          <table className="ld-det-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Entrée</th>
                <th>Durée</th>
                <th>PnL</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => {
                const pnl = typeof t.pnl_pct === 'number' ? t.pnl_pct : null;
                const entry = (t.entry_time ?? t.timestamp) as string | undefined;
                const isOpen = String(t.outcome ?? '').toUpperCase() === 'OPEN';
                return (
                  <tr key={i} className={isOpen ? 'row-open' : pnl != null ? (pnl >= 0 ? 'row-win' : 'row-loss') : ''}>
                    <td className="cell-date">{entry ? new Date(entry).toLocaleDateString('fr-FR') : '–'}</td>
                    <td className="cell-price">
                      {t.close_entry != null ? fmtPrice(Number(t.close_entry)) : '–'}
                    </td>
                    <td className="cell-dur">{isOpen ? duration(entry) : '–'}</td>
                    <td className={pnl != null ? (pnl >= 0 ? 'pos' : 'neg') : ''}>
                      {isOpen ? <span className="open-badge">OUVERT</span>
                       : pnl != null ? fmtPct(pnl, true) : '–'}
                    </td>
                    <td>{t.outcome ?? '–'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Alertes */}
      {(agent.crash_halt_until || agent.consecutive_losses > 2) && (
        <div className="ld-det-section">
          <div className="ld-det-section-title">Alertes</div>
          {agent.crash_halt_until && (
            <div className="ld-det-alert halt">
              ✕ CRASH HALT jusqu'au {tsShort(agent.crash_halt_until)}
            </div>
          )}
          {agent.consecutive_losses > 2 && (
            <div className="ld-det-alert warn">
              ⚠ {agent.consecutive_losses} pertes consécutives
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Scheduler Bar ─────────────────────────────────────────────────────────────

interface SchedStatus {
  enabled: boolean;
  running: boolean;
  last_run: string | null;
  next_run: string | null;
  interval_hours: number;
  log: string[];
}

function useCountdown(nextRunIso: string | null): string {
  const [label, setLabel] = useState('');
  useEffect(() => {
    if (!nextRunIso) { setLabel(''); return; }
    const tick = () => {
      const secs = Math.max(0, Math.floor((new Date(nextRunIso).getTime() - Date.now()) / 1000));
      if (secs === 0) { setLabel('maintenant'); return; }
      const h = Math.floor(secs / 3600);
      const m = Math.floor((secs % 3600) / 60);
      const s = secs % 60;
      setLabel(h > 0 ? `${h}h ${m.toString().padStart(2,'0')}m` : m > 0 ? `${m}m ${s.toString().padStart(2,'0')}s` : `${s}s`);
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [nextRunIso]);
  return label;
}

function SchedulerBar() {
  const [status, setStatus]   = useState<SchedStatus | null>(null);
  const [showLog, setShowLog] = useState(false);

  const fetchStatus = useCallback(async () => {
    try { setStatus(await PaperApi.schedulerStatus()); } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 5_000);
    return () => clearInterval(t);
  }, [fetchStatus]);

  const countdown = useCountdown(status?.next_run ?? null);

  const handleToggle = async () => {
    try { await PaperApi.schedulerToggle(); fetchStatus(); } catch {}
  };

  const handleRunNow = async () => {
    try { await PaperApi.schedulerRunNow(); fetchStatus(); } catch {}
  };

  const running  = status?.running ?? false;
  const enabled  = status?.enabled ?? true;

  return (
    <div className="ld-sched-bar">
      {/* Status indicator */}
      <div className={`ld-sched-status ${running ? 'sched-running' : enabled ? 'sched-on' : 'sched-off'}`}>
        {running
          ? <><span className="ld-spinner" /><span>En cours…</span></>
          : enabled
            ? <><span className="ld-sched-dot on" />AUTO</>
            : <><span className="ld-sched-dot off" />PAUSE</>
        }
      </div>

      {/* Countdown */}
      {enabled && !running && status?.next_run && (
        <div className="ld-sched-countdown">
          Prochain run dans <strong>{countdown}</strong>
        </div>
      )}

      {/* Last run */}
      {status?.last_run && (
        <div className="ld-sched-last">
          Dernier&nbsp;
          {new Date(status.last_run).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}
        </div>
      )}

      {/* Interval */}
      <div className="ld-sched-interval">
        ⟳ toutes les {status?.interval_hours}h
      </div>

      <div className="ld-sched-spacer" />

      {/* Log toggle */}
      {status?.log && status.log.length > 0 && (
        <button
          className="ld-sched-log-btn"
          onClick={() => setShowLog(v => !v)}
        >
          {showLog ? 'Masquer logs' : 'Logs'}
        </button>
      )}

      {/* Force run */}
      <button
        className="ld-sched-force-btn"
        onClick={handleRunNow}
        disabled={running}
        title="Forcer un run immédiat"
      >
        {running ? <span className="ld-spinner" /> : '⚡'} Forcer
      </button>

      {/* Toggle auto */}
      <button
        className={`ld-sched-toggle-btn ${enabled ? 'on' : 'off'}`}
        onClick={handleToggle}
        title={enabled ? 'Mettre en pause' : 'Activer le scheduler'}
      >
        {enabled ? '⏸ Pause' : '▶ Activer'}
      </button>

      {/* Log panel */}
      {showLog && status?.log && (
        <div className="ld-sched-log-panel">
          {status.log.map((line, i) => (
            <div key={i} className={`ld-sched-log-line ${line.includes('ERROR') ? 'err' : line.includes('✓') ? 'ok' : ''}`}>
              {line}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Portfolio updater from SSE prices ────────────────────────────────────────

function applyLivePrices(prev: PortfolioLive, prices: Record<string, number>): PortfolioLive {
  let totalUnrealized = 0;
  const agents = prev.agents.map(a => {
    const lp = prices[a.symbol] ?? a.live_price;
    if (!lp || !a.open_positions.length) {
      return { ...a, live_price: lp ?? a.live_price };
    }
    let unrealized = 0;
    const newPositions = a.open_positions.map(pos => {
      const qty   = pos.quantity;
      const cv    = qty * lp;
      const pnlU  = cv - pos.position_usd;
      const pnlP  = (lp / pos.entry_price - 1) * 100;
      unrealized += pnlU;
      return { ...pos, current_price: lp, current_value_usd: +cv.toFixed(2), unrealized_pnl_usd: +pnlU.toFixed(2), unrealized_pnl_pct: +pnlP.toFixed(3) };
    });
    totalUnrealized += unrealized;
    const agentValue = 10_000 + (a.realized_pnl_usd ?? 0) + unrealized;
    return { ...a, live_price: lp, open_positions: newPositions, unrealized_pnl_usd: +unrealized.toFixed(2), agent_value_usd: +agentValue.toFixed(2) };
  });
  const totalPnl  = prev.total_realized_pnl_usd + totalUnrealized;
  const totalVal  = prev.initial_capital_usd + totalPnl;
  return {
    ...prev,
    agents,
    total_unrealized_pnl_usd: +totalUnrealized.toFixed(2),
    total_pnl_usd:            +totalPnl.toFixed(2),
    total_pnl_pct:            +(totalPnl / prev.initial_capital_usd * 100).toFixed(3),
    total_value_usd:          +totalVal.toFixed(2),
    btc_price:                prices['BTCUSDT'] ?? prev.btc_price,
  };
}

// ── Main LiveDashboard ────────────────────────────────────────────────────────

export function LiveDashboard() {
  const [data, setData]               = useState<PortfolioLive | null>(null);
  const [prevPrices, setPrevPrices]   = useState<Record<string, number | null>>({});
  const [lastUpdate, setLastUpdate]   = useState<Date | null>(null);
  const [livePulse, setLivePulse]     = useState(false);
  const [refreshing, setRefreshing]   = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [selectedSym, setSelectedSym] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const fetch_data = useCallback(async () => {
    setRefreshing(true);
    try {
      const d = await PaperApi.portfolioLive();
      setData(prev => {
        if (prev) {
          const pp: Record<string, number | null> = {};
          for (const a of prev.agents) pp[a.symbol] = a.live_price;
          setPrevPrices(pp);
        }
        return d;
      });
      setLastUpdate(new Date());
      setError(null);
    } catch (e) {
      setError('API inaccessible');
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Full state refresh every 60s (picks up new trades, signals)
  useEffect(() => {
    fetch_data();
    const t = setInterval(fetch_data, 60_000);
    return () => clearInterval(t);
  }, [fetch_data]);

  // SSE for live prices — reconnects automatically on error
  useEffect(() => {
    function connect() {
      const es = new EventSource(`${API_BASE_URL}/api/stream`);
      esRef.current = es;

      es.onmessage = (e: MessageEvent) => {
        try {
          const ev = JSON.parse(e.data);
          if (ev.type === 'prices' && ev.prices) {
            setData(prev => {
              if (!prev) return prev;
              const pp: Record<string, number | null> = {};
              for (const a of prev.agents) pp[a.symbol] = a.live_price;
              setPrevPrices(pp);
              return applyLivePrices(prev, ev.prices);
            });
            setLastUpdate(new Date());
            setLivePulse(p => !p);
          }
        } catch {}
      };

      es.onerror = () => {
        es.close();
        esRef.current = null;
        setTimeout(connect, 5_000);
      };
    }

    connect();
    return () => { esRef.current?.close(); };
  }, []);

  const agents = data?.agents ?? [];
  const selectedAgent = agents.find(a => a.symbol === selectedSym) ?? null;

  return (
    <div className="ld-root">
      <PortfolioHeader data={data} lastUpdate={lastUpdate} refreshing={refreshing} />

      <div className="ld-live-banner">
        <span className={`ld-live-dot-sm ${livePulse ? 'pulse-a' : 'pulse-b'}`} />
        <span className="ld-live-label">LIVE · prix Binance mis à jour toutes les ~8s</span>
        {refreshing && <span className="ld-refreshing-tag">sync…</span>}
      </div>

      {error && (
        <div className="ld-error">{error}</div>
      )}

      <SchedulerBar />

      <div className="ld-body">
        {/* Agent grid */}
        <div className="ld-grid-wrap">
          <div className="ld-grid">
            {agents.length === 0
              ? Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className="ld-agent-card skeleton" />
                ))
              : agents.map(agent => (
                  <AgentCard
                    key={agent.symbol}
                    agent={agent}
                    selected={selectedSym === agent.symbol}
                    prevPrice={prevPrices[agent.symbol] ?? null}
                    onClick={() =>
                      setSelectedSym(prev => prev === agent.symbol ? null : agent.symbol)
                    }
                  />
                ))}
          </div>
        </div>

        {/* Detail panel */}
        {selectedAgent && (
          <AgentDetailPanel
            agent={selectedAgent}
            onClose={() => setSelectedSym(null)}
          />
        )}
      </div>
    </div>
  );
}
