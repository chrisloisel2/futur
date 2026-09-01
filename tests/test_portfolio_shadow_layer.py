"""tests/test_portfolio_shadow_layer.py — PORTFOLIO_SHADOW_LAYER, phase
ECONOMIC TRUTH (mark-to-market). Covers: intent adapters, correlation dedup,
budget/exposure caps, the WHALE_LSR_SCREEN gate, and the MTM engine itself
(item 17 of the mission: long up/down, short down, fees-once, funding,
realize-on-close, equity invariant, no-double-count, stale-mark detection,
restart reproducibility, no-future-price).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.live_alpha_lab.gate import active_screen_symbols, apply_screen
from src.institutional.live_alpha_lab.intents import PortfolioIntent, build_intents
from src.institutional.live_alpha_lab.marks import MarkQuote
import src.institutional.live_alpha_lab.portfolio as portfolio_mod
from src.institutional.live_alpha_lab.portfolio import aggregate, load_state, step
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def _intent(alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
           correlation_family="FAM1", instrument="BTCUSDT", direction="LONG",
           frac=1.0, confidence=1.0, multi_leg=False, leg_b=None, ts=None):
    ts = ts or _ts("2026-09-01T00:00:00Z")
    return PortfolioIntent(
        alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
        correlation_family=correlation_family, timestamp=ts, instrument=instrument,
        direction=direction, target_position_fraction=frac, confidence=confidence,
        horizon_hours=4.0, expiry=ts + pd.Timedelta(hours=4),
        multi_leg=multi_leg, leg_instrument_b=leg_b,
    )


def _mock_mark(price, source="TEST_MOCK", age_ms=0.0, ts=None):
    def fn(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=price, mark_source=source,
                         mark_timestamp=ts or (as_of or _ts("2026-09-01T00:00:00Z")), mark_age_ms=age_ms)
    return fn


# ── adapters (inchangé) ──────────────────────────────────────────────────

def test_build_intents_liq_cascade():
    df = pd.DataFrame([{"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"}])
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
            "correlation_family": "LIQ_CASCADE_DETECTOR"}
    out = build_intents("LIQ_CASCADE_REPEAT_V1", entry, df)
    assert len(out) == 1 and out[0].direction == "LONG" and out[0].target_position_fraction == 1.0


def test_build_intents_unknown_alpha_raises():
    with pytest.raises(KeyError):
        build_intents("NOT_A_REAL_ALPHA", {}, pd.DataFrame([{"x": 1}]))


def test_build_intents_gate_alpha_returns_empty_not_error():
    assert build_intents("WHALE_LSR_SCREEN_V1", {}, pd.DataFrame([{"x": 1}])) == []


def test_build_intents_cross_sectional_equal_weights_real_basket_size():
    df = pd.DataFrame([
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"},
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "ETHUSDT"},
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "SOLUSDT"},
        {"event_time": _ts("2026-09-08T00:00:00Z"), "symbol": "BTCUSDT"},
    ])
    entry = {"family": "cross_sectional", "risk_bucket": "CROSS_SECTIONAL_FAMILY",
            "correlation_family": "CROSS_SECTIONAL_XSMOM"}
    out = build_intents("CROSS_SECTIONAL_MOMENTUM_LIVE_V1", entry, df)
    week1 = [i for i in out if i.timestamp == _ts("2026-09-01T00:00:00Z")]
    week2 = [i for i in out if i.timestamp == _ts("2026-09-08T00:00:00Z")]
    assert all(i.target_position_fraction == pytest.approx(1.0 / 3) for i in week1)
    assert week2[0].target_position_fraction == 1.0


def test_build_intents_cross_sectional_v2_uses_same_adapter():
    df = pd.DataFrame([{"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"}])
    entry = {"family": "cross_sectional", "risk_bucket": "CROSS_SECTIONAL_FAMILY",
            "correlation_family": "CROSS_SECTIONAL_XSMOM"}
    out = build_intents("CROSS_SECTIONAL_MOMENTUM_LIVE_V2", entry, df)
    assert len(out) == 1 and out[0].target_position_fraction == 1.0


# ── gate (inchangé) ─────────────────────────────────────────────────────

def test_screen_blocks_long_on_screened_symbol():
    assert apply_screen(1.0, "BTCUSDT", "LONG", {"BTCUSDT"}) == 0.0


def test_active_screen_symbols_respects_lookback_window():
    df = pd.DataFrame([
        {"timestamp": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT", "screen_flag": True},
        {"timestamp": _ts("2026-08-01T00:00:00Z"), "symbol": "ETHUSDT", "screen_flag": True},
    ])
    assert active_screen_symbols(df, _ts("2026-09-01T02:00:00Z"), lookback_hours=24.0) == {"BTCUSDT"}


# ── dedup / aggregate ─────────────────────────────────────────────────────

def test_aggregate_dedups_correlated_intents_same_instrument():
    intents = [_intent(alpha_id="A1", correlation_family="LIQ_CASCADE_DETECTOR", frac=0.5),
              _intent(alpha_id="A2", correlation_family="LIQ_CASCADE_DETECTOR", frac=1.0)]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=1.0, max_per_asset_fraction=1.0)
    agg = aggregate(intents, config, screened_symbols=set(), as_of=_ts("2026-09-01T00:00:00Z"))
    assert len(agg.target_notional) == 1
    assert agg.owner["BTCUSDT"] == "A2"
    # item 6 : le ledger brut garde les DEUX intents, même si un seul "gagne"
    assert len(agg.raw_intents_by_instrument["BTCUSDT"]) == 2


def test_aggregate_respects_per_asset_cap():
    agg = aggregate([_intent(frac=1.0)],
                    PortfolioConfig(name="TEST", capital_eur=100_000,
                                    family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                                    max_gross_exposure_fraction=1.0, max_per_asset_fraction=0.05),
                    screened_symbols=set(), as_of=_ts("2026-09-01T00:00:00Z"))
    assert abs(agg.target_notional["BTCUSDT"]) <= 100_000 * 0.05 + 1e-6


def test_aggregate_screen_zeroes_out_target():
    agg = aggregate([_intent(instrument="BTCUSDT", frac=1.0)],
                    PortfolioConfig(name="TEST", capital_eur=100_000,
                                    family_budget_fraction={"LIQUIDATION_FAMILY": 1.0}),
                    screened_symbols={"BTCUSDT"}, as_of=_ts("2026-09-01T00:00:00Z"))
    assert "BTCUSDT" not in agg.target_notional or agg.target_notional["BTCUSDT"] == 0


def test_aggregate_excludes_expired_intents():
    """Régression : PortfolioIntent.expiry existait mais n'était jamais
    vérifié -- une vieille décision 4h-horizon continuait à peser sur la
    position indéfiniment. Un intent dont expiry <= as_of ne doit produire
    AUCUN target."""
    ts_old = _ts("2026-09-01T00:00:00Z")   # horizon 4h -> expire à 04:00Z
    intent_expired = _intent(instrument="BTCUSDT", frac=1.0, ts=ts_old)
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0})
    as_of_after_expiry = ts_old + pd.Timedelta(hours=5)   # 1h après l'expiry
    agg = aggregate([intent_expired], config, screened_symbols=set(), as_of=as_of_after_expiry)
    assert "BTCUSDT" not in agg.target_notional or agg.target_notional["BTCUSDT"] == 0
    # mais reste tracé pour la traçabilité (item 6) même expiré
    assert len(agg.raw_intents_by_instrument["BTCUSDT"]) == 1


def test_aggregate_keeps_not_yet_expired_intents():
    ts = _ts("2026-09-01T00:00:00Z")
    intent = _intent(instrument="BTCUSDT", frac=1.0, ts=ts)   # expiry = ts+4h
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0})
    as_of_before_expiry = ts + pd.Timedelta(hours=1)
    agg = aggregate([intent], config, screened_symbols=set(), as_of=as_of_before_expiry)
    assert agg.target_notional["BTCUSDT"] != 0


def test_aggregate_multi_leg_produces_two_opposite_instruments():
    agg = aggregate(
        [_intent(instrument="BTCUSDT_QUARTERLY", direction="LONG", frac=1.0, multi_leg=True,
                leg_b="BTCUSDT_PERP", risk_bucket="RELATIVE_VALUE_FAMILY",
                correlation_family="CALENDAR_BASIS_CURVE")],
        PortfolioConfig(name="TEST", capital_eur=100_000,
                        family_budget_fraction={"RELATIVE_VALUE_FAMILY": 1.0},
                        max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0),
        screened_symbols=set(), as_of=_ts("2026-09-01T00:00:00Z"))
    assert agg.target_notional["BTCUSDT_QUARTERLY"] > 0
    assert agg.target_notional["BTCUSDT_PERP"] < 0


# ── MTM engine (item 17) ────────────────────────────────────────────────

def _config():
    return PortfolioConfig(name="TEST_MTM", capital_eur=100_000,
                           family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                           max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)


def test_mtm_long_price_up_gives_positive_unrealized(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")

    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    step("T", config, agg, ts0)

    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(110.0, ts=ts1))   # +10%
    agg2 = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts1)   # même intent -> pas de nouveau trade
    state = step("T", config, agg2, ts1)
    assert state.equity_curve[-1]["unrealized_pnl"] > 0


def test_mtm_long_price_down_gives_negative_unrealized(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    step("T", config, agg, ts0)

    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(90.0, ts=ts1))
    state = step("T", config, agg, ts1)
    assert state.equity_curve[-1]["unrealized_pnl"] < 0


def test_mtm_short_price_down_gives_positive_unrealized(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, direction="SHORT", ts=ts0)], config, set(), as_of=ts0)
    step("T", config, agg, ts0)

    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(80.0, ts=ts1))
    state = step("T", config, agg, ts1)
    assert state.equity_curve[-1]["unrealized_pnl"] > 0


def test_mtm_fees_charged_once_per_delta_not_per_step(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    s1 = step("T", config, agg, ts0)
    fees_after_open = s1.cumulative_fees_usd
    assert fees_after_open > 0

    ts1 = ts0 + pd.Timedelta(minutes=5)
    s2 = step("T", config, agg, ts1)   # même target -> pas de nouveau delta -> pas de nouveaux frais
    assert s2.cumulative_fees_usd == fees_after_open


def test_mtm_position_close_realizes_pnl(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg_open = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    step("T", config, agg_open, ts0)

    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(120.0, ts=ts1))
    agg_close = aggregate([], config, set(), as_of=ts1)   # plus aucun intent -> target=0 -> ferme la position
    state = step("T", config, agg_close, ts1)
    pos = list(state.positions.values())
    assert all(abs(p["quantity"]) < 1e-9 for p in pos)   # bien fermé
    assert state.equity_curve[-1]["realized_pnl"] > 0    # PnL réalisé positif (acheté 100, vendu 120)
    assert state.equity_curve[-1]["unrealized_pnl"] == 0


def test_mtm_realized_plus_unrealized_equity_invariant(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg, ts0)
    snap = state.equity_curve[-1]
    expected_equity = (config.capital_eur + snap["realized_pnl"] + snap["unrealized_pnl"]
                       - snap["fees"] + snap["funding"])
    assert snap["equity"] == pytest.approx(expected_equity)


def test_mtm_multiple_alphas_same_asset_no_double_count(tmp_path, monkeypatch):
    """Deux alphas corrélés visant BTCUSDT : dedup garde UN seul intent
    gagnant -> une seule position, pas deux positions sommées."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    intents = [_intent(alpha_id="A1", correlation_family="FAM1", frac=0.5, ts=ts0),
              _intent(alpha_id="A2", correlation_family="FAM1", frac=1.0, ts=ts0)]
    agg = aggregate(intents, config, set(), as_of=ts0)
    state = step("T", config, agg, ts0)
    assert len(state.positions) == 1
    assert state.equity_curve[-1]["n_positions"] == 1


