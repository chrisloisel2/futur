#!/usr/bin/env bash
set -euo pipefail

mkdir -p data/raw data/clean data/cache artifacts/backtests artifacts/validation
export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"
echo "Environment bootstrapped"
