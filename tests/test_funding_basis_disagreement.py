"""tests/test_funding_basis_disagreement.py — FUNDING_BASIS_DISAGREEMENT_V1 (Live Alpha Lab).

Covers: regime classification boundaries (frozen thresholds), episode +
non-overlap decluster, empty input, no-lookahead/causality of the panel and
decluster logic, universe/registry fail-closed behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.funding_basis_disagreement.disagreement import (
    FROZEN_HORIZON_DAYS, FROZEN_THRESHOLDS, classify_regime, select_tradeable,
)
from src.institutional.engines.funding_basis_disagreement.panel import (
    MIN_DTE, build_panel,
)


# ── classify_regime boundaries ──────────────────────────────────────────────

def test_classify_regime_boundaries():
    lo, hi = -6.7, 12.3
    assert classify_regime(12.3, lo, hi) == "RICH"     # bound inclusive
    assert classify_regime(12.31, lo, hi) == "RICH"
    assert classify_regime(12.29, lo, hi) == "NEUTRAL"
    assert classify_regime(-6.7, lo, hi) == "CHEAP"    # bound inclusive
    assert classify_regime(-6.71, lo, hi) == "CHEAP"
    assert classify_regime(-6.69, lo, hi) == "NEUTRAL"
    assert classify_regime(0.0, lo, hi) == "NEUTRAL"


def test_frozen_thresholds_are_hardcoded_constants_not_data_derived():
    """Le point clé de 'figé' : les seuils sont des constantes du module, PAS
    calculées depuis un DataFrame passé en argument (aucune fonction de ce
    module ne recalcule un quantile à partir de l'historique)."""
    assert FROZEN_THRESHOLDS["BTCUSDT"] == {"lo": -6.7, "hi": 12.3}
    assert FROZEN_THRESHOLDS["ETHUSDT"] == {"lo": -5.8, "hi": 15.6}
    assert FROZEN_HORIZON_DAYS == 30


# ── select_tradeable: empty / boundary ──────────────────────────────────────

def test_select_tradeable_empty_input():
    out = select_tradeable(pd.DataFrame())
    assert out.empty


def _panel_row(date, symbol, disagreement, near_dte=20):
    ts = pd.Timestamp(date)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return {
        "date": ts, "symbol": symbol,
        "disagreement": disagreement, "basis_near_ann": 5.0, "funding_ann_pct": 3.0,
        "near_dte": near_dte, "near_contract": f"{symbol[:3]}USDT_TEST",
    }


def test_select_tradeable_unknown_symbol_ignored_fail_closed():
    """Un symbole absent de FROZEN_THRESHOLDS (ex: pas de futures trimestriels)
    ne doit jamais produire de décision -- pas de seuil deviné."""
    panel = pd.DataFrame([_panel_row("2026-01-01", "SOLUSDT", 50.0)])
    out = select_tradeable(panel)
    assert out.empty


# ── episode + non-overlap decluster ─────────────────────────────────────────

def test_select_tradeable_single_rich_episode():
    panel = pd.DataFrame([_panel_row("2026-01-01", "BTCUSDT", 20.0)])  # >= hi=12.3
    out = select_tradeable(panel)
    assert len(out) == 1
    assert out.iloc[0]["regime"] == "RICH"
    assert out.iloc[0]["direction"] == "LONG_QUARTERLY_SHORT_PERP"


def test_select_tradeable_single_cheap_episode():
    panel = pd.DataFrame([_panel_row("2026-01-01", "BTCUSDT", -20.0)])  # <= lo=-6.7
    out = select_tradeable(panel)
    assert len(out) == 1
    assert out.iloc[0]["regime"] == "CHEAP"
    assert out.iloc[0]["direction"] == "SHORT_QUARTERLY_LONG_PERP"


def test_select_tradeable_neutral_never_emitted():
    panel = pd.DataFrame([_panel_row("2026-01-01", "BTCUSDT", 0.0)])
    out = select_tradeable(panel)
    assert out.empty


def test_select_tradeable_contiguous_regime_run_only_fires_once():
    """Décluster #1 (contiguïté d'épisode) : un régime RICH qui persiste sur
    plusieurs jours consécutifs ne doit produire qu'UNE seule entrée (le
    premier jour du run), pas une par jour."""
    dates = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    panel = pd.DataFrame([_panel_row(d, "BTCUSDT", 20.0) for d in dates])
    out = select_tradeable(panel)
    assert len(out) == 1
    assert out.iloc[0]["date"] == dates[0]


def test_select_tradeable_nonoverlap_filter_respects_horizon():
    """Décluster #2 : une deuxième entrée RICH moins de FROZEN_HORIZON_DAYS
    après la première (même symbole) est rejetée, même si elle démarre un
    nouvel épisode contigu (régime repasse par NEUTRAL entre les deux)."""
    rows = []
    rows.append(_panel_row("2026-01-01", "BTCUSDT", 20.0))     # RICH, entry 1
    rows.append(_panel_row("2026-01-02", "BTCUSDT", 0.0))      # NEUTRAL (coupe l'épisode)
    rows.append(_panel_row("2026-01-10", "BTCUSDT", 20.0))     # RICH again, only 9d later -> rejected
    panel = pd.DataFrame(rows)
    out = select_tradeable(panel, horizon_days=30)
    assert len(out) == 1
    assert out.iloc[0]["date"] == pd.Timestamp("2026-01-01", tz="UTC")


def test_select_tradeable_nonoverlap_filter_keeps_entry_past_horizon():
    rows = []
    rows.append(_panel_row("2026-01-01", "BTCUSDT", 20.0))
    rows.append(_panel_row("2026-01-02", "BTCUSDT", 0.0))
    rows.append(_panel_row("2026-02-05", "BTCUSDT", 20.0))     # 35 days later -> kept
    panel = pd.DataFrame(rows)
    out = select_tradeable(panel, horizon_days=30)
    assert len(out) == 2


def test_select_tradeable_symbols_declustered_independently():
    """BTC et ETH ne doivent pas se contaminer dans le décluster non-chevauchement
    (chacun a sa propre fenêtre glissante)."""
    rows = [
        _panel_row("2026-01-01", "BTCUSDT", 20.0),
        _panel_row("2026-01-05", "ETHUSDT", 20.0),   # ETH même semaine, symbole différent
    ]
    panel = pd.DataFrame(rows)
    out = select_tradeable(panel, horizon_days=30)
    assert len(out) == 2
    assert set(out["symbol"]) == {"BTCUSDT", "ETHUSDT"}


# ── causality / no-lookahead ────────────────────────────────────────────────

def test_decluster_prefix_consistency_no_lookahead():
    """Propriété clé de causalité : le résultat déclusterisé restreint à une
    date de coupure D doit être identique à celui obtenu en tournant le
    décluster directement sur l'historique tronqué à D -- aucune décision
    prise à J ne doit dépendre de données postérieures à J. C'est aussi ce qui
    garantit l'idempotence incrémentale du runner (les décisions déjà
    émises ne changent jamais quand de nouvelles lignes arrivent)."""
    dates = pd.date_range("2026-01-01", periods=90, freq="D", tz="UTC")
    # motif oscillant pour générer plusieurs épisodes RICH/CHEAP/NEUTRAL
    values = [20.0 if (i // 7) % 3 == 0 else (-20.0 if (i // 7) % 3 == 1 else 0.0)
             for i in range(90)]
    panel_full = pd.DataFrame([_panel_row(d, "BTCUSDT", v) for d, v in zip(dates, values)])

    cutoff = 60
    panel_prefix = panel_full.iloc[:cutoff].copy()

    out_full = select_tradeable(panel_full)
    out_prefix = select_tradeable(panel_prefix)

    cutoff_date = dates[cutoff - 1]
    out_full_up_to_cutoff = out_full[out_full["date"] <= cutoff_date]

    assert list(out_prefix["date"]) == list(out_full_up_to_cutoff["date"])
    assert list(out_prefix["regime"]) == list(out_full_up_to_cutoff["regime"])


def test_panel_basis_and_funding_math_no_lookahead_same_day_only():
    """disagreement/basis_near_ann/funding_ann_pct à la date D ne doivent
    dépendre que des données datées D (pas de fenêtre glissante nécessitant
    des données futures) -- on vérifie que build_panel() avec un flux live
    tronqué à D donne, pour les lignes <=D, EXACTEMENT les mêmes valeurs
    qu'avec un flux live complet."""
    quarterly = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-01", tz="UTC"), "near_contract": "BTCUSDT_260327",
         "near_close": 105_000.0, "near_expiry": pd.Timestamp("2026-03-27", tz="UTC"), "near_dte": 85},
        {"date": pd.Timestamp("2026-01-02", tz="UTC"), "near_contract": "BTCUSDT_260327",
         "near_close": 106_000.0, "near_expiry": pd.Timestamp("2026-03-27", tz="UTC"), "near_dte": 84},
        {"date": pd.Timestamp("2026-01-03", tz="UTC"), "near_contract": "BTCUSDT_260327",
         "near_close": 107_000.0, "near_expiry": pd.Timestamp("2026-03-27", tz="UTC"), "near_dte": 83},
    ])
    live_full = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-01", tz="UTC"), "funding_rate_mean": 0.0001, "perp_close": 100_000.0},
        {"date": pd.Timestamp("2026-01-02", tz="UTC"), "funding_rate_mean": -0.0002, "perp_close": 101_000.0},
        {"date": pd.Timestamp("2026-01-03", tz="UTC"), "funding_rate_mean": 0.0003, "perp_close": 102_000.0},
    ])
    live_truncated = live_full.iloc[:2].copy()

    p_full = build_panel("BTCUSDT", quarterly=quarterly, live_daily=live_full)
    p_trunc = build_panel("BTCUSDT", quarterly=quarterly, live_daily=live_truncated)

    common = p_trunc["date"]
    merged = p_full[p_full["date"].isin(common)].reset_index(drop=True)
    trunc = p_trunc.reset_index(drop=True)
    for col in ("basis_near_pct", "basis_near_ann", "funding_ann_pct", "disagreement"):
        assert (merged[col].values == trunc[col].values).all(), f"lookahead detected in {col}"


