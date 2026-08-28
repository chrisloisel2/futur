#!/usr/bin/env python3
"""EXECUTION_ECONOMICS for the sealed Phase 5.2 CONFIRMED_INFORMATION_CANDIDATE
mechanism (okx__queue_imbalance_l5, horizon 30s, LOO fair value of
binance+bybit+hyperliquid). Reads the exact confirmation tape Phase 5.2 sealed;
does not touch its verdict or thresholds. Simulates a weighted execution split
across the three target venues using real observed spreads from the tape and
public standard-tier taker fees (see phase5_2_execution_economics.TAKER_FEE_BPS).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.contracts import EconomicEvidence
from alpha_foundry_v5.validation import DEFAULT_POLICY, ValidationEngine
from market_physics_v3.phase5_2_execution_economics import build_trades, summarize_trades
from market_physics_v3.phase5_audit import load_parquet_dataset

PRIMARY_SYMBOLS = ("BTCUSDT", "ETHUSDT")
SUPPORT_SYMBOLS = ("SOLUSDT",)


def _evidence_from_summary(summary: dict) -> EconomicEvidence:
    return EconomicEvidence(
        gross_edge_bps=summary["gross_edge_bps"],
        net_edge_bps=summary["net_edge_bps"],
        net_edge_cost_x2_bps=summary["net_edge_cost_x2_bps"],
        delayed_entry_net_bps=summary["delayed_entry_net_bps"],
        profit_factor=summary["profit_factor"],
        max_drawdown=summary["max_drawdown"],
        capacity_usd=summary["capacity_usd"],
        top_contributors_removed_net_bps=summary["top_contributors_removed_net_bps"],
        recent_period_net_bps=summary["recent_period_net_bps"],
        paper_live_net_bps=summary["paper_live_net_bps"],
        fill_rate=summary["fill_rate"],
        realized_slippage_bps=summary["realized_slippage_bps"],
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--out", default="reports/market_physics_v3/phase5_2_execution_economics")
    args = ap.parse_args()

    print(f"[exec-econ] loading {args.tape}", flush=True)
    frame = load_parquet_dataset(args.tape)
    print(f"[exec-econ] loaded rows={len(frame)} columns={len(frame.columns)}", flush=True)

    engine = ValidationEngine(DEFAULT_POLICY)
    results: dict[str, dict] = {}
    all_trades = []
    for symbol in (*PRIMARY_SYMBOLS, *SUPPORT_SYMBOLS):
        trades = build_trades(frame, symbol, cadence_ms=args.cadence_ms)
        all_trades.extend(trades)
        summary = summarize_trades(trades)
        evidence = _evidence_from_summary(summary)
        decision = engine.economic_gate(evidence, require_paper=False)
        summary["gate_passed"] = decision.passed
        summary["gate_failures"] = list(decision.failures)
        summary["role"] = "PRIMARY" if symbol in PRIMARY_SYMBOLS else "SUPPORT"
        results[symbol] = summary
        print(
            f"[exec-econ] {symbol:<9s} n={summary['n_trades']:<4d} "
            f"net_edge_bps={summary['net_edge_bps']:+7.3f} "
            f"cost_x2={summary['net_edge_cost_x2_bps']:+7.3f} "
            f"delayed={summary['delayed_entry_net_bps']:+7.3f} "
            f"top_removed={summary['top_contributors_removed_net_bps']:+7.3f} "
            f"recent={summary['recent_period_net_bps']:+7.3f} "
            f"pf={summary['profit_factor']:.2f} "
            f"capacity=${summary['capacity_usd']:.0f} "
            f"gate={decision.passed} {decision.failures}",
            flush=True,
        )

    combined_summary = summarize_trades(all_trades)
    combined_evidence = _evidence_from_summary(combined_summary)
    combined_decision = engine.economic_gate(combined_evidence, require_paper=False)
    combined_summary["gate_passed"] = combined_decision.passed
    combined_summary["gate_failures"] = list(combined_decision.failures)
    results["COMBINED"] = combined_summary

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mechanism": "okx__queue_imbalance_l5",
        "horizon_ms": 30_000,
        "excluded_venue": "okx",
        "execution_venues": ["binance", "bybit", "hyperliquid"],
        "tape": str(args.tape),
        "assumptions": {
            "taker_fee_bps": {"binance": 5.0, "bybit": 5.5, "hyperliquid": 3.5},
            "spread_cost": "half of observed venue__price_spread_bps at entry and exit",
            "delayed_entry_latency_ms": 300,
            "top_contributor_trim": 0.05,
            "sizing": "weighted by venue__price_weight at entry, normalized across binance/bybit/hyperliquid",
        },
        "policy": {
            "execution_min_pf": DEFAULT_POLICY.execution_min_pf,
            "execution_min_capacity_usd": DEFAULT_POLICY.execution_min_capacity_usd,
        },
        "results": results,
    }
    out_path = out_dir / "SUMMARY.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"[exec-econ] wrote {out_path}", flush=True)
    print(json.dumps(results["COMBINED"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
