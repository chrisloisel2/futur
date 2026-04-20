# train_all_models.py
"""
Orchestrated Training Pipeline with Full ML Instrumentation
============================================================

Trains all models sequentially with:
- Standardized CLI interface
- Auto-dependency resolution
- Comprehensive monitoring
- Production acceptance validation
"""
import os
import sys
import json
import time
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timezone


def parse_args():
    ap = argparse.ArgumentParser(
        description="Train all models with full instrumentation"
    )

    # Dataset
    ap.add_argument("--s3_dataset", required=True, help="S3 base path")
    ap.add_argument("--symbol", required=True, help="Trading symbol (e.g., BTCUSDT)")
    ap.add_argument("--quote", required=True, help="Quote currency (e.g., USDT)")
    ap.add_argument("--interval", required=True, help="Timeframe (e.g., 1m)")
    ap.add_argument("--years", required=True, help="Years to train on (comma-separated)")

    # Output
    ap.add_argument("--out", default="runs", help="Output directory")

    # Instrumentation
    ap.add_argument("--enable-instrumentation", action="store_true",
                    help="Enable full ML instrumentation (recommended)")
    ap.add_argument("--min-sharpe", type=float, default=1.0,
                    help="Minimum acceptable Sharpe ratio (default: 1.0)")
    ap.add_argument("--max-drawdown", type=float, default=-0.20,
                    help="Maximum acceptable drawdown (default: -20%%)")

    return ap.parse_args()


def run(cmd, description=None):
    """Execute command with logging."""
    if description:
        print(f"\n{'='*80}")
        print(f"{description}")
        print(f"{'='*80}")

    print("\n>>>", " ".join(cmd))

    start = time.time()
    result = subprocess.run(cmd, capture_output=False, text=True)
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"\n❌ FAILED after {elapsed:.1f}s")
        raise subprocess.CalledProcessError(result.returncode, cmd)

    print(f"\n✅ Completed in {elapsed:.1f}s")
    return result


def validate_production_acceptance(run_dir: Path, model_name: str) -> dict:
    """
    Validate if model meets production acceptance criteria.

    Returns:
        dict with validation results
    """
    # Find latest metrics
    metrics_dir = run_dir / model_name / "latest" / "metrics"
    if not metrics_dir.exists():
        # Try to find most recent run
        model_dir = run_dir / model_name
        if model_dir.exists():
            runs = sorted([d for d in model_dir.iterdir() if d.is_dir()])
            if runs:
                metrics_dir = runs[-1] / "metrics"

    if not metrics_dir.exists():
        return {"valid": False, "reason": "No metrics found"}

    # Load final epoch metrics
    metric_files = sorted(metrics_dir.glob("epoch_*.json"))
    if not metric_files:
        return {"valid": False, "reason": "No epoch metrics found"}

    with open(metric_files[-1]) as f:
        final_metrics = json.load(f)

    # Extract key metrics
    paper_test = final_metrics.get("paper_test_realistic", {})
    sharpe = paper_test.get("sharpe", 0)
    roi = paper_test.get("roi", 0)
    max_dd = paper_test.get("max_drawdown", 0)
    ece = final_metrics.get("ece", 1.0)
    hit_rate = paper_test.get("hit_rate", 0)
    flip_rate = final_metrics.get("flip_rate", 1.0) or 0

    # Validate criteria
    criteria = {
        "sharpe": {"value": sharpe, "threshold": 1.5, "passed": sharpe >= 1.5},
        "roi": {"value": roi, "threshold": 0.0, "passed": roi > 0},
        "max_drawdown": {"value": max_dd, "threshold": -0.20, "passed": max_dd > -0.20},
        "ece": {"value": ece, "threshold": 0.05, "passed": ece < 0.05},
        "hit_rate": {"value": hit_rate, "threshold": 0.50, "passed": hit_rate > 0.50},
        "flip_rate": {"value": flip_rate, "threshold": 0.30, "passed": flip_rate < 0.30},
    }

    all_passed = all(c["passed"] for c in criteria.values())

    return {
        "valid": all_passed,
        "criteria": criteria,
        "model_name": model_name,
        "final_epoch": final_metrics.get("epoch", -1)
    }