def test_build_panel_min_dte_eligibility_filter():
    quarterly = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-01", tz="UTC"), "near_contract": "X",
         "near_close": 100.0, "near_expiry": pd.Timestamp("2026-01-05", tz="UTC"), "near_dte": 4},  # < MIN_DTE
        {"date": pd.Timestamp("2026-01-02", tz="UTC"), "near_contract": "X",
         "near_close": 100.0, "near_expiry": pd.Timestamp("2026-03-01", tz="UTC"), "near_dte": 58},  # eligible
    ])
    live = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-01", tz="UTC"), "funding_rate_mean": 0.0001, "perp_close": 100.0},
        {"date": pd.Timestamp("2026-01-02", tz="UTC"), "funding_rate_mean": 0.0001, "perp_close": 100.0},
    ])
    p = build_panel("BTCUSDT", quarterly=quarterly, live_daily=live)
    assert MIN_DTE == 7
    assert len(p) == 1
    assert p.iloc[0]["date"] == pd.Timestamp("2026-01-02", tz="UTC")


def test_build_panel_empty_when_no_data():
    empty = pd.DataFrame(columns=["date", "near_contract", "near_close", "near_expiry", "near_dte"])
    p = build_panel("BTCUSDT", quarterly=empty, live_daily=empty)
    assert p.empty


