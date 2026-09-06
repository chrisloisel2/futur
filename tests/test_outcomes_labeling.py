"""
tests/test_outcomes_labeling.py — labellisation SCELLÉE des résultats forward
(src/institutional/live_alpha_lab/outcomes.py).

Ce que ces tests protègent, dans l'ordre d'importance :
  1. la porte à sens unique (un label déjà scellé n'est JAMAIS réécrit) — sans
     elle, le forward redevient un backtest qu'on relance jusqu'à ce qu'il
     plaise ;
  2. l'honnêteté du compteur d'épisodes (un choc commun à N symboles reste UNE
     preuve) ;
  3. l'exhaustivité de la classification (aucun alpha portant des décisions
     forward ne peut rester ni labellisé ni explicitement exclu) ;
  4. l'équivalence stricte du cache de prix avec marks.get_mark — le cache
     n'existe que pour la vitesse, il ne doit RIEN changer au prix retenu.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.institutional.live_alpha_lab import outcomes as O
from src.institutional.live_alpha_lab.marks import get_mark

ROOT = Path(__file__).resolve().parents[1]
LAB_DIR = ROOT / "reports" / "live_alpha_lab"


# ── fixtures synthétiques ────────────────────────────────────────────────────

def _decisions(n=3, base="2026-09-02T00:00:00Z", lag_h=0.5):
    t0 = pd.Timestamp(base)
    rows = []
    for i in range(n):
        et = t0 + pd.Timedelta(hours=i)
        rows.append({
            "event_time": et, "symbol": f"SYM{i}USDT", "direction": "LONG",
            "decided_at": et + pd.Timedelta(hours=lag_h), "provenance": "FORWARD_LIVE",
        })
    return pd.DataFrame(rows)


@pytest.fixture
def fake_prices(monkeypatch):
    """Prix déterministe : +1 % par heure, même source pour toutes les jambes."""
    def at(self, symbol, as_of):
        hours = (pd.Timestamp(as_of) - pd.Timestamp("2026-09-01T00:00:00Z")).total_seconds() / 3600.0
        return 100.0 * (1.01 ** hours), "DERIVATIVES_RAW_MARK", 0.0
    monkeypatch.setattr(O.MarkSeriesCache, "at", at)
    monkeypatch.setattr(O, "_bench_memo", {})
    return None


# ── 1. porte à sens unique ───────────────────────────────────────────────────

def test_append_sealed_refuses_to_rewrite_a_known_key(tmp_path):
    p = tmp_path / "outcomes.parquet"
    row = {"decision_key": "abc", "dec_gross_bps": 10.0}
    assert O.append_sealed(p, [row]) == (1, 0)
    # même clé, valeur différente : refusée, et l'ancienne valeur survit
    assert O.append_sealed(p, [{"decision_key": "abc", "dec_gross_bps": 999.0}]) == (0, 1)
    got = pd.read_parquet(p)
    assert len(got) == 1
    assert got["dec_gross_bps"].iloc[0] == 10.0


def test_relabeling_the_same_ledger_twice_adds_nothing(tmp_path, fake_prices):
    spec = O.LabelSpec("event_time", "symbol", "direction", "fwd_4h")
    df = _decisions()
    now = pd.Timestamp("2026-09-03T00:00:00Z")
    r1 = O.label_alpha("A", df, spec, now=now, lab_dir=tmp_path)
    r2 = O.label_alpha("A", df, spec, now=now, lab_dir=tmp_path)
    assert r1["n_new"] == 3 and r2["n_new"] == 0


def test_seal_digest_changes_if_a_sealed_field_is_touched():
    row = {"alpha_id": "A", "decision_key": "k", "dec_gross_bps": 12.0, "dec_status": "OK"}
    before = O.seal_digest(row)
    assert O.seal_digest(dict(row)) == before          # stable
    row["dec_gross_bps"] = 12.5
    assert O.seal_digest(row) != before                # détecte l'altération


# ── 2. maturité et fenêtre de scellement ─────────────────────────────────────

def test_decision_not_yet_mature_is_not_labeled(fake_prices):
    spec = O.LabelSpec("event_time", "symbol", "direction", "fwd_4h")
    row = _decisions(1).iloc[0]
    # horizon 4 h à partir de decided_at -> échéance 04:30 ; on demande à 02:00
    assert O.label_one("A", spec, row, pd.Timestamp("2026-09-02T02:00:00Z"), "sha", "p") is None


def test_timeliness_splits_sealed_at_maturity_from_late_backfill(fake_prices):
    spec = O.LabelSpec("event_time", "symbol", "direction", "fwd_4h")
    row = _decisions(1).iloc[0]           # échéance 2026-09-02T04:30Z
    fresh = O.label_one("A", spec, row, pd.Timestamp("2026-09-02T05:00:00Z"), "sha", "p")
    late = O.label_one("A", spec, row, pd.Timestamp("2026-09-04T05:00:00Z"), "sha", "p")
    assert fresh["label_timeliness"] == "SEALED_AT_MATURITY"
    assert late["label_timeliness"] == "LATE_BACKFILL"


def test_missing_price_is_refused_not_invented(monkeypatch):
    monkeypatch.setattr(O.MarkSeriesCache, "at",
                        lambda self, s, t: (None, "NO_PRICE", None))
    monkeypatch.setattr(O, "_bench_memo", {})
    spec = O.LabelSpec("event_time", "symbol", "direction", "fwd_4h")
    row = _decisions(1).iloc[0]
    # jeune : on n'écrit rien, la donnée peut encore arriver
    assert O.label_one("A", spec, row, pd.Timestamp("2026-09-02T05:00:00Z"), "s", "p") is None
    # vieux : refus SCELLÉ, jamais un prix inventé ni un trou silencieux
    old = O.label_one("A", spec, row, pd.Timestamp("2026-09-10T00:00:00Z"), "s", "p")
    assert old is not None
    assert old["dec_status"] == "ENTRY_NO_PRICE"
    assert old["dec_gross_bps"] is None


# ── 3. identité des décisions ────────────────────────────────────────────────

def test_decision_key_is_stable_and_separates_redecisions():
    k1 = O.decision_key("A", "2026-09-01T00:00:00Z", "BTCUSDT", "LONG", "2026-09-01T00:10:00Z")
    k2 = O.decision_key("A", "2026-09-01T00:00:00Z", "BTCUSDT", "LONG", "2026-09-01T00:10:00Z")
    k3 = O.decision_key("A", "2026-09-01T00:00:00Z", "BTCUSDT", "LONG", "2026-09-01T00:25:00Z")
    assert k1 == k2 and k1 != k3


# ── 4. épisodes : un choc commun n'est pas N preuves ─────────────────────────

def test_cross_sectional_shock_counts_as_one_episode():
    """31 symboles, même choc BTC dans la même fenêtre de 15 min : 1 preuve."""
    t = pd.Timestamp("2026-09-04T12:30:00Z")
    df = pd.DataFrame([{
        "event_time": t + pd.Timedelta(minutes=5 * (i % 4)),
        "symbol": f"ALT{i}USDT", "dec_excess_bps": -50.0 + i, "dec_status": "OK",
        "label_timeliness": "LATE_BACKFILL",
    } for i in range(31)])
    assert O.summarize_outcomes(df, anchor="dec", cross_sectional=True).n_episodes == 1
    assert O.summarize_outcomes(df, anchor="dec", cross_sectional=False).n_episodes == 31


def test_registry_marks_btc_lead_alt_as_cross_sectional():
    assert O.LABELABLE["BTC_LEAD_ALT_CASCADE_V1"].cross_sectional is True


# ── 5. excess = brut - référence de marché ───────────────────────────────────

def test_excess_is_gross_minus_signed_benchmark(fake_prices, monkeypatch):
    monkeypatch.setattr(O, "universe_return_bps", lambda a, b, c=None: (40.0, 47))
    monkeypatch.setattr(O, "_bench_memo", {})
    leg = O._anchor_leg("BTCUSDT", "LONG", pd.Timestamp("2026-09-02T00:00:00Z"),
                        pd.Timestamp("2026-09-02T04:00:00Z"), "dec")
    assert leg["dec_excess_bps"] == pytest.approx(leg["dec_gross_bps"] - 40.0)


def test_short_direction_flips_the_benchmark_sign(fake_prices, monkeypatch):
    monkeypatch.setattr(O, "universe_return_bps", lambda a, b, c=None: (40.0, 47))
    monkeypatch.setattr(O, "_bench_memo", {})
    leg = O._anchor_leg("BTCUSDT", "SHORT", pd.Timestamp("2026-09-02T00:00:00Z"),
                        pd.Timestamp("2026-09-02T04:00:00Z"), "dec")
    assert leg["dec_excess_bps"] == pytest.approx(leg["dec_gross_bps"] + 40.0)


def test_thin_benchmark_is_none_not_a_fake_market(monkeypatch):
    monkeypatch.setattr(O, "benchmark_universe", lambda: ["AUSDT", "BUSDT"])
    monkeypatch.setattr(O.MarkSeriesCache, "at",
                        lambda self, s, t: (100.0, "DERIVATIVES_RAW_MARK", 0.0))
    bench, n = O.universe_return_bps(pd.Timestamp("2026-09-02T00:00:00Z"),
                                     pd.Timestamp("2026-09-02T04:00:00Z"))
    assert bench is None and n == 2


# ── 6. edge_retention ────────────────────────────────────────────────────────

@pytest.mark.parametrize("expected", [None, 0.0, -57.8])
def test_edge_retention_refuses_a_nonpositive_reference(expected):
    assert O.edge_retention(expected, O.EXPECTED_BASIS_ABSOLUTE, gross_net_bps=20.0) is None


def test_edge_retention_refuses_an_undeclared_basis():
    """La colonne `expected_net_bps` du registre porte DEUX grandeurs : un net
    absolu pour LIQ_CASCADE_REPEAT_V1 (27,1), un excess vs baseline pour
    SHORT_COVERING (9,2, dont le net absolu vaut -2,72). Deviner laquelle est
    exactement l'erreur que ce paramètre rend impossible."""
    assert O.edge_retention(27.1, None, gross_net_bps=13.55, excess_net_bps=5.0) is None
    assert O.edge_retention(27.1, "PAS_UNE_BASE", gross_net_bps=13.55) is None