def test_mtm_stale_mark_sets_degraded_status(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, age_ms=999_999_999, ts=ts0))  # très vieux
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg, ts0)
    assert state.equity_curve[-1]["status"] == "DEGRADED"


def test_mtm_no_mark_available_skips_trade_not_hallucinate_price(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", lambda instrument, as_of=None: None)
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg, ts0)
    assert state.cumulative_fees_usd == 0   # rien tradé, aucun prix halluciné
    assert "BTCUSDT" in state.equity_curve[-1]["skipped_no_mark"]


def test_mtm_restart_reproduces_same_state(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    step("T", config, agg, ts0)

    reloaded = load_state("T", config.capital_eur)   # simule un "restart" -- relit juste le fichier
    fresh = load_state("T", config.capital_eur)
    assert reloaded.positions == fresh.positions
    assert reloaded.cumulative_fees_usd == fresh.cumulative_fees_usd
    assert reloaded.peak_equity == fresh.peak_equity


def test_expiry_closes_exactly_once_with_correct_pnl_fee_and_no_double_close_on_restart(tmp_path, monkeypatch):
    """Item 3 du mandat "PHASE FORWARD TRUTH V2" -- audit expiry/close bout en
    bout : expiry -> target recalculé à 0 -> delta généré -> exécution shadow
    à un prix réel -> frais de sortie -> realized PnL -> position fermée,
    EXACTEMENT une fois, jamais une deuxième fois après un "restart" (relire
    l'état persistée et rejouer le même step ne doit produire aucun nouveau
    delta -- l'idempotence déjà garantie pour l'ouverture doit aussi tenir à
    la fermeture)."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts_signal = _ts("2026-09-01T00:00:00Z")   # horizon 4h -> expiry 04:00Z

    # 1) ouverture : le signal est encore valide
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts_signal))
    agg_open = aggregate([_intent(frac=1.0, ts=ts_signal)], config, set(), as_of=ts_signal)
    step("T", config, agg_open, ts_signal)
    fees_after_open = load_state("T", config.capital_eur).cumulative_fees_usd
    assert fees_after_open > 0

    # 2) le signal EXPIRE (5h plus tard, > horizon 4h) ET le prix a bougé --
    # aggregate() avec le MÊME as_of que le step doit exclure l'intent expiré,
    # target retombe à 0 -> step() doit fermer la position à CE prix, pas au
    # prix figé à l'ouverture (no-future-price mais aussi no-stale-price).
    ts_after_expiry = ts_signal + pd.Timedelta(hours=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(115.0, ts=ts_after_expiry))
    agg_expired = aggregate([_intent(frac=1.0, ts=ts_signal)], config, set(), as_of=ts_after_expiry)
    assert "BTCUSDT" not in agg_expired.target_notional or agg_expired.target_notional["BTCUSDT"] == 0
    state_after_close = step("T", config, agg_expired, ts_after_expiry)

    assert all(abs(p["quantity"]) < 1e-9 for p in state_after_close.positions.values())   # fermée
    realized_after_close = state_after_close.cumulative_realized_pnl
    assert realized_after_close > 0   # acheté 100, fermé ~115 -> gain réalisé positif
    fees_after_close = state_after_close.cumulative_fees_usd
    assert fees_after_close > fees_after_open   # frais de sortie facturés UNE fois, en plus des frais d'entrée

    # 3) "restart" : recharger l'état (simule un redémarrage du process) et
    # REJOUER le même step (même intents expirés, même as_of, même marché) --
    # ne doit produire NI un deuxième close, NI un deuxième frais, NI un
    # deuxième PnL réalisé.
    reloaded = load_state("T", config.capital_eur)
    assert reloaded.cumulative_realized_pnl == realized_after_close
    assert reloaded.cumulative_fees_usd == fees_after_close

    agg_replay = aggregate([_intent(frac=1.0, ts=ts_signal)], config, set(), as_of=ts_after_expiry)
    state_replayed = step("T", config, agg_replay, ts_after_expiry)
    assert state_replayed.cumulative_realized_pnl == realized_after_close   # inchangé -- pas de double comptage
    assert state_replayed.cumulative_fees_usd == fees_after_close           # inchangé -- pas de deuxième frais


def test_drawdown_peak_current_and_persistence_after_restart(tmp_path, monkeypatch):
    """Item 5 du mandat : equity monte -> baisse -> remonte. Vérifie
    peak_equity, drawdown courant à chaque étape, ET que peak_equity/drawdown
    survivent à un restart (relecture de l'état) sans être recalculés depuis
    zéro à partir du marché courant."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")

    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    s1 = step("T", config, agg, ts0)
    peak_after_open = s1.peak_equity
    dd_after_open = s1.equity_curve[-1]["drawdown"]
    # le pic reste le capital de départ (jamais mis à jour tant que l'équity
    # ne le dépasse pas) -- ET l'ouverture coûte déjà frais+slippage, donc le
    # DD au tout premier snapshot est LÉGÈREMENT négatif, pas nul. C'est le
    # comportement correct (le "pic" avant tout trade = le capital pristine),
    # pas une erreur d'arrondi à masquer avec pytest.approx(0.0).
    assert peak_after_open == pytest.approx(config.capital_eur)
    assert dd_after_open < 0
    assert dd_after_open > -0.01   # petit -- juste frais+slippage d'une seule ouverture, pas un vrai DD

    # monte : nouveau pic, DD toujours 0
    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(120.0, ts=ts1))
    s2 = step("T", config, agg, ts1)
    assert s2.peak_equity > peak_after_open
    assert s2.equity_curve[-1]["drawdown"] == pytest.approx(0.0)
    peak_at_top = s2.peak_equity

    # baisse : le pic ne redescend JAMAIS, le DD devient négatif
    ts2 = ts1 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(90.0, ts=ts2))
    s3 = step("T", config, agg, ts2)
    assert s3.peak_equity == peak_at_top   # inchangé -- le pic ne redescend jamais
    assert s3.equity_curve[-1]["drawdown"] < 0
    dd_at_bottom = s3.equity_curve[-1]["drawdown"]

    # remonte partiellement, mais pas au-dessus du pic précédent -> toujours en drawdown, DD moins négatif
    ts3 = ts2 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(105.0, ts=ts3))
    s4 = step("T", config, agg, ts3)
    assert s4.peak_equity == peak_at_top
    assert s4.equity_curve[-1]["drawdown"] < 0
    assert s4.equity_curve[-1]["drawdown"] > dd_at_bottom   # moins négatif, pas encore récupéré

    # maxDD sur toute la courbe = le pire drawdown observé, pas le dernier
    max_dd_observed = min(pt["drawdown"] for pt in s4.equity_curve)
    assert max_dd_observed == pytest.approx(dd_at_bottom)

    # restart : peak_equity et l'historique de la courbe survivent tels quels
    reloaded = load_state("T", config.capital_eur)
    assert reloaded.peak_equity == peak_at_top
    assert len(reloaded.equity_curve) == len(s4.equity_curve)
    assert reloaded.equity_curve[-1]["drawdown"] == s4.equity_curve[-1]["drawdown"]


