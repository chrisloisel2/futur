"""Tests logique pure de la sonde maker (aucun réseau)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.institutional.execution.maker_fill_probe import (
    MakerFillProbe, VirtualOrder, check_fill)


def _order(side, limit):
    return VirtualOrder(symbol="BTCUSDT", side=side, limit=limit, t_place=0.0,
                        ts_place="t0", bid_at_place=100.0, ask_at_place=100.1)


def test_buy_fill_requires_book_through():
    o = _order("BUY", 100.0)
    assert not check_fill(o, 100.0, 100.1)   # touch = pas rempli (conservateur)
    assert not check_fill(o, 99.9, 100.0)    # ask == limite : ambigu → non
    assert check_fill(o, 99.8, 99.9)         # ask < limite : traversé → rempli


def test_sell_fill_requires_book_through():
    o = _order("SELL", 100.1)
    assert not check_fill(o, 100.0, 100.1)
    assert not check_fill(o, 100.1, 100.2)   # bid == limite → non
    assert check_fill(o, 100.2, 100.3)       # bid > limite → rempli


def test_expiry_records_unfilled():
    p = MakerFillProbe(symbols=["BTCUSDT"])
    p.on_book("BTCUSDT", 100.0, 100.1, now_t=0.0)
    p.place_orders(now_t=0.0)
    assert len(p.open_orders) == 2
    p.on_book("BTCUSDT", 100.0, 100.1, now_t=601.0)   # TTL 600 dépassé
    assert len(p.open_orders) == 0
    assert len(p.done_rows) == 2
    assert all(r["filled"] is False and r["ttf_s"] is None for r in p.done_rows)


def test_fill_then_marks_then_complete():
    p = MakerFillProbe(symbols=["BTCUSDT"])
    p.on_book("BTCUSDT", 100.0, 100.1, now_t=0.0)
    p.place_orders(now_t=0.0)
    # le carnet traverse le bid → BUY rempli à t=1
    p.on_book("BTCUSDT", 99.5, 99.6, now_t=1.0)
    buy = [o for o in p.open_orders if o.side == "BUY"][0]
    assert buy.t_fill == 1.0
    # marks +60s / +300s → l'ordre se complète
    p.on_book("BTCUSDT", 99.0, 99.1, now_t=62.0)
    p.on_book("BTCUSDT", 98.0, 98.1, now_t=302.0)
    rows = [r for r in p.done_rows if r["side"] == "BUY"]
    assert len(rows) == 1
    r = rows[0]
    assert r["filled"] is True and r["ttf_s"] == 1.0
    # mid +60s = 99.05 vs limite 100 → adverse négatif pour un BUY
    assert r["adv_bps_60s"] < 0


def test_sell_adverse_sign():
    p = MakerFillProbe(symbols=["BTCUSDT"])
    p.on_book("BTCUSDT", 100.0, 100.1, now_t=0.0)
    p.place_orders(now_t=0.0)
    # bid passe AU-DESSUS de l'ask limite → SELL rempli
    p.on_book("BTCUSDT", 100.5, 100.6, now_t=2.0)
    p.on_book("BTCUSDT", 101.0, 101.1, now_t=63.0)    # mid monte → adverse
    p.on_book("BTCUSDT", 99.0, 99.1, now_t=303.0)     # mid redescend → favorable
    rows = [r for r in p.done_rows if r["side"] == "SELL"]
    assert len(rows) == 1
    assert rows[0]["adv_bps_60s"] < 0 < rows[0]["adv_bps_300s"]
