import React, { useState, useEffect, useCallback } from 'react';
import { DataService } from '../services/DataService';
import './AIStackDashboard.css';

// ── Types ──────────────────────────────────────────────────────────────────────

interface FilterMetrics {
  model: string;
  val_acc: number | null;
  val_f1: number | null;
  val_auc: number | null;
  recall_tradeable: number | null;
  recall_not_tradeable: number | null;
  calibrated_threshold_long: number;
  calibrated_threshold_short: number;
}

interface ModelEntry {
  model: string;
  acc: number;
  macro_f1: number;
  auc: number;
  precision_long?: number;
  precision_short?: number;
  recall_long?: number;
  recall_short?: number;
}

interface SideMetrics {
  side: 'long' | 'short';
  best_model: string;
  acc: number | null;
  macro_f1: number | null;
  auc: number | null;
  precision: number | null;
  recall: number | null;
  all_models: ModelEntry[];
  direction_threshold: number;
  filter_threshold: number;
  status: string;
  disabled_reason?: string;
  calibration?: { ece_before: number; ece_after: number; recommended_threshold: number } | null;
  beats_threshold?: boolean;
}

interface TRMSpecialist {
  name: string;
  side: string;
  weight: number | null;
  auc: number | null;
  macro_f1: number | null;
  n_train: number | null;
  n_val: number | null;
  acc: number | null;
  precision_pos: number | null;
  recall_pos: number | null;
  ece: number | null;
  status: string;
  label: string | null;
}

interface TRMConfig {
  default_weight: number;
  max_weight: number;
  min_auc: number;
  min_samples: number;
  n_trained: number;
  n_accepted: number;
}

interface BacktestResult {
  side?: string;
  n_tested: number;
  n_trades: number;
  profit_factor: number;
  max_drawdown: number;
  sharpe_annualized: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  expectancy_per_trade: number;
  initial_equity: number;
  final_equity: number;
  total_return_pct: number;
  by_year: Record<string, { trades: number; pnl_sum: number; pf: number; win_rate: number }>;
  model: string;
  cost_pct: number;
  filter_threshold: number;
  direction_threshold: number;
  risk_per_trade: number;
}

interface LevelDef {
  id: number;
  name: string;
  description: string;
  model: string;
  status: string;
  color: string;
}

interface ConfigInfo {
  horizon_bars: number;
  horizon_str: string;
  bar_frequency: string;
  cost_pct_base: number;
  cost_pct_stress: number;
  cost_pct_pessimistic: number;
  tradeable_quantile_long: number;
  tradeable_quantile_short: number;
  long_min_abs_return: number;
  short_min_abs_return: number;
  train_end_year: number;
  val_year: number;
  test_from_year: number;
  initial_equity: number;
}