def test_mtm_no_future_price_used_between_steps(tmp_path, monkeypatch):
    """Le prix utilisé au step ts0 ne doit JAMAIS être celui fourni pour ts1
    -- vérifié en donnant des mocks strictement différents par timestamp et
    en s'assurant que le PnL réalisé au step ts0 (s'il y avait une clôture)
    ne reflète pas le prix ts1."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    ts1 = ts0 + pd.Timedelta(minutes=5)

    prices_by_ts = {ts0: 100.0, ts1: 200.0}

    def mark_fn(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=prices_by_ts[as_of], mark_source="TEST",
                         mark_timestamp=as_of, mark_age_ms=0.0)

    monkeypatch.setattr(portfolio_mod, "get_mark", mark_fn)
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state0 = step("T", config, agg, ts0)
    # à ts0, seul le prix ts0 (100) doit avoir été utilisé -> entry_price == 100-ish (+slippage)
    pos = list(state0.positions.values())[0]
    assert pos["entry_price"] < 105   # pas 200 (le prix futur ts1)


def test_latest_funding_rate_finds_historical_rate_despite_hundreds_of_newer_files(tmp_path, monkeypatch):
    """Même bug/même fix que marks.py::_from_derivatives_raw (P0.1) : cette
    fonction avait sa PROPRE copie du heuristique cassé "derniers 4 fichiers"
    -- corrigée pour partager marks.eligible_files_for_as_of. Sans le fix,
    un as_of historique avec beaucoup de fichiers plus récents que lui
    retournait silencieusement None -> funding jamais accru pour ce step
    (sous-comptage silencieux des coûts, pas une exception)."""
    import src.institutional.live_alpha_lab.marks as marks_mod
    monkeypatch.setattr(marks_mod, "DERIVATIVES_RAW", tmp_path / "derivatives_raw")
    base = (marks_mod.DERIVATIVES_RAW / "exchange=binance" / "market=usdm" /
           "stream=open_interest" / "symbol=BTCUSDT")
    old_as_of = pd.Timestamp("2026-09-01T00:10:00+00:00")

    def write(date_str, seq, ts, funding_rate):
        d = base / f"date={date_str}"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"timestamp": [int(pd.Timestamp(ts).value // 1_000_000)],
                     "funding_rate": [funding_rate]}).to_parquet(d / f"part-{seq:06d}.parquet")

    write("2026-09-01", 0, "2026-09-01T00:05:00", 0.0001)
    for i in range(1, 200):
        ts = pd.Timestamp("2026-09-01T00:05:00") + pd.Timedelta(minutes=i)
        write("2026-09-01", i, ts.isoformat(), 0.0001 + i * 1e-7)

    rate = portfolio_mod._latest_funding_rate("BTCUSDT", old_as_of)
    # doit trouver le DERNIER rate <= as_of (00:10:00, i=5), pas le premier
    # fichier du jour ni un fichier postérieur à as_of.
    assert rate == pytest.approx(0.0001 + 5 * 1e-7)


def test_two_portfolios_identical_config_and_intents_produce_byte_identical_equity(tmp_path, monkeypatch):
    """Régression directe du bug P1_EQUAL_RISK vs P1_CONTROL (phase CLOSE THE
    EXECUTION LOOP, P0.1) : root-cause était get_mark() non-pur en (instrument,
    as_of) pour la source REST (elle ignorait as_of et retournait le prix
    "maintenant" -- deux portefeuilles traités séquentiellement dans le même
    run pouvaient donc recevoir deux prix différents pour le "même" as_of,
    faisant diverger executed_delta puis entry_price/unrealized_pnl/
    realized_pnl malgré une config et des intents strictement identiques).

    Ici get_mark est un mock strictement pur en (instrument, as_of) -- comme
    il doit toujours l'être après le fix de marks.py::_from_derivatives_raw.
    Deux portefeuilles à la config identique (mêmes intents, mêmes as_of,
    traités l'un après l'autre comme dans run_portfolio_shadow.py) DOIVENT
    produire des equity_curve strictement identiques."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config_a = _config()
    config_b = PortfolioConfig(**{**config_a.__dict__, "name": "TEST_MTM_B"})
    ts0 = _ts("2026-09-01T00:00:00Z")
    ts1 = ts0 + pd.Timedelta(minutes=5)
    ts2 = ts1 + pd.Timedelta(minutes=5)

    prices_by_ts_instrument = {
        (ts0, "BTCUSDT"): 100.0, (ts0, "ETHUSDT"): 50.0,
        (ts1, "BTCUSDT"): 103.0, (ts1, "ETHUSDT"): 48.0,
        (ts2, "BTCUSDT"): 97.0, (ts2, "ETHUSDT"): 52.0,
    }

    def pure_mark_fn(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=prices_by_ts_instrument[(as_of, instrument)],
                         mark_source="TEST_PURE", mark_timestamp=as_of, mark_age_ms=0.0)

    monkeypatch.setattr(portfolio_mod, "get_mark", pure_mark_fn)

    intents_by_ts = {
        ts0: [_intent(instrument="BTCUSDT", frac=0.6, ts=ts0), _intent(instrument="ETHUSDT", frac=0.4, ts=ts0)],
        ts1: [_intent(instrument="BTCUSDT", frac=0.3, ts=ts1), _intent(instrument="ETHUSDT", frac=0.7, ts=ts1)],
        ts2: [_intent(instrument="BTCUSDT", frac=0.0, ts=ts2, direction="SHORT")],
    }

    curves = {}
    for name, config in (("T1", config_a), ("T2", config_b)):
        for ts in (ts0, ts1, ts2):
            agg = aggregate(intents_by_ts[ts], config, set(), as_of=ts)
            state = step(name, config, agg, ts)
        curves[name] = state.equity_curve

    assert len(curves["T1"]) == len(curves["T2"]) == 3
    for row_a, row_b in zip(curves["T1"], curves["T2"]):
        for field in ("n_positions", "gross_exposure", "net_exposure", "realized_pnl",
                      "unrealized_pnl", "fees", "funding", "equity", "drawdown", "status"):
            assert row_a[field] == row_b[field], f"{field}: {row_a[field]} != {row_b[field]}"


