import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  PaperApi,
  FleetSummary,
  AssetSummary,
  AssetDetail,
  Trade,
  Signal,
  FleetRunStatus,
} from '../services/PaperApi';
import './FleetView.css';

// ── Helpers ───────────────────────────────────────────────────────────────────

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

const fmt = (v: number | null | undefined, dec = 2, suffix = '') =>
  v == null ? '–' : `${v.toFixed(dec)}${suffix}`;

const fmtPct = (v: number | null | undefined) => fmt(v, 2, '%');

const sign = (v: number | null | undefined): 'pos' | 'neg' | 'neu' =>
  v == null ? 'neu' : v > 0.001 ? 'pos' : v < -0.001 ? 'neg' : 'neu';

const ts = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString('fr-FR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–';

// ── BTC Header ────────────────────────────────────────────────────────────────

function BtcHeader({ summary }: { summary: FleetSummary | null }) {
  if (!summary) return <div className="fleet-btc-header skeleton" />;
  const bull = summary.btc_regime === 'BULL';
  return (
    <div className={`fleet-btc-header regime-${(summary.btc_regime ?? 'unknown').toLowerCase()}`}>
      <div className="btc-price-block">
        <span className="btc-label">BTC/USDT</span>
        <span className="btc-price">
          ${summary.btc_price?.toLocaleString('en-US', { maximumFractionDigits: 0 }) ?? '–'}
        </span>
        <span className={`btc-ema ${bull ? 'pos' : 'neg'}`}>
          EMA200 {summary.btc_vs_ema200 != null ? `${summary.btc_vs_ema200 > 0 ? '+' : ''}${summary.btc_vs_ema200.toFixed(1)}%` : '–'}
        </span>
      </div>

      <span className={`regime-pill ${bull ? 'bull' : 'bear'}`}>{summary.btc_regime}</span>

      <div className="fleet-kpis">
        <div className="fleet-kpi">
          <span className="fk-label">Assets actifs</span>
          <span className="fk-val">{summary.assets?.filter(a => a.available).length ?? 0} / {summary.assets?.length ?? 0}</span>
        </div>
        <div className="fleet-kpi">
          <span className="fk-label">LONG</span>
          <span className="fk-val green">{summary.n_long_signals ?? 0}</span>
        </div>
        <div className="fleet-kpi">
          <span className="fk-label">WATCH</span>
          <span className="fk-val amber">{summary.n_watch ?? 0}</span>
        </div>
        {summary.val_stats && (
          <div className="fleet-kpi">
            <span className="fk-label">Val BTC {summary.val_stats.n}t</span>
            <span className="fk-val">PF {summary.val_stats.pf.toFixed(1)} · WR {(summary.val_stats.wr * 100).toFixed(0)}%</span>
          </div>
        )}
      </div>

      <span className="fleet-ts">{summary.timestamp ? ts(summary.timestamp) : ''}</span>
    </div>
  );
}

// ── Asset Card ────────────────────────────────────────────────────────────────

function AssetCard({
  asset,
  selected,
  onClick,
}: {
  asset: AssetSummary;
  selected: boolean;
  onClick: () => void;
}) {
  const ticker  = TICKER[asset.symbol] ?? asset.symbol.replace('USDT', '');
  const color   = COLORS[asset.symbol] ?? '#6b7280';
  const action  = asset.action ?? 'PENDING';
  const isLong  = action === 'LONG';
  const isWatch = action === 'WATCH';
  const isBlocked = action.startsWith('BLOCKED') || action === 'ERROR';
  const noData  = !asset.available || action === 'NO_DATA' || action === 'PENDING';

  const progress = asset.p_long != null && asset.threshold != null
    ? Math.min((asset.p_long / asset.threshold) * 100, 100)
    : 0;

  return (
    <button
      className={`asset-card ${isLong ? 'ac-long' : isWatch ? 'ac-watch' : isBlocked ? 'ac-blocked' : noData ? 'ac-nodata' : 'ac-neutral'} ${selected ? 'ac-selected' : ''}`}
      onClick={onClick}
      style={{ '--asset-color': color } as React.CSSProperties}
    >
      <div className="ac-top">
        <div className="ac-icon" style={{ background: `${color}22`, borderColor: `${color}44` }}>
          <span style={{ color }}>{ticker}</span>
        </div>
        <div className="ac-action-wrap">
          <span className={`ac-action ${isLong ? 'long' : isWatch ? 'watch' : isBlocked ? 'blocked' : 'neutral'}`}>
            {isLong ? '▲ LONG' : isWatch ? '◉ WATCH' : noData ? '○ –' : isBlocked ? '✕ BLOQUÉ' : '● NO'}
          </span>
        </div>
      </div>

      {!noData && asset.p_long != null && (
        <>
          <div className="ac-prob-row">
            <span className="ac-p-label">p</span>
            <span className="ac-p-val">{asset.p_long.toFixed(3)}</span>
            {asset.threshold != null && (
              <span className="ac-p-thr">/{asset.threshold.toFixed(3)}</span>
            )}
          </div>
          <div className="ac-bar-bg">
            <div
              className={`ac-bar-fill ${isLong ? 'fill-long' : 'fill-neutral'}`}
              style={{ width: `${progress}%` }}
            />
          </div>
        </>
      )}

      {noData && (
        <div className="ac-nodata-msg">
          {asset.available ? 'En attente de run' : 'Bootstrap requis'}
        </div>
      )}

      <div className="ac-stats">
        {asset.total_trades != null && (
          <span className="ac-stat">{asset.total_trades}t</span>
        )}
        {asset.pf_live != null && (
          <span className="ac-stat">PF {asset.pf_live.toFixed(2)}</span>
        )}
        {asset.cumulative_pnl_pct != null && (
          <span className={`ac-stat ${sign(asset.cumulative_pnl_pct)}`}>
            {asset.cumulative_pnl_pct > 0 ? '+' : ''}{asset.cumulative_pnl_pct.toFixed(1)}%
          </span>
        )}
        {asset.max_dd_pct != null && asset.max_dd_pct > 0 && (
          <span className={`ac-stat ${asset.max_dd_pct > 2 ? 'neg' : ''}`}>
            DD {asset.max_dd_pct.toFixed(1)}%
          </span>
        )}
      </div>

      {asset.timestamp && (
        <span className="ac-ts">{ts(asset.timestamp)}</span>
      )}
    </button>
  );
}

// ── Asset Detail Panel ────────────────────────────────────────────────────────

function AssetPanel({
  symbol,
  onClose,
}: {
  symbol: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      PaperApi.fleetAsset(symbol),
      PaperApi.fleetAssetTrades(symbol),
    ]).then(([d, t]) => {
      setDetail(d);
      setTrades([...t].reverse());
    }).catch(() => {}).finally(() => setLoading(false));
  }, [symbol]);

  const ticker = TICKER[symbol] ?? symbol.replace('USDT', '');
  const color  = COLORS[symbol] ?? '#6b7280';

  return (
    <div className="asset-panel">
      <div className="ap-header">
        <div className="ap-title">
          <span className="ap-dot" style={{ background: color }} />
          <span className="ap-name">{ticker}</span>
          <span className="ap-symbol">{symbol}</span>
        </div>
        <button className="ap-close" onClick={onClose}>✕</button>
      </div>

      {loading ? (
        <div className="skeleton h300" />
      ) : !detail?.available ? (
        <div className="empty-state">
          Asset non disponible. Lancer bootstrap_enriched.py pour créer le parquet.
        </div>
      ) : (
        <>
          {/* Signal courant */}
          <div className="ap-section">
            <span className="ap-section-title">Signal courant</span>
            <div className="ap-signal-row">
              <span className={`ap-action ${detail.latest_signal?.action === 'LONG' ? 'long' : 'neutral'}`}>
                {detail.latest_signal?.action ?? 'NO_DATA'}
              </span>
              {detail.latest_signal?.p_long != null && (
                <span className="ap-p">p = {Number(detail.latest_signal.p_long).toFixed(4)}</span>
              )}
              {detail.latest_signal?.threshold != null && (
                <span className="ap-thr">seuil {Number(detail.latest_signal.threshold).toFixed(4)}</span>
              )}
            </div>
          </div>

          {/* Métriques */}
          <div className="ap-metrics">
            <div className="ap-metric">
              <span>Trades</span><strong>{detail.total_trades}</strong>
            </div>
            <div className="ap-metric">
              <span>WR</span>
              <strong>{detail.wr_live != null ? fmtPct(detail.wr_live * 100) : '–'}</strong>
            </div>
            <div className="ap-metric">
              <span>PF</span>
              <strong>{detail.pf_live != null ? fmt(detail.pf_live, 3) : 'N/A'}</strong>
            </div>
            <div className="ap-metric">
              <span>DD max</span>
              <strong className={detail.max_dd_pct > 2 ? 'neg' : ''}>{fmtPct(detail.max_dd_pct)}</strong>
            </div>
            <div className="ap-metric">
              <span>PnL cumul</span>
              <strong className={sign(detail.cumulative_pnl_pct)}>{fmtPct(detail.cumulative_pnl_pct)}</strong>
            </div>
          </div>

          {/* Gates */}
          <div className="ap-section">
            <span className="ap-section-title">Gates</span>
            <div className="ap-gates">
              {detail.gates?.map(g => (
                <div key={g.name} className={`ap-gate ${g.ok ? 'ok' : 'pending'}`}>
                  <span>{g.ok ? '✓' : '·'}</span>
                  <span>{g.name}</span>
                  <span className="ap-gate-val">{g.value ?? '–'}{g.unit}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Trades */}
          {trades.length > 0 && (
            <div className="ap-section">
              <span className="ap-section-title">Derniers trades</span>
              <table className="ap-trades">
                <thead>
                  <tr><th>Date</th><th>Entrée</th><th>PnL</th><th>Statut</th></tr>
                </thead>
                <tbody>
                  {trades.slice(0, 10).map((t, i) => {
                    const pnl = typeof t.pnl_pct === 'number' ? t.pnl_pct : null;
                    const entry = t.entry_time ?? t.timestamp;
                    return (
                      <tr key={i} className={pnl != null ? (pnl >= 0 ? 'win' : 'loss') : ''}>
                        <td>{entry ? new Date(entry).toLocaleDateString('fr-FR') : '–'}</td>
                        <td>{t.close_entry != null ? `$${Number(t.close_entry).toLocaleString()}` : '–'}</td>
                        <td className={pnl != null ? (pnl >= 0 ? 'pos' : 'neg') : ''}>{fmtPct(pnl)}</td>
                        <td>{t.outcome ?? '–'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Run Controls ──────────────────────────────────────────────────────────────

function FleetRunControls({
  status,
  onRun,
}: {
  status: FleetRunStatus | null;
  onRun: () => void;
}) {
  const running = status?.fleet_running;
  return (
    <div className="fleet-run-bar">
      <button className="fleet-run-btn" onClick={onRun} disabled={!!running}>
        {running ? (
          <><span className="spinner-sm" />  Exécution en cours…</>
        ) : (
          '▶  Lancer le fleet'
        )}
      </button>
      {status?.log && status.log.length > 0 && (
        <pre className="fleet-run-log">{status.log.slice(-6).join('\n')}</pre>
      )}
    </div>
  );
}

// ── Main FleetView ────────────────────────────────────────────────────────────

export function FleetView() {
  const [summary, setSummary] = useState<FleetSummary | null>(null);
  const [runStatus, setRunStatus] = useState<FleetRunStatus | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const prevRunning = useRef(false);

  const refresh = useCallback(async () => {
    try { setSummary(await PaperApi.fleet()); } catch {}
    try { setRunStatus(await PaperApi.fleetRunStatus()); } catch {}
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  }, [refresh]);

  // Fast poll while running; reload summary when run finishes
  useEffect(() => {
    const running = runStatus?.fleet_running ?? false;
    if (running) {
      const t = setInterval(async () => {
        try { setRunStatus(await PaperApi.fleetRunStatus()); } catch {}
      }, 4_000);
      prevRunning.current = true;
      return () => clearInterval(t);
    } else if (prevRunning.current) {
      prevRunning.current = false;
      refresh();
    }
  }, [runStatus?.fleet_running, refresh]);

  const handleRun = async () => {
    try {
      await PaperApi.triggerFleetRun();
      setRunStatus(await PaperApi.fleetRunStatus());
    } catch {}
  };

  const assets = summary?.assets ?? [];
  const available = assets.filter(a => a.available);
  const unavailable = assets.filter(a => !a.available);

  return (
    <div className="fleet-view">
      <BtcHeader summary={summary} />

      <FleetRunControls status={runStatus} onRun={handleRun} />

      <div className="fleet-body">
        {/* ── Asset grid ── */}
        <div className="fleet-grid-wrap">
          {/* Available assets */}
          {available.length > 0 && (
            <div className="fleet-grid">
              {available.map(asset => (
                <AssetCard
                  key={asset.symbol}
                  asset={asset}
                  selected={selectedSymbol === asset.symbol}
                  onClick={() =>
                    setSelectedSymbol(prev => prev === asset.symbol ? null : asset.symbol)
                  }
                />
              ))}
            </div>
          )}

          {/* Unavailable / bootstrap needed */}
          {unavailable.length > 0 && (
            <>
              <div className="fleet-section-label">
                Bootstrap requis ({unavailable.length})
                <span className="fleet-hint">
                  python3 scripts/bootstrap_enriched.py --symbols {unavailable.map(a => a.symbol).join(' ')}
                </span>
              </div>
              <div className="fleet-grid fleet-grid-dim">
                {unavailable.map(asset => (
                  <AssetCard
                    key={asset.symbol}
                    asset={asset}
                    selected={false}
                    onClick={() => {}}
                  />
                ))}
              </div>
            </>
          )}

          {assets.length === 0 && !summary && (
            <div className="fleet-grid">
              {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="asset-card skeleton" style={{ height: 140 }} />
              ))}
            </div>
          )}
        </div>

        {/* ── Detail panel ── */}
        {selectedSymbol && (
          <AssetPanel
            symbol={selectedSymbol}
            onClose={() => setSelectedSymbol(null)}
          />
        )}
      </div>
    </div>
  );
}
