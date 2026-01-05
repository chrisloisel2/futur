#!/bin/bash
set -e

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

python3 scripts/paper_test_trained.py "$@"
