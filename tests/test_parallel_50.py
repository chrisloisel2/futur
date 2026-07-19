"""tests/test_parallel_50.py — sélection univers 50 : ranker caps, buckets, qualité, isolation officielle."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.risk.correlation_buckets import bucket_of, is_meme, CORRELATION_BUCKETS
from src.institutional.portfolio.opportunity_ranker import (
    rank_opportunities, selected, RankerLimits,
)
from src.institutional.universe.asset_quality_filter import AssetQualityStatus as Q

ROOT = Path(__file__).parents[1]


def _cands(*syms):
    return [{"symbol": s, "engine": "X", "score": 1.0 - i * 0.01} for i, s in enumerate(syms)]


def _all_pass(syms):
    return {s: Q.PASS for s in syms}


def test_max_total_positions():
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT"]
    r = selected(rank_opportunities(_cands(*syms), _all_pass(syms), RankerLimits()))
    assert len(r) <= 7


def test_max_alt_positions():
    # 6 alts proposés, max 5 alts
    syms = ["LINKUSDT", "AVAXUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "FILUSDT"]
    r = selected(rank_opportunities(_cands(*syms), _all_pass(syms), RankerLimits()))
    assert len([x for x in r if x.symbol not in ("BTCUSDT", "ETHUSDT")]) <= 5


def test_max_bucket_positions():
    # 4 infra (même bucket) → max 2
    syms = ["AVAXUSDT", "LINKUSDT", "NEARUSDT", "ATOMUSDT"]
    r = selected(rank_opportunities(_cands(*syms), _all_pass(syms), RankerLimits()))
    assert sum(1 for x in r if x.bucket == "infra") <= 2


def test_max_meme_positions():
    syms = ["DOGEUSDT", "PEPEUSDT", "WIFUSDT"]
    r = selected(rank_opportunities(_cands(*syms), _all_pass(syms), RankerLimits()))
    assert sum(1 for x in r if is_meme(x.symbol)) <= 1


def test_blocked_asset_cannot_trade():
    syms = ["BTCUSDT", "NEWCOINUSDT"]
    q = {"BTCUSDT": Q.PASS, "NEWCOINUSDT": Q.BLOCK}
    r = rank_opportunities(_cands(*syms), q)
    blocked = [x for x in r if x.symbol == "NEWCOINUSDT"][0]
    assert not blocked.allowed and blocked.rejection_reason == "ASSET_BLOCKED_QUALITY"


def test_buckets_no_overlap_except_intended():
    # WIFUSDT volontairement dans sol_beta ET memes (les 2 mappings existent) — bucket_of déterministe
    assert bucket_of("BTCUSDT") == "majors"
    assert bucket_of("OPUSDT") == "eth_l2"
    assert bucket_of("UNKNOWNUSDT") == "other"


def test_parallel_config_isolation():
    cfg = yaml.safe_load((ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml").read_text())
    assert cfg["strategy"]["real_capital"] == 0
    assert cfg["strategy"]["affect_official_baseline"] is False
    assert cfg["execution"]["send_real_orders"] is False
    assert cfg["carry"]["active_symbols"] == ["BTCUSDT", "ETHUSDT"]      # carry BTC/ETH only
    assert cfg["carry"]["forbidden_alt_carry"] is True
    assert cfg["carry"]["carry_gate_v2_execution"] is False             # déclassé
    assert cfg["liquidation_engine"]["enabled"] is False
    assert cfg["ledgers_dir"] != "artifacts/paper_live/v1_1_official"   # ledgers séparés


def test_official_baseline_unchanged():
    base = yaml.safe_load((ROOT / "configs" / "portfolio_v1_1_baseline.yaml").read_text())
    assert base["config"]["carry_fraction"] == 0.50
    assert base["config"]["carry_gate_v2"] is False
