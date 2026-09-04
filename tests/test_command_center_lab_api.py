"""
tests/test_command_center_lab_api.py — API /api/lab (Live Alpha Lab, lecture
seule) + gel du portefeuille paper legacy (/api/portfolio/live, /init).

Tout est isolé dans tmp_path : les constantes de chemin de lab_api sont
monkeypatchées, l'horloge aussi, le scoreboard réel n'est jamais appelé
(fallback parquet sur des fichiers de test). Aucun accès à reports/ réel.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend_pipeline import command_center, lab_api  # noqa: E402
from frontend_pipeline.command_center import app  # noqa: E402

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
CAP = 200_000.0


def _iso(dt):
    return dt.isoformat()


def _wjson(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def _pt(ts, equity, gross, n_pos, status="OK", **extra):
    d = {"ts": _iso(ts), "status": status, "cash": CAP, "realized_pnl": 100.0,
         "unrealized_pnl": 50.0, "fees": 12.5, "funding": 0.5, "equity": equity,
         "drawdown": -0.001, "gross_exposure": gross, "net_exposure": gross,
         "n_positions": n_pos, "skipped_no_mark": []}
    d.update(extra)
    return d


def _fill(ts, symbol, qty, price, fee, alpha="A", pf="P1_EQUAL_RISK"):
    return {"fill_id": f"{pf}:{symbol}:{_iso(ts)}:F0", "order_id": f"{pf}:{symbol}:{_iso(ts)}:1",
            "intent_id": "secret", "signal_id": "secret", "alpha_id": alpha, "portfolio_id": pf,
            "timestamp": _iso(ts), "symbol": symbol, "quantity": qty, "fill_price": price,
            "fee_usd": fee, "mark_source": "DERIVATIVES_RAW_MARK", "mark_stale": False}


REGISTRY = {
    "schema_version": 2,
    "alphas": [
        # 1) VALIDATED_FORWARD (candidat frozen_alpha_id + validated_for_forward true)
        {"alpha_id": "ALPHA_VALID", "family": "cross_sectional", "risk_bucket": "CROSS_SECTIONAL_FAMILY",
         "correlation_family": "XSMOM", "scientific_status": "FROZEN", "operational_status": "SIGNAL_SHADOW",
         "freeze_timestamp": "2026-09-02T11:20:10+00:00"},
        # 2) NO_CAPITAL (scientific_status invalidé)
        {"alpha_id": "ALPHA_DEAD", "family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
         "correlation_family": "LIQ", "scientific_status": "INVALIDATED_PENDING_RESPEC",
         "operational_status": "SIGNAL_SHADOW", "freeze_timestamp": "2026-08-31T18:08:39+00:00"},
        # 2b) NO_CAPITAL prime sur le rôle gate
        {"alpha_id": "ALPHA_DEAD_GATE", "family": "positioning", "risk_bucket": "POS",
         "correlation_family": "POS", "scientific_status": "REJECTED",
         "operational_status": "SIGNAL_SHADOW", "freeze_timestamp": None},
        # 3) GATE (rôle runner) — un candidat le référence mais validated_for_forward false
        {"alpha_id": "ALPHA_GATE", "family": "positioning", "risk_bucket": "POSITIONING_WALLET_FAMILY",
         "correlation_family": "POSITIONING_STANDALONE", "scientific_status": "RECONSTRUCTED",
         "operational_status": "SIGNAL_SHADOW", "freeze_timestamp": "2026-08-31T18:08:39+00:00"},
        # 4) OVERLAY (rôle runner)
        {"alpha_id": "ALPHA_OVERLAY", "family": "options_vol_overlay", "risk_bucket": "VOLATILITY_FAMILY",
         "correlation_family": "OPTIONS_DERIBIT_BTC", "scientific_status": "FROZEN",
         "operational_status": "SIGNAL_SHADOW", "freeze_timestamp": "2026-08-31T22:05:00+00:00"},
        # 5) EXPERIMENTAL_SHADOW (aucun runner, EXECUTION_SHADOW)
        {"alpha_id": "ALPHA_EXP", "family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
         "correlation_family": "LIQ_CASCADE_DETECTOR", "scientific_status": "FROZEN",
         "operational_status": "EXECUTION_SHADOW", "freeze_timestamp": "2026-08-31T00:00:00+00:00"},
        # exclu du roster (pas shadow)
        {"alpha_id": "ALPHA_HIDDEN", "family": "microstructure", "risk_bucket": "MICRO",
         "correlation_family": "MICRO", "scientific_status": "DISCOVERY",
         "operational_status": "CODE_MISSING", "freeze_timestamp": None},
    ],
}
RUNNERS = {"schema_version": 1, "runners": [
    {"alpha_id": "ALPHA_VALID", "script": "x.py", "cadence_minutes": 360, "role": "position"},
    {"alpha_id": "ALPHA_DEAD", "script": "x.py", "cadence_minutes": 15, "role": "position"},
    {"alpha_id": "ALPHA_DEAD_GATE", "script": "x.py", "cadence_minutes": 15, "role": "gate"},
    {"alpha_id": "ALPHA_GATE", "script": "x.py", "cadence_minutes": 15, "role": "gate"},
    {"alpha_id": "ALPHA_OVERLAY", "script": "x.py", "cadence_minutes": 60, "role": "overlay"},
]}
VALIDATION = {"candidates": [
    {"candidate_id": "VALID_CAND", "validated_for_forward": True, "frozen_alpha_id": "ALPHA_VALID"},
    {"candidate_id": "GATE_CAND", "validated_for_forward": False, "frozen_alpha_id": "ALPHA_GATE"},
    {"candidate_id": "NO_FREEZE", "validated_for_forward": True, "frozen_alpha_id": None},
]}

T0 = NOW - timedelta(days=2)
T1 = NOW - timedelta(minutes=5)


def _write_cycle(lab: Path, finished_at, status="OK"):
    _wjson(lab / "CYCLE_STATE.json", {
        "cycle_started_at": _iso(finished_at - timedelta(minutes=3)),
        "cycle_finished_at": _iso(finished_at), "status": status,
        "producers_run": 2, "producers_ok": 1, "producers_failed": ["ALPHA_DEAD"],
        "last_success": {"ALPHA_VALID": _iso(finished_at)},
    })


@pytest.fixture
def lab(tmp_path, monkeypatch):
    lab = tmp_path / "live_alpha_lab"
    cfg = tmp_path / "configs"
    cfg.mkdir()
    (cfg / "live_alpha_registry.yaml").write_text(json.dumps(REGISTRY), encoding="utf-8")  # JSON ⊂ YAML
    (cfg / "live_alpha_runners.yaml").write_text(json.dumps(RUNNERS), encoding="utf-8")
    (cfg / "validation_registry.yaml").write_text(json.dumps(VALIDATION), encoding="utf-8")

    # SUMMARY mentionne les 5 ; seuls 2 state.json existent → 3 omis
    _wjson(lab / "portfolios" / "SUMMARY.json", {
        "generated_at": _iso(T1), "n_forward_intents_total": 7, "screened_symbols": ["JUPUSDT"],
        "vol_overlay_multiplier": 0.9662,
        "portfolios": {n: {"status": "OK", "equity": 1.0} for n in lab_api.PORTFOLIO_NAMES},
    })
    _wjson(lab / "portfolios" / "P1_EQUAL_RISK" / "state.json", {
        "positions": {
            "ATOMUSDT": {"instrument": "ATOMUSDT", "quantity": 100.0, "entry_price": 1.5,
                         "realized_pnl": 1.0, "fees_paid": 0.1, "funding_paid": 0.0, "owner_alpha": "A"},
            "BTCUSDT": {"instrument": "BTCUSDT", "quantity": -0.5, "entry_price": 60000.0,
                        "realized_pnl": 2.0, "fees_paid": 0.2, "funding_paid": -0.1, "owner_alpha": "B"},
            "ETHUSDT": {"instrument": "ETHUSDT", "quantity": 2.0, "entry_price": 3000.0,
                        "realized_pnl": 0.0, "fees_paid": 0.3, "funding_paid": 0.0, "owner_alpha": "A"},
        },
        "cash": CAP, "peak_equity": 202000.0, "cumulative_fees_usd": 12.5,
        "cumulative_turnover_usd": 1000.0, "cumulative_funding_usd": 0.5,
        "cumulative_realized_pnl": 100.0, "cumulative_cost_by_alpha": {"A": 10.0, "B": 2.5},
        "cumulative_pnl_by_alpha": {"A": 120.0, "B": 30.0},
        "equity_curve": [
            _pt(T0, 199900.0, 100000.0, 1),
            _pt(T1, 202000.0, 150000.0, 3, pnl_by_alpha={"A": 1500.0, "B": 500.0}),
        ],
        "last_step_ts": _iso(T1), "initialized": True, "orders": [],
        "fills": [_fill(T0 + timedelta(minutes=i), "ATOMUSDT" if i % 2 else "BTCUSDT",
                        1.0 if i % 2 else -0.1, 1.5 + i, 0.01) for i in range(25)],
    })
    _wjson(lab / "portfolios" / "P3_ALL_CANDIDATES" / "state.json", {
        "positions": {}, "cash": CAP, "peak_equity": CAP, "cumulative_fees_usd": 0.0,
        "cumulative_turnover_usd": 0.0, "cumulative_funding_usd": 0.0, "cumulative_realized_pnl": 0.0,
        "cumulative_cost_by_alpha": {}, "cumulative_pnl_by_alpha": {},
        "equity_curve": [_pt(T1, 199000.0, 0.0, 0)], "last_step_ts": _iso(T1),
        "initialized": True, "orders": [], "fills": [],
    })
    _write_cycle(lab, T1, "OK")

    # décisions parquet pour le fallback (ALPHA_EXP seulement)
    dec = lab / "ALPHA_EXP" / "decisions.parquet"
    dec.parent.mkdir(parents=True)
    pd.DataFrame({"provenance": ["REPLAY"] * 3 + ["FORWARD_LIVE"] * 2,
                  "symbol": ["BTCUSDT"] * 5}).to_parquet(dec)

    monkeypatch.setattr(lab_api, "LAB_DIR", lab)
    monkeypatch.setattr(lab_api, "REGISTRY_PATH", cfg / "live_alpha_registry.yaml")
    monkeypatch.setattr(lab_api, "RUNNERS_PATH", cfg / "live_alpha_runners.yaml")
    monkeypatch.setattr(lab_api, "VALIDATION_PATH", cfg / "validation_registry.yaml")
    monkeypatch.setattr(lab_api, "_now", lambda: NOW)
    monkeypatch.setattr(lab_api, "_scoreboard_row", lambda entry: None)   # → fallback parquet
    lab_api._cache.clear()
    return lab


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client AUTHENTIFIÉ (admin) : la garde auth protège tout /api. Fichier
    utilisateurs + secret temporaires (jamais les vrais)."""
    import base64, hashlib
    from frontend_pipeline import auth
    salt = b"t" * 16
    users = tmp_path / "cc_users.json"
    users.write_text(json.dumps({"schema_version": 1, "users": {"t": {
        "algo": "pbkdf2_sha256", "iterations": 10, "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"t", salt, 10)).decode(),
        "role": "admin"}}}), encoding="utf-8")
    secret = tmp_path / "cc_secret"
    secret.write_text("ef" * 32, encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users)
    monkeypatch.setattr(auth, "SECRET_FILE", secret)
    monkeypatch.setattr(auth, "_users_cache", {"mtime": None, "users": None})
    monkeypatch.setattr(auth, "_secret_cache", {"mtime": None, "secret": None})
    c = TestClient(app)
    r = c.post("/login", data={"username": "t", "password": "t"}, follow_redirects=False)
    assert r.status_code == 303
    return c


