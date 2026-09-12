"""MEXC_TO_BINANCE_V1 harness: pure parts only (funnel, statistic, verdict order, placebo draw, ledger refusals). No price is read."""
import csv
import io
import json
import sys
import zipfile
import importlib.util as ilu
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
_spec = ilu.spec_from_file_location("m2b_first_look", ROOT / "mechanisms" / "mexc_to_binance_migration_v1" / "first_look.py")
H = ilu.module_from_spec(_spec); _spec.loader.exec_module(H)


def test_pins_block_names_every_input_and_refuses_an_unpinned_harness():
    p = H.pins()
    for k in ("universe", "conditioning", "causal_matrix", "capacity", "wash", "fee_decision", "population", "harness", "harness_seq8", "harness_seq7", "look_ledger_tool", "multiplicity", "pre_binance_features"):
        assert k in p and p[k]["path"]
    if p["harness"]["sha256"] == "TO_BE_PINNED_BEFORE_SEAL" or p["harness"]["sha256"] != H.sha(ROOT / p["harness"]["path"]):
        with pytest.raises(SystemExit):
            H.check_pins(require_harness=True)


def test_population_funnel_is_reproducible_from_the_pinned_files():
    pop = H.build_population()
    assert [n for _, n in pop["funnel"]] == [113, 107, 84, 84] and pop["n"] == 84 and pop["n_high"] == 30 and pop["n_low"] == 54
    assert pop["high_threshold"] == 0.20 and all((e["group"] == "HIGH") == (e["pre_announcement_return_24h"] >= 0.20) for e in pop["events"])
    assert all(e["hours_in_window"] >= H.MIN_CLOSES_24H and e["cost_rt_bps"] is not None for e in pop["events"]) and pop["n_cost_imputed"] == 5
    assert pop["no_return_computed"] and "raw" not in json.dumps(pop["events"][0])
    frozen = json.loads((ROOT / "mechanisms" / "mexc_to_binance_migration_v1" / "population.json").read_text())
    assert frozen["high_sha256"] == pop["high_sha256"] and frozen["low_sha256"] == pop["low_sha256"]


def test_event_cost_uses_measured_slippage_or_the_declared_imputation():
    ev = {"windows": [{"window_min": 15, "effective_spread_bps": 10.0, "sell_slippage_500_usd_bps": 1.5, "buy_slippage_500_usd_bps": 2.0}]}
    assert H.event_cost(ev) == (23.5, False)
    gap = {"windows": [{"window_min": 15, "effective_spread_bps": 10.0, "sell_slippage_500_usd_bps": None, "buy_slippage_500_usd_bps": None}]}
    assert H.event_cost(gap) == (None, None) and H.event_cost(gap, 3.6) == (23.6, True)
    assert H.event_cost({"windows": [{"window_min": 15, "effective_spread_bps": None}]}, 3.6) == (None, None)


def test_permutation_test_is_seeded_one_sided_and_level_invariant():
    x = [i / 10 for i in range(40)]; y = [500.0 + 30 * i for i in range(40)]                        # ordre parfait, niveau eleve
    r = H.permutation_test(x, y, n_perm=500); assert r["rho"] == 1.0 and r["p_one_sided"] <= 1 / 500 * 1.01 + 1e-9
    r2 = H.permutation_test(x, [v - 500.0 for v in y], n_perm=500); assert r2["p_one_sided"] == r["p_one_sided"]         # invariant au niveau
    import random
    rng = random.Random(3); z = [rng.gauss(0, 1) for _ in range(40)]
    a = H.permutation_test(x, z, n_perm=500); b = H.permutation_test(x, z, n_perm=500); assert a == b and 0 < a["p_one_sided"] <= 1
    assert H.permutation_test(x, [-v for v in y], n_perm=200)["p_one_sided"] > 0.99                                 # anti-ordre : jamais significatif unilateralement


def _res(n=84, rho=0.4, p=0.001, high_n=30, gross_med=80.0, gross_mean=150.0, net_mean=120.0, n_eff=20.0, top1=0.1, wo_largest=50.0, mean_cost=25.0, t=2.0):
    st = lambda mean: {"n": high_n, "mean_bps": mean, "median_bps": gross_med, "t": t, "n_eff": n_eff, "top1_share": top1, "mean_without_largest_bps": wo_largest}
    return {"n_measured": n, "primary": {"rho": rho, "p_one_sided": p}, "economic_gate": {"gross": st(gross_mean), "net": st(net_mean), "mean_cost_rt_bps": mean_cost}}


