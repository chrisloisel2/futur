"""MEXC_VOLUME_TRUST_V1 : fenetre declaree, portes de support, covariables, rangs, drapeau pre-declare, controle meme actif.
Jamais de bougie qui cloture apres t0 ; jamais de lecture post-t0 ; jamais un ban par evenement."""
import json
import random
import re
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.indices import mexc_volume_trust as M
from data_lake.indices.pre_binance_features import PostT0Leak

H = M.H; T0 = 1_700_000_000_000 + 30 * 60_000        # t0 a HH:30
PUB = T0 - 2 * H - 15 * 60_000                        # annonce 2 h 15 avant t0


def _c(open_ms, hi, lo, qv):
    c = (hi + lo) / 2.0
    return {"open_time_ms": open_ms, "open": c, "high": hi, "low": lo, "close": c, "volume": qv / c, "quote_volume": qv}


def _programme(n=80, seed=0):
    rnd = random.Random(seed)
    return [_c(T0 - n * H + i * H, 1 + 0.01 * rnd.random(), 1 - 0.01 * rnd.random(), 1000 + rnd.uniform(-30, 30)) for i in range(n)]


def _organic(n=80, seed=1):
    rnd = random.Random(seed); out = []
    for i in range(n):
        rg = abs(rnd.gauss(0, 0.03)); out.append(_c(T0 - n * H + i * H, 1 + rg, 1 - rg, max(1.0, rnd.lognormvariate(6, 0.6)) * (1 + 60 * rg)))   # volume couple a l'amplitude
    return out


