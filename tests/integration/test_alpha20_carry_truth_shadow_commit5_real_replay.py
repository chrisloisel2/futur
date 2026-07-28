"""tests/integration/test_alpha20_carry_truth_shadow_commit5_real_replay.py
-- Phase 4C commit 5: replay on real CarryBasisAdapter data.

DETERMINATION: BLOCKED.

carry_basis_v12 (the registered runner CarryBasisAdapter shadows) is
configured for carry_assets = [BTCUSDT, ETHUSDT] on venue binance_usdm.
CarryBasisAdapter.decide() sources ALL of its price/funding data through
src.institutional.engines.legacy_bridge.load_enriched(), which reads
data/enriched/{asset}_1h_enriched.parquet. That directory does not exist
in this environment (confirmed below, and independently by grepping the
whole repo for any other enriched-data location -- there is none; only
raw, unenriched klines exist under data/raw/binance_um_klines/, which
load_enriched() does not read and this commit does not attempt to
derive features from, since doing so would mean fabricating the
conditions for a PASS rather than replaying real, already-existing data,
exactly what commit 5 point 2 forbids).

Per commit 5 point 5 ("si les données nécessaires n'existent pas, rendre
BLOCKED avec la couverture exacte manquante"), this file both DECLARES
and VERIFIES that determination -- not a skip, not a prose claim in a
report: the tests below fail loudly if the data situation ever changes
without this file being updated, and independently confirm (via the
REAL CarryBasisAdapter.decide() call path, not a mock) that no
MultiLegResult can be produced.

Exact missing coverage (of the 6 points commit 5 requires at minimum) --
ALL SIX are blocked on the same root cause, price/funding data:
  1. opening both legs (spot + perp) of a carry position   -- BLOCKED
  2. distinct spot and perp marks                          -- BLOCKED
  3. at least one funding accrual                          -- BLOCKED
  4. at least one fee                                      -- BLOCKED
  5. a reduction or close of an open leg                   -- BLOCKED
  6. a terminal close (position fully flat)                -- BLOCKED

Recorded per commit 5 point 3, for the record even though the replay
itself is blocked:
  - runner_id: carry_basis_v12
  - venue: binance_usdm
  - config_hash: see test_commit5_records_the_real_runner_configuration
  - git_commit (of the runner's own registry entry): see same test
  - ProductSpecs that WOULD be used (from truth_shadow.mapping.
    product_spec_for_leg), for BTCUSDT/ETHUSDT, SPOT and LINEAR_PERP: see
    test_commit5_records_the_product_specs_that_would_be_used
  - data hash: N/A -- there is no data file to hash (see above); this is
    itself part of the recorded determination, not an omission.

If this ever needs to be un-blocked: obtain data/enriched/BTCUSDT_1h_
enriched.parquet and data/enriched/ETHUSDT_1h_enriched.parquet (matching
legacy_bridge.load_enriched()'s expected schema), then replace this
file's tests with a real end-to-end run: CarryBasisShadowRunner.run_cycle()
against real snapshots spanning the coverage list above, piped through
DifferentialComparator, asserting zero SHADOW_MAPPING_ERROR and zero
UNEXPLAINED_DIVERGENCE in the resulting JSONL log. No profitability
figure of any kind belongs in that log or this file, then or now.
"""
from __future__ import annotations

from pathlib import Path

from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_registry import get_spec
from src.alpha20.tournament.truth_shadow.mapping import product_spec_for_leg
from src.alpha20.tournament.truth_shadow.shadow_runner import _capture_multileg_result

ROOT = Path(__file__).resolve().parents[2]
ENRICHED_DIR = ROOT / "data" / "enriched"
REQUIRED_ASSETS = ("BTCUSDT", "ETHUSDT")


def _missing_enriched_files() -> list[str]:
    return [f"data/enriched/{a}_1h_enriched.parquet" for a in REQUIRED_ASSETS
           if not (ENRICHED_DIR / f"{a}_1h_enriched.parquet").exists()]


def test_commit5_no_other_enriched_data_location_exists_anywhere_in_the_repo():
    """Exhaustive, not just the one expected path -- confirms there is no
    OTHER copy of enriched BTCUSDT/ETHUSDT data anywhere under data/ that
    a narrower check might have missed."""
    hits = list((ROOT / "data").rglob("*BTCUSDT*enriched*")) + \
        list((ROOT / "data").rglob("*ETHUSDT*enriched*")) + \
        list((ROOT / "data").rglob("*_1h_enriched.parquet"))
    assert hits == [], f"expected zero enriched-data files anywhere, found: {hits}"


def test_commit5_data_availability_is_blocked_on_exactly_these_files():
    missing = _missing_enriched_files()
    assert missing == [
        "data/enriched/BTCUSDT_1h_enriched.parquet",
        "data/enriched/ETHUSDT_1h_enriched.parquet",
    ], (
        f"data situation changed (missing={missing}) -- if this now passes with "
        f"an empty list, commit 5 should be re-run for real instead of BLOCKED")


def test_commit5_missing_data_confirmed_via_the_real_capture_path_not_a_guess():
    """Not just a file-existence check -- confirms the REAL runner path
    (CarryBasisAdapter.decide(), via the shadow's own capture function,
    with the REAL registered spec) genuinely cannot produce a
    MultiLegResult in this environment."""
    spec = get_spec("carry_basis_v12")
    snapshot = MarketSnapshot(market_event_id="commit5-probe", cutoff="2026-01-01T00:00:00Z",
                              decision_ts="2026-01-01T00:00:00Z",
                              received_ts="2026-01-01T00:00:00Z")
    events, new_state, result = _capture_multileg_result(spec, snapshot, state={})
    assert isinstance(events, list) and isinstance(new_state, dict)
    assert result is None, (
        "expected no MultiLegResult (no price data available) -- if this now "
        "returns a result, real data has become available and commit 5 should "
        "be re-run for real, not left BLOCKED")


def test_commit5_records_the_real_runner_configuration():
    """Point 3's config/commit record -- kept as an assertion (not a
    print) so it stays accurate: if the registry entry changes, this test
    changes with it rather than silently going stale in a report."""
    spec = get_spec("carry_basis_v12")
    assert spec.venue == "binance_usdm"
    assert spec.config.get("carry_assets") == ["BTCUSDT", "ETHUSDT"]
    assert spec.config_hash == "9e025f4590c1dd39aec94210"
    assert spec.git_commit == "2fe693b"


def test_commit5_records_the_product_specs_that_would_be_used():
    """Point 3's ProductSpec record. These are exactly what
    truth_shadow.mapping would construct for a real replay -- recorded
    now, verified against the same function a real run would call, so
    there is no drift between "what we said we'd use" and "what the code
    actually builds"."""
    btc_spot = product_spec_for_leg("BTCUSDT", "CARRY_LONG_SPOT", "binance_usdm")
    btc_perp = product_spec_for_leg("BTCUSDT", "CARRY_SHORT_PERP", "binance_usdm")
    eth_spot = product_spec_for_leg("ETHUSDT", "CARRY_LONG_SPOT", "binance_usdm")
    eth_perp = product_spec_for_leg("ETHUSDT", "CARRY_SHORT_PERP", "binance_usdm")
    for spec, base in ((btc_spot, "BTC"), (btc_perp, "BTC"), (eth_spot, "ETH"), (eth_perp, "ETH")):
        assert spec.venue == "binance_usdm"
        assert spec.base_ccy == base
        assert spec.quote_ccy == "USD"
    assert btc_spot.type.value == "SPOT"
    assert btc_perp.type.value == "LINEAR_PERP"