interface StackData {
  success: boolean;
  run_id: string | null;
  timestamp: string;
  config: ConfigInfo;
  levels: LevelDef[];
  filter: FilterMetrics | null;
  long: SideMetrics | null;
  short: SideMetrics | null;
  trm_fleet: TRMSpecialist[];
  trm_config: TRMConfig;
  backtest_long: BacktestResult | null;
  backtest_short: BacktestResult | null;
  label_stats: Record<string, any> | null;
  regime_report: Record<string, any> | null;
  short_enabled: boolean;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? '—' : `${(v * 100).toFixed(digits)}%`;

const fixed = (v: number | null | undefined, digits = 4) =>
  v == null ? '—' : v.toFixed(digits);

const num = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('fr-FR');

const colorPF = (pf: number) => pf >= 1.3 ? '#4ade80' : pf >= 1.0 ? '#facc15' : '#f87171';
const colorPct = (v: number) => v > 0 ? '#4ade80' : v < 0 ? '#f87171' : '#94a3b8';
const colorWR = (wr: number) => wr >= 0.55 ? '#4ade80' : wr >= 0.45 ? '#facc15' : '#f87171';
const colorAUC = (auc: number | null) => {
  if (auc == null) return '#94a3b8';
  return auc >= 0.70 ? '#4ade80' : auc >= 0.60 ? '#facc15' : '#f87171';
};

// ── Sub-components ─────────────────────────────────────────────────────────────

const MetricBadge: React.FC<{ label: string; value: string; color?: string }> = ({ label, value, color }) => (
  <div className="asd-metric-badge">
    <span className="asd-metric-label">{label}</span>
    <span className="asd-metric-value" style={color ? { color } : undefined}>{value}</span>
  </div>
);

const SectionHeader: React.FC<{ icon: string; title: string; sub?: string }> = ({ icon, title, sub }) => (
  <div className="asd-section-header">
    <span className="asd-section-icon">{icon}</span>
    <div>
      <h2 className="asd-section-title">{title}</h2>
      {sub && <p className="asd-section-sub">{sub}</p>}
    </div>
  </div>
);

const StatusPill: React.FC<{ status: string }> = ({ status }) => {
  const map: Record<string, string> = {
    active: 'asd-pill-active',
    accepted: 'asd-pill-active',
    disabled: 'asd-pill-disabled',
    rejected: 'asd-pill-rejected',
    pending: 'asd-pill-pending',
  };
  return <span className={`asd-pill ${map[status] ?? 'asd-pill-pending'}`}>{status}</span>;
};

// ── Architecture Pipeline bar ──────────────────────────────────────────────────

const ArchitecturePipeline: React.FC<{ levels: LevelDef[] }> = ({ levels }) => (
  <div className="asd-pipeline">
    {levels.map((lvl, i) => (
      <React.Fragment key={lvl.id}>
        <div className="asd-pipeline-node" style={{ borderColor: lvl.color }}>
          <div className="asd-pipeline-badge" style={{ background: lvl.color }}>L{lvl.id}</div>
          <div className="asd-pipeline-info">
            <span className="asd-pipeline-name">{lvl.name}</span>
            <span className="asd-pipeline-model">{lvl.model}</span>
          </div>
          <StatusPill status={lvl.status} />
        </div>
        {i < levels.length - 1 && <div className="asd-pipeline-arrow">▶</div>}
      </React.Fragment>
    ))}
  </div>
);

// ── TRM Fleet ─────────────────────────────────────────────────────────────────

const TRMFleetSection: React.FC<{ fleet: TRMSpecialist[]; config: TRMConfig }> = ({ fleet, config }) => (
  <section className="asd-section">
    <SectionHeader
      icon="⚡"
      title="TRM Fleet — Spécialistes Contextuels"
      sub={`${config.n_accepted}/${config.n_trained} spécialistes actifs · min AUC ${config.min_auc} · min samples ${num(config.min_samples)}`}
    />
    <div className="asd-trm-grid">
      {fleet.map(sp => (
        <div key={sp.name} className={`asd-trm-card ${sp.status === 'accepted' ? 'asd-trm-active' : 'asd-trm-inactive'}`}>
          <div className="asd-trm-card-header">
            <span className="asd-trm-name">{sp.name}</span>
            <StatusPill status={sp.status} />
          </div>
          <div className="asd-trm-details">
            <div className="asd-trm-row">
              <span>Side</span>
              <span className={sp.side === 'long' ? 'asd-long-tag' : sp.side === 'short' ? 'asd-short-tag' : ''}>{sp.side}</span>
            </div>
            <div className="asd-trm-row">
              <span>Poids router</span>
              <strong>{sp.weight != null ? `${(sp.weight * 100).toFixed(1)}%` : '—'}</strong>
            </div>
            <div className="asd-trm-row">
              <span>AUC val</span>
              <strong style={{ color: colorAUC(sp.auc) }}>{fixed(sp.auc, 4)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Macro F1</span>
              <strong>{fixed(sp.macro_f1, 4)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Acc</span>
              <strong>{pct(sp.acc)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Precision+</span>
              <strong>{pct(sp.precision_pos)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Recall+</span>
              <strong>{pct(sp.recall_pos)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>ECE</span>
              <strong>{sp.ece != null ? sp.ece.toFixed(5) : '—'}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Train samples</span>
              <strong>{num(sp.n_train)}</strong>
            </div>
            <div className="asd-trm-row">
              <span>Val samples</span>
              <strong>{num(sp.n_val)}</strong>
            </div>
            {sp.label && (
              <div className="asd-trm-row">
                <span>Label</span>
                <code>{sp.label}</code>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  </section>
);

// ── Model card (Long / Short) ──────────────────────────────────────────────────

const ModelCard: React.FC<{ data: SideMetrics }> = ({ data }) => {
  const isLong = data.side === 'long';
  const accentColor = isLong ? '#4ade80' : '#f87171';

  return (
    <div className={`asd-model-card ${isLong ? 'asd-model-long' : 'asd-model-short'}`}>
      <div className="asd-model-card-header">
        <div className="asd-model-side-badge" style={{ background: accentColor }}>
          {isLong ? '↑ LONG' : '↓ SHORT'}
        </div>
        <div className="asd-model-header-right">
          <span className="asd-model-name">{data.best_model}</span>
          <StatusPill status={data.status} />
        </div>
      </div>

      {data.disabled_reason && (
        <div className="asd-model-warning">
          {data.disabled_reason}
        </div>
      )}

      <div className="asd-model-metrics-grid">
        <MetricBadge label="Accuracy" value={pct(data.acc)} />
        <MetricBadge label="Macro F1" value={fixed(data.macro_f1)} />
        <MetricBadge label="AUC" value={fixed(data.auc)} color={colorAUC(data.auc)} />
        <MetricBadge label="Precision" value={pct(data.precision)} />
        <MetricBadge label="Recall" value={pct(data.recall)} />
        {isLong && data.beats_threshold != null && (
          <MetricBadge
            label="Seuil passé"
            value={data.beats_threshold ? 'OUI' : 'NON'}
            color={data.beats_threshold ? '#4ade80' : '#f87171'}
          />
        )}
      </div>

      <div className="asd-model-thresholds">
        <div className="asd-threshold-row">
          <span>Seuil filtre</span>
          <strong>{data.filter_threshold.toFixed(2)}</strong>
        </div>
        <div className="asd-threshold-row">
          <span>Seuil direction</span>
          <strong>{data.direction_threshold.toFixed(2)}</strong>
        </div>
        {data.calibration && (
          <>
            <div className="asd-threshold-row">
              <span>ECE avant calib.</span>
              <strong>{data.calibration.ece_before.toFixed(5)}</strong>
            </div>
            <div className="asd-threshold-row">
              <span>ECE après calib.</span>
              <strong>{data.calibration.ece_after.toFixed(5)}</strong>
            </div>
            <div className="asd-threshold-row">
              <span>Seuil calibré</span>
              <strong>{data.calibration.recommended_threshold.toFixed(2)}</strong>
            </div>
          </>
        )}
      </div>

      {data.all_models.length > 1 && (
        <div className="asd-model-comparison">
          <h4>Comparatif modèles</h4>
          <table className="asd-model-table">
            <thead>
              <tr>
                <th>Modèle</th>
                <th>Acc</th>
                <th>F1</th>
                <th>AUC</th>
                <th>Precision</th>
                <th>Recall</th>
              </tr>
            </thead>
            <tbody>
              {data.all_models.map(m => (
                <tr key={m.model} className={m.model === data.best_model ? 'asd-best-row' : ''}>
                  <td>{m.model} {m.model === data.best_model && <span className="asd-best-tag">★</span>}</td>
                  <td>{pct(m.acc)}</td>
                  <td>{fixed(m.macro_f1)}</td>
                  <td style={{ color: colorAUC(m.auc) }}>{fixed(m.auc)}</td>
                  <td>{pct(isLong ? m.precision_long : m.precision_short)}</td>
                  <td>{pct(isLong ? m.recall_long : m.recall_short)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// ── Backtest card ──────────────────────────────────────────────────────────────

const BacktestCard: React.FC<{ data: BacktestResult; side: 'long' | 'short' }> = ({ data, side }) => {
  const isLong = side === 'long';
  const years = Object.entries(data.by_year || {});

  return (
    <div className={`asd-bt-card ${isLong ? 'asd-bt-long' : 'asd-bt-short'}`}>
      <div className="asd-bt-header">
        <span className={`asd-bt-side ${isLong ? 'asd-long-tag' : 'asd-short-tag'}`}>
          {isLong ? '↑ LONG Backtest' : '↓ SHORT Backtest'}
        </span>
        <span className="asd-bt-model">{data.model}</span>
      </div>

      <div className="asd-bt-kpis">
        <div className="asd-kpi">
          <span className="asd-kpi-val" style={{ color: colorPct(data.total_return_pct) }}>
            {data.total_return_pct > 0 ? '+' : ''}{data.total_return_pct.toFixed(2)}%
          </span>
          <span className="asd-kpi-label">ROI Total</span>
        </div>
        <div className="asd-kpi">
          <span className="asd-kpi-val" style={{ color: colorPF(data.profit_factor) }}>
            {data.profit_factor.toFixed(4)}
          </span>
          <span className="asd-kpi-label">Profit Factor</span>
        </div>
        <div className="asd-kpi">
          <span className="asd-kpi-val" style={{ color: colorWR(data.win_rate) }}>
            {pct(data.win_rate)}
          </span>
          <span className="asd-kpi-label">Win Rate</span>
        </div>
        <div className="asd-kpi">
          <span className="asd-kpi-val" style={{ color: data.sharpe_annualized > 0 ? '#4ade80' : '#f87171' }}>
            {data.sharpe_annualized.toFixed(2)}
          </span>
          <span className="asd-kpi-label">Sharpe Ann.</span>
        </div>
        <div className="asd-kpi">
          <span className="asd-kpi-val" style={{ color: '#f87171' }}>
            -{pct(data.max_drawdown)}
          </span>
          <span className="asd-kpi-label">Max DD</span>
        </div>
        <div className="asd-kpi">
          <span className="asd-kpi-val">{data.n_trades}</span>
          <span className="asd-kpi-label">Trades</span>
        </div>
      </div>

      <div className="asd-bt-details">
        <div className="asd-bt-detail-row">
          <span>Equity initiale</span><strong>${num(data.initial_equity)}</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Equity finale</span>
          <strong style={{ color: colorPct(data.final_equity - data.initial_equity) }}>
            ${num(Math.round(data.final_equity))}
          </strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Expectancy/trade</span>
          <strong style={{ color: colorPct(data.expectancy_per_trade) }}>
            {data.expectancy_per_trade > 0 ? '+' : ''}{data.expectancy_per_trade.toFixed(4)}$
          </strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Avg Win</span>
          <strong style={{ color: '#4ade80' }}>+{data.avg_win.toFixed(2)}$</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Avg Loss</span>
          <strong style={{ color: '#f87171' }}>{data.avg_loss.toFixed(2)}$</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Coûts</span><strong>{pct(data.cost_pct)}</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Risque/trade</span><strong>{pct(data.risk_per_trade)}</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Seuil filtre</span><strong>{data.filter_threshold.toFixed(2)}</strong>
        </div>
        <div className="asd-bt-detail-row">
          <span>Seuil direction</span><strong>{data.direction_threshold.toFixed(2)}</strong>
        </div>
      </div>

      {years.length > 0 && (
        <div className="asd-by-year">
          <h4>Par année</h4>
          <table className="asd-year-table">
            <thead>
              <tr>
                <th>Année</th>
                <th>Trades</th>
                <th>PnL ($)</th>
                <th>PF</th>
                <th>Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {years.map(([year, y]) => (
                <tr key={year}>
                  <td><strong>{year}</strong></td>
                  <td>{y.trades}</td>
                  <td style={{ color: colorPct(y.pnl_sum) }}>
                    {y.pnl_sum > 0 ? '+' : ''}{y.pnl_sum.toFixed(2)}$
                  </td>
                  <td style={{ color: colorPF(y.pf) }}>{y.pf.toFixed(3)}</td>
                  <td style={{ color: colorWR(y.win_rate) }}>{pct(y.win_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// ── Filter section ─────────────────────────────────────────────────────────────

const FilterSection: React.FC<{ filter: FilterMetrics }> = ({ filter }) => (
  <section className="asd-section">
    <SectionHeader icon="🚪" title="Level 0 — Global Gating (Filtre Tradeable)" sub={`Modèle : ${filter.model}`} />
    <div className="asd-filter-grid">
      <MetricBadge label="Accuracy val" value={pct(filter.val_acc)} />
      <MetricBadge label="F1 val" value={fixed(filter.val_f1)} />
      <MetricBadge label="AUC val" value={fixed(filter.val_auc)} color={colorAUC(filter.val_auc)} />
      <MetricBadge label="Recall tradeable" value={pct(filter.recall_tradeable)} color={colorWR(filter.recall_tradeable ?? 0)} />
      <MetricBadge label="Recall non-tradeable" value={pct(filter.recall_not_tradeable)} />
      <MetricBadge label="Seuil Long" value={filter.calibrated_threshold_long.toFixed(2)} />
      <MetricBadge label="Seuil Short" value={filter.calibrated_threshold_short.toFixed(2)} />
    </div>
  </section>
);

// ── Label stats ────────────────────────────────────────────────────────────────

const LabelStatsSection: React.FC<{ stats: Record<string, any> }> = ({ stats }) => (
  <section className="asd-section">
    <SectionHeader icon="🏷️" title="Statistiques Labels" sub="Distribution du dataset sur les splits train/val/test" />
    <div className="asd-label-grid">
      <MetricBadge label="Total barres" value={num(stats.n_total)} />
      <MetricBadge label="Tradeables" value={num(stats.n_tradeable ?? stats.n_long_net)} />
      <MetricBadge label="Long labels" value={num(stats.n_long)} />
      <MetricBadge label="Short labels" value={num(stats.n_short)} />
      <MetricBadge label="Long gris" value={num(stats.n_long_gray)} />
      <MetricBadge label="Short gris" value={num(stats.n_short_gray)} />
      <MetricBadge label="Frac Long" value={stats.frac_long != null ? `${(stats.frac_long * 100).toFixed(2)}%` : '—'} />
      <MetricBadge label="Frac Short" value={stats.frac_short != null ? `${(stats.frac_short * 100).toFixed(2)}%` : '—'} />
      <MetricBadge label="Seuil Long (thr)" value={stats.thr_long != null ? `${(stats.thr_long * 100).toFixed(3)}%` : '—'} />
      <MetricBadge label="Seuil Short (thr)" value={stats.thr_short_with_cost != null ? `${(stats.thr_short_with_cost * 100).toFixed(3)}%` : '—'} />
    </div>
    {stats.regime_distribution && (
      <div className="asd-regime-dist">
        <h4>Distribution régimes</h4>
        <div className="asd-regime-pills">
          {Object.entries(stats.regime_distribution).map(([regime, count]) => (
            <div key={regime} className="asd-regime-pill">
              <span>{regime}</span>
              <strong>{num(count as number)}</strong>
            </div>
          ))}
        </div>
      </div>
    )}
  </section>
);

// ── Config section ─────────────────────────────────────────────────────────────

const ConfigSection: React.FC<{ config: ConfigInfo }> = ({ config }) => (
  <section className="asd-section">
    <SectionHeader icon="⚙️" title="Configuration Globale" sub="Source de vérité — ai/level_0/constants.py" />
    <div className="asd-config-grid">
      <div className="asd-config-group">
        <h4>Horizon</h4>
        <MetricBadge label="Barres" value={`${config.horizon_bars} (${config.horizon_str})`} />
        <MetricBadge label="Fréquence" value={config.bar_frequency} />
      </div>
      <div className="asd-config-group">
        <h4>Coûts</h4>
        <MetricBadge label="Base" value={pct(config.cost_pct_base)} />
        <MetricBadge label="Stress" value={pct(config.cost_pct_stress)} />
        <MetricBadge label="Pessimiste" value={pct(config.cost_pct_pessimistic)} />
      </div>
      <div className="asd-config-group">
        <h4>Labels</h4>
        <MetricBadge label="Quantile Long" value={pct(config.tradeable_quantile_long)} />
        <MetricBadge label="Quantile Short" value={pct(config.tradeable_quantile_short)} />
        <MetricBadge label="Min ret Long" value={pct(config.long_min_abs_return)} />
        <MetricBadge label="Min ret Short" value={pct(config.short_min_abs_return)} />
      </div>
      <div className="asd-config-group">
        <h4>Splits temporels</h4>
        <MetricBadge label="Train" value={`≤ ${config.train_end_year}`} />
        <MetricBadge label="Validation" value={`${config.val_year}`} />
        <MetricBadge label="Test" value={`≥ ${config.test_from_year}`} />
      </div>
    </div>
  </section>
);

// ── Main Dashboard ─────────────────────────────────────────────────────────────

const AIStackDashboard: React.FC = () => {
  const [data, setData] = useState<StackData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await DataService.getAIStackOverview();
      setData(res);
      setLastRefresh(new Date());
      setError(null);
    } catch (e: any) {
      setError(e.message || 'Erreur chargement');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="asd-loading">
        <div className="asd-spinner" />
        <p>Chargement de la Stack IA…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="asd-error">
        <span>⚠</span>
        <p>{error || 'Données indisponibles'}</p>
        <button onClick={load}>Réessayer</button>
      </div>
    );
  }

  const shortStatus = data.short_enabled ? 'ACTIVÉ' : 'DÉSACTIVÉ';

  return (
    <div className="asd-root">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="asd-header">
        <div className="asd-header-left">
          <h1 className="asd-title">Stack IA — Vue Complète</h1>
          <p className="asd-subtitle">
            Run: <code>{data.run_id ?? 'N/A'}</code>
            {lastRefresh && (
              <span className="asd-refresh-time"> · Actualisé {lastRefresh.toLocaleTimeString('fr-FR')}</span>
            )}
          </p>
        </div>
        <div className="asd-header-kpis">
          <div className="asd-header-kpi">
            <span>{data.levels.length}</span>
            <small>Niveaux actifs</small>
          </div>
          <div className="asd-header-kpi">
            <span>{data.trm_fleet.filter(t => t.status === 'accepted').length}</span>
            <small>TRM actifs</small>
          </div>
          <div className="asd-header-kpi">
            <span className={data.short_enabled ? 'asd-green' : 'asd-red'}>{shortStatus}</span>
            <small>Short</small>
          </div>
          {data.backtest_long && (
            <div className="asd-header-kpi">
              <span style={{ color: colorPF(data.backtest_long.profit_factor) }}>
                PF {data.backtest_long.profit_factor.toFixed(3)}
              </span>
              <small>Long PF</small>
            </div>
          )}
          {data.backtest_long && (
            <div className="asd-header-kpi">
              <span style={{ color: colorPct(data.backtest_long.total_return_pct) }}>
                {data.backtest_long.total_return_pct > 0 ? '+' : ''}{data.backtest_long.total_return_pct.toFixed(2)}%
              </span>
              <small>ROI Long</small>
            </div>
          )}
        </div>
        <button className="asd-refresh-btn" onClick={load}>↻ Actualiser</button>
      </div>

      {/* ── Architecture Pipeline ────────────────────────────────────────────── */}
      <section className="asd-section">
        <SectionHeader icon="🏗️" title="Architecture Pipeline" sub="Flux de décision : Gating → Régime → Edge → TRM → Risk" />
        <ArchitecturePipeline levels={data.levels} />
      </section>

      {/* ── Config ────────────────────────────────────────────────────────────── */}
      <ConfigSection config={data.config} />

      {/* ── Filter (Level 0) ──────────────────────────────────────────────────── */}
      {data.filter && <FilterSection filter={data.filter} />}

      {/* ── Long & Short Models ───────────────────────────────────────────────── */}
      <section className="asd-section">
        <SectionHeader icon="🧠" title="Level 2 — Edge Specialists (Long & Short)" sub="Modèles asymétriques de scoring directionnel" />
        <div className="asd-models-row">
          {data.long && <ModelCard data={data.long} />}
          {data.short && <ModelCard data={data.short} />}
        </div>
      </section>

      {/* ── TRM Fleet ─────────────────────────────────────────────────────────── */}
      {data.trm_fleet.length > 0 && (
        <TRMFleetSection fleet={data.trm_fleet} config={data.trm_config} />
      )}

      {/* ── Backtest Performance ──────────────────────────────────────────────── */}
      <section className="asd-section">
        <SectionHeader icon="📈" title="Performance Backtestée" sub="Métriques sur données test (out-of-sample)" />
        <div className="asd-bt-row">
          {data.backtest_long && <BacktestCard data={data.backtest_long} side="long" />}
          {data.backtest_short && <BacktestCard data={data.backtest_short} side="short" />}
        </div>
      </section>

      {/* ── Label Stats ───────────────────────────────────────────────────────── */}
      {data.label_stats && <LabelStatsSection stats={data.label_stats} />}
    </div>
  );
};

export default AIStackDashboard;