# ── P0.2 (phase CLOSE THE EXECUTION LOOP) : ShadowExecutionAdapter, ordres,
# fills partiels réels ──────────────────────────────────────────────────

def _capped_mark(price, liquidity_notional, ts=None, source="TEST_MOCK"):
    from src.institutional.live_alpha_lab.marks import MarkQuote

    def fn(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=price, mark_source=source,
                         mark_timestamp=ts or (as_of or _ts("2026-09-01T00:00:00Z")),
                         mark_age_ms=0.0, liquidity_notional=liquidity_notional)
    return fn


def test_submit_order_full_fill_when_no_liquidity_cap():
    from src.institutional.live_alpha_lab.execution_adapter import ShadowExecutionAdapter
    from src.institutional.live_alpha_lab.marks import MarkQuote
    adapter = ShadowExecutionAdapter()
    ts0 = _ts("2026-09-01T00:00:00Z")
    mark = MarkQuote(instrument="BTCUSDT", price=100.0, mark_source="TEST",
                     mark_timestamp=ts0, mark_age_ms=0.0, liquidity_notional=None)
    order, fill = adapter.submit_order(
        portfolio_id="T", alpha_id="A1", intent_id="I1", signal_id="S1",
        symbol="BTCUSDT", delta_quantity=10.0, as_of=ts0,
        timestamp_decision=ts0.isoformat(), mark=mark,
    )
    assert order.status == "FILLED"
    assert order.filled_quantity == pytest.approx(10.0)
    assert order.remaining_quantity == pytest.approx(0.0)
    assert fill is not None and fill.quantity == pytest.approx(10.0)