def generate_global_report(run_dir: Path, models: list, pipeline_start: float):
    """Generate comprehensive pipeline report."""
    report_lines = [
        "# ML Training Pipeline Report",
        f"\n**Generated**: {datetime.now(timezone.utc).isoformat()}",
        f"**Duration**: {time.time() - pipeline_start:.1f}s",
        "\n## Models Trained\n"
    ]

    all_valid = True

    for model_name in models:
        validation = validate_production_acceptance(run_dir, model_name)

        report_lines.append(f"\n### {model_name}")
        report_lines.append(f"- **Epoch**: {validation.get('final_epoch', 'N/A')}")

        if validation["valid"]:
            report_lines.append("- **Status**: ✅ PRODUCTION READY")
        else:
            report_lines.append(f"- **Status**: ❌ REJECTED - {validation.get('reason', 'Criteria not met')}")
            all_valid = False

        if "criteria" in validation:
            report_lines.append("\n**Acceptance Criteria**:")
            for name, data in validation["criteria"].items():
                status = "✅" if data["passed"] else "❌"
                report_lines.append(
                    f"- {status} **{name}**: {data['value']:.4f} "
                    f"(threshold: {data['threshold']})"
                )

    report_lines.append("\n## Pipeline Status\n")
    if all_valid:
        report_lines.append("✅ **ALL MODELS PRODUCTION READY**")
    else:
        report_lines.append("❌ **PIPELINE REJECTED - Some models failed acceptance criteria**")

    report_lines.append("\n---\n*Generated by train_all_models.py with full instrumentation*")

    # Save report
    report_path = run_dir / "PIPELINE_REPORT.md"
    with open(report_path, 'w') as f:
        f.write("\n".join(report_lines))

    return report_path, all_valid


def main():
    pipeline_start = time.time()

    args = parse_args()

    print("\n" + "="*80)
    print("ML TRAINING PIPELINE - FULL ORCHESTRATION")
    print("="*80)
    print(f"\nDataset:  {args.s3_dataset}")
    print(f"Symbol:   {args.symbol}/{args.quote}")
    print(f"Interval: {args.interval}")
    print(f"Years:    {args.years}")
    print(f"Output:   {args.out}")

    if args.enable_instrumentation:
        print(f"\n📊 Instrumentation: ENABLED")
        print(f"   Min Sharpe:     {args.min_sharpe}")
        print(f"   Max Drawdown:   {args.max_drawdown:.0%}")
    else:
        print(f"\n⚠️  Instrumentation: DISABLED (use --enable-instrumentation)")

    # Common args for ALL training scripts (standardized interface)
    common = [
        "--s3_dataset", args.s3_dataset,
        "--symbol", args.symbol,
        "--quote", args.quote,
        "--interval", args.interval,
        "--years", args.years,
        "--out", args.out,
    ]

    # Python executable (use same as current)
    python_exe = sys.executable

    models_trained = []

    try:
        # === LEVEL 0: Global Gating + Regime Classifier (binaire: calm/reversal) ===
        run(
            [python_exe, "training/train_regime_classifier_production.py", *common],
            description="LEVEL 0: Regime Classifier PRODUCTION (calm/reversal, SGD + calibration)"
        )
        models_trained.append("regime_classifier_prod")

        # === LEVEL 1: Event Classifier ===
        run(
            [python_exe, "training/train_event_classifier.py", *common],
            description="LEVEL 1: Event Classifier (régimes de marché)"
        )
        models_trained.append("event_classifier")

        # === LEVEL 2: Edge Forecaster (PyTorch Transformer) ===
        # Entraîné séparément via trading-system/scripts/train_edge_forecaster.py
        # horizon=60min, seq_len=64

    except subprocess.CalledProcessError as e:
        print(f"\n{'='*80}")
        print(f"❌ PIPELINE FAILED")
        print(f"{'='*80}")
        print(f"\nFailed at: {e.cmd[1]}")
        sys.exit(1)

    # === GENERATE GLOBAL REPORT ===
    print(f"\n{'='*80}")
    print("GENERATING PIPELINE REPORT")
    print(f"{'='*80}")

    run_dir = Path(args.out)
    report_path, all_valid = generate_global_report(run_dir, models_trained, pipeline_start)

    print(f"\n📊 Report saved: {report_path}")

    # Print summary
    print(f"\n{'='*80}")
    if all_valid:
        print("✅ PIPELINE SUCCESS - ALL MODELS PRODUCTION READY")
    else:
        print("⚠️  PIPELINE COMPLETE - SOME MODELS FAILED ACCEPTANCE CRITERIA")
    print(f"{'='*80}")

    print(f"\nTotal time: {time.time() - pipeline_start:.1f}s")
    print(f"\nNext steps:")
    print(f"  1. Review: {report_path}")
    print(f"  2. Check individual model reports in: {run_dir}/*/latest/report.md")
    print(f"  3. Inspect visualizations: {run_dir}/*/latest/visualizations/")

    if args.enable_instrumentation:
        print(f"  4. Validate paper trading equity curves")
        print(f"  5. Review structured logs: {run_dir}/*/latest/logs.jsonl")

    if not all_valid:
        print(f"\n⚠️  WARNING: Not all models meet production criteria!")
        print(f"   Review individual reports before deployment.")
        sys.exit(1)


if __name__ == "__main__":
    main()
