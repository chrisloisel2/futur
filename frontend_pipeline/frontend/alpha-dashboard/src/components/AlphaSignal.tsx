import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactECharts from 'echarts-for-react';
import { API_BASE_URL } from '../config/api';

// ─── Types ────────────────────────────────────────────────────────────────────

interface Indicator {
  name: string; value: string; label: string;
  color: string; icon: string; interpretation: string;
}

interface Gate {
  name: string; score: number; weight: number;
}

interface Risk {
  entry: number; stop: number; tp1: number; tp2: number;
  sl_pct: number; rr_ratio: number; size_pct: number;
}

interface Candle {
  time: number; open: number; high: number; low: number; close: number; volume: number; taker: number;
}

interface SignalData {
  symbol: string; name: string; icon: string; color: string;
  timestamp: string;
  action: 'LONG' | 'SHORT' | 'WAIT';
  confidence: number;
  raw_score: number;
  current_price: number;
  change_24h_pct?: number;
  change_7d_pct?: number;
  atr: number; atr_pct: number;
  indicators: Indicator[];
  risk: Risk;
  details: { gates: Gate[]; raw_score: number; long_gates: number; short_gates: number };
  derivatives: Record<string, any>;
  chart_5m: Candle[];
  error?: string;
}

interface MLPrediction {
  symbol: string;
  action: string;
  action_raw: string;
  confidence: number;
  current_price: number;
  p_filter: number;
  filter_thr_long: number;
  filter_passed_long: boolean;
  filter_passed_short: boolean;
  regime: string;
  dist_ema50: number;
  rsi: number;
  p_long: number;
  p_short: number;
  long_signal: boolean;
  short_signal: boolean;
  thr_edge_long: number;
  thr_edge_short: number;
  stop_price: number;
  take_profit: number;
  reason: string;
  refreshed_at: string;
  run_id: string;
  uncertainty?: { allow_trade?: boolean; width?: number };
  size_multiplier: number;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SYMBOLS = [
  { key: 'BTCUSDT', label: 'BTC', icon: '₿', color: '#F7931A' },
  { key: 'ETHUSDT', label: 'ETH', icon: 'Ξ', color: '#627EEA' },
  { key: 'SOLUSDT', label: 'SOL', icon: '◎', color: '#9945FF' },
];

const ACTION_COLOR = { LONG: '#10b981', SHORT: '#ef4444', WAIT: '#64748b', HOLD: '#64748b' };
const ACTION_BG    = { LONG: 'rgba(16,185,129,0.12)', SHORT: 'rgba(239,68,68,0.12)', WAIT: 'rgba(100,116,139,0.08)', HOLD: 'rgba(100,116,139,0.08)' };
const ACTION_ICON  = { LONG: '▲', SHORT: '▼', WAIT: '◼', HOLD: '◼' };

const INDICATOR_COLORS: Record<string, string> = {
  green: '#10b981', red: '#ef4444', amber: '#f59e0b', blue: '#60a5fa', dim: '#64748b'
};

const REGIME_COLOR: Record<string, string> = {
  NEUTRAL:    '#60a5fa',
  SHORTABLE:  '#ef4444',
  NO_SHORT:   '#f59e0b',
};

const fmt$ = (n: number, digits = 2) =>
  '$' + n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtPct = (n: number) => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';

const fmtScore = (v: number) => `${(v * 100).toFixed(1)}%`;

// ─── Candlestick chart ────────────────────────────────────────────────────────

const CandleChart: React.FC<{ candles: Candle[]; color: string; action: string }> = ({ candles, color, action }) => {
  const option = React.useMemo(() => {
    if (!candles.length) return {};

    const times  = candles.map(c => new Date(c.time * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }));
    const ohlc   = candles.map(c => [c.open, c.close, c.low, c.high]);
    const vols   = candles.map(c => c.volume);

    const upColor   = '#10b981';
    const downColor = '#ef4444';

    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: [
        { left: 50, right: 12, top: 8,  bottom: 60 },
        { left: 50, right: 12, top: '72%', bottom: 8  },
      ],
      xAxis: [
        { type: 'category', data: times, gridIndex: 0,
          axisLabel: { show: false }, axisLine: { lineStyle: { color: '#1e2537' } }, splitLine: { show: false } },
        { type: 'category', data: times, gridIndex: 1,
          axisLabel: { color: '#475569', fontSize: 9 }, axisLine: { lineStyle: { color: '#1e2537' } }, splitLine: { show: false } },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0,
          axisLabel: { color: '#475569', fontSize: 9, formatter: (v: number) =>
            v >= 1000 ? `$${(v/1000).toFixed(0)}K` : `$${v.toFixed(0)}` },
          splitLine: { lineStyle: { color: '#1e2537' } } },
        { type: 'value', gridIndex: 1,
          axisLabel: { show: false }, splitLine: { show: false } },
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#0d1117',
        borderColor: '#1e2537',
        textStyle: { color: '#94a3b8', fontSize: 11 },
        formatter: (params: any[]) => {
          const c = params.find(p => p.seriesType === 'candlestick');
          if (!c) return '';
          const [o, cl, lo, hi] = c.value;
          const chg = ((cl - o) / o * 100).toFixed(2);
          const col = cl >= o ? upColor : downColor;
          return `${c.name}<br/>O:${fmt$(o)} H:${fmt$(hi)} L:${fmt$(lo)} C:${fmt$(cl)}<br/><span style="color:${col}">${chg}%</span>`;
        },
      },
      series: [
        {
          type: 'candlestick',
          xAxisIndex: 0, yAxisIndex: 0,
          data: ohlc,
          itemStyle: { color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor },
        },
        {
          type: 'bar',
          xAxisIndex: 1, yAxisIndex: 1,
          data: vols.map((v, i) => ({
            value: v,
            itemStyle: { color: ohlc[i][1] >= ohlc[i][0] ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)' },
          })),
          barMaxWidth: 6,
        },
      ],
    };
  }, [candles]);

  if (!candles.length) {
    return (
      <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--txt-dim)', fontSize: '0.75rem' }}>
        Chargement des données…
      </div>
    );
  }

  return <ReactECharts option={option} style={{ height: 220 }} opts={{ renderer: 'canvas' }} />;
};