def test_submit_order_partial_fill_when_liquidity_capped():
    from src.institutional.live_alpha_lab.execution_adapter import ShadowExecutionAdapter
    from src.institutional.live_alpha_lab.marks import MarkQuote
    adapter = ShadowExecutionAdapter()
    ts0 = _ts("2026-09-01T00:00:00Z")
    # liquidity_notional=10_000 -> plafond = 0.002 * 10_000 / 100 = 0.2 unité,
    # bien en-dessous des 10 unités demandées.
    mark = MarkQuote(instrument="TIAUSDT", price=100.0, mark_source="TEST",
                     mark_timestamp=ts0, mark_age_ms=0.0, liquidity_notional=10_000.0)
    order, fill = adapter.submit_order(
        portfolio_id="T", alpha_id="A1", intent_id="I1", signal_id="S1",
        symbol="TIAUSDT", delta_quantity=10.0, as_of=ts0,
        timestamp_decision=ts0.isoformat(), mark=mark,
    )
    assert order.status == "PARTIALLY_FILLED"
    assert 0 < order.filled_quantity < 10.0
    assert order.remaining_quantity == pytest.approx(10.0 - order.filled_quantity)
    assert order.requested_quantity == pytest.approx(10.0)
    assert fill is not None
    assert fill.quantity == pytest.approx(order.filled_quantity)


