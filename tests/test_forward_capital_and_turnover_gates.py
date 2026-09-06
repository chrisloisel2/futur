"""tests/test_forward_capital_and_turnover_gates.py

Les trois portes ajoutées par l'audit forward du 2026-09-04 :

  P0.1  is_forward_eligible()  -- pas de capital à un alpha non validé
  P0.2  score_net < 0          -- pas d'ordre sur une espérance nette négative
  P0.3  bande de non-négociation + classification du turnover

plus les correctifs d'infrastructure P0.4 (fuite de .tmp, verrouillage de
seuils du watchdog, terminaison propre des sous-processus).

Chaque test correspond à un point de la liste « TESTS OBLIGATOIRES » de la
mission ; le numéro est rappelé dans le nom ou la docstring.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.contracts import ReasonCode
from src.institutional.live_alpha_lab.eligibility import (
    EligibilityReason, ValidationLink, is_forward_eligible)
from src.institutional.live_alpha_lab.intents import (
    PortfolioIntent, build_intents, filter_negative_expected_value)
from src.institutional.live_alpha_lab.marks import MarkQuote
import src.institutional.live_alpha_lab.portfolio as portfolio_mod
from src.institutional.live_alpha_lab.portfolio import (
    TURNOVER_ENTRY, TURNOVER_MECHANICAL_RESIZE, TURNOVER_SIGNAL_RESIZE,
    aggregate, no_trade_band_fraction, round_trip_cost_fraction, step)
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def _mock_mark(price, source="TEST_MOCK", age_ms=0.0, ts=None):
    def fn(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=price, mark_source=source,
                         mark_timestamp=ts or (as_of or _ts("2026-09-01T00:00:00Z")),
                         mark_age_ms=age_ms)
    return fn


def _config(**kw):
    base = dict(name="TEST_GATES", capital_eur=100_000,
                family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)
    base.update(kw)
    return PortfolioConfig(**base)


def _intent(instrument="BTCUSDT", frac=1.0, ts=None, alpha_id="A1", edge=0.02,
            direction="LONG"):
    """edge=0.02 (200 bps) -> bande = 14/200 = 7 %, assez fine pour que les
    tests de resize ne soient pas triviaux."""
    ts = ts or _ts("2026-09-01T00:00:00Z")
    return PortfolioIntent(
        alpha_id=alpha_id, family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
        correlation_family="FAM1", timestamp=ts, instrument=instrument,
        direction=direction, target_position_fraction=frac, confidence=1.0,
        horizon_hours=4.0, expiry=ts + pd.Timedelta(hours=4),
        expected_edge_fraction=edge)


# ══════════════════════════════════════════════════════════════════════════
# 1. alpha non validé -> aucun capital forward                       (P0.1)
# ══════════════════════════════════════════════════════════════════════════

def test_1_unvalidated_alpha_gets_no_forward_capital():
    """La porte de validation, isolée. Le cas réel dont elle vient est
    SHORT_COVERING_CONTINUATION_V1 (validated_for_forward: false), mais depuis
    le 2026-09-06 cet alpha est arrêté PLUS TÔT par BLOCK_UNRESOLVED_SPEC
    (scientific_status=RECONSTRUCTED, item C2) -- voir le test suivant. On
    teste donc ici la porte de validation sur un alpha dont la spec est
    établie, sinon on testerait l'autre porte sans le savoir."""
    alpha = {"alpha_id": "UN_ALPHA_FIGE_V1",
             "operational_status": "SIGNAL_SHADOW", "scientific_status": "FROZEN"}
    index = {"UN_ALPHA_FIGE_V1": [
        ValidationLink("UN_CANDIDAT", "NEEDS_MORE_RESEARCH", False, 2.53)]}
    v = is_forward_eligible(alpha, index)
    assert v.eligible is False
    assert v.reason is EligibilityReason.BLOCK_NOT_VALIDATED_FOR_FORWARD
    assert not v          # __bool__ : utilisable directement dans un `if`