def test_window_is_cut_at_the_announcement_and_never_after_t0():
    start, end = M.window_bounds(T0, PUB, None)
    assert end == (PUB // H) * H and start == T0 - 72 * H
    sel = M.select_hours(_programme(), T0, start, end)
    assert sel["n_post_announcement_dropped"] == 3 and all(r["open_time_ms"] + H <= end for r in sel["rows"])   # HH:00 de l'annonce, HH+1, et l'heure ouverte a t0-30min
    with pytest.raises(PostT0Leak):
        M.select_hours(_programme() + [_c(T0 - 30 * 60_000, 1.0, 1.0, 1.0)], T0, start, end)                    # chevauche t0 : refusee, pas filtree
    s2, _ = M.window_bounds(T0, PUB, T0 - 40 * H)
    assert s2 == T0 - 16 * H                                                                                    # 24 h de rodage apres la cotation MEXC


def test_support_gate_gives_not_computable_without_any_value_or_flag():
    r = M.compute_event(_programme(30), T0, PUB, None, "spot")
    assert r["status"] == "NOT_COMPUTABLE" and r["volume_floor"] is None and r["n_hours_used"] < M.MIN_HOURS
    assert M.compute_event([], T0, PUB, None, "spot")["status"] == "NO_TAPE"
    rk = M.rank_events({"a": r}); assert rk["a"]["wash_volume_suspect"] is None and rk["a"]["volume_anomaly_rank"] is None


def test_integrity_gate_catches_an_inconsistent_candle_and_infers_perp_multiplier():
    rows = _organic(); rows[10]["quote_volume"] *= 3.0                                                          # vwap implicite hors [low, high]
    r = M.compute_event(rows, T0, PUB, None, "spot"); assert r["status"] == "INTEGRITY_FAIL" and r["integrity"]["n_violations"] == 1
    perp = [{**c, "volume": c["volume"] / 100.0} for c in _organic()]                                           # contrats de 100 unites
    assert M.infer_multiplier(perp, "perp") == 100.0 and M.compute_event(perp, T0, PUB, None, "perp")["status"] == "MEASURED"


def test_programme_and_organic_tapes_separate_on_the_declared_covariates():
    p = M.compute_event(_programme(), T0, PUB, None, "spot"); o = M.compute_event(_organic(), T0, PUB, None, "spot")
    assert p["status"] == "MEASURED" and o["status"] == "MEASURED"
    assert p["volume_floor"] > 0.9 > 0.5 > o["volume_floor"] and p["volume_cv"] < 0.1 < 0.8 < o["volume_cv"]
    assert o["volume_range_coupling"] > 0.3 and (p["volume_range_coupling"] is None or abs(p["volume_range_coupling"]) < 0.3)
    assert o["anticipation_ratio"] is not None and abs(p["anticipation_ratio"] - 1.0) < 0.1


def test_ranks_flag_the_top_decile_only_with_enough_events_and_never_ban():
    res = {"org%d" % i: M.compute_event(_organic(seed=i), T0, PUB, None, "spot") for i in range(11)}
    res["prog"] = M.compute_event(_programme(), T0, PUB, None, "spot")
    rk = M.rank_events(res)
    flagged = [e for e, o in rk.items() if o["wash_volume_suspect"]]
    assert "prog" in flagged and len(flagged) == 2 and rk["prog"]["programme_like"] is True                        # 12 MEASURED -> ceil(1.2) = 2 drapeaux, regle declaree
    assert max(rk.values(), key=lambda o: o["volume_anomaly_rank"] or 0) is rk["prog"] and rk["prog"]["volume_anomaly_rank"] > 0.9
    small = M.rank_events({"a": res["prog"], "b": res["org0"]}); assert small["a"]["wash_volume_suspect"] is None      # < MIN_RANKED : aucun drapeau
    assert not any("ban" in k for k in rk["prog"])                                                                # aucun champ de ban par evenement


def test_same_asset_control_needs_common_hours_and_measures_the_multiple():
    mexc = [{**c, "quote_volume": c["quote_volume"] * 10, "volume": c["volume"] * 10} for c in _organic()]
    c = M.same_asset_control(mexc, _organic()); assert c["status"] == "MEASURED" and abs(c["log10_venue_volume_multiple"] - 1.0) < 1e-6 and c["coupling_gap"] == 0.0
    assert M.same_asset_control(mexc[:20], _organic())["status"] == "NOT_COMPUTABLE"


def test_venue_level_difference_reports_ci_and_label():
    d = M.venue_level_difference([3.0, 3.2, 3.4, 3.1], [2.5, 2.7, 2.6]); assert d["diff_log10"] > 0 and d["ci95_log10"][0] < d["diff_log10"] < d["ci95_log10"][1] and "not a per-event control" in d["label"]
    assert M.venue_level_difference([3.0], [2.0])["diff_log10"] is None


def test_policy_is_global_and_limits_are_declared():
    assert all(p["allowed_as"] in ("conditioning", "covariate", "banned_cross_venue") for p in M.FEATURE_POLICY)
    assert all(p["allowed_as"] != "conditioning" for p in M.FEATURE_POLICY if p["class"] != "price_only")
    assert any("UNKNOWN" in l for l in M.DECLARED_LIMITS) and M.TOP_FRACTION == 0.10 and M.MIN_HOURS == 36


def test_report_module_reads_nothing_post_t0():
    src = (ROOT / "data_lake" / "indices" / "mexc_volume_trust_report.py").read_text() + (ROOT / "data_lake" / "indices" / "mexc_volume_trust.py").read_text()
    for banned in ("RESULT", "vision_backfill", "mark_price", "return_after", "post_t0_return", "LOOK_LEDGER", "BUDGET_LEDGER"):
        assert banned not in src, banned
    assert re.search(r'"reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE\.json"', src)          # la seule lecture sous first_look : l'univers (t0, annonce)
    spec = ROOT / "reports" / "prereg" / "MEXC_VOLUME_TRUST_V1_SPEC.md"; assert spec.exists() and "décile" in spec.read_text()


def test_report_end_to_end_on_a_temporary_store(tmp_path, monkeypatch):
    from data_lake.indices import mexc_volume_trust_report as R
    from data_lake.collectors import venue_pre_binance_paths as VP
    evs = [{"event_id": "e%d" % i, "asset": "A%d" % i, "binance_symbol": "A%dUSDT" % i, "t0": "2023-11-14T22:43:20+00:00", "mexc_symbol": "A%dUSDT" % i, "mexc_market": "spot", "mexc_listed_ts": None, "lead_days": 9.0} for i in range(12)]
    monkeypatch.setattr(R, "mexc_first_events", lambda: evs); monkeypatch.setattr(R, "other_venue_first_events", lambda: [])
    monkeypatch.setattr(R, "_universe", lambda: {e["event_id"]: {"publication_ts_ms": 1_700_000_000_000 - 2 * H} for e in evs}); monkeypatch.setattr(R, "_causal_class", lambda: {"e11": "BAD_TIMESTAMP"})
    t0_ms = int(VP.parse_ts(evs[0]["t0"]).timestamp() * 1000)
    for i, e in enumerate(evs):
        rows = _programme() if i == 0 else _organic(seed=i); rows = [{**c, "open_time_ms": c["open_time_ms"] - T0 + t0_ms} for c in rows]
        p = tmp_path / "mexc" / "spot" / e["mexc_symbol"] / "h.json"; p.parent.mkdir(parents=True); p.write_text(json.dumps({"rows": rows}))
        VP.manifest_path("mexc", e["event_id"], tmp_path).parent.mkdir(parents=True, exist_ok=True)
        VP.manifest_path("mexc", e["event_id"], tmp_path).write_text(json.dumps({"status": "collected", "market": "spot", "files": {"60m": {"path": str(p)}}}))
    out = R.write_reports(out=tmp_path / "out", root=tmp_path)
    assert out["by_status"]["MEASURED"] == 11 and out["by_status"]["NOT_COMPUTABLE"] == 1 and out["suspect"] >= 1
    doc = json.loads((tmp_path / "out" / "MEXC_VOLUME_TRUST.json").read_text())
    assert doc["no_post_t0_data"] and doc["no_alpha_test"] and doc["no_verdict"] and doc["spec_sha256"] and next(r for r in doc["rows"] if r["event_id"] == "e11")["reason"].startswith("BAD_TIMESTAMP")
    assert (tmp_path / "out" / "FEATURE_POLICY.md").exists() and (tmp_path / "out" / "SAME_ASSET_CROSS_VENUE_CONTROL.md").exists() and (tmp_path / "out" / "MEXC_VOLUME_TRUST.csv").exists()