def test_step_position_reflects_partial_fill_not_full_requested_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _capped_mark(100.0, liquidity_notional=10_000.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)   # target notional = 100_000
    state = step("T", config, agg, ts0)

    pos = list(state.positions.values())[0]
    assert 0 < pos["quantity"] < 1000.0   # 1000 = target_notional/price si fill complet
    assert len(state.orders) == 1
    assert state.orders[0]["status"] == "PARTIALLY_FILLED"
    assert len(state.fills) == 1


def test_multiple_partial_fills_across_steps_converge_to_target_no_double_count(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    # plafond = 0.002 * 5_000_000 / 100 = 100 unités/step ; target = 1000 unités -> ~10 steps
    mark_fn = _capped_mark(100.0, liquidity_notional=5_000_000.0, ts=ts0)
    monkeypatch.setattr(portfolio_mod, "get_mark", mark_fn)
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)   # target = 1000 unités

    state = None
    for _ in range(40):   # largement assez de steps pour converger au plafond
        state = step("T", config, agg, ts0)
        pos = list(state.positions.values())[0]
        if pos["quantity"] >= 1000.0 - 1e-6:
            break

    pos = list(state.positions.values())[0]
    assert pos["quantity"] == pytest.approx(1000.0, abs=1e-6)   # jamais dépassé -- no double count
    assert sum(1 for o in state.orders if o["status"] in ("FILLED", "PARTIALLY_FILLED")) >= 2
    # frais cohérents avec le notional RÉELLEMENT exécuté, pas le notional demandé à chaque step
    total_fee_from_orders = sum(o["fee_amount"] for o in state.orders)
    assert state.cumulative_fees_usd == pytest.approx(total_fee_from_orders)


def test_partial_fill_on_close_reduces_position_and_realizes_proportional_pnl(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    # ouverture complète, non plafonnée
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg_open = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg_open, ts0)
    pos = list(state.positions.values())[0]
    opened_qty = pos["quantity"]
    assert opened_qty == pytest.approx(1000.0)

    # clôture visée (target=0) mais plafonnée par la liquidité -> partielle
    ts1 = ts0 + pd.Timedelta(minutes=5)
    monkeypatch.setattr(portfolio_mod, "get_mark", _capped_mark(110.0, liquidity_notional=10_000.0, ts=ts1))
    agg_close = aggregate([_intent(frac=0.0, ts=ts0, direction="LONG")], config, set(), as_of=ts1)
    state = step("T", config, agg_close, ts1)

    pos = list(state.positions.values())[0]
    assert 0 < pos["quantity"] < opened_qty   # partiellement clôturée, pas totalement
    assert state.cumulative_realized_pnl > 0   # prix monté (100->110) sur la portion clôturée -> gain
    assert state.orders[-1]["status"] == "PARTIALLY_FILLED"


def test_restart_between_partial_fills_no_double_fill_no_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    mark_fn = _capped_mark(100.0, liquidity_notional=10_000.0, ts=ts0)
    monkeypatch.setattr(portfolio_mod, "get_mark", mark_fn)
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)   # target = 1000 unités

    state1 = step("T", config, agg, ts0)
    qty_after_1 = list(state1.positions.values())[0]["quantity"]
    n_orders_after_1 = len(state1.orders)

    # "restart" : recharge l'état depuis disque (comme le ferait un nouveau process)
    reloaded = load_state("T", config.capital_eur)
    assert reloaded.positions == state1.positions
    assert len(reloaded.orders) == n_orders_after_1

    state2 = step("T", config, agg, ts0)   # même target, même as_of, après "restart"
    qty_after_2 = list(state2.positions.values())[0]["quantity"]
    assert qty_after_2 > qty_after_1   # a progressé, pas rejoué le même fill
    assert len(state2.orders) == n_orders_after_1 + 1   # un seul nouvel ordre, pas de duplication