// ─── ML Pipeline panel (TRM Fleet v2 — BTCUSDT uniquement) ───────────────────

const MLPipelinePanel: React.FC<{ ml: MLPrediction | null; loading: boolean }> = ({ ml, loading }) => {
  if (loading && !ml) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {[40, 60, 40].map((h, i) => (
          <div key={i} style={{ height: h, background: 'var(--bg2)', borderRadius: 6, animation: 'pulse 1.5s infinite' }} />
        ))}
      </div>
    );
  }
  if (!ml) {
    return (
      <div style={{
        padding: '0.875rem', borderRadius: 8,
        background: 'rgba(100,116,139,0.06)', border: '1px solid var(--border)',
        fontSize: '0.72rem', color: 'var(--txt-muted)', textAlign: 'center',
      }}>
        Modèle ML non disponible (aucun run chargé)
      </div>
    );
  }

  const action = (ml.action || 'HOLD') as keyof typeof ACTION_COLOR;
  const aColor = ACTION_COLOR[action] || '#64748b';
  const aBg    = ACTION_BG[action]    || 'rgba(100,116,139,0.08)';
  const aIcon  = ACTION_ICON[action]  || '◼';

  const stages = [
    {
      label: 'Filtre',
      sub: 'HistGBT tradeable',
      pass: ml.filter_passed_long,
      value: fmtScore(ml.p_filter),
      thr: fmtScore(ml.filter_thr_long),
      color: ml.filter_passed_long ? '#10b981' : '#64748b',
    },
    {
      label: 'Régime',
      sub: ml.regime || 'NEUTRAL',
      pass: ml.regime !== 'NO_SHORT',
      value: ml.regime || '—',
      thr: null,
      color: REGIME_COLOR[ml.regime] || '#60a5fa',
    },
    {
      label: 'TRM Fleet v2',
      sub: '6 spécialistes contextuels',
      pass: ml.long_signal,
      value: fmtScore(ml.p_long),
      thr: fmtScore(ml.thr_edge_long),
      color: ml.long_signal ? '#10b981' : (ml.p_long > ml.thr_edge_long * 0.85 ? '#f59e0b' : '#64748b'),
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: '0.6rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#60a5fa' }}>
          ◭ TRM Fleet v2 · 4h · ML
        </div>
        {ml.run_id && (
          <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)', fontFamily: 'monospace' }}>
            {ml.run_id.slice(0, 20)}
          </div>
        )}
      </div>

      {/* Decision badge */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.75rem 0.875rem',
        background: aBg, border: `1px solid ${aColor}33`, borderRadius: 8,
      }}>
        <div>
          <div style={{ fontSize: '1.5rem', fontWeight: 800, color: 'var(--txt)', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
            {fmt$(ml.current_price, ml.current_price < 10 ? 3 : 2)}
          </div>
          <div style={{ fontSize: '0.68rem', color: 'var(--txt-muted)', marginTop: 4, lineHeight: 1.4, maxWidth: 220 }}>
            {ml.reason}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
            padding: '0.35rem 0.75rem', borderRadius: 7,
            background: `${aColor}22`, border: `1px solid ${aColor}55`,
            color: aColor, fontWeight: 800, fontSize: '0.875rem', letterSpacing: '0.04em',
          }}>
            <span>{aIcon}</span><span>{action}</span>
          </div>
          <div style={{ marginTop: 4, fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
            Score <strong style={{ color: 'var(--txt)' }}>{fmtScore(ml.p_long)}</strong>
          </div>
        </div>
      </div>

      {/* Pipeline stages */}
      <div style={{ display: 'flex', gap: '0.25rem', alignItems: 'stretch' }}>
        {stages.map((s, i) => (
          <React.Fragment key={s.label}>
            <div style={{
              flex: 1,
              padding: '0.5rem 0.625rem',
              background: 'var(--bg2)', borderRadius: 6,
              border: `1px solid ${s.color}33`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', marginBottom: '0.25rem' }}>
                <span style={{ fontSize: '0.7rem', color: s.color, fontWeight: 700 }}>
                  {s.pass ? '✓' : '—'}
                </span>
                <span style={{ fontSize: '0.62rem', fontWeight: 700, color: 'var(--txt)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {s.label}
                </span>
              </div>
              <div style={{ fontSize: '0.7rem', fontWeight: 700, color: s.color, fontVariantNumeric: 'tabular-nums' }}>
                {s.value}
              </div>
              {s.thr && (
                <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)' }}>seuil {s.thr}</div>
              )}
              <div style={{ fontSize: '0.55rem', color: 'var(--txt-muted)', marginTop: 2 }}>{s.sub}</div>
            </div>
            {i < stages.length - 1 && (
              <div style={{ display: 'flex', alignItems: 'center', color: 'var(--txt-dim)', fontSize: '0.6rem', padding: '0 2px' }}>▶</div>
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Uncertainty gate */}
      {ml.uncertainty && ml.uncertainty.allow_trade === false && (
        <div style={{
          padding: '0.4rem 0.75rem', borderRadius: 6,
          background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)',
          fontSize: '0.65rem', color: '#f59e0b',
        }}>
          ⚠ Uncertainty gate actif — signal filtré (incertitude trop large)
        </div>
      )}

      {/* Stop / TP */}
      {action === 'LONG' && ml.stop_price > 0 && (
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr',
          gap: '0.375rem', padding: '0.5rem 0.75rem',
          background: 'var(--bg2)', borderRadius: 6, border: '1px solid var(--border)',
        }}>
          <div>
            <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)', textTransform: 'uppercase' }}>Stop Loss</div>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#ef4444', fontVariantNumeric: 'tabular-nums' }}>
              {fmt$(ml.stop_price)}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)', textTransform: 'uppercase' }}>Take Profit</div>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#10b981', fontVariantNumeric: 'tabular-nums' }}>
              {fmt$(ml.take_profit)}
            </div>
          </div>
        </div>
      )}

      <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)', textAlign: 'right' }}>
        Actualisé {ml.refreshed_at ? new Date(ml.refreshed_at).toLocaleTimeString() : '—'}
      </div>
    </div>
  );
};

