"""
tests/unit/test_risk_controller.py
===================================
Tests complets du RiskController — Phase 2

Couvre :
  - Sizing (0.2% equity par trade)
  - Stop journalier (-2%)
  - Pertes consécutives (max 3)
  - Cooldown entre trades
  - Cap d'exposition
  - Filtre edge/scale
  - Persistence JSON (save/load)
  - Reset journalier
  - Cas limites (prix négatif, NaN, equity zéro)
  - on_fill_pnl : mise à jour correcte equity/pnl
  - Séquence complète de trades acceptés/refusés
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Inject path pour importer RiskController depuis level_7
_RC_PATH = Path(__file__).resolve().parent.parent.parent.parent / "ai" / "models" / "level_7"
if str(_RC_PATH) not in sys.path:
    sys.path.insert(0, str(_RC_PATH))

from RiskController import RiskController, RiskConfig, RiskState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_rc(
    equity: float = 10_000.0,
    risk_per_trade: float = 0.002,
    daily_loss_limit_pct: float = 0.02,
    max_consecutive_losses: int = 3,
    cooldown_bars: int = 3,
    min_abs_edge: float = 0.05,
    min_scale: float = 0.15,
) -> RiskController:
    cfg = RiskConfig(
        equity                  = equity,
        risk_per_trade          = risk_per_trade,
        daily_loss_limit_pct    = daily_loss_limit_pct,
        max_consecutive_losses  = max_consecutive_losses,
        cooldown_bars           = cooldown_bars,
        min_abs_edge            = min_abs_edge,
        min_scale               = min_scale,
        stop_atr_mult           = 2.5,
        min_stop_pct            = 0.001,
        max_stop_pct            = 0.03,
        rr                      = 1.5,
    )
    return RiskController(cfg)


def features_ok(price: float = 50_000.0) -> dict:
    """Features avec ATR valide (0.5% du prix)."""
    return {"atr_14": price * 0.005, "rv_60": 0.005}


def decide_buy(rc: RiskController, bar: int = 0, price: float = 50_000.0) -> dict:
    """Appelle decide() avec des paramètres valides → doit retourner BUY."""
    return rc.decide(
        price      = price,
        edge_final = 0.15,
        scale      = 0.5,
        bar_index  = bar,
        features   = features_ok(price),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Sizing : 0.2% equity risqué par trade
# ─────────────────────────────────────────────────────────────────────────────

class TestSizing:

    def test_risk_budget_is_02_pct(self):
        rc = make_rc(equity=10_000.0)
        d  = decide_buy(rc)
        assert d["action"] == "BUY"
        assert abs(d["risk_budget"] - 20.0) < 0.01   # 0.2% × 10000 = $20

    def test_risk_budget_scales_with_equity(self):
        rc = make_rc(equity=50_000.0)
        d  = decide_buy(rc, price=50_000.0)
        assert abs(d["risk_budget"] - 100.0) < 0.01   # 0.2% × 50000 = $100

    def test_qty_equals_budget_over_stop_dist(self):
        rc    = make_rc(equity=10_000.0)
        price = 50_000.0
        atr   = price * 0.005   # 0.5%
        d     = rc.decide(
            price      = price,
            edge_final = 0.15,
            scale      = 0.5,
            bar_index  = 0,
            features   = {"atr_14": atr},
        )
        stop_dist_expected = 2.5 * atr   # stop_atr_mult=2.5
        qty_expected       = 20.0 / stop_dist_expected  # risk_budget / stop_dist
        assert abs(d["qty"] - qty_expected) < 1e-6

    def test_notional_capped_at_equity(self):
        """notional ne doit pas dépasser max_position_notional × equity."""
        rc    = make_rc(equity=10_000.0)
        rc.cfg = RiskConfig(
            equity=10_000.0, risk_per_trade=0.002,
            daily_loss_limit_pct=0.02, max_consecutive_losses=3,
            cooldown_bars=3, min_abs_edge=0.05, min_scale=0.15,
            stop_atr_mult=2.5, min_stop_pct=0.001, max_stop_pct=0.03,
            max_position_notional=0.5,   # cap 50% equity = $5000
        )
        d = decide_buy(rc, price=50_000.0)
        assert d["notional"] <= 10_000.0 * 0.5 + 1.0   # tolérance arrondi

    def test_stop_price_below_entry_for_buy(self):
        rc    = make_rc()
        price = 50_000.0
        d     = decide_buy(rc, price=price)
        assert d["stop_price"] < price
        assert d["take_profit"] > price

    def test_stop_price_above_entry_for_sell(self):
        rc = make_rc()
        d  = rc.decide(
            price=50_000.0, edge_final=-0.15, scale=0.5,
            bar_index=0, features=features_ok(),
        )
        assert d["action"] == "SELL"
        assert d["stop_price"] > 50_000.0
        assert d["take_profit"] < 50_000.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Stop journalier (-2%)
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyStop:

    def test_trading_blocked_after_2pct_daily_loss(self):
        rc = make_rc(equity=10_000.0, daily_loss_limit_pct=0.02)
        # Simule une perte de $201 (> 2% × $10000 = $200)
        rc.on_fill_pnl(-201.0)

        d = decide_buy(rc, bar=10)
        assert d["action"] == "HOLD"
        assert "daily_stop" in d["reason"]

    def test_trading_allowed_below_daily_limit(self):
        rc = make_rc(equity=10_000.0, daily_loss_limit_pct=0.02)
        rc.on_fill_pnl(-150.0)    # seulement $150 de perte, limite = $200

        d = decide_buy(rc, bar=10)
        assert d["action"] == "BUY"

    def test_daily_limit_calculated_from_day_start_equity(self):
        """La limite doit être calculée sur le capital de début de journée."""
        rc = make_rc(equity=10_000.0, daily_loss_limit_pct=0.02)
        rc.reset_day(equity=8_000.0)   # début de jour à $8000
        # 2% × $8000 = $160
        rc.on_fill_pnl(-161.0)

        d = decide_buy(rc, bar=10)
        assert d["action"] == "HOLD"
        assert "daily_stop" in d["reason"]

    def test_reset_day_clears_daily_stop(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-250.0)        # déclenche stop

        d1 = decide_buy(rc, bar=10)
        assert d1["action"] == "HOLD"

        rc.reset_day(equity=10_000.0, day_str="2024-01-02")
        d2 = decide_buy(rc, bar=11)
        assert d2["action"] == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pertes consécutives (max 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveLosses:

    def test_blocked_after_3_consecutive_losses(self):
        rc = make_rc(max_consecutive_losses=3)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        assert rc.state.consecutive_losses == 3

        d = decide_buy(rc, bar=20)
        assert d["action"] == "HOLD"
        assert "consecutive_losses" in d["reason"]

    def test_win_resets_consecutive_counter(self):
        rc = make_rc(max_consecutive_losses=3)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(+50.0)     # victoire → reset
        assert rc.state.consecutive_losses == 0

    def test_exactly_2_losses_still_allowed(self):
        rc = make_rc(max_consecutive_losses=3)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        assert rc.state.consecutive_losses == 2

        d = decide_buy(rc, bar=20)
        assert d["action"] == "BUY"

    def test_reset_day_clears_consecutive_losses(self):
        rc = make_rc(max_consecutive_losses=3)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)

        rc.reset_day()
        assert rc.state.consecutive_losses == 0
        d = decide_buy(rc, bar=20)
        assert d["action"] == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Cooldown entre trades
# ─────────────────────────────────────────────────────────────────────────────

class TestCooldown:

    def test_blocked_during_cooldown(self):
        rc = make_rc(cooldown_bars=3)
        d1 = decide_buy(rc, bar=10)
        assert d1["action"] == "BUY"    # trade accepté à bar 10

        d2 = decide_buy(rc, bar=11)
        assert d2["action"] == "HOLD"
        assert "cooldown" in d2["reason"]

        d3 = decide_buy(rc, bar=12)
        assert d3["action"] == "HOLD"

    def test_allowed_after_cooldown(self):
        rc = make_rc(cooldown_bars=3)
        decide_buy(rc, bar=10)

        d = decide_buy(rc, bar=13)   # 10 + 3 = 13, ok
        assert d["action"] == "BUY"

    def test_cooldown_zero_allows_consecutive(self):
        rc = make_rc(cooldown_bars=0)
        d1 = decide_buy(rc, bar=0)
        d2 = decide_buy(rc, bar=1)
        assert d1["action"] == "BUY"
        assert d2["action"] == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Filtres signal
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalFilters:

    def test_low_edge_rejected(self):
        rc = make_rc(min_abs_edge=0.05)
        d  = rc.decide(
            price=50_000.0, edge_final=0.03, scale=0.5,
            bar_index=0, features=features_ok(),
        )
        assert d["action"] == "HOLD"
        assert "low_edge" in d["reason"]

    def test_low_scale_rejected(self):
        rc = make_rc(min_scale=0.15)
        d  = rc.decide(
            price=50_000.0, edge_final=0.15, scale=0.10,
            bar_index=0, features=features_ok(),
        )
        assert d["action"] == "HOLD"
        assert "low_scale" in d["reason"]

    def test_tradeable_false_rejected(self):
        rc = make_rc()
        d  = rc.decide(
            price=50_000.0, edge_final=0.15, scale=0.5,
            bar_index=0, features=features_ok(), tradeable=False,
        )
        assert d["action"] == "HOLD"
        assert d["reason"] == "not_tradeable"

    def test_tradeable_none_passes(self):
        rc = make_rc()
        d  = rc.decide(
            price=50_000.0, edge_final=0.15, scale=0.5,
            bar_index=0, features=features_ok(), tradeable=None,
        )
        assert d["action"] == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Cas limites / garde-fous
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_bad_price_rejected(self):
        rc = make_rc()
        d  = rc.decide(price=0.0, edge_final=0.15, scale=0.5,
                       bar_index=0, features=features_ok())
        assert d["action"] == "HOLD"
        assert d["reason"] == "bad_price"

    def test_negative_price_rejected(self):
        rc = make_rc()
        d  = rc.decide(price=-100.0, edge_final=0.15, scale=0.5,
                       bar_index=0, features=features_ok())
        assert d["action"] == "HOLD"

    def test_nan_edge_treated_as_zero(self):
        import math
        rc = make_rc()
        d  = rc.decide(price=50_000.0, edge_final=math.nan, scale=0.5,
                       bar_index=0, features=features_ok())
        assert d["action"] == "HOLD"
        assert "low_edge" in d["reason"]

    def test_nan_scale_treated_as_zero(self):
        import math
        rc = make_rc()
        d  = rc.decide(price=50_000.0, edge_final=0.15, scale=math.nan,
                       bar_index=0, features=features_ok())
        assert d["action"] == "HOLD"
        assert "low_scale" in d["reason"]

    def test_missing_atr_uses_rv_fallback(self):
        rc = make_rc()
        d  = rc.decide(
            price=50_000.0, edge_final=0.15, scale=0.5,
            bar_index=0, features={"rv_60": 0.01},   # pas d'ATR
        )
        assert d["action"] == "BUY"
        assert d["stop_pct"] > 0

    def test_exposure_cap_blocks_trade(self):
        rc = make_rc()
        d  = rc.decide(
            price=50_000.0, edge_final=0.15, scale=0.5,
            bar_index=0, features=features_ok(),
            current_gross_exposure_frac=1.0,   # cap atteint
        )
        assert d["action"] == "HOLD"
        assert "exposure_cap" in d["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. on_fill_pnl : mise à jour equity et compteurs
# ─────────────────────────────────────────────────────────────────────────────

class TestOnFillPnl:

    def test_equity_updated_after_profit(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(+100.0)
        assert abs(rc.state.equity - 10_100.0) < 0.01

    def test_equity_updated_after_loss(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-50.0)
        assert abs(rc.state.equity - 9_950.0) < 0.01

    def test_explicit_new_equity_overrides(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-50.0, new_equity=9_900.0)
        assert abs(rc.state.equity - 9_900.0) < 0.01

    def test_day_pnl_accumulated(self):
        rc = make_rc()
        rc.on_fill_pnl(+100.0)
        rc.on_fill_pnl(-30.0)
        assert abs(rc.state.day_pnl - 70.0) < 0.01

    def test_total_trades_counter(self):
        rc = make_rc()
        rc.on_fill_pnl(+10.0)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(+10.0)
        assert rc.state.total_trades == 3
        assert rc.state.total_wins == 2
        assert rc.state.total_losses == 1

    def test_zero_pnl_does_not_reset_consecutive(self):
        """Un résultat nul ne remet pas les pertes à zéro."""
        rc = make_rc()
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(-10.0)
        rc.on_fill_pnl(0.0)   # neutre
        assert rc.state.consecutive_losses == 2


# ─────────────────────────────────────────────────────────────────────────────
# 8. Persistence JSON (save / load)
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistence:

    def test_save_and_load_roundtrip(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-50.0)
        rc.on_fill_pnl(-30.0)
        rc.state.consecutive_losses = 2
        rc.state.total_trades = 5
        rc.state.current_day = "2024-01-15"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rc_state.json"
            rc.save_state(path)

            rc2 = RiskController.load_state(path)

            assert abs(rc2.state.equity - rc.state.equity) < 0.01
            assert rc2.state.consecutive_losses == rc.state.consecutive_losses
            assert rc2.state.total_trades == rc.state.total_trades
            assert rc2.state.current_day == rc.state.current_day
            assert abs(rc2.cfg.risk_per_trade - rc.cfg.risk_per_trade) < 1e-10

    def test_loaded_rc_makes_same_decisions(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-10.0)   # 1 perte

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            rc.save_state(path)
            rc2 = RiskController.load_state(path)

        assert rc2.state.consecutive_losses == 1
        d = rc2.decide(
            price=50_000.0, edge_final=0.15, scale=0.5,
            bar_index=100, features=features_ok(),
        )
        assert d["action"] == "BUY"

    def test_state_file_is_valid_json(self):
        rc = make_rc()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            rc.save_state(path)
            data = json.loads(path.read_text())
            assert "config" in data
            assert "state" in data
            assert "equity" in data["config"]
            assert "day_pnl" in data["state"]

    def test_save_creates_parent_dirs(self):
        rc = make_rc()
        with tempfile.TemporaryDirectory() as tmp:
            deep_path = Path(tmp) / "a" / "b" / "c" / "state.json"
            rc.save_state(deep_path)
            assert deep_path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Scénario complet : séquence de trades
# ─────────────────────────────────────────────────────────────────────────────

class TestFullScenario:

    def test_3_losses_then_blocked_then_reset(self):
        """Séquence : 3 trades, 3 pertes → blocage → reset → trade autorisé."""
        rc = make_rc(equity=10_000.0, cooldown_bars=1)

        # Trade 1 — accepté
        d1 = decide_buy(rc, bar=0)
        assert d1["action"] == "BUY"
        rc.on_fill_pnl(-30.0)

        # Trade 2 — accepté (bar 2 > 0 + cooldown 1)
        d2 = decide_buy(rc, bar=2)
        assert d2["action"] == "BUY"
        rc.on_fill_pnl(-30.0)

        # Trade 3 — accepté (bar 4)
        d3 = decide_buy(rc, bar=4)
        assert d3["action"] == "BUY"
        rc.on_fill_pnl(-30.0)

        # Bar 6 → 3 pertes consécutives → blocage
        d4 = decide_buy(rc, bar=6)
        assert d4["action"] == "HOLD"
        assert "consecutive_losses" in d4["reason"]

        # Reset journalier → deblocage
        rc.reset_day(equity=rc.state.equity)
        d5 = decide_buy(rc, bar=8)
        assert d5["action"] == "BUY"

    def test_daily_pnl_stop_blocks_correctly(self):
        """Perd 2% en 2 trades → stop journalier activé."""
        equity = 10_000.0
        rc = make_rc(equity=equity, daily_loss_limit_pct=0.02, cooldown_bars=1)
        limit = equity * 0.02   # $200

        # Trade 1 : perd $120
        d1 = decide_buy(rc, bar=0)
        assert d1["action"] == "BUY"
        rc.on_fill_pnl(-120.0)

        # Trade 2 : perd $90 → total -$210 > limite $200
        d2 = decide_buy(rc, bar=2)
        assert d2["action"] == "BUY"
        rc.on_fill_pnl(-90.0)

        # Trade 3 → bloqué
        d3 = decide_buy(rc, bar=4)
        assert d3["action"] == "HOLD"
        assert "daily_stop" in d3["reason"]

    def test_sizing_uses_current_equity_not_initial(self):
        """Après pertes, le sizing doit utiliser l'equity courante."""
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(-1_000.0)   # equity maintenant $9000
        assert abs(rc.state.equity - 9_000.0) < 0.01
        # Reset le jour pour repartir à $9000 sans stop journalier actif
        rc.reset_day(equity=9_000.0, day_str="2024-01-02")

        d = decide_buy(rc, bar=100)
        assert d["action"] == "BUY"
        # risk_budget = 0.2% × 9000 = $18 (pas $20)
        assert abs(d["risk_budget"] - 18.0) < 0.01

    def test_summary_reflects_state(self):
        rc = make_rc(equity=10_000.0)
        rc.on_fill_pnl(+50.0)
        rc.on_fill_pnl(-20.0)
        rc.on_fill_pnl(+30.0)
        s = rc.summary()
        assert s["total_trades"] == 3
        assert s["total_wins"] == 2
        assert s["total_losses"] == 1
        assert abs(s["equity"] - 10_060.0) < 0.01
        assert s["pnl_pct"] > 0