# ── /api/lab/portfolios ──────────────────────────────────────────────────────

def test_portfolios_order_and_pnl(lab, client):
    r = client.get("/api/lab/portfolios")
    assert r.status_code == 200
    d = r.json()
    assert d["capital_eur"] == CAP
    assert d["generated_at"] == _iso(T1)
    assert d["vol_overlay_multiplier"] == 0.9662
    assert d["screened_symbols"] == ["JUPUSDT"]
    assert set(d.keys()) == {"generated_at", "capital_eur", "cycle", "vol_overlay_multiplier",
                             "screened_symbols", "portfolios", "roster"}

    names = [p["name"] for p in d["portfolios"]]
    assert names == ["P1_EQUAL_RISK", "P3_ALL_CANDIDATES"]   # ordre fixe, 3 sans state.json omis

    p1 = d["portfolios"][0]
    assert p1["equity"] == 202000.0
    assert p1["pnl_eur"] == pytest.approx(2000.0)
    assert p1["pnl_pct"] == pytest.approx(0.01)
    assert p1["gross_exposure"] == 150000.0 and p1["net_exposure"] == 150000.0
    assert p1["n_positions"] == 3
    assert p1["realized_pnl"] == 100.0 and p1["unrealized_pnl"] == 50.0
    assert p1["fees"] == 12.5 and p1["funding"] == 0.5
    assert p1["drawdown"] == -0.001
    assert p1["status"] == "OK"
    assert p1["last_step_ts"] == _iso(T1)
    assert p1["since"] == _iso(T0)
    assert p1["n_equity_points"] == 2
    assert p1["pnl_by_alpha"] == {"A": 1500.0, "B": 500.0}      # dernier point de la courbe
    assert p1["cost_by_alpha"] == {"A": 10.0, "B": 2.5}
    assert set(p1.keys()) == {"name", "equity", "pnl_eur", "pnl_pct", "gross_exposure", "net_exposure",
                              "n_positions", "realized_pnl", "unrealized_pnl", "fees", "funding",
                              "drawdown", "status", "last_step_ts", "since", "n_equity_points",
                              "pnl_by_alpha", "cost_by_alpha"}

    p3 = d["portfolios"][1]
    assert p3["equity"] == 199000.0
    assert p3["pnl_eur"] == pytest.approx(-1000.0)
    assert p3["pnl_pct"] == pytest.approx(-0.005)
    assert p3["n_positions"] == 0 and p3["n_equity_points"] == 1
    assert p3["since"] == _iso(T1)
    assert p3["pnl_by_alpha"] == {} and p3["cost_by_alpha"] == {}


