"""Le plafond de taille adossé à la PROFONDEUR, pas au stock de positions.

Le plafond précédent était `open_interest × 0,002`. L'open interest est un
stock de positions ouvertes ; il est de plusieurs ordres de grandeur supérieur
à ce qu'un carnet laisse traverser. Mesuré : il mordait 1,0 % des ordres, là où
19,4 % dépassent réellement la profondeur affichée au meilleur limite — et où
63,2 % du notionnel exécuté aurait dû être refusé.

Ce fichier protège trois choses :
  1. la nouvelle règle mord là où l'ancienne ne mordait pas ;
  2. elle est fail-open sans sonde — un plafond inventé serait pire qu'aucun ;
  3. la FRONTIÈRE DE SEGMENT tient : les ordres antérieurs gardent l'ancienne
     règle, sinon deux régimes d'exécution se mélangent dans une même courbe
     d'équité et les deux deviennent illisibles.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.orders import (
    CAP_POLICY_OPEN_INTEREST,
    CAP_POLICY_TOP_OF_BOOK,
    DEPTH_CAP_EFFECTIVE_FROM,
    MAX_FILL_FRACTION_OF_DEPTH,
    MAX_FILL_FRACTION_OF_LIQUIDITY,
    cap_policy_for,
    depth_cap_quantity,
    liquidity_cap_quantity,
)


@dataclass
class _Mark:
    price: float
    liquidity_notional: Optional[float] = None


# ── 1. la frontière de segment ─────────────────────────────────────────────

def test_the_policy_is_a_function_of_the_date_not_a_setting():
    assert cap_policy_for("2026-09-06T23:59:59+00:00") == CAP_POLICY_OPEN_INTEREST
    assert cap_policy_for(DEPTH_CAP_EFFECTIVE_FROM) == CAP_POLICY_TOP_OF_BOOK
    assert cap_policy_for("2027-01-01T00:00:00+00:00") == CAP_POLICY_TOP_OF_BOOK


def test_a_naive_timestamp_is_read_as_utc_not_refused():
    assert cap_policy_for("2026-09-08T00:00:00") == CAP_POLICY_TOP_OF_BOOK


def test_the_historical_rule_is_untouched():
    """Elle a produit tous les ordres antérieurs. La modifier réécrirait le
    passé et rendrait la courbe d'équité incomparable à elle-même."""
    mark = _Mark(price=100.0, liquidity_notional=1_000_000.0)
    assert liquidity_cap_quantity(mark) == pytest.approx(
        1_000_000.0 * MAX_FILL_FRACTION_OF_LIQUIDITY / 100.0)


# ── 2. la nouvelle règle mord là où l'ancienne ne mordait pas ──────────────

def test_depth_bites_where_open_interest_does_not():
    """Le cas réel : ARUSDT affiche ~476 $ au meilleur limite pour un open
    interest de plusieurs millions."""
    mark = _Mark(price=10.0, liquidity_notional=5_000_000.0)
    oi_cap = liquidity_cap_quantity(mark)               # 5 M × 0,002 / 10 = 1000 unités
    depth_cap = depth_cap_quantity(mark, 476.0)         # 476 / 10 = 47,6 unités
    assert depth_cap < oi_cap
    assert oi_cap / depth_cap > 20


def test_the_depth_cap_is_exactly_the_observed_size():
    """La fraction vaut 1,0 : le plafond EST la taille pour laquelle le spread
    coté a été observé, pas une fraction prudente de celle-ci."""
    assert MAX_FILL_FRACTION_OF_DEPTH == 1.0
    mark = _Mark(price=50.0)
    assert depth_cap_quantity(mark, 5_000.0) == pytest.approx(100.0)


# ── 3. fail-open, et jamais un plafond inventé ─────────────────────────────

def test_no_probe_means_no_cap_never_a_guessed_one():
    mark = _Mark(price=100.0)
    assert depth_cap_quantity(mark, None) is None
    assert depth_cap_quantity(mark, 0.0) is None
    assert depth_cap_quantity(mark, -5.0) is None


def test_a_zero_price_never_produces_an_infinite_cap():
    assert depth_cap_quantity(_Mark(price=0.0), 1_000.0) is None
    assert liquidity_cap_quantity(_Mark(price=0.0, liquidity_notional=1_000.0)) is None


# ── 4. l'ordre porte sa politique, pour qu'une série reste lisible ─────────

def test_the_order_records_which_rule_capped_it():
    from src.institutional.live_alpha_lab.orders import ShadowOrder
    order = ShadowOrder(
        order_id="o", intent_id="i", signal_id="s", alpha_id="a", portfolio_id="p",
        timestamp_decision="t", timestamp_submit="t", timestamp_fill=None,
        symbol="ARUSDT", side="BUY", requested_quantity=100.0, filled_quantity=47.6,
        remaining_quantity=52.4, requested_notional=1000.0, fill_price=10.0,
        mark_price_at_decision=10.0, spread_bps=4.0, slippage_bps=2.0,
        fee_bps=5.0, fee_amount=0.5, status="PARTIALLY_FILLED",
        cap_policy=CAP_POLICY_TOP_OF_BOOK, cap_notional_usd=476.0,
        refused_notional_usd=524.0,
    )
    assert order.cap_policy == CAP_POLICY_TOP_OF_BOOK
    assert order.refused_notional_usd == pytest.approx(524.0)


def test_the_new_fields_default_to_none_so_old_call_sites_still_build():
    """Les champs sont ajoutés en fin de dataclass avec des défauts : aucun
    appelant existant n'est cassé, et un ordre sans politique se lit comme
    « produit avant la frontière », pas comme « non plafonné »."""
    from src.institutional.live_alpha_lab.orders import ShadowOrder
    order = ShadowOrder(
        order_id="o", intent_id="i", signal_id="s", alpha_id="a", portfolio_id="p",
        timestamp_decision="t", timestamp_submit="t", timestamp_fill=None,
        symbol="BTCUSDT", side="BUY", requested_quantity=1.0, filled_quantity=1.0,
        remaining_quantity=0.0, requested_notional=100.0, fill_price=100.0,
        mark_price_at_decision=100.0, spread_bps=4.0, slippage_bps=2.0,
        fee_bps=5.0, fee_amount=0.05, status="FILLED",
    )
    assert order.cap_policy is None
    assert order.refused_notional_usd == 0.0