def test_orders_and_fills_have_full_schema_and_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(alpha_id="A1", frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg, ts0)

    assert len(state.orders) == 1
    o = state.orders[0]
    for field in ("order_id", "intent_id", "signal_id", "alpha_id", "portfolio_id",
                 "timestamp_decision", "timestamp_submit", "timestamp_fill", "symbol", "side",
                 "requested_quantity", "filled_quantity", "remaining_quantity",
                 "requested_notional", "fill_price", "mark_price_at_decision",
                 "spread_bps", "slippage_bps", "fee_bps", "fee_amount", "status"):
        assert field in o, f"champ manquant dans ShadowOrder : {field}"
    assert o["status"] in ("SUBMITTED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED", "EXPIRED")
    assert o["alpha_id"] == "A1"
    assert o["portfolio_id"] == "T"

    assert len(state.fills) == 1
    f = state.fills[0]
    for field in ("fill_id", "order_id", "timestamp", "symbol", "quantity",
                 "fill_price", "fee_usd", "mark_source", "mark_stale"):
        assert field in f, f"champ manquant dans ShadowFill : {field}"
    assert f["order_id"] == o["order_id"]

    # persisté sur disque, relisible après "restart"
    reloaded = load_state("T", config.capital_eur)
    assert reloaded.orders == state.orders
    assert reloaded.fills == state.fills


def test_step_execution_is_the_sole_path_no_bypass(tmp_path, monkeypatch):
    """P0.2 : step() ne doit JAMAIS mettre à jour une position sans passer
    par execution_adapter.submit_order() -- vérifié en fournissant un
    adapter custom qui refuse tout fill (retourne toujours filled_quantity=0)
    et en s'assurant qu'aucune position n'apparaît malgré un delta demandé
    non-nul."""
    from src.institutional.live_alpha_lab.execution_adapter import ExecutionAdapter
    from src.institutional.live_alpha_lab.orders import ShadowOrder

    class RefuseAllAdapter(ExecutionAdapter):
        def submit_order(self, **kwargs):
            order = ShadowOrder(
                order_id="refused", intent_id="i", signal_id="s",
                alpha_id=kwargs["alpha_id"], portfolio_id=kwargs["portfolio_id"],
                timestamp_decision=kwargs["timestamp_decision"],
                timestamp_submit=kwargs["as_of"].isoformat(), timestamp_fill=None,
                symbol=kwargs["symbol"], side="BUY", requested_quantity=abs(kwargs["delta_quantity"]),
                filled_quantity=0.0, remaining_quantity=abs(kwargs["delta_quantity"]),
                requested_notional=0.0, fill_price=None, mark_price_at_decision=0.0,
                spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0, fee_amount=0.0, status="SUBMITTED",
            )
            return order, None

    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    agg = aggregate([_intent(frac=1.0, ts=ts0)], config, set(), as_of=ts0)
    state = step("T", config, agg, ts0, execution_adapter=RefuseAllAdapter())

    assert state.positions == {} or all(abs(p["quantity"]) < 1e-9 for p in state.positions.values())
    assert state.cumulative_fees_usd == 0.0
    assert len(state.orders) == 1
    assert state.orders[0]["status"] == "SUBMITTED"


# ── P0.4 (phase CLOSE THE EXECUTION LOOP) : audit expiry -> exécution,
# EXIT_REASON ────────────────────────────────────────────────────────────

def test_expiry_close_sets_exit_reason_alpha_horizon_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))
    intent = _intent(alpha_id="A1", correlation_family="FAM1", frac=1.0, ts=ts0)
    agg_open = aggregate([intent], config, set(), as_of=ts0)
    step("T", config, agg_open, ts0)

    ts1 = intent.expiry + pd.Timedelta(seconds=1)   # après l'horizon -- plus aucun intent vivant
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts1))
    agg_expired = aggregate([intent], config, set(), as_of=ts1)   # même intent, mais expiré à ts1
    state = step("T", config, agg_expired, ts1)

    assert list(state.positions.values()) == [] or all(
        abs(p["quantity"]) < 1e-9 for p in state.positions.values()
    )
    closing_orders = [o for o in state.orders if o["status"] == "FILLED" and o["side"] == "SELL"]
    assert len(closing_orders) == 1
    assert closing_orders[0]["exit_reason"] == "ALPHA_HORIZON_EXPIRY"