def test_edge_retention_picks_the_metric_the_basis_names():
    assert O.edge_retention(27.1, O.EXPECTED_BASIS_ABSOLUTE,
                            gross_net_bps=13.55, excess_net_bps=99.0) == 0.5
    assert O.edge_retention(9.2, O.EXPECTED_BASIS_EXCESS,
                            gross_net_bps=99.0, excess_net_bps=4.6) == 0.5


def test_every_registry_expected_net_bps_declares_its_basis_or_is_unused():
    """Une entrée avec `expected_net_bps` mais sans base ne produira jamais
    d'edge_retention — fail closed. Ce test ne l'interdit pas, il rend le
    manque VISIBLE plutôt que silencieux."""
    import yaml
    reg = yaml.safe_load((ROOT / "configs" / "live_alpha_registry.yaml").read_text())
    undeclared = [a["alpha_id"] for a in reg["alphas"]
                  if a.get("expected_net_bps") is not None
                  and a.get("expected_net_bps_basis") is None]
    # les alphas sans base sont ceux sans décision forward : aucun n'est labellisable
    assert not (set(undeclared) & set(O.LABELABLE)), (
        f"alphas labellisables sans expected_net_bps_basis : "
        f"{sorted(set(undeclared) & set(O.LABELABLE))}")