def test_cycle_live_true_when_recent(lab, client):
    d = client.get("/api/lab/portfolios").json()
    c = d["cycle"]
    assert c == {"finished_at": _iso(T1), "status": "OK", "producers_ok": 1, "producers_run": 2,
                 "producers_failed": ["ALPHA_DEAD"], "age_min": 5.0, "live": True,
                 "timer_every_min": 15}


def test_cycle_live_false_when_stale_or_failed(lab, client):
    _write_cycle(lab, NOW - timedelta(minutes=25), "OK")
    lab_api._cache.clear()
    c = client.get("/api/lab/portfolios").json()["cycle"]
    assert c["age_min"] == 25.0 and c["live"] is False

    _write_cycle(lab, NOW - timedelta(minutes=2), "FAILED")
    lab_api._cache.clear()
    c = client.get("/api/lab/portfolios").json()["cycle"]
    assert c["age_min"] == 2.0 and c["status"] == "FAILED" and c["live"] is False


def test_cycle_missing_file(lab, client):
    (lab / "CYCLE_STATE.json").unlink()
    lab_api._cache.clear()
    c = client.get("/api/lab/portfolios").json()["cycle"]
    assert c["finished_at"] is None and c["status"] is None and c["age_min"] is None
    assert c["live"] is False and c["producers_failed"] == []


