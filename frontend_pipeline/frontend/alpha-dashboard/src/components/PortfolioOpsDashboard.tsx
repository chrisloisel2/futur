import React, { useEffect, useState } from 'react';
import { API_BASE_URL } from '../config/api';

/**
 * PortfolioOpsDashboard — observabilité de l'usine à opportunités.
 *
 * Répond à la question quotidienne : le bot a-t-il évité du bruit ou raté de
 * bons trades ? Affiche A/B/C, λ par moteur, PnL shadow, near-miss, statut de
 * promotion. Données 100% réelles depuis /api/portfolio/* (jamais de mock).
 */

interface EngineRow {
  engine_id: string;
  status: string;
  lambda_a_per_month: number;
  n_a: number; n_b: number; n_c: number;
  a_pnl_mean: number | null;
  shadow_pnl_mean: number | null;
  a_pnl_sum: number | null;
}

interface LedgerSummary {
  status: string;
  n?: number; n_A_trade?: number; n_B_shadow?: number; n_C_reject?: number;
  shadow_pnl_mean?: number | null; near_miss_count?: number;
  near_miss_pnl_mean?: number | null; a_trade_pnl_mean?: number | null;
  pct_explained?: number;
}

const STATUS_COLORS: Record<string, string> = {
  DISABLED: '#666', SHADOW: '#8a8a8a', PAPER: '#3b82f6',
  MICRO_LIVE: '#eab308', HALF_LIVE: '#f97316', FULL_LIVE: '#22c55e',
};

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(2)}%`;

export default function PortfolioOpsDashboard() {
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [engines, setEngines] = useState<EngineRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [s, e] = await Promise.all([
          fetch(`${API_BASE_URL}/api/portfolio/ledger/summary`).then(r => r.json()),
          fetch(`${API_BASE_URL}/api/portfolio/engines`).then(r => r.json()),
        ]);
        setSummary(s);
        setEngines(e.status === 'ok' ? e.engines : []);
        setErr(null);
      } catch (e: any) {
        setErr(String(e));
      }
    };
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, []);

  if (err) return <div style={{ padding: 16, color: '#f87171' }}>Erreur API: {err}</div>;
  if (!summary) return <div style={{ padding: 16 }}>Chargement…</div>;
  if (summary.status === 'disabled')
    return <div style={{ padding: 16 }}>Ledger vide — lancer backfill_decision_ledger.py</div>;

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui' }}>
      <h2>Usine à opportunités — décisions</h2>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '12px 0' }}>
        <Card label="Décisions" value={summary.n?.toLocaleString() ?? '—'} />
        <Card label="A · trade" value={summary.n_A_trade?.toLocaleString() ?? '—'} color="#22c55e" />
        <Card label="B · shadow" value={summary.n_B_shadow?.toLocaleString() ?? '—'} color="#3b82f6" />
        <Card label="C · reject" value={summary.n_C_reject?.toLocaleString() ?? '—'} color="#8a8a8a" />
        <Card label="Shadow PnL moy." value={pct(summary.shadow_pnl_mean)} />
        <Card label="Near-miss PnL" value={pct(summary.near_miss_pnl_mean)} />
        <Card label="Non-trades expliqués" value={pct(summary.pct_explained)} color="#22c55e" />
      </div>

      <h3>Moteurs (λ = trades A / mois)</h3>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid #444' }}>
            <th>Moteur</th><th>Statut</th><th>λ A/mois</th>
            <th>A</th><th>B</th><th>C</th><th>A PnL moy.</th><th>Shadow PnL moy.</th>
          </tr>
        </thead>
        <tbody>
          {engines.map(e => (
            <tr key={e.engine_id} style={{ borderBottom: '1px solid #2a2a2a' }}>
              <td>{e.engine_id}</td>
              <td><span style={{
                background: STATUS_COLORS[e.status] || '#666', color: '#fff',
                padding: '2px 8px', borderRadius: 4, fontSize: 12,
              }}>{e.status}</span></td>
              <td>{e.lambda_a_per_month.toFixed(1)}</td>
              <td>{e.n_a}</td><td>{e.n_b}</td><td>{e.n_c}</td>
              <td style={{ color: (e.a_pnl_mean ?? 0) >= 0 ? '#22c55e' : '#f87171' }}>{pct(e.a_pnl_mean)}</td>
              <td>{pct(e.shadow_pnl_mean)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ color: '#8a8a8a', fontSize: 12, marginTop: 12 }}>
        Données réelles depuis le Decision Ledger · rafraîchi toutes les 60s.
      </p>
    </div>
  );
}

function Card({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: '#1a1a1a', borderRadius: 8, padding: '10px 14px', minWidth: 120,
      border: '1px solid #333',
    }}>
      <div style={{ fontSize: 12, color: '#8a8a8a' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color: color || '#fff' }}>{value}</div>
    </div>
  );
}