// ─── Technical signal card ────────────────────────────────────────────────────

const TechSignalCard: React.FC<{ signal: SignalData | null; loading: boolean }> = ({ signal, loading }) => {
  if (loading && !signal) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {[80, 60, 140].map((h, i) => (
          <div key={i} style={{ height: h, background: 'var(--bg2)', borderRadius: 8, animation: 'pulse 1.5s infinite' }} />
        ))}
      </div>
    );
  }
  if (!signal) return null;

  const action = signal.action;
  const aColor = ACTION_COLOR[action];
  const aBg    = ACTION_BG[action];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {/* Header */}
      <div style={{ fontSize: '0.6rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8' }}>
        ◈ Analyse Technique Multi-TF
      </div>

      {/* Action badge */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.75rem 0.875rem',
        background: aBg, border: `1px solid ${aColor}33`, borderRadius: 8,
      }}>
        <div>
          <div style={{ fontSize: '1.25rem', fontWeight: 800, color: 'var(--txt)', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
            {fmt$(signal.current_price, signal.current_price < 10 ? 3 : 2)}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: 4 }}>
            {signal.change_24h_pct !== undefined && (
              <span style={{ fontSize: '0.72rem', fontWeight: 600, color: signal.change_24h_pct >= 0 ? '#10b981' : '#ef4444' }}>
                {fmtPct(signal.change_24h_pct)} 24h
              </span>
            )}
            {signal.change_7d_pct !== undefined && (
              <span style={{ fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
                {fmtPct(signal.change_7d_pct)} 7j
              </span>
            )}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
            padding: '0.3rem 0.7rem', borderRadius: 7,
            background: `${aColor}22`, border: `1px solid ${aColor}55`,
            color: aColor, fontWeight: 800, fontSize: '0.82rem',
          }}>
            <span>{ACTION_ICON[action]}</span><span>{action}</span>
          </div>
          <div style={{ marginTop: 4, fontSize: '0.65rem', color: 'var(--txt-muted)' }}>
            Score <strong style={{ color: 'var(--txt)' }}>{signal.confidence.toFixed(0)}%</strong>
          </div>
        </div>
      </div>

      {/* Confidence bar */}
      <div>
        <div style={{ height: 4, background: 'var(--bg3)', borderRadius: 99, overflow: 'hidden' }}>
          <div style={{
            height: '100%', borderRadius: 99,
            width: `${signal.confidence}%`,
            background: `linear-gradient(90deg, ${aColor}88, ${aColor})`,
            transition: 'width 0.5s ease',
          }} />
        </div>
      </div>

      {/* Gates */}
      {signal.details?.gates?.length > 0 && (
        <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
          {signal.details.gates.map(g => (
            <div key={g.name} style={{
              display: 'flex', alignItems: 'center', gap: '0.2rem',
              padding: '2px 7px', borderRadius: 99, fontSize: '0.6rem', fontWeight: 600,
              background: g.score > 0 ? 'rgba(16,185,129,0.1)' : g.score < 0 ? 'rgba(239,68,68,0.1)' : 'rgba(100,116,139,0.08)',
              color: g.score > 0 ? '#10b981' : g.score < 0 ? '#ef4444' : '#64748b',
              border: `1px solid ${g.score > 0 ? '#10b98122' : g.score < 0 ? '#ef444422' : '#64748b18'}`,
            }}>
              {g.score > 0 ? '✓' : g.score < 0 ? '✗' : '—'} {g.name.replace('_', ' ')}
            </div>
          ))}
        </div>
      )}

      {/* Chart 5m */}
      <div style={{ background: 'var(--bg2)', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }}>
        <CandleChart candles={signal.chart_5m || []} color={signal.color} action={action} />
      </div>

      {/* Risk management */}
      {signal.risk && action !== 'WAIT' && (
        <div style={{ background: 'var(--bg2)', borderRadius: 7, padding: '0.75rem', border: '1px solid var(--border)' }}>
          <div style={{ fontSize: '0.6rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--txt-muted)', marginBottom: '0.5rem' }}>
            Risk Management
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem' }}>
            {[
              { label: 'Entry',  value: fmt$(signal.risk.entry, 2), color: aColor },
              { label: 'Stop',   value: fmt$(signal.risk.stop,  2), color: '#ef4444' },
              { label: 'TP1',    value: fmt$(signal.risk.tp1,   2), color: '#10b981' },
              { label: 'TP2',    value: fmt$(signal.risk.tp2,   2), color: '#10b981' },
            ].map(item => (
              <div key={item.label}>
                <div style={{ fontSize: '0.55rem', color: 'var(--txt-dim)', textTransform: 'uppercase' }}>{item.label}</div>
                <div style={{ fontSize: '0.78rem', fontWeight: 700, color: item.color, fontVariantNumeric: 'tabular-nums' }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Indicators */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        {(signal.indicators || []).map(ind => {
          const col = INDICATOR_COLORS[ind.color] || 'var(--txt)';
          return (
            <div key={ind.name} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '0.35rem 0.6rem',
              background: 'var(--bg2)', borderRadius: 5,
              border: `1px solid ${col}14`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <span style={{ fontSize: '0.85rem', width: 18, textAlign: 'center' }}>{ind.icon}</span>
                <span style={{ fontSize: '0.68rem', color: 'var(--txt-muted)' }}>{ind.label}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <span style={{ fontSize: '0.65rem', color: 'var(--txt-dim)' }}>{ind.interpretation}</span>
                <span style={{ fontSize: '0.75rem', fontWeight: 700, color: col, fontVariantNumeric: 'tabular-nums' }}>{ind.value}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ fontSize: '0.58rem', color: 'var(--txt-dim)', textAlign: 'right' }}>
        Mis à jour {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : '—'}
      </div>
    </div>
  );
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function AlphaSignal() {
  const [signals, setSignals]   = useState<Record<string, SignalData>>({});
  const [mlPred, setMlPred]     = useState<MLPrediction | null>(null);
  const [selected, setSelected] = useState('BTCUSDT');
  const [loading, setLoading]   = useState(true);
  const [mlLoading, setMlLoading] = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mlTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadSignals = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/v2/signals/all`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSignals(data.signals || {});
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadML = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/pipeline/signal`);
      if (r.ok) {
        const data = await r.json();
        setMlPred(data);
      }
    } catch {
      // ML non disponible — pas d'erreur bloquante
    } finally {
      setMlLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSignals();
    loadML();
    timerRef.current   = setInterval(loadSignals, 60_000);
    mlTimerRef.current = setInterval(loadML, 60_000);
    return () => {
      if (timerRef.current)   clearInterval(timerRef.current);
      if (mlTimerRef.current) clearInterval(mlTimerRef.current);
    };
  }, [loadSignals, loadML]);

  const current  = signals[selected] || null;
  const isBTC    = selected === 'BTCUSDT';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: '100%' }} className="animate-fadeIn">

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        {SYMBOLS.map(sym => {
          const sig = signals[sym.key];
          const act = sig?.action || 'WAIT';
          const ac  = ACTION_COLOR[act as keyof typeof ACTION_COLOR];
          const isActive = selected === sym.key;

          return (
            <button
              key={sym.key}
              onClick={() => setSelected(sym.key)}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                padding: '0.5rem 1.125rem',
                borderRadius: 10,
                border: isActive ? `1px solid ${sym.color}55` : '1px solid var(--border)',
                background: isActive ? `${sym.color}12` : 'var(--bg1)',
                cursor: 'pointer', transition: 'all 0.15s', minWidth: 88,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', marginBottom: '0.2rem' }}>
                <span style={{ fontSize: '1rem', color: isActive ? sym.color : 'var(--txt-muted)' }}>{sym.icon}</span>
                <span style={{ fontSize: '0.82rem', fontWeight: 700, color: isActive ? 'var(--txt)' : 'var(--txt-muted)' }}>
                  {sym.label}
                </span>
              </div>
              {sym.key === 'BTCUSDT' && mlPred ? (
                <>
                  <div style={{ fontSize: '0.65rem', fontWeight: 600, color: ACTION_COLOR[(mlPred.action || 'HOLD') as keyof typeof ACTION_COLOR] || '#64748b' }}>
                    {ACTION_ICON[(mlPred.action || 'HOLD') as keyof typeof ACTION_ICON] || '◼'} ML
                  </div>
                  <div style={{ fontSize: '0.6rem', color: 'var(--txt-muted)', fontVariantNumeric: 'tabular-nums' }}>
                    {fmtScore(mlPred.p_long)}
                  </div>
                </>
              ) : sig ? (
                <>
                  <div style={{ fontSize: '0.65rem', fontWeight: 600, color: ac }}>
                    {ACTION_ICON[act as keyof typeof ACTION_ICON]} {act}
                  </div>
                  <div style={{ fontSize: '0.6rem', color: 'var(--txt-muted)', fontVariantNumeric: 'tabular-nums' }}>
                    {sig.confidence.toFixed(0)}%
                  </div>
                </>
              ) : (
                <div style={{ fontSize: '0.65rem', color: 'var(--txt-dim)' }}>…</div>
              )}
            </button>
          );
        })}

        {error && (
          <div style={{ marginLeft: 'auto', fontSize: '0.7rem', color: '#ef4444', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            ⚠ {error}
            <button className="btn btn-ghost btn-xs" onClick={loadSignals}>↻</button>
          </div>
        )}
      </div>

      {/* Overview bar */}
      {Object.keys(signals).length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
          {SYMBOLS.map(sym => {
            const sig = signals[sym.key];
            if (!sig) return <div key={sym.key} className="stat-tile" />;
            const ac = ACTION_COLOR[sig.action];
            return (
              <div
                key={sym.key}
                className="stat-tile"
                style={{
                  cursor: 'pointer',
                  border: selected === sym.key ? `1px solid ${sym.color}44` : '1px solid var(--border)',
                  background: selected === sym.key ? `${sym.color}08` : 'var(--bg2)',
                }}
                onClick={() => setSelected(sym.key)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ fontSize: '0.62rem', color: 'var(--txt-muted)', marginBottom: 3 }}>{sym.label}/USDT</div>
                    <div style={{ fontSize: '0.9rem', fontWeight: 700, color: 'var(--txt)', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt$(sig.current_price, sig.current_price < 10 ? 3 : 2)}
                    </div>
                    {sig.change_24h_pct !== undefined && (
                      <div style={{ fontSize: '0.68rem', marginTop: 2, color: sig.change_24h_pct >= 0 ? '#10b981' : '#ef4444' }}>
                        {fmtPct(sig.change_24h_pct)}
                      </div>
                    )}
                  </div>
                  <div style={{
                    padding: '2px 7px', borderRadius: 99,
                    background: `${ac}18`, color: ac,
                    fontSize: '0.67rem', fontWeight: 800,
                    border: `1px solid ${ac}33`,
                  }}>
                    {ACTION_ICON[sig.action]} {sig.action}
                  </div>
                </div>
                {/* Pour BTC, montrer le score ML */}
                {sym.key === 'BTCUSDT' && mlPred && (
                  <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--border)', display: 'flex', gap: 8, fontSize: '0.58rem', color: 'var(--txt-muted)' }}>
                    <span>ML <strong style={{ color: ACTION_COLOR[(mlPred.action || 'HOLD') as keyof typeof ACTION_COLOR] || '#64748b' }}>{mlPred.action}</strong></span>
                    <span>p={fmtScore(mlPred.p_long)}</span>
                    <span style={{ color: REGIME_COLOR[mlPred.regime] || '#60a5fa' }}>{mlPred.regime}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Detail panels */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {isBTC ? (
          /* BTC : deux colonnes — ML (primaire) + Analyse Technique */
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div style={{
              padding: '0.875rem',
              background: 'var(--bg1)',
              border: '1px solid rgba(96,165,250,0.2)',
              borderRadius: 10,
            }}>
              <MLPipelinePanel ml={mlPred} loading={mlLoading} />
            </div>
            <div style={{
              padding: '0.875rem',
              background: 'var(--bg1)',
              border: '1px solid var(--border)',
              borderRadius: 10,
            }}>
              <TechSignalCard signal={current} loading={loading} />
            </div>
          </div>
        ) : (
          /* ETH / SOL : analyse technique seulement */
          <div style={{
            padding: '0.875rem',
            background: 'var(--bg1)',
            border: '1px solid var(--border)',
            borderRadius: 10,
          }}>
            <div style={{ marginBottom: '0.625rem', fontSize: '0.6rem', color: 'var(--txt-dim)' }}>
              Analyse Technique uniquement — le modèle ML TRM Fleet v2 opère sur BTCUSDT
            </div>
            <TechSignalCard signal={current} loading={loading} />
          </div>
        )}
      </div>
    </div>
  );
}
