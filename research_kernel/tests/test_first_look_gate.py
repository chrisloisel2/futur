"""The first-look gate must refuse to freeze while its criteria are not met, and a frozen
manifest must leave the hypothesis fields empty: only a sealed preregistration fills them."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import first_look_gate as G  # noqa: E402

CRITERIA = {"n_events_ge_300", "enriched_ratio_ge_95pct", "bbo_age_ge_0_everywhere",
            "stream_delay_measured", "no_post_event_contamination"}


def test_gate_criteria_are_the_protocol_ones_and_status_never_joins_prices():
    assert set(G.forced_flow_status()["criteria"]) == CRITERIA
    assert G.GATE["forced_flow"] == {"min_events": 300, "min_enriched_ratio": 0.95}
    assert G.event_tape_status()["no_price_join"] is True


def test_freeze_refuses_when_gate_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "OUT", tmp_path)
    monkeypatch.setattr(G, "forced_flow_status", lambda: {"open": False, "criteria": {"n_events_ge_300": False}})
    with pytest.raises(SystemExit, match="REFUS"):
        G.freeze("forced_flow", "x")
    assert not (tmp_path / "x").exists()


def test_freeze_leaves_hypothesis_fields_empty(monkeypatch, tmp_path):
    tape = tmp_path / "tape.jsonl"
    tape.write_text('{"event_id":"e1","raw_url":"https://x","publication_ts_exchange":"2026-01-01T00:00:00+00:00"}\n')
    monkeypatch.setattr(G, "OUT", tmp_path / "out"); monkeypatch.setattr(G, "EVENT_TAPE", tape)
    man = G.freeze("event_tape", "t")
    empty = man["TO_BE_FILLED_BY_SEALED_PREREGISTRATION_ONLY"]
    assert all(v is None for v in empty.values()) and {"exact_hypothesis", "exact_horizons", "exact_cost_model"} <= set(empty)
    assert man["row_count"] == 1 and len(man["sha256"]) == 64 and man["min_event_ts"] == man["max_event_ts"]


def test_protocol_and_units_are_versioned():
    assert (ROOT / "reports" / "first_look" / "FIRST_LOOK_PROTOCOL.md").exists()
    for u in ("futur-forced-flow-tape.service", "futur-event-tape.service", "futur-event-tape.timer"):
        assert (ROOT / "deploy" / "systemd" / u).exists()