def test_roster_label_rule(lab, client):
    d = client.get("/api/lab/portfolios").json()
    roster = {r["alpha_id"]: r for r in d["roster"]}
    assert list(roster) == ["ALPHA_VALID", "ALPHA_DEAD", "ALPHA_DEAD_GATE", "ALPHA_GATE",
                            "ALPHA_OVERLAY", "ALPHA_EXP"]           # ALPHA_HIDDEN exclu
    assert roster["ALPHA_VALID"]["label"] == "VALIDATED_FORWARD"
    assert roster["ALPHA_DEAD"]["label"] == "NO_CAPITAL"
    assert roster["ALPHA_DEAD_GATE"]["label"] == "NO_CAPITAL"      # NO_CAPITAL prime sur gate
    assert roster["ALPHA_GATE"]["label"] == "GATE"                 # candidat non validé → pas VALIDATED
    assert roster["ALPHA_OVERLAY"]["label"] == "OVERLAY"
    assert roster["ALPHA_EXP"]["label"] == "EXPERIMENTAL_SHADOW"

    assert roster["ALPHA_GATE"]["role"] == "gate"
    assert roster["ALPHA_EXP"]["role"] is None
    assert roster["ALPHA_EXP"]["operational_status"] == "EXECUTION_SHADOW"
    assert roster["ALPHA_VALID"]["risk_bucket"] == "CROSS_SECTIONAL_FAMILY"
    assert roster["ALPHA_VALID"]["correlation_family"] == "XSMOM"
    assert roster["ALPHA_VALID"]["freeze_timestamp"] == "2026-09-02T11:20:10+00:00"
    for r in d["roster"]:
        assert set(r.keys()) == {"alpha_id", "family", "risk_bucket", "correlation_family",
                                 "scientific_status", "operational_status", "role", "label",
                                 "freeze_timestamp", "replay_decisions", "forward_decisions",
                                 "independent_episodes", "confidence", "last_trigger_h_ago"}


def test_roster_fallback_counts_provenance_only(lab, client):
    roster = {r["alpha_id"]: r for r in client.get("/api/lab/portfolios").json()["roster"]}
    e = roster["ALPHA_EXP"]
    assert e["replay_decisions"] == 3 and e["forward_decisions"] == 2
    assert e["independent_episodes"] is None and e["confidence"] is None
    assert e["last_trigger_h_ago"] is None
    v = roster["ALPHA_VALID"]     # pas de parquet → 0/0
    assert v["replay_decisions"] == 0 and v["forward_decisions"] == 0


def test_roster_uses_scoreboard_row_when_available(lab, client, monkeypatch):
    def fake_row(entry):
        return {"replay_decisions": 189, "forward_decisions": 242, "independent_episodes": 66,
                "confidence": "MEANINGFUL", "last_trigger_h_ago": 0.9}
    monkeypatch.setattr(lab_api, "_scoreboard_row", fake_row)
    lab_api._cache.clear()
    r = {x["alpha_id"]: x for x in client.get("/api/lab/portfolios").json()["roster"]}["ALPHA_EXP"]
    assert (r["replay_decisions"], r["forward_decisions"], r["independent_episodes"],
            r["confidence"], r["last_trigger_h_ago"]) == (189, 242, 66, "MEANINGFUL", 0.9)


def test_scoreboard_row_maps_real_row_for_keys(lab, monkeypatch):
    """_scoreboard_row (non stubbé) traduit les clés de row_for vers le contrat."""
    monkeypatch.undo()   # retire le stub de la fixture pour tester la vraie fonction
    import types
    fake_mod = types.ModuleType("scripts.compute_live_alpha_lab_scoreboard")
    fake_mod.row_for = lambda e: {"replay_decisions": 5, "forward_decisions": 7,
                                  "forward_independent_episodes": 3, "confidence_level": "EARLY",
                                  "time_since_last_trigger_hours": 12.5}
    monkeypatch.setitem(sys.modules, "scripts.compute_live_alpha_lab_scoreboard", fake_mod)
    assert lab_api._scoreboard_row({"alpha_id": "X"}) == {
        "replay_decisions": 5, "forward_decisions": 7, "independent_episodes": 3,
        "confidence": "EARLY", "last_trigger_h_ago": 12.5}
    fake_mod.row_for = lambda e: 1 / 0
    assert lab_api._scoreboard_row({"alpha_id": "X"}) is None