def test_expiry_with_another_alpha_still_wanting_exposure_does_not_blind_close(tmp_path, monkeypatch):
    """Item P0.4 : l'expiration d'UN intent alpha n'implique pas forcément la
    clôture physique -- si un AUTRE alpha (famille de corrélation différente)
    vise toujours le même instrument, le portefeuille recalcule la target
    agrégée (réduction vers ce que B veut seul) au lieu de fermer à zéro, et
    exit_reason doit refléter TARGET_CHANGE, pas ALPHA_HORIZON_EXPIRY."""
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    # deux risk_bucket DISTINCTS et indépendants -- le budget de B ne doit
    # PAS "absorber" automatiquement la part libérée par le départ de A (ce
    # qui arriverait s'ils partageaient le même bucket, où le budget se
    # répartit entre alphas vivants du bucket) : on veut isoler l'effet de
    # l'expiration de A sur la target agrégée de l'instrument commun.
    config = PortfolioConfig(name="TEST_MTM", capital_eur=100_000,
                             family_budget_fraction={"BUCKET_A": 0.7, "BUCKET_B": 0.3},
                             max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)
    ts0 = _ts("2026-09-01T00:00:00Z")
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts0))

    intent_a = PortfolioIntent(
        alpha_id="A1", family="liquidation", risk_bucket="BUCKET_A",
        correlation_family="FAM_A", timestamp=ts0, instrument="BTCUSDT", direction="LONG",
        target_position_fraction=1.0, confidence=1.0, horizon_hours=4.0,
        expiry=ts0 + pd.Timedelta(hours=4), multi_leg=False, leg_instrument_b=None,
    )
    intent_b = PortfolioIntent(
        alpha_id="A2", family="liquidation", risk_bucket="BUCKET_B",
        correlation_family="FAM_B", timestamp=ts0, instrument="BTCUSDT", direction="LONG",
        target_position_fraction=1.0, confidence=1.0, horizon_hours=100.0,
        expiry=ts0 + pd.Timedelta(hours=100), multi_leg=False, leg_instrument_b=None,
    )
    agg_open = aggregate([intent_a, intent_b], config, set(), as_of=ts0)
    state = step("T", config, agg_open, ts0)
    qty_both_active = list(state.positions.values())[0]["quantity"]
    assert qty_both_active > 0

    ts1 = intent_a.expiry + pd.Timedelta(seconds=1)   # A expiré, B toujours vivant (expiry dans 100h)
    monkeypatch.setattr(portfolio_mod, "get_mark", _mock_mark(100.0, ts=ts1))
    agg_after_a_expires = aggregate([intent_a, intent_b], config, set(), as_of=ts1)
    assert "BTCUSDT" not in agg_after_a_expires.expired_driven_instruments   # B vivant -> pas "tout expiré"
    state = step("T", config, agg_after_a_expires, ts1)

    qty_after = list(state.positions.values())[0]["quantity"]
    assert qty_after > 0   # PAS fermé à zéro -- B veut toujours l'exposition
    assert qty_after < qty_both_active   # mais réduit (recalcul vers la target de B seul)
    last_order = state.orders[-1]
    assert last_order["side"] == "SELL"
    assert last_order["exit_reason"] == "TARGET_CHANGE"   # PAS ALPHA_HORIZON_EXPIRY


# ── P1.1 (phase CLOSE THE EXECUTION LOOP) : replay déterministe ──────────

def test_deterministic_replay_identical_hash_across_two_independent_runs(tmp_path, monkeypatch):
    """Rejouer EXACTEMENT la même séquence (intents, config, as_of) dans deux
    portefeuilles indépendants doit produire un état final identique au bit
    près (hash SHA256 du JSON canonique) -- y compris order_id/fill_id, dont
    le suffixe dépendait auparavant de l'ordre d'itération d'un `set`
    (non garanti stable entre deux PROCESSUS séparés, à cause du hash-seed
    randomisé par défaut de Python) : fixé en triant explicitement les
    instruments avant itération (portfolio.py::step, item P1.1)."""
    import hashlib
    import json
    from dataclasses import asdict as _asdict

    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    config = _config()
    ts0 = _ts("2026-09-01T00:00:00Z")
    ts1 = ts0 + pd.Timedelta(minutes=5)
    ts2 = ts1 + pd.Timedelta(minutes=5)
    prices = {(ts0, "BTCUSDT"): 100.0, (ts0, "ETHUSDT"): 50.0, (ts0, "SOLUSDT"): 20.0,
             (ts1, "BTCUSDT"): 103.0, (ts1, "ETHUSDT"): 48.0, (ts1, "SOLUSDT"): 22.0,
             (ts2, "BTCUSDT"): 97.0, (ts2, "ETHUSDT"): 52.0, (ts2, "SOLUSDT"): 19.0}

    def pure_mark(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=prices[(as_of, instrument)],
                         mark_source="TEST", mark_timestamp=as_of, mark_age_ms=0.0)

    monkeypatch.setattr(portfolio_mod, "get_mark", pure_mark)

    intents_by_ts = {
        ts0: [_intent(instrument="BTCUSDT", frac=0.5, ts=ts0),
             _intent(instrument="ETHUSDT", frac=0.3, ts=ts0),
             _intent(instrument="SOLUSDT", frac=0.2, ts=ts0)],
        ts1: [_intent(instrument="BTCUSDT", frac=0.2, ts=ts1),
             _intent(instrument="ETHUSDT", frac=0.5, ts=ts1),
             _intent(instrument="SOLUSDT", frac=0.3, ts=ts1)],
        ts2: [_intent(instrument="BTCUSDT", frac=0.0, ts=ts2, direction="SHORT")],
    }

    def run(name):
        state = None
        for ts in (ts0, ts1, ts2):
            agg = aggregate(intents_by_ts[ts], config, set(), as_of=ts)
            state = step(name, config, agg, ts)
        return state

    state_a = run("RUN_A")
    state_b = run("RUN_B")

    def canonical_hash(state):
        # portfolio_id apparaît dans order_id/intent_id/etc -- normalisé
        # avant hash puisque RUN_A/RUN_B sont volontairement deux noms
        # différents (seule façon d'avoir deux répertoires d'état isolés ici).
        blob = json.dumps(_asdict(state), sort_keys=True, default=str)
        blob = blob.replace("RUN_A", "PORTFOLIO").replace("RUN_B", "PORTFOLIO")
        return hashlib.sha256(blob.encode()).hexdigest()

    assert canonical_hash(state_a) == canonical_hash(state_b)
    assert len(state_a.orders) == len(state_b.orders) > 0
