"""tests/integration/test_alpha20_carry_truth_shadow_data_manifest.py --
Phase 4D commit 7: the real-data provenance manifest.

The underlying market data (data/raw/, data/enriched/) is gitignored (too
large to commit -- see .gitignore) so it will NOT exist on a fresh clone.
The manifest itself (data/manifests/carry_shadow_data_manifest.json) IS
committed -- these tests validate its structure unconditionally, and
additionally verify its hashes against the real files whenever those
happen to be present locally (they are, in the environment this was
generated in), skipping that specific check otherwise rather than failing
on an expected, documented absence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data" / "manifests" / "carry_shadow_data_manifest.json"
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.exists(), (
        f"missing {MANIFEST_PATH} -- run scripts/generate_carry_shadow_data_manifest.py")
    return json.loads(MANIFEST_PATH.read_text())


def test_manifest_has_both_symbols(manifest):
    assert set(manifest["symbols"]) == set(SYMBOLS)


def test_manifest_records_causality_verification(manifest):
    cv = manifest["causality_verification"]
    assert "funding_merge" in cv and "no forward-fill" in cv["funding_merge"] \
        or "no backward-fill" in cv["funding_merge"]
    assert "carry_backtest_consumption" in cv


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_funding_entry_has_required_provenance_fields(manifest, symbol):
    entry = manifest["symbols"][symbol]["funding_raw"]
    required = {"source", "endpoint", "venue", "market", "symbol", "n_rows",
               "first_timestamp", "last_timestamp", "duplicate_timestamps",
               "n_gaps", "sha256", "file"}
    assert required <= entry.keys()
    assert entry["symbol"] == symbol
    assert entry["duplicate_timestamps"] == 0
    assert entry["n_gaps"] == 0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_enriched_entry_has_required_provenance_fields(manifest, symbol):
    entry = manifest["symbols"][symbol]["enriched"]
    required = {"source", "endpoint", "venue", "market", "symbol",
               "period_requested_start", "n_rows", "first_timestamp", "last_timestamp",
               "duplicate_timestamps", "n_gaps", "sha256", "file",
               "raw_klines_cross_check", "close_price_cross_check_vs_raw_klines"}
    assert required <= entry.keys()
    assert entry["symbol"] == symbol
    assert entry["duplicate_timestamps"] == 0
    assert entry["n_gaps"] == 0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_close_price_cross_check_agrees_within_a_reasonable_tolerance(manifest, symbol):
    """Two independently-fetched real sources for the same market (the
    canonical enrichment pipeline's own live fetch vs. the pre-existing
    local raw klines ingest) must agree almost exactly -- this is the
    manifest's own evidence that neither is fabricated."""
    cc = manifest["symbols"][symbol]["enriched"]["close_price_cross_check_vs_raw_klines"]
    assert cc["performed"] is True
    assert cc["n_overlapping_rows"] > 1000
    # mean diff should be tiny (sub-dollar on BTC/ETH prices); max diff can
    # be a bit larger on isolated bars (timing/exchange revision noise)
    # but never a different order of magnitude from the asset's own price
    assert cc["mean_abs_close_diff"] < 1.0
    assert cc["max_abs_close_diff"] < 100.0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_hashes_match_the_real_files_when_present_locally(manifest, symbol):
    for kind in ("funding_raw", "enriched"):
        entry = manifest["symbols"][symbol][kind]
        path = ROOT / entry["file"]
        if not path.exists():
            pytest.skip(f"{entry['file']} not present locally (gitignored, expected on a "
                       f"fresh clone) -- cannot verify its hash here")
        assert _sha256_file(path) == entry["sha256"], (
            f"{entry['file']} has changed since the manifest was generated -- "
            f"re-run scripts/generate_carry_shadow_data_manifest.py")