# ── universe / registry fail-closed (mirrors test_liq_cascade_repeat_variant.py) ──

def test_universe_hash_deterministic():
    from scripts.run_funding_basis_disagreement_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT"])
    b = universe_hash(["ETHUSDT", "BTCUSDT"])  # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT"])
    assert a != c


def test_load_universe_matches_expected_frozen_list():
    from scripts.run_funding_basis_disagreement_shadow import EXPECTED_UNIVERSE, load_universe
    assert load_universe() == sorted(EXPECTED_UNIVERSE)


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_funding_basis_disagreement_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID")


def test_check_registry_freeze_passes_for_shadow_live_entry():
    from scripts.run_funding_basis_disagreement_shadow import check_registry_freeze
    # Correction 2026-08-31 : V1 (jambe perp stale) est REJECTED/superseded --
    # l'implémentation réelle (jambe perp live) est FUNDING_BASIS_DISAGREEMENT_V2,
    # operational_status=SIGNAL_SHADOW dans configs/live_alpha_registry.yaml.
    check_registry_freeze("FUNDING_BASIS_DISAGREEMENT_V2")


def test_check_registry_freeze_fails_closed_for_superseded_v1():
    from scripts.run_funding_basis_disagreement_shadow import check_registry_freeze
    # V1 est REJECTED/DATA_BLOCKED (jamais réellement lancé sous cette identité,
    # la jambe perp stale n'a pas de source live) -- ne doit jamais pouvoir écrire.
    with pytest.raises(RuntimeError, match="operational_status="):
        check_registry_freeze("FUNDING_BASIS_DISAGREEMENT_V1")


def test_check_registry_freeze_fails_closed_for_blocked_alpha():
    from scripts.run_funding_basis_disagreement_shadow import check_registry_freeze
    # LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 est explicitement BUG_FOUND/bloqué
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1")
