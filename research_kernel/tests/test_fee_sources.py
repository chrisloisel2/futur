"""P1.1 — a fee schedule used in a FINAL verdict must come from the venue itself.
Third-party numbers are allowed only as an explicitly labelled fallback, never as
the basis of a final cost verdict. The VIP0 verdict (the account we actually have)
must rest on official sources for every venue."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data_lake" / "manifests" / "published_fee_schedules_2026-09-10.json"
OFFICIAL_HOSTS = ("binance.com", "okx.com", "bybit.com", "hyperliquid.gitbook.io", "hyperliquid.xyz")


def _venues():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["venues"]


def test_manifest_exists_and_separates_the_three_classes():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {"official", "account_actual", "third_party_fallback"} <= set(m["source_classes"])
    for v, d in m["venues"].items():
        assert "published_schedule" in d and "account_actual" in d, v
        assert d["account_actual"]["status"].startswith("not_fetched"), "account fees are never copied into the published manifest"


def test_vip0_verdict_rests_on_official_sources_only():
    for v, d in _venues().items():
        e = d["published_schedule"]["vip0"]
        assert e["source_class"] == "official", f"{v} vip0 is not official"
        assert all(any(h in u for h in OFFICIAL_HOSTS) for u in e["sources"]), f"{v} vip0 cites a non-official URL"


def test_no_third_party_source_is_marked_final():
    for v, d in _venues().items():
        for name, e in d["published_schedule"].items():
            if not isinstance(e, dict) or "source_class" not in e or "final" not in e:
                continue
            if e["final"]:
                assert e["source_class"] == "official", f"{v}/{name} is final but {e['source_class']}"
                assert all(any(h in u for h in OFFICIAL_HOSTS) for u in e["sources"]), f"{v}/{name} final with non-official URL"
            if e["source_class"] == "third_party_fallback":
                assert e["final"] is False and e.get("official_confirmation"), f"{v}/{name} fallback must be non-final and explain"


def test_vip0_values_unchanged_by_the_audit():
    v = _venues()
    assert (v["binance"]["published_schedule"]["vip0"]["maker"], v["binance"]["published_schedule"]["vip0"]["taker"]) == (2.0, 5.0)
    assert (v["okx"]["published_schedule"]["vip0"]["maker"], v["okx"]["published_schedule"]["vip0"]["taker"]) == (2.0, 5.0)
    assert (v["bybit"]["published_schedule"]["vip0"]["maker"], v["bybit"]["published_schedule"]["vip0"]["taker"]) == (2.0, 5.5)
    assert (v["hyperliquid"]["published_schedule"]["vip0"]["maker"], v["hyperliquid"]["published_schedule"]["vip0"]["taker"]) == (1.5, 4.5)


def test_metrics_reference_the_manifest_and_keep_fallback_out_of_the_final_decision():
    mp = ROOT / "mechanisms" / "p1_payer_discovery_v1" / "results" / "metrics.json"
    if not mp.exists():
        return
    m = json.loads(mp.read_text(encoding="utf-8"))
    assert m["fee_manifest"]["path"].endswith("published_fee_schedules_2026-09-10.json")
    cls = m["fee_manifest"]["source_class"]
    for k in m["decision"]["microstructure_reopen_keys_best_final"]:
        assert cls[k.split("/")[0]]["best_final"], f"{k} in the final decision without an official best tier"