def test_portfolios_cached_20s(lab, client):
    d1 = client.get("/api/lab/portfolios").json()
    _write_cycle(lab, NOW - timedelta(minutes=40), "OK")
    d2 = client.get("/api/lab/portfolios").json()
    assert d2["cycle"] == d1["cycle"]          # servi depuis le cache (TTL 20 s)
    assert lab_api.PORTFOLIOS_TTL_S <= 20.0 and lab_api.DETAIL_TTL_S <= 20.0


# ── /api/lab/portfolio/{name}/history ────────────────────────────────────────

def test_history_shape(lab, client):
    r = client.get("/api/lab/portfolio/P1_EQUAL_RISK/history")
    assert r.status_code == 200
    d = r.json()
    assert set(d.keys()) == {"name", "capital_eur", "history"}
    assert d["name"] == "P1_EQUAL_RISK" and d["capital_eur"] == CAP
    assert d["history"] == [
        {"t": _iso(T0), "v": 199900.0, "gross": 100000.0, "n_positions": 1, "status": "OK"},
        {"t": _iso(T1), "v": 202000.0, "gross": 150000.0, "n_positions": 3, "status": "OK"},
    ]


def test_history_404_unknown_or_missing_state(lab, client):
    assert client.get("/api/lab/portfolio/NOPE/history").status_code == 404
    assert client.get("/api/lab/portfolio/P1_CONTROL/history").status_code == 404  # connu, sans state
    assert client.get("/api/lab/portfolio/NOPE/positions").status_code == 404
    assert client.get("/api/lab/portfolio/P1_CONTROL/positions").status_code == 404


# ── /api/lab/portfolio/{name}/positions ──────────────────────────────────────

def test_positions_sorted_and_fills_safe(lab, client):
    r = client.get("/api/lab/portfolio/P1_EQUAL_RISK/positions")
    assert r.status_code == 200
    d = r.json()
    assert set(d.keys()) == {"name", "as_of", "positions", "fills_recent"}
    assert d["name"] == "P1_EQUAL_RISK" and d["as_of"] == _iso(T1)

    pos = d["positions"]
    assert [p["instrument"] for p in pos] == ["BTCUSDT", "ETHUSDT", "ATOMUSDT"]  # |notional| desc
    assert pos[0]["notional_entry"] == pytest.approx(-30000.0)
    assert pos[1]["notional_entry"] == pytest.approx(6000.0)
    assert pos[2]["notional_entry"] == pytest.approx(150.0)
    assert pos[0]["owner_alpha"] == "B" and pos[0]["quantity"] == -0.5 and pos[0]["entry_price"] == 60000.0
    for p in pos:
        assert set(p.keys()) == {"instrument", "owner_alpha", "quantity", "entry_price", "notional_entry",
                                 "realized_pnl", "fees_paid", "funding_paid"}

    fills = d["fills_recent"]
    assert len(fills) == 20                                    # 25 fills → 20 plus récents
    assert fills[0]["timestamp"] == _iso(T0 + timedelta(minutes=24))   # plus récent d'abord
    assert fills[-1]["timestamp"] == _iso(T0 + timedelta(minutes=5))
    for f in fills:
        assert set(f.keys()) <= set(lab_api.FILL_KEYS)
        assert "intent_id" not in f and "mark_source" not in f and "fill_id" not in f
    f0 = fills[0]
    assert f0["instrument"] == "BTCUSDT" and f0["price"] == 1.5 + 24 and f0["fee"] == 0.01
    assert f0["side"] == "SELL" and f0["quantity"] == -0.1
    assert fills[1]["side"] == "BUY"
    assert f0["alpha_id"] == "A" and f0["portfolio_id"] == "P1_EQUAL_RISK" and "order_id" in f0


def test_positions_empty_portfolio(lab, client):
    d = client.get("/api/lab/portfolio/P3_ALL_CANDIDATES/positions").json()
    assert d["positions"] == [] and d["fills_recent"] == []


# ── LEGACY GELÉ : /api/portfolio/live, /api/portfolio/init ───────────────────

class _FakeCol:
    def __init__(self, doc):
        self.doc = doc
        self.calls = []

    def update_one(self, flt, upd):
        self.calls.append((flt, upd))
        assert flt == {"_id": "main"} and set(upd.keys()) == {"$set"}
        self.doc.update(upd["$set"])           # persiste comme Mongo le ferait