# ── 7. exhaustivité de la classification ─────────────────────────────────────

def test_every_forward_bearing_alpha_is_classified():
    """Aucun alpha portant des décisions forward ne peut rester hors des deux
    listes : soit il est labellisable, soit son exclusion est écrite AVEC son
    motif. Même détecteur de dérive que registry_drift() dans le cycle."""
    unclassified = []
    for p in sorted(LAB_DIR.glob("*/decisions.parquet")):
        alpha_id = p.parent.name
        try:
            df = pd.read_parquet(p, columns=["provenance"])
        except Exception:
            continue
        if "provenance" not in df.columns:
            continue
        if (df["provenance"] == "FORWARD_LIVE").any():
            if alpha_id not in O.LABELABLE and alpha_id not in O.NOT_LABELABLE:
                unclassified.append(alpha_id)
    assert not unclassified, (
        f"alphas avec des décisions forward et aucune décision de labellisation : "
        f"{unclassified} — les ajouter à LABELABLE ou à NOT_LABELABLE (avec motif)")


def test_exclusions_carry_a_reason():
    for alpha_id, reason in O.NOT_LABELABLE.items():
        assert reason and " — " in reason, f"{alpha_id} : motif d'exclusion vide ou non motivé"


# ── 8. le cache ne change RIEN au prix retenu ────────────────────────────────

@pytest.mark.skipif(not (ROOT / "data" / "derivatives_raw").exists(),
                    reason="derivatives_raw absent (machine sans archive de prix)")
def test_cache_matches_get_mark():
    """Le cache n'existe que pour la vitesse. S'il devait diverger de
    marks.get_mark, tous les labels seraient calculés sur une autre source de
    prix que le mark-to-market du portefeuille."""
    cache = O.MarkSeriesCache()
    base = pd.Timestamp("2026-09-04T00:00:00Z")
    rng = np.random.default_rng(0)
    checked = 0
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for _ in range(6):
            as_of = base + pd.Timedelta(minutes=int(rng.integers(0, 24 * 60)))
            px, _, _ = cache.at(symbol, as_of)
            q = get_mark(symbol, as_of)
            ref = None if (q is None or q.mark_age_ms > O.MAX_MARK_AGE_MINUTES * 60_000) \
                else float(q.price)
            assert (px is None) == (ref is None), f"{symbol} {as_of}"
            if px is not None:
                assert px == pytest.approx(ref, rel=0, abs=1e-12), f"{symbol} {as_of}"
            checked += 1
    assert checked == 18