def test_1_reconstructed_short_covering_is_stopped_earlier_still():
    """Le cas réel, tel qu'il se comporte AUJOURD'HUI : SHORT_COVERING est
    doublement bloqué, et c'est la porte de spec qui parle en premier. Elle est
    la plus forte des deux : elle tient même si le registre de validation
    changeait d'avis demain."""
    alpha = {"alpha_id": "SHORT_COVERING_CONTINUATION_V1",
             "operational_status": "SIGNAL_SHADOW", "scientific_status": "RECONSTRUCTED"}
    index = {"SHORT_COVERING_CONTINUATION_V1": [
        ValidationLink("SHORT_COVERING_CONTINUATION", "NEEDS_MORE_RESEARCH", False, 2.53)]}
    v = is_forward_eligible(alpha, index)
    assert v.eligible is False
    assert v.reason is EligibilityReason.BLOCK_UNRESOLVED_SPEC
    # et il resterait bloqué même validé -- c'est tout l'intérêt
    validated = {"SHORT_COVERING_CONTINUATION_V1": [
        ValidationLink("SHORT_COVERING_CONTINUATION", "VALIDATED_FOR_FORWARD", True, 9.2)]}
    assert is_forward_eligible(alpha, validated).eligible is False


def test_1b_alpha_without_any_validation_record_fails_closed():
    """Absence de preuve != preuve d'absence de problème."""
    alpha = {"alpha_id": "ORPHELIN_V1", "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "FROZEN"}
    v = is_forward_eligible(alpha, {})
    assert v.eligible is False
    assert v.reason is EligibilityReason.BLOCK_NO_VALIDATION_RECORD


def test_1c_status_and_flag_must_agree_before_granting_capital():
    """Un seul des deux champs édité à la main n'ouvre pas la porte."""
    alpha = {"alpha_id": "X_V1", "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "FROZEN"}
    only_flag = {"X_V1": [ValidationLink("X", "NEEDS_MORE_RESEARCH", True)]}
    only_status = {"X_V1": [ValidationLink("X", "VALIDATED_FOR_FORWARD", None)]}
    assert is_forward_eligible(alpha, only_flag).eligible is False
    assert is_forward_eligible(alpha, only_status).eligible is False


def test_1d_blocked_alpha_still_produces_decisions_for_collection():
    """La porte coupe le CAPITAL, jamais la collecte : build_intents reste
    parfaitement fonctionnel pour un alpha bloqué (c'est le runner qui
    décide de ne pas l'appeler)."""
    df = pd.DataFrame([{"timestamp": _ts("2026-09-01T00:00:00Z"), "asset": "BTCUSDT",
                        "decision_zone": "A_TRADE", "p_success": 0.9,
                        "direction": "LONG", "confidence": 0.8, "score_net": 0.001,
                        "expected_return": 0.003}])
    out = build_intents("SHORT_COVERING_CONTINUATION_V1",
                        {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
                         "correlation_family": "F"}, df)
    assert len(out) == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. alpha validé -> capital possible                                (P0.1)
# ══════════════════════════════════════════════════════════════════════════

def test_2_validated_alpha_is_eligible_for_capital():
    alpha = {"alpha_id": "AMIHUD_ILLIQUIDITY_PREMIUM_V1",
             "operational_status": "SIGNAL_SHADOW", "scientific_status": "FROZEN"}
    index = {"AMIHUD_ILLIQUIDITY_PREMIUM_V1": [
        ValidationLink("AMIHUD_ILLIQUIDITY_PREMIUM", "VALIDATED_FOR_FORWARD", True, 105.7)]}
    v = is_forward_eligible(alpha, index)
    assert v.eligible is True
    assert v.reason is EligibilityReason.ELIGIBLE_VALIDATED
    assert "AMIHUD_ILLIQUIDITY_PREMIUM" in v.detail


def test_2b_one_validated_candidate_among_several_is_enough():
    """LIQ_CASCADE_REPEAT_V1 réel : 4 candidats, 2 validés, 2 non."""
    alpha = {"alpha_id": "LIQ_CASCADE_REPEAT_V1",
             "operational_status": "SIGNAL_SHADOW", "scientific_status": "FROZEN"}
    index = {"LIQ_CASCADE_REPEAT_V1": [
        ValidationLink("LIQ_REPEAT_VOL_GATE", "NEEDS_MORE_RESEARCH", False),
        ValidationLink("LIQ_REPEAT_DENSITY", "VALIDATED_FOR_FORWARD", True),
        ValidationLink("OI_CVD_MEMORY_OVERLAP", "REJECTED", False)]}
    assert is_forward_eligible(alpha, index).eligible is True


def test_2c_dead_scientific_status_still_wins_over_a_validated_candidate():
    """La porte historique n'est pas affaiblie : un mécanisme invalidé reste
    exclu même si un candidat validé le vise."""
    alpha = {"alpha_id": "Z_V1", "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "INVALIDATED_PENDING_RESPEC"}
    index = {"Z_V1": [ValidationLink("Z", "VALIDATED_FOR_FORWARD", True)]}
    v = is_forward_eligible(alpha, index)
    assert v.eligible is False
    assert v.reason is EligibilityReason.BLOCK_SCIENTIFIC_STATUS


def test_2d_gate_and_overlay_are_not_subject_to_the_capital_gate():
    """Un screen/overlay ne consomme pas de capital, il en retire : le bloquer
    augmenterait le risque."""
    alpha = {"alpha_id": "WHALE_LSR_SCREEN_V1", "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "RECONSTRUCTED"}
    v = is_forward_eligible(alpha, {}, position_alpha=False)
    assert v.eligible is True
    assert v.reason is EligibilityReason.NOT_A_POSITION_ALPHA


# ══════════════════════════════════════════════════════════════════════════
# 3. score_net non rentable -> aucun ordre                           (P0.2)
# ══════════════════════════════════════════════════════════════════════════

def _sc_row(score_net, asset="BTCUSDT", ts=None):
    return {"timestamp": ts or _ts("2026-09-01T00:00:00Z"), "asset": asset,
            "decision_zone": "A_TRADE", "p_success": 0.9, "direction": "LONG",
            "confidence": 0.8, "score_net": score_net, "expected_return": 0.003,
            "reason": ReasonCode.ACCEPT_SHADOW.value}


def test_3_negative_score_net_produces_no_intent_and_no_order(tmp_path, monkeypatch):
    """Le cas réel : ACCEPT_SHADOW avec expected_return 10 bps < expected_cost
    14 bps -> score_net = -4 bps. Aucun capital ne doit partir dessus."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
             "correlation_family": "F"}
    stats = {}
    intents = build_intents("SHORT_COVERING_CONTINUATION_V1", entry,
                            pd.DataFrame([_sc_row(-0.0004)]), stats=stats)
    assert intents == []
    assert stats["n_blocked_negative_ev"] == 1
    assert stats["blocked_reason"] == ReasonCode.REJECT_NEGATIVE_EXPECTED_VALUE.value

    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    state = step("T3", _config(), aggregate(intents, _config(), set(), as_of=ts0), ts0)
    assert state.orders == []
    assert state.cumulative_turnover_usd == 0.0


def test_3b_positive_score_net_still_passes():
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
             "correlation_family": "F"}
    stats = {}
    intents = build_intents("SHORT_COVERING_CONTINUATION_V1", entry,
                            pd.DataFrame([_sc_row(+0.0009)]), stats=stats)
    assert len(intents) == 1 and stats["n_blocked_negative_ev"] == 0


def test_3c_mixed_batch_blocks_only_the_negative_rows():
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
             "correlation_family": "F"}
    df = pd.DataFrame([_sc_row(+0.001, "BTCUSDT"), _sc_row(-0.001, "ETHUSDT"),
                       _sc_row(+0.002, "SOLUSDT")])
    stats = {}
    out = build_intents("SHORT_COVERING_CONTINUATION_V1", entry, df, stats=stats)
    assert sorted(i.instrument for i in out) == ["BTCUSDT", "SOLUSDT"]
    assert stats["n_blocked_negative_ev"] == 1


def test_3d_no_arbitrary_threshold_zero_and_nan_are_not_blocked():
    """La frontière est exactement 0 : `score_net == 0` (espérance nulle) et
    `NaN` (le moteur ne dit rien) passent -- les bloquer supposerait un seuil
    inventé, ce que la mission interdit explicitement."""
    df = pd.DataFrame({"score_net": [0.0, float("nan"), -1e-12]})
    kept, blocked = filter_negative_expected_value(df)
    assert len(kept) == 2 and len(blocked) == 1


def test_3e_ledger_without_score_net_column_is_untouched():
    """Les ledgers liq_cascade ne déclarent pas de coût par décision : ne rien
    inventer là où le moteur ne dit rien."""
    df = pd.DataFrame([{"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"}])
    kept, blocked = filter_negative_expected_value(df)
    assert len(kept) == 1 and blocked.empty


# ══════════════════════════════════════════════════════════════════════════
# 4. intents inchangés sur N cycles -> turnover ~ 0                  (P0.3)
# ══════════════════════════════════════════════════════════════════════════

def test_4_unchanged_intents_over_many_cycles_produce_no_further_turnover(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    intents = [_intent(ts=ts0)]

    state = step("T4", config, aggregate(intents, config, set(), as_of=ts0), ts0)
    turnover_after_entry = state.cumulative_turnover_usd
    n_orders_after_entry = len(state.orders)
    assert turnover_after_entry > 0        # l'ouverture, elle, a bien eu lieu

    for i in range(1, 12):                 # 12 cycles, signal figé, prix figé
        ts = ts0 + pd.Timedelta(minutes=15 * i)
        state = step("T4", config, aggregate(intents, config, set(), as_of=ts), ts)

    assert state.cumulative_turnover_usd == pytest.approx(turnover_after_entry)
    assert len(state.orders) == n_orders_after_entry


def test_4b_price_drift_alone_does_not_cause_turnover(tmp_path, monkeypatch):
    """Cause (b) de l'item P0.3 : la cible est un NOTIONNEL, donc la quantité
    cible dérive avec le prix. Sans bande, un ordre partait à chaque cycle."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    intents = [_intent(ts=ts0)]
    state = step("T4b", config, aggregate(intents, config, set(), as_of=ts0), ts0)
    base = state.cumulative_turnover_usd

    for i, price in enumerate([100.5, 101.0, 100.8, 101.5, 100.2], start=1):
        ts = ts0 + pd.Timedelta(minutes=15 * i)
        monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(price, ts=ts))
        state = step("T4b", config, aggregate(intents, config, set(), as_of=ts), ts)

    # dérive de prix <= 1.5 %, bande = 14/200 = 7 % -> rien ne doit bouger
    assert state.cumulative_turnover_usd == pytest.approx(base)
    assert state.suppressed_order_count > 0     # et on le COMPTE, on ne l'ignore pas


# ══════════════════════════════════════════════════════════════════════════
# 5. disparition d'un intent -> les autres ne sont pas repricées     (P0.3)
# ══════════════════════════════════════════════════════════════════════════

def test_5_one_intent_disappearing_does_not_reprice_the_others(tmp_path, monkeypatch):
    """LE cas de l'audit. Trois positions ; la troisième expire. Sans la
    correction, `sum_frac_by_alpha` passait de 3 à 2, donc les cibles de
    BTC et ETH augmentaient de 50 % et les DEUX étaient retradées alors
    qu'aucun de leurs signaux n'avait bougé."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))

    # BTC/ETH portent un horizon long (toujours vivants au 2e step) ; SOL a
    # l'horizon court et expire entre les deux. Les MÊMES objets d'intent sont
    # réutilisés : c'est exactement ce que fait le vrai runner, qui relit un
    # ledger append-only où les décisions gardent leur event_time.
    def _long(instr):
        return PortfolioIntent(
            alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
            correlation_family="FAM1", timestamp=ts0, instrument=instr,
            direction="LONG", target_position_fraction=1.0, confidence=1.0,
            horizon_hours=24.0, expiry=ts0 + pd.Timedelta(hours=24),
            expected_edge_fraction=0.02)

    keep = [_long("BTCUSDT"), _long("ETHUSDT")]
    expiring = _intent("SOLUSDT", ts=ts0)          # horizon 4 h
    state = step("T5", config, aggregate(keep + [expiring], config, set(), as_of=ts0), ts0)
    assert set(state.positions) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    btc_qty = state.positions["BTCUSDT"]["quantity"]
    eth_qty = state.positions["ETHUSDT"]["quantity"]

    # 5 h plus tard : SOL a expiré, BTC/ETH sont inchangés.
    ts1 = ts0 + pd.Timedelta(hours=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts1))
    hw = dict(state.alpha_denominator_high_water)
    state = step("T5", config,
                 aggregate(keep + [expiring], config, set(), as_of=ts1,
                           denominator_high_water=hw), ts1)

    # SOL est bien sortie...
    assert "SOLUSDT" not in state.positions
    # ...et BTC/ETH n'ont PAS été redimensionnées par ricochet.
    assert state.positions["BTCUSDT"]["quantity"] == pytest.approx(btc_qty)
    assert state.positions["ETHUSDT"]["quantity"] == pytest.approx(eth_qty)
    # la sortie de SOL est comptée comme telle, pas comme du rebalancement
    assert state.cumulative_turnover_by_class.get(TURNOVER_MECHANICAL_RESIZE, 0.0) == 0.0


def test_5b_turnover_is_classified_signal_vs_mechanical(tmp_path, monkeypatch):
    """« Distinguer turnover de signal et turnover mécanique » : la mesure
    existe et les deux classes sont bien séparées."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))

    state = step("T5b", config, aggregate([_intent(ts=ts0)], config, set(), as_of=ts0), ts0)
    assert state.cumulative_turnover_by_class[TURNOVER_ENTRY] > 0

    # vrai changement de signal sur CET instrument : conviction divisée par 2
    ts1 = ts0 + pd.Timedelta(minutes=15)
    state = step("T5b", config,
                 aggregate([_intent(ts=ts1, frac=0.5)], config, set(), as_of=ts1), ts1)
    assert state.cumulative_turnover_by_class[TURNOVER_SIGNAL_RESIZE] > 0
    assert TURNOVER_MECHANICAL_RESIZE not in state.cumulative_turnover_by_class

    for o in state.orders:
        assert o["turnover_class"] in (TURNOVER_ENTRY, TURNOVER_SIGNAL_RESIZE)
        assert 0.0 <= o["no_trade_band_fraction"] <= 1.0


# ══════════════════════════════════════════════════════════════════════════
# 6. petit changement non rentable après coûts -> aucun ordre        (P0.3)
# ══════════════════════════════════════════════════════════════════════════

def test_6_band_is_derived_from_costs_not_invented():
    """band = coût aller-retour / edge. Aucune constante magique."""
    assert round_trip_cost_fraction() == pytest.approx(0.0014)     # 2 x (5 + 2) bps
    assert no_trade_band_fraction(0.006) == pytest.approx(0.0014 / 0.006)
    assert no_trade_band_fraction(0.0014) == pytest.approx(1.0)    # edge == coût -> rien
    assert no_trade_band_fraction(0.0007) == 1.0                   # edge < coût -> rien
    for degenerate in (None, 0.0, -0.01, float("nan")):
        assert no_trade_band_fraction(degenerate) == 1.0           # inconnu -> prudent


def test_6_small_unprofitable_target_change_produces_no_order(tmp_path, monkeypatch):
    """Un changement de cible de 2 % pour un edge de 200 bps : la bande vaut
    7 %, le mouvement ne paie pas son aller-retour -> aucun ordre."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))

    state = step("T6", config, aggregate([_intent(ts=ts0, frac=1.0)], config, set(), as_of=ts0), ts0)
    qty0 = state.positions["BTCUSDT"]["quantity"]
    n0 = len(state.orders)

    # même signal, cible mécaniquement rabotée de 2 % (plafond/overlay)
    ts1 = ts0 + pd.Timedelta(minutes=15)
    agg = aggregate([_intent(ts=ts0, frac=1.0)], config, set(), as_of=ts1)
    agg.target_notional["BTCUSDT"] *= 0.98
    state = step("T6", config, agg, ts1)

    assert len(state.orders) == n0                                   # aucun ordre
    assert state.positions["BTCUSDT"]["quantity"] == pytest.approx(qty0)
    assert state.suppressed_turnover_usd > 0


def test_6b_a_change_large_enough_to_pay_for_itself_does_execute(tmp_path, monkeypatch):
    """Symétrie indispensable : la bande ne doit pas tout figer."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))

    state = step("T6b", config, aggregate([_intent(ts=ts0, frac=1.0)], config, set(), as_of=ts0), ts0)
    n0 = len(state.orders)

    ts1 = ts0 + pd.Timedelta(minutes=15)
    agg = aggregate([_intent(ts=ts0, frac=1.0)], config, set(), as_of=ts1)
    agg.target_notional["BTCUSDT"] *= 0.50      # 50 % >> bande de 7 %
    state = step("T6b", config, agg, ts1)
    assert len(state.orders) == n0 + 1
    assert state.orders[-1]["turnover_class"] == TURNOVER_MECHANICAL_RESIZE


def test_6c_exit_is_never_blocked_by_the_band(tmp_path, monkeypatch):
    """Aucune bande ne doit pouvoir empêcher de sortir -- même avec un edge
    inconnu (bande maximale)."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    no_edge = PortfolioIntent(
        alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
        correlation_family="FAM1", timestamp=ts0, instrument="BTCUSDT",
        direction="LONG", target_position_fraction=1.0, confidence=1.0,
        horizon_hours=4.0, expiry=ts0 + pd.Timedelta(hours=4))
    state = step("T6c", config, aggregate([no_edge], config, set(), as_of=ts0), ts0)
    assert state.positions["BTCUSDT"]["quantity"] > 0

    ts1 = ts0 + pd.Timedelta(hours=5)     # intent expiré -> cible 0
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts1))
    state = step("T6c", config, aggregate([no_edge], config, set(), as_of=ts1), ts1)
    assert "BTCUSDT" not in state.positions


# ══════════════════════════════════════════════════════════════════════════
# 7. replay déterministe toujours valide                             (P0.3)
# ══════════════════════════════════════════════════════════════════════════

def test_7_deterministic_replay_still_holds_with_the_band(tmp_path, monkeypatch):
    """Deux process indépendants, mêmes entrées -> mêmes états, y compris les
    nouveaux dictionnaires de bande (cible acceptée, empreintes, convergence)."""
    ts0 = _ts("2026-09-01T00:00:00Z")
    config = _config()
    intents = [_intent("BTCUSDT", ts=ts0), _intent("ETHUSDT", ts=ts0, frac=0.6)]

    def run(root):
        monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", root)
        out = None
        for i in range(6):
            ts = ts0 + pd.Timedelta(minutes=15 * i)
            monkeypatch.setattr(portfolio_mod, "get_mark",
                                _mock_mark(100.0 + 0.3 * i, ts=ts))
            out = step("R", config, aggregate(intents, config, set(), as_of=ts), ts)
        return out

    a = run(tmp_path / "run_a")
    b = run(tmp_path / "run_b")
    assert a.positions == b.positions
    assert a.accepted_target_notional == b.accepted_target_notional
    assert a.accepted_intent_signature == b.accepted_intent_signature
    assert a.converging == b.converging
    assert a.cumulative_turnover_by_class == b.cumulative_turnover_by_class
    assert a.cumulative_turnover_usd == b.cumulative_turnover_usd
    assert [o["order_id"] for o in a.orders] == [o["order_id"] for o in b.orders]
    assert [e["equity"] for e in a.equity_curve] == [e["equity"] for e in b.equity_curve]


def test_7b_intent_signature_is_stable_across_processes():
    """L'empreinte ne doit dépendre ni du hash-seed ni de l'ordre des intents."""
    ts0 = _ts("2026-09-01T00:00:00Z")
    config = _config()
    a = aggregate([_intent("BTCUSDT", ts=ts0), _intent("ETHUSDT", ts=ts0)],
                  config, set(), as_of=ts0)
    b = aggregate([_intent("ETHUSDT", ts=ts0), _intent("BTCUSDT", ts=ts0)],
                  config, set(), as_of=ts0)
    assert a.intent_signature == b.intent_signature


# ══════════════════════════════════════════════════════════════════════════
# P0.4 — infrastructure
# ══════════════════════════════════════════════════════════════════════════

def test_p04_orphan_tmp_sweep_is_dry_run_by_default(tmp_path):
    from src.institutional.data.atomic_parquet import sweep_orphan_tmp
    old = tmp_path / ".X_1h_enriched.parquet.deadbeef.tmp"
    old.write_bytes(b"0" * 4096)
    recent = tmp_path / ".Y_1h_enriched.parquet.cafe.tmp"
    recent.write_bytes(b"0" * 4096)
    import os
    os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))

    r = sweep_orphan_tmp(tmp_path)
    assert r["dry_run"] is True and r["deleted"] == []
    assert r["n_orphans"] == 1                 # le récent n'est jamais un orphelin
    assert old.exists() and recent.exists()    # RIEN n'a été supprimé

    r = sweep_orphan_tmp(tmp_path, delete=True)
    assert r["n_orphans"] == 1 and len(r["deleted"]) == 1
    assert not old.exists() and recent.exists()


def test_p04_atomic_write_cleans_its_tmp_on_sigterm(tmp_path):
    """La fuite corrigée : un SIGTERM (ce qu'envoie `systemctl stop` et le
    nouveau helper de l'API) ne doit plus laisser de .tmp au sol."""
    target = tmp_path / "big.parquet"
    code = f'''
import signal, sys, time
sys.path.insert(0, {str(Path(__file__).parents[1])!r})
import pandas as pd
from src.institutional.data.atomic_parquet import _install_signal_handlers, _TMP_IN_FLIGHT
from pathlib import Path
_install_signal_handlers()
tmp = Path({str(target)!r}).with_name(".big.parquet.deadbeef.tmp")
tmp.write_bytes(b"x" * 1024)
_TMP_IN_FLIGHT.add(tmp)
print("READY", flush=True)
time.sleep(30)
'''
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "READY"
    proc.terminate()
    proc.wait(timeout=15)
    assert not (tmp_path / ".big.parquet.deadbeef.tmp").exists()


def test_p04_watchdog_threshold_deadlock_is_resolved_and_detector_stays_armed(monkeypatch):
    """Le plancher disque du collecteur (20 Go) était EXACTEMENT le seuil
    d'arrêt du watchdog (20 Go) : état absorbant, jamais de redémarrage.
    Corrigé le 2026-09-05 (plancher 15 dans l'unit systemd). Ce test attest
    (a) que la configuration courante n'a plus de verrouillage, (b) que
    l'hystérésis est réelle, (c) que le détecteur attrape toujours une
    réintroduction du bug."""
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import importlib
    wd = importlib.import_module("global_disk_watchdog")
    assert wd.detect_threshold_deadlocks() == []                    # (a)
    assert wd.RESUME_FREE_GB > wd.CRITICAL_FREE_GB                   # (b)
    floor = wd.SERVICE_MIN_FREE_GB["futur-microstructure-reduced.service"]
    assert wd.EMERGENCY_FREE_GB < floor < wd.CRITICAL_FREE_GB        # entre les deux seuils
    # (c) réintroduire le bug de configuration : le détecteur doit le voir
    monkeypatch.setitem(wd.SERVICE_MIN_FREE_GB, "futur-microstructure-reduced.service",
                        wd.CRITICAL_FREE_GB)
    dl = wd.detect_threshold_deadlocks()
    assert len(dl) == 1 and dl[0]["service"] == "futur-microstructure-reduced.service"


def test_p03_converging_flag_does_not_leak_and_keep_the_band_disabled(tmp_path, monkeypatch):
    """Régression : une acceptation de cible dont le delta était déjà nul
    laissait `converging=True` pour toujours, ce qui neutralisait la bande sur
    cet instrument (la dérive de prix repassait librement au cycle suivant)."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    intents = [_intent(ts=ts0)]

    state = step("TC", config, aggregate(intents, config, set(), as_of=ts0), ts0)
    # cycle identique : la cible est ré-acceptée, mais le delta est nul
    ts1 = ts0 + pd.Timedelta(minutes=15)
    state = step("TC", config, aggregate(intents, config, set(), as_of=ts1), ts1)
    assert state.converging["BTCUSDT"] is False

    # dérive de prix de 1 % : sous la bande de 7 %, donc aucun ordre
    n = len(state.orders)
    ts2 = ts1 + pd.Timedelta(minutes=15)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(101.0, ts=ts2))
    state = step("TC", config, aggregate(intents, config, set(), as_of=ts2), ts2)
    assert len(state.orders) == n