class _FakePaper:
    def __init__(self, doc, mongo=True):
        self._doc = doc
        self.col = _FakeCol(doc) if (mongo and doc is not None) else (_FakeCol({}) if mongo else None)
        self.events = None

    def exists(self):
        return self._doc is not None

    def get(self):
        return self._doc

    def history(self):
        return (self._doc or {}).get("history", [])

    def mark_to_market(self):
        raise AssertionError("mark_to_market must not be called")

    def _mark_unlocked(self):
        raise AssertionError("must not be called")

    def initialize(self, *a, **k):
        raise AssertionError("initialize must not be called")

    def initialize_strategy(self, *a, **k):
        raise AssertionError("initialize_strategy must not be called")


def _legacy_doc():
    return {"_id": "main", "mode": "strategy", "preset": "adaptive", "capital_eur": 200000.0,
            "created_at": "2026-07-17T09:00:00+00:00", "rebalanced_at": "2026-09-03T08:00:00+00:00",
            "positions": [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": 1.0}],
            "history": [{"t": "2026-07-17T09:00:00+00:00", "v": 200000.0},
                        {"t": "2026-09-03T10:00:00+00:00", "v": 199312.5}]}


def test_legacy_live_frozen_sets_stopped_at_once(client, monkeypatch):
    doc = _legacy_doc()
    fake = _FakePaper(doc)
    monkeypatch.setattr(command_center, "_paper", lambda: fake)

    r = client.get("/api/portfolio/live")
    assert r.status_code == 200
    d = r.json()
    assert d["exists"] is True and d["stopped"] is True
    assert isinstance(d["stopped_at"], str) and d["stopped_at"]
    datetime.fromisoformat(d["stopped_at"])
    assert d["value_eur"] == 199312.5
    assert d["capital_eur"] == 200000.0
    assert d["pnl_eur"] == pytest.approx(-687.5)
    assert d["pnl_pct"] == pytest.approx(-687.5 / 200000.0)
    assert d["created_at"] == "2026-07-17T09:00:00+00:00"
    assert d["rebalanced_at"] == "2026-09-03T08:00:00+00:00"
    assert d["preset"] == "adaptive"
    assert d["policy_label"].startswith("ADAPTATIF")
    # libellé au passé : ex-politique, arrêtée — aucun présent qui suggère une cadence active
    assert "était re-alloué" in d["policy_label"] and "arrêtée le 2026-09-03" in d["policy_label"]
    assert "— re-alloué toutes" not in d["policy_label"]
    assert d["history_points"] == 2
    assert d["hint"] == "arrêté le 2026-09-03 — remplacé par /api/lab/portfolios"
    assert set(d.keys()) == {"exists", "stopped", "stopped_at", "value_eur", "capital_eur", "pnl_eur",
                             "pnl_pct", "created_at", "rebalanced_at", "preset", "policy_label",
                             "history_points", "hint"}
    assert len(fake.col.calls) == 1
    assert fake.col.calls[0] == ({"_id": "main"}, {"$set": {"stopped_at": d["stopped_at"]}})
    assert doc["stopped_at"] == d["stopped_at"]

    # 2e GET : le doc porte déjà stopped_at → aucune nouvelle écriture, même valeur
    d2 = client.get("/api/portfolio/live").json()
    assert len(fake.col.calls) == 1
    assert d2["stopped_at"] == d["stopped_at"] and d2["stopped"] is True


def test_legacy_live_no_history_falls_back_to_capital(client, monkeypatch):
    doc = _legacy_doc()
    doc["history"] = []
    doc["stopped_at"] = "2026-09-03T11:00:00+00:00"
    fake = _FakePaper(doc)
    monkeypatch.setattr(command_center, "_paper", lambda: fake)
    d = client.get("/api/portfolio/live").json()
    assert d["value_eur"] == 200000.0 and d["pnl_eur"] == 0.0 and d["pnl_pct"] == 0.0
    assert d["history_points"] == 0 and d["stopped_at"] == "2026-09-03T11:00:00+00:00"
    assert fake.col.calls == []


def test_legacy_live_mongo_unavailable(client, monkeypatch):
    monkeypatch.setattr(command_center, "_paper", lambda: _FakePaper(None, mongo=False))
    assert client.get("/api/portfolio/live").json() == {"exists": False, "stopped": True,
                                                        "backend": "unavailable"}


def test_legacy_live_no_doc(client, monkeypatch):
    monkeypatch.setattr(command_center, "_paper", lambda: _FakePaper(None, mongo=True))
    assert client.get("/api/portfolio/live").json() == {"exists": False, "stopped": True}


def test_legacy_init_gone_410(client, monkeypatch):
    fake = _FakePaper(_legacy_doc())
    monkeypatch.setattr(command_center, "_paper", lambda: fake)
    r = client.post("/api/portfolio/init", json={"policy": "adaptive"})
    assert r.status_code == 410
    assert r.json()["detail"] == "portefeuille legacy arrêté le 2026-09-03 — voir /api/lab/portfolios"
    assert fake.col.calls == []
    r2 = client.post("/api/portfolio/init")
    assert r2.status_code == 410


def test_legacy_source_never_marks():
    """Garde-fou statique : le handler gelé ne référence plus mark_to_market."""
    import inspect
    src = inspect.getsource(command_center.api_portfolio_live)
    assert "mark_to_market" not in src and "initialize" not in src
    assert "update_one" in src and "stopped_at" in src


def test_legacy_policy_labels_all_past_tense():
    """Toutes les ex-politiques (max/aggressive/adaptive/défaut) portent l'arrêt."""
    labels = list(command_center._LEGACY_POLICY_LABEL.values()) + [command_center._LEGACY_POLICY_DEFAULT]
    assert len(labels) == 4
    for lab in labels:
        assert "arrêtée le 2026-09-03" in lab and "plus aucune ré-allocation" in lab
        assert " cible" not in lab and "re-alloué toutes" not in lab.replace("était re-alloué", "")


# ── cached() : un seul recalcul par clé, valeur périmée servie pendant ce temps ──

def test_cached_stale_while_revalidate_single_flight():
    import threading
    import time as _time
    key = "test_swr"
    command_center._cache.pop(key, None)
    command_center._cache_locks.pop(key, None)
    calls, started, gate, out = [], threading.Event(), threading.Event(), {}

    def slow():
        calls.append(1)
        started.set()
        assert gate.wait(5)
        return "fresh"

    command_center._cache[key] = (_time.time() - 100.0, "stale")     # périmée (TTL 1 s)
    t = threading.Thread(target=lambda: out.__setitem__("bg", command_center.cached(key, 1.0, slow)))
    t.start()
    assert started.wait(5)
    # pendant le recalcul : la valeur périmée est servie immédiatement, sans 2e calcul
    t0 = _time.time()
    assert command_center.cached(key, 1.0, slow) == "stale"
    assert _time.time() - t0 < 1.0
    assert len(calls) == 1
    gate.set()
    t.join(5)
    assert out["bg"] == "fresh"
    assert command_center.cached(key, 60.0, slow) == "fresh"
    assert len(calls) == 1


def test_cached_no_stale_value_waits_for_single_compute():
    import threading
    key = "test_cold"
    command_center._cache.pop(key, None)
    command_center._cache_locks.pop(key, None)
    calls, started, gate, out = [], threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        started.set()
        assert gate.wait(5)
        return "v"

    ts = [threading.Thread(target=lambda: out.append(command_center.cached(key, 60.0, slow)))
          for _ in range(3)]
    for t in ts:
        t.start()
    assert started.wait(5)
    gate.set()
    for t in ts:
        t.join(5)
    assert out == ["v", "v", "v"]
    assert len(calls) == 1                     # un seul calcul pour 3 appelants concurrents
    assert command_center.cached(key, 60.0, slow) == "v" and len(calls) == 1


def test_cached_fresh_hit_bypasses_lock_and_tournament_ttl():
    import time as _time
    key = "test_fresh"
    command_center._cache[key] = (_time.time(), "hit")
    assert command_center.cached(key, 60.0, lambda: pytest.fail("ne doit pas recalculer")) == "hit"
    # le calcul de /api/tournament/live dure ≈ 40 s : TTL ≥ 60 s obligatoire
    assert command_center.TOURNAMENT_LIVE_TTL_S >= 60.0
    import inspect
    src = inspect.getsource(command_center.api_tournament_live)
    assert 'cached("tournament_live", TOURNAMENT_LIVE_TTL_S' in src


def test_lab_router_mounted():
    paths = {r.path for r in app.routes}
    assert {"/api/lab/portfolios", "/api/lab/portfolio/{name}/history",
            "/api/lab/portfolio/{name}/positions"} <= paths


# ── /api/lab/cycles ──────────────────────────────────────────────────────────

def _cycle_line(finished_at, status="OK", ok=5, run=5, failed=None, dur=290.5):
    return json.dumps({"cycle_started_at": _iso(finished_at - timedelta(minutes=5)),
                       "cycle_finished_at": _iso(finished_at), "duration_sec": dur, "status": status,
                       "producers_run": run, "producers_ok": ok, "producers_failed": failed or [],
                       "steps": [{"name": "X", "stdout_tail": "…" * 200}]})


def test_cycles_tail_newest_first_and_skips_malformed(lab, client):
    lines = [_cycle_line(T0 + timedelta(minutes=15 * i), status="OK" if i % 7 else "DEGRADED",
                         failed=[{"name": "ALPHA_DEAD"}] if i % 7 == 0 else [])
             for i in range(45)]
    lines.insert(10, "{not json at all")
    lines.insert(20, "")
    lines.insert(30, "[1, 2, 3]")
    (lab / "cycle_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = client.get("/api/lab/cycles")
    assert r.status_code == 200
    cyc = r.json()["cycles"]
    assert len(cyc) == 30
    assert cyc[0]["finished_at"] == _iso(T0 + timedelta(minutes=15 * 44))   # plus récent d'abord
    assert cyc[-1]["finished_at"] == _iso(T0 + timedelta(minutes=15 * 15))
    for c in cyc:
        assert set(c) == {"started_at", "finished_at", "duration_sec", "status",
                          "producers_ok", "producers_run", "producers_failed"}
        assert "steps" not in c
    deg = [c for c in cyc if c["status"] == "DEGRADED"]
    assert deg and deg[0]["producers_failed"] == ["ALPHA_DEAD"]
    assert cyc[0]["duration_sec"] == 290.5 and cyc[0]["producers_ok"] == 5


def test_cycles_missing_file_empty(lab, client):
    assert client.get("/api/lab/cycles").json() == {"cycles": []}


def test_cycles_tail_reads_only_end_of_big_file(lab, monkeypatch):
    monkeypatch.setattr(lab_api, "CYCLES_TAIL_BYTES", 2000)
    lines = [_cycle_line(T0 + timedelta(minutes=15 * i)) for i in range(50)]
    (lab / "cycle_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    cyc = lab_api._build_cycles()["cycles"]
    assert 0 < len(cyc) < 30                       # seule la fin du fichier est lue
    assert cyc[0]["finished_at"] == _iso(T0 + timedelta(minutes=15 * 49))


# ── /api/lab/marks ───────────────────────────────────────────────────────────

def test_marks_union_of_held_instruments(lab, client, monkeypatch):
    _wjson(lab / "portfolios" / "P1_CONTROL" / "state.json", {
        "positions": {"SOLUSDT": {"instrument": "SOLUSDT", "quantity": 1.0, "entry_price": 1.0},
                      "BTCUSDT": {"instrument": "BTCUSDT", "quantity": 1.0, "entry_price": 1.0}},
        "cash": CAP, "equity_curve": [], "fills": []})
    seen = []

    def fake_mark(instr):
        seen.append(instr)
        if instr == "ETHUSDT":
            return None                              # pas de source locale → omis
        return {"price": 1.5, "ts": _iso(T1)}
    monkeypatch.setattr(lab_api, "_mark_for", fake_mark)
    r = client.get("/api/lab/marks")
    assert r.status_code == 200
    d = r.json()
    assert d["as_of"] == _iso(NOW)
    # union P1_EQUAL_RISK {ATOMUSDT, BTCUSDT, ETHUSDT} ∪ P1_CONTROL {SOLUSDT, BTCUSDT}
    assert sorted(seen) == ["ATOMUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert set(d["marks"]) == {"ATOMUSDT", "BTCUSDT", "SOLUSDT"}
    assert d["marks"]["BTCUSDT"] == {"price": 1.5, "ts": _iso(T1)}


def test_marks_empty_on_missing_states_and_never_raises(lab, client, monkeypatch, tmp_path):
    monkeypatch.setattr(lab_api, "LAB_DIR", tmp_path / "nowhere")
    assert client.get("/api/lab/marks").json()["marks"] == {}
    lab_api._cache.clear()
    monkeypatch.setattr(lab_api, "LAB_DIR", lab)

    def boom(instr):
        raise RuntimeError("parquet cassé")
    monkeypatch.setattr(lab_api, "_mark_for", boom)
    r = client.get("/api/lab/marks")
    assert r.status_code == 200 and r.json()["marks"] == {}


def test_mark_for_reads_newest_local_file_only(lab, monkeypatch, tmp_path):
    """Source = derivatives_raw open_interest.mark_price (comme marks.get_mark),
    fichier le plus récent de la dernière partition, aucun appel réseau."""
    from src.institutional.live_alpha_lab import marks as m
    raw = tmp_path / "derivatives_raw"
    monkeypatch.setattr(m, "DERIVATIVES_RAW", raw)
    base = raw / "exchange=binance" / "market=usdm" / "stream=open_interest" / "symbol=BTCUSDT"
    old = base / "date=2026-09-02"
    new = base / "date=2026-09-03"
    old.mkdir(parents=True); new.mkdir(parents=True)
    pd.DataFrame({"timestamp": [1_000_000], "mark_price": [1.0], "open_interest": [1.0]}).to_parquet(old / "part-000001-a.parquet")
    pd.DataFrame({"timestamp": [1788460000000, 1788460300000], "mark_price": [80000.0, 80877.9],
                  "open_interest": [1.0, 1.0]}).to_parquet(new / "part-000002-b.parquet")
    pd.DataFrame({"timestamp": [1788460400000], "mark_price": [80900.5], "open_interest": [1.0]}).to_parquet(new / "part-000003-c.parquet")
    got = lab_api._mark_for("BTCUSDT_PERP")
    assert got["price"] == 80900.5 and got["ts"].startswith("2026-09-03T18:33:20")
    assert lab_api._mark_for("NOPEUSDT") is None            # pas de fichier → None, pas de REST
    monkeypatch.setattr(m, "_from_rest_bookticker", lambda *a, **k: pytest.fail("REST interdit"))
    assert lab_api._mark_for("NOPEUSDT") is None


def test_new_lab_routes_mounted():
    paths = {r.path for r in app.routes}
    assert {"/api/lab/cycles", "/api/lab/marks", "/api/status", "/api/me", "/login", "/logout",
            "/health"} <= paths
