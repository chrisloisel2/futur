#!/usr/bin/env python3
"""
View Backtest Results - Pretty Print Latest Metrics
"""
import json
from pathlib import Path
from datetime import datetime

# Find latest backtest
backtest_dir = Path("artifacts/backtests")
if not backtest_dir.exists():
    print("❌ No backtest results found. Run ./backtest.sh first.")
    exit(1)

latest = sorted(backtest_dir.glob("backtest_*"), key=lambda p: p.stat().st_mtime, reverse=True)
if not latest:
    print("❌ No backtest results found. Run ./backtest.sh first.")
    exit(1)

latest_dir = latest[0]
metrics_file = latest_dir / "metrics.json"

if not metrics_file.exists():
    print(f"❌ Metrics file not found in {latest_dir}")
    exit(1)

with open(metrics_file) as f:
    metrics = json.load(f)

print("=" * 60)
print(f"📊 BACKTEST RESULTS - {latest_dir.name}")
print("=" * 60)
print()

# Performance Summary
print("🎯 PERFORMANCE SUMMARY")
print("-" * 60)
print(f"Trades:          {metrics['trades']}")
print(f"Gross PnL:       ${metrics['gross_pnl']:.2f}")
print(f"Net PnL:         ${metrics['net_pnl']:.2f}")
print(f"Total Costs:     ${metrics['total_costs']:.2f}")
print()

# Risk Metrics
print("⚠️  RISK METRICS")
print("-" * 60)
print(f"Sharpe Ratio:    {metrics['sharpe_ratio']:.2f}  {'✅' if metrics['sharpe_ratio'] > 1.0 else '❌'}")
print(f"Sortino Ratio:   {metrics['sortino_ratio']:.2f}  {'✅' if metrics['sortino_ratio'] > 1.5 else '❌'}")
print(f"Calmar Ratio:    {metrics['calmar_ratio']:.2f}")
print(f"Max Drawdown:    ${metrics['max_drawdown']:.2f}")
print()

# Win/Loss Stats
print("📈 WIN/LOSS STATISTICS")
print("-" * 60)
print(f"Win Rate:        {metrics['win_rate']*100:.1f}%  {'✅' if metrics['win_rate'] > 0.52 else '⚠️'}")
print(f"Wins:            {metrics['wins']}")
print(f"Losses:          {metrics['losses']}")
print(f"Avg Win:         ${metrics['avg_win']:.2f}")
print(f"Avg Loss:        ${metrics['avg_loss']:.2f}")
print(f"Win/Loss Ratio:  {metrics['win_loss_ratio']:.2f}")
print(f"Profit Factor:   {metrics['profit_factor']:.2f}  {'✅' if metrics['profit_factor'] > 1.0 else '❌'}")
print()

# Overall Assessment
print("=" * 60)
print("📋 ASSESSMENT")
print("=" * 60)

sharpe_ok = metrics['sharpe_ratio'] > 1.0
profit_ok = metrics['profit_factor'] > 1.0
win_rate_ok = metrics['win_rate'] > 0.52

if sharpe_ok and profit_ok and win_rate_ok:
    print("✅ PASSED - System shows promise")
    print("   → Good Sharpe (>1.0)")
    print("   → Profitable (PF >1.0)")
    print("   → Decent win rate (>52%)")
elif sharpe_ok:
    print("⚠️  MIXED - Good risk/reward but needs optimization")
    if not profit_ok:
        print("   ❌ Profit Factor <1.0 (losing money)")
        print("   → Reduce costs or improve trade selection")
    if not win_rate_ok:
        print("   ⚠️  Win rate <52%")
        print("   → Tighten entry criteria")
else:
    print("❌ FAILED - Needs significant work")
    print("   → Sharpe <1.0 (poor risk/reward)")
    print("   → Review decision logic and thresholds")

print()
print("📁 Results saved to:", latest_dir)
print("=" * 60)
