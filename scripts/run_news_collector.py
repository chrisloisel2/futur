#!/usr/bin/env python3
"""scripts/run_news_collector.py — un poll du collecteur news (timer systemd)."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.institutional.data.news_collector.collector import collect_once

if __name__ == "__main__":
    r = collect_once()
    print(f"news: fetched={r['fetched']} new={r['new_written']} tagged={r['tagged']} "
          f"| {r['per_source']}")
