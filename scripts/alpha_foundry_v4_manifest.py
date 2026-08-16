#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v4.manifest import foundry_manifest


if __name__ == "__main__":
    print(json.dumps(foundry_manifest(), indent=2, sort_keys=True))
