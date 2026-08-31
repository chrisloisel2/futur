"""tests/test_symbol_resolver.py — regression guard for the 2026-08-31 silent
symbol-resolution bug (MKRUSDT/PEPEUSDT/RNDRUSDT silently dropped from the
live derivatives collector with no explicit trace). No canonical symbol in
the frozen universe may vanish from the resolution output without an
explicit, non-empty eligibility_reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.derivatives_collector.symbol_resolver import (
    KNOWN_RENAMES, eligible_exchange_symbols, resolve_symbol, resolve_universe)


def _fake_exchange_info():
    def sym(s, status="TRADING"):
        return {"symbol": s, "status": status}
    return {"symbols": [
        sym("BTCUSDT"), sym("ETHUSDT"), sym("SOLUSDT"),
        sym("MKRUSDT", status="SETTLING"),      # non-TRADING, réel statut préservé
        sym("1000PEPEUSDT"),                    # rename PEPEUSDT -> 1000PEPEUSDT
        sym("RENDERUSDT"),                      # rename RNDRUSDT -> RENDERUSDT
        # PEPEUSDT et RNDRUSDT canoniques : ABSENTS d'exchangeInfo (comme en réalité).
    ]}


def test_trading_symbol_resolves_directly():
    r = resolve_symbol("BTCUSDT", _fake_exchange_info())
    assert r.exchange_symbol == "BTCUSDT"
    assert r.instrument_status == "TRADING"
    assert r.eligible
    assert r.eligibility_reason


def test_non_trading_symbol_is_ineligible_with_real_status_preserved():
    r = resolve_symbol("MKRUSDT", _fake_exchange_info())
    assert r.exchange_symbol is None
    assert r.instrument_status == "SETTLING"   # jamais un label générique "DELISTED"
    assert not r.eligible
    assert "SETTLING" in r.eligibility_reason


def test_known_rename_resolves_and_is_eligible():
    r = resolve_symbol("PEPEUSDT", _fake_exchange_info())
    assert r.exchange_symbol == "1000PEPEUSDT"
    assert r.instrument_status == "RENAMED"
    assert r.eligible

    r2 = resolve_symbol("RNDRUSDT", _fake_exchange_info())
    assert r2.exchange_symbol == "RENDERUSDT"
    assert r2.eligible


def test_unknown_missing_symbol_is_explicitly_not_found_never_silent():
    r = resolve_symbol("TOTALLYFAKEUSDT", _fake_exchange_info())
    assert r.exchange_symbol is None
    assert r.instrument_status == "NOT_FOUND"
    assert not r.eligible
    assert r.eligibility_reason   # jamais vide


def test_no_frozen_symbol_ever_silently_disappears():
    """Le coeur de la régression : pour CHAQUE symbole en entrée, il existe
    EXACTEMENT une ResolvedSymbol en sortie (éligible ou non), jamais un
    symbole qui manque juste sans trace."""
    universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "MKRUSDT", "PEPEUSDT",
               "RNDRUSDT", "TOTALLYFAKEUSDT"]
    resolved = resolve_universe(universe, _fake_exchange_info())
    assert len(resolved) == len(universe)
    assert [r.canonical_asset for r in resolved] == universe
    for r in resolved:
        assert r.eligibility_reason, f"{r.canonical_asset} sans raison explicite"
        assert r.instrument_status, f"{r.canonical_asset} sans statut"


def test_eligible_exchange_symbols_excludes_ineligible_only():
    universe = ["BTCUSDT", "MKRUSDT", "PEPEUSDT", "TOTALLYFAKEUSDT"]
    out = eligible_exchange_symbols(universe, _fake_exchange_info())
    assert out == ["BTCUSDT", "1000PEPEUSDT"]


def test_known_renames_table_is_explicit_not_guessed():
    """Documente que la table de renames est petite et intentionnelle, pas
    une heuristique de matching approximatif."""
    assert KNOWN_RENAMES == {"PEPEUSDT": "1000PEPEUSDT", "RNDRUSDT": "RENDERUSDT"}