def test_verdict_order_and_the_three_words():
    ok = {"fake_symbol": {"veto_eligible": True, "gross": {"n": 30, "t": 0.2, "mean_bps": 5.0}}}
    assert H.verdict_for(_res(), ok)[0] == "CANDIDATE_ALPHA_REQUIRES_FORWARD"
    assert H.verdict_for(_res(n=20), ok)[0] == "INDECIDABLE"
    assert H.verdict_for(_res(rho=-0.1), ok)[0] == "REJECTED"
    assert H.verdict_for(_res(p=0.02), ok)[0] == "INDECIDABLE"
    assert H.verdict_for(_res(gross_med=10.0), ok)[0] == "REJECTED"
    assert H.verdict_for(_res(net_mean=60.0), ok)[0] == "REJECTED"                                                  # 60 < 3 x 25
    assert H.verdict_for(_res(n_eff=10.0), ok)[0] == "INDECIDABLE" and H.verdict_for(_res(wo_largest=-1.0), ok)[0] == "INDECIDABLE"
    veto = {"fake_symbol": {"veto_eligible": True, "gross": {"n": 30, "t": 1.5, "mean_bps": 100.0}}}
    v, why = H.verdict_for(_res(), veto); assert v == "INDECIDABLE" and "placebo fake_symbol" in why[0]
    weak = {"fake_symbol": {"veto_eligible": True, "gross": {"n": 30, "t": 0.5, "mean_bps": 100.0}}}                   # positif mais dans le bruit : pas de veto
    assert H.verdict_for(_res(), weak)[0] == "CANDIDATE_ALPHA_REQUIRES_FORWARD"
    assert H.verdict_for(_res(), {"fake_symbol": {"veto_eligible": True, "n": 0, "status": "NOT_COMPUTABLE: x"}})[0] == "INDECIDABLE"
    assert set(H.VERDICTS) == {"REJECTED", "INDECIDABLE", "CANDIDATE_ALPHA_REQUIRES_FORWARD"}


def test_fake_symbol_draw_is_deterministic_and_old_enough():
    t0 = 1_700_000_000_000; D = 86_400_000
    info = {"symbols": [{"symbol": "A%02dUSDT" % i, "contractType": "PERPETUAL", "quoteAsset": "USDT", "onboardDate": t0 - (i * 10 + 5) * D} for i in range(30)]
             + [{"symbol": "NEWUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "onboardDate": t0 - 10 * D}, {"symbol": "XUSD", "contractType": "PERPETUAL", "quoteAsset": "USD", "onboardDate": 1}]}
    pop = {"events": [{"event_id": "e1", "symbol": "A15USDT", "group": "HIGH", "tradable_start_ms": t0}, {"event_id": "e2", "symbol": "ZUSDT", "group": "LOW", "tradable_start_ms": t0}]}
    d = H.fake_symbols(pop, info); d2 = H.fake_symbols(pop, info)
    assert d == d2 and set(d) == {"e1"} and d["e1"] not in ("A15USDT", "NEWUSDT", "XUSD")
    od = next(s["onboardDate"] for s in info["symbols"] if s["symbol"] == d["e1"]); assert od <= t0 - 90 * D


def test_funding_for_a_short_reads_only_settlements_inside_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "FUNDING_ROOT", tmp_path)
    sym = "XUSDT"; (tmp_path / sym).mkdir()
    rows = "calc_time,funding_interval_hours,last_funding_rate\n1706745600000,8,0.0010\n1706774400000,8,-0.0005\n1706803200000,8,0.0020\n"
    with zipfile.ZipFile(tmp_path / sym / "XUSDT-fundingRate-2024-02.zip", "w") as z:
        z.writestr("XUSDT-fundingRate-2024-02.csv", rows)
    f = H.funding_bps_for_short(sym, 1706745600000, 1706803200000)                                                   # (entree, sortie] : 2e et 3e reglements
    assert abs(f - ((-0.0005 + 0.0020) * 1e4)) < 1e-9
    assert H.funding_bps_for_short("NOPEUSDT", 1706745600000, 1706803200000) is None


def test_ledger_preconditions_refuse_on_a_chain_without_seq9(monkeypatch):
    entries = [json.loads(l) for l in (ROOT / "reports" / "loop" / "LOOK_LEDGER.jsonl").read_text().splitlines() if l.strip()]
    if not any(e.get("hash") == H.SEQ9_HASH for e in entries):
        with pytest.raises(SystemExit) as ei:
            H.check_ledgers(("2023-05-05T00:00:00+00:00", "2026-09-06T00:00:00+00:00"), "historical")
        assert "seq 9" in str(ei.value)


def test_no_price_is_read_before_the_ledger_entry():
    src = (ROOT / "mechanisms" / "mexc_to_binance_migration_v1" / "first_look.py").read_text()
    run_body = src[src.index("def run("):src.index("def main(")]
    assert run_body.index("look_ledger.record(") < run_body.index("debit_budget(") < run_body.index("VisionStore4()") < run_body.index("evaluate(")
    assert "check_ledgers(" in run_body and run_body.index("check_ledgers(") < run_body.index("look_ledger.record(")
