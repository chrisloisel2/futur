#!/usr/bin/env python3
"""
scripts/institutional_portfolio_report.py
─────────────────────────────────────────────────────────────────────────────
Génère un rapport complet du portefeuille institutionnel.

Usage
-----
python3 scripts/institutional_portfolio_report.py \
    --portfolio institutional_v1 \
    --version v1.0
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--portfolio", default="institutional_v1")
    p.add_argument("--version", default="v1.0")
    return p.parse_args()


def main():
    args = parse_args()
    backtest_dir = Path("artifacts/institutional/backtests") / args.portfolio / args.version

    if not backtest_dir.exists():
        logger.error(f"Backtest non trouvé : {backtest_dir}")
        logger.error("Exécuter institutional_run_backtest.py d'abord")
        sys.exit(1)

    metrics_path = backtest_dir / "metrics.json"
    if not metrics_path.exists():
        logger.error(f"metrics.json non trouvé dans {backtest_dir}")
        sys.exit(1)

    metrics = json.loads(metrics_path.read_text())

    report_lines = [
        f"# Portfolio Report — {args.portfolio} {args.version}",
        f"",
        f"## Métriques principales",
        f"| Métrique       | Valeur   |",
        f"|----------------|----------|",
        f"| PF             | {metrics.get('pf', 0):.4f} |",
        f"| Sharpe         | {metrics.get('sharpe', 0):.4f} |",
        f"| Sortino        | {metrics.get('sortino', 0):.4f} |",
        f"| Calmar         | {metrics.get('calmar', 0):.4f} |",
        f"| CAGR           | {metrics.get('cagr', 0):.2%} |",
        f"| Max DD         | {metrics.get('max_drawdown', 0):.2%} |",
        f"| Hit rate       | {metrics.get('hit_rate', 0):.2%} |",
        f"| N trades       | {metrics.get('n_trades', 0)} |",
        f"",
        f"## Robustesse aux coûts",
        f"| Test           | PF       |",
        f"|----------------|----------|",
        f"| PF base        | {metrics.get('pf', 0):.4f} |",
        f"| PF cost ×2     | {metrics.get('pf_cost_x2', 0):.4f} |",
        f"| PF cost ×3     | {metrics.get('pf_cost_x3', 0):.4f} |",
        f"",
        f"## Rendements annuels",
    ]

    annual = metrics.get("annual_returns", {})
    for year, ret in sorted(annual.items()):
        status = "✓" if ret > 0 else "✗"
        report_lines.append(f"  {status} {year}: {ret:.2%}")

    report_lines.extend([
        f"",
        f"## Verdict institutionnel",
        f"",
        f"**{metrics.get('verdict', 'N/A')}**",
        f"",
        f"- Worst year  : {metrics.get('worst_year', 0):.2%}",
        f"- Best year   : {metrics.get('best_year', 0):.2%}",
        f"- Worst month : {metrics.get('worst_month', 0):.2%}",
        f"",
    ])

    report = "\n".join(report_lines)
    print(report)

    # Sauvegarder le rapport
    report_path = backtest_dir / "report.md"
    report_path.write_text(report)
    logger.info(f"Rapport sauvegardé : {report_path}")


if __name__ == "__main__":
    main()
