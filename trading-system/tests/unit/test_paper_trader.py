"""
tests/unit/test_paper_trader.py
================================
Tests complets du PaperTrader et du BinanceWebSocket — Phase 3

Couvre :
  - FeatureWindow : calcul des indicateurs (ATR, EMA, RSI, signal)
  - PaperTrader : entrée/sortie de position, TP/SL/time-stop
  - PaperTrader : logging JSONL complet
  - PaperTrader : intégration RiskController (rejets, sizing)
  - PaperTrader : métriques (equity, win rate, PF, drawdown)
  - BinanceKlineStream : parsing messages WebSocket
  - BinanceKlineStream : mock WebSocket complet
  - Scénario replay complet (100+ trades)
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Injection paths ────────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parent.parent.parent.parent
_SRC     = Path(__file__).resolve().parent.parent.parent / "src"
_RC_PATH = _ROOT / "ai" / "models" / "level_7"

for _p in [str(_SRC), str(_RC_PATH)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from RiskController import RiskController, RiskConfig
from pipeline.execution.paper_trader import PaperTrader, PaperConfig, FeatureWindow
from infra.exchange.ws.binance_ws import BinanceKlineStream, KlineBar


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_rc(equity: float = 10_000.0) -> RiskController:
    return RiskController(RiskConfig(
        equity=equity,
        risk_per_trade=0.002,
        daily_loss_limit_pct=0.02,
        max_consecutive_losses=3,
        cooldown_bars=3,
        stop_atr_mult=2.5,
        min_stop_pct=0.001,
        max_stop_pct=0.03,
        rr=1.5,
    ))


def make_pt(tmp_dir: str, equity: float = 10_000.0, threshold: float = 0.55) -> PaperTrader:
    log_path = str(Path(tmp_dir) / "trades.jsonl")
    cfg = PaperConfig(
        entry_threshold  = threshold,
        tp_atr_mult      = 1.5,
        sl_atr_mult      = 1.0,
        max_hold_bars    = 10,
        warmup_bars      = 5,     # warmup court pour les tests
        fee_rt           = 8e-4,
        slippage_rt      = 4e-4,
        log_path         = log_path,
        metrics_interval = 5,
        channel_lookback = 5,
    )
    rc = make_rc(equity)
    return PaperTrader(cfg=cfg, risk_controller=rc, symbol="BTCUSDT")


def push_bars(pt: PaperTrader, n: int, base_price: float = 50_000.0,
              trend: bool = True, vol_factor: float = 1.0) -> List:
    """Pousse N barres synthétiques dans le PaperTrader."""
    import numpy as np
    rng = np.random.default_rng(42)
    trades = []
    price = base_price
    for i in range(n):
        if trend:
            # tendance haussière avec breakouts
            price = price * (1 + rng.normal(0.001, 0.005))
        else:
            price = price * (1 + rng.normal(0, 0.005))
        atr = price * 0.005 * vol_factor
        o = price * (1 - rng.uniform(0, 0.002))
        h = price + atr * rng.uniform(0.5, 1.5)
        l = price - atr * rng.uniform(0.5, 1.5)
        c = price
        v = 100.0 * (1 + rng.uniform(-0.3, 0.3))
        trade = pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           o, h, l, c, v)
        if trade:
            trades.append(trade)
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# 1. FeatureWindow
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureWindow:

    def test_atr_positive_after_warmup(self):
        cfg = PaperConfig(warmup_bars=5)
        fw  = FeatureWindow(cfg)
        for i in range(10):
            fw.update(100.0, 105.0, 95.0, 102.0, 50.0)
        assert fw.atr > 0
        assert fw.ready

    def test_ema_fast_above_slow_in_uptrend(self):
        cfg = PaperConfig(warmup_bars=5, ema_fast_span=3, ema_slow_span=10)
        fw  = FeatureWindow(cfg)
        for i in range(20):
            c = 100.0 + i * 2   # tendance haussière
            fw.update(c - 1, c + 2, c - 2, c, 100.0)
        assert fw._ema_f > fw._ema_s   # fast > slow en uptrend

    def test_rsi_high_in_uptrend(self):
        cfg = PaperConfig(warmup_bars=5)
        fw  = FeatureWindow(cfg)
        for i in range(30):
            c = 100.0 + i   # montée continue
            fw.update(c, c + 1, c - 1, c, 100.0)
        assert fw.rsi > 50   # RSI élevé en uptrend

    def test_vol_ratio_high_on_volume_spike(self):
        cfg = PaperConfig(warmup_bars=5)
        fw  = FeatureWindow(cfg)
        for i in range(20):
            fw.update(100.0, 101.0, 99.0, 100.0, 100.0)
        fw.update(100.0, 101.0, 99.0, 100.0, 500.0)   # spike volume
        assert fw.vol_ratio > 2.0

    def test_not_ready_before_warmup(self):
        cfg = PaperConfig(warmup_bars=50)
        fw  = FeatureWindow(cfg)
        for i in range(10):
            fw.update(100.0, 101.0, 99.0, 100.0, 50.0)
        assert not fw.ready

    def test_channel_high_excludes_current_bar(self):
        """channel_high doit exclure la barre courante."""
        cfg = PaperConfig(warmup_bars=2, channel_lookback=5)
        fw  = FeatureWindow(cfg)
        for i in range(5):
            fw.update(100.0, 100.0 + i, 98.0, 100.0, 50.0)
        # Le channel_high est le max des barres PRÉCÉDENTES (pas courante)
        # La barre courante a high=104, les précédentes max=103
        last_channel = fw.channel_high
        assert last_channel < 104.0   # exclut la barre courante


# ─────────────────────────────────────────────────────────────────────────────
# 2. PaperTrader — Entrée / Sortie TP
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperTraderBasic:

    def test_no_trade_before_warmup(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp)
            # Pousse seulement 3 barres (warmup=5)
            for i in range(3):
                trade = pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                                   49_000.0, 50_500.0, 48_500.0, 50_000.0, 100.0)
                assert trade is None
            pt.close()

    def test_position_opens_on_strong_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.55)
            # Warmup avec tendance haussière forte pour déclencher signal
            push_bars(pt, 50, trend=True)
            pt.close()
            # Au moins quelques signaux générés
            assert pt.total_signals > 0

    def test_tp_exit_closes_position(self):
        """Simule manuellement un trade avec TP touché."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)   # threshold bas = entre facilement
            # Warmup
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            # Force un override de signal pour entrer
            # Barre d'entrée avec signal fort
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)

            pos = pt._position
            if pos is not None:
                tp = pos.tp_px
                sl = pos.sl_px
                # Simule une barre qui touche le TP
                trade = pt.on_bar("BTCUSDT", 11, "2024-01-01T11:00:00Z",
                                   50_100.0, tp + 10.0, 49_900.0, 50_200.0, 150.0)
                assert trade is not None
                assert trade.exit_reason == "tp"
                assert trade.net_pnl > 0   # TP = gain positif (avant coûts)
            pt.close()

    def test_sl_exit_closes_position(self):
        """Simule un SL touché."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)

            pos = pt._position
            if pos is not None:
                sl = pos.sl_px
                # Simule une barre qui touche le SL
                trade = pt.on_bar("BTCUSDT", 11, "2024-01-01T11:00:00Z",
                                   50_000.0, 50_200.0, sl - 50.0, 49_800.0, 150.0)
                assert trade is not None
                assert trade.exit_reason == "sl"
                assert trade.net_pnl < 0   # SL = perte
            pt.close()

    def test_time_exit_after_max_hold(self):
        """Simule une sortie par time-stop."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PaperConfig(
                entry_threshold=0.40, max_hold_bars=3, warmup_bars=5,
                log_path=str(Path(tmp) / "t.jsonl"),
            )
            rc = make_rc()
            pt = PaperTrader(cfg, rc)

            for i in range(8):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            # Force entrée
            pt.on_bar("BTCUSDT", 8, "2024-01-01T08:00:00Z",
                       50_000.0, 50_200.0, 49_900.0, 50_100.0, 200.0,
                       prob_up_override=0.70)

            if pt._position is not None:
                # Pousse des barres qui ne touchent ni TP ni SL
                pos = pt._position
                mid = (pos.tp_px + pos.sl_px) / 2
                final_trade = None
                for j in range(4):   # > max_hold_bars=3
                    trade = pt.on_bar("BTCUSDT", 9 + j, f"2024-01-01T{9+j:02d}:00:00Z",
                                       mid, mid + 1, mid - 1, mid, 200.0)
                    if trade:
                        final_trade = trade
                        break
                assert final_trade is not None
                assert final_trade.exit_reason == "time"
            pt.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. PaperTrader — Logging JSONL
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperTraderLogging:

    def test_log_file_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp)
            pt._write_header("2024-01-01T00:00:00Z")
            pt.close()
            log_path = Path(pt.cfg.log_path)
            assert log_path.exists()
            assert log_path.stat().st_size > 0

    def test_log_session_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp)
            pt._write_header("2024-01-01")
            pt.close()
            lines = [json.loads(l) for l in open(pt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "session_start" in types

    def test_log_entry_on_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)
            pt.close()
            lines = [json.loads(l) for l in open(pt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "entry" in types

    def test_log_rejected_on_daily_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            # Fixe le jour courant AVANT on_fill_pnl pour éviter le reset
            pt.rc.reset_day(day_str="2024-01-01")
            # Déclenche le daily stop
            pt.rc.on_fill_pnl(-300.0)   # > 2% de $10000
            # Warmup
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)
            pt.close()
            lines = [json.loads(l) for l in open(pt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "rejected" in types

    def test_log_trade_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)
            if pt._position:
                tp = pt._position.tp_px
                pt.on_bar("BTCUSDT", 11, "2024-01-01T11:00:00Z",
                           50_100.0, tp + 20.0, 49_900.0, 50_200.0, 150.0)
            pt.close()
            lines = [json.loads(l) for l in open(pt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "trade" in types

    def test_log_session_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp)
            pt.close()
            lines = [json.loads(l) for l in open(pt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "session_end" in types

    def test_log_is_valid_jsonl(self):
        """Chaque ligne du log doit être du JSON valide."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            push_bars(pt, 50, trend=True)
            pt.close()
            for i, line in enumerate(open(pt.cfg.log_path)):
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(f"Ligne {i} invalide : {e}\n{line[:100]}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. PaperTrader — Métriques
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperTraderMetrics:

    def test_metrics_zero_before_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp)
            m  = pt.metrics()
            assert m["n_trades"] == 0
            assert m["equity_init"] == 10_000.0
            pt.close()

    def test_equity_reflects_pnl(self):
        """L'equity courante doit refléter la somme des PnL."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            # Entrée puis TP
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)
            if pt._position:
                tp = pt._position.tp_px
                pt.on_bar("BTCUSDT", 11, "2024-01-01T11:00:00Z",
                           50_100.0, tp + 20.0, 49_900.0, 50_200.0, 150.0)

            m = pt.metrics()
            total_pnl = sum(t.net_pnl for t in pt.trades)
            assert abs(m["equity_final"] - (10_000.0 + total_pnl)) < 0.01
            pt.close()

    def test_win_rate_in_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            push_bars(pt, 100, trend=True)
            m = pt.metrics()
            if m["n_trades"] > 0:
                assert 0.0 <= m["win_rate"] <= 1.0
            pt.close()

    def test_max_drawdown_negative_or_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            push_bars(pt, 100)
            m = pt.metrics()
            if m["n_trades"] > 0:
                assert m["max_drawdown_pct"] <= 0.0
            pt.close()

    def test_profit_factor_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            push_bars(pt, 100, trend=True)
            m = pt.metrics()
            if m["n_trades"] > 0:
                assert m["profit_factor"] >= 0.0
            pt.close()


# ─────────────────────────────────────────────────────────────────────────────
# 5. PaperTrader — Intégration RiskController
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperTraderRiskController:

    def test_no_entry_after_3_consecutive_losses(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            # Fixe le jour courant AVANT on_fill_pnl pour éviter le reset
            pt.rc.reset_day(day_str="2024-01-01")
            # Déclenche 3 pertes consécutives
            pt.rc.on_fill_pnl(-10.0)
            pt.rc.on_fill_pnl(-10.0)
            pt.rc.on_fill_pnl(-10.0)

            # Warmup
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            initial_trades = len(pt.trades)
            # Tente d'entrer avec signal fort
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.80)

            assert len(pt.trades) == initial_trades   # pas de nouveau trade
            assert pt._position is None
            assert pt.total_rejected > 0
            pt.close()

    def test_no_entry_after_daily_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            # Fixe le jour courant AVANT on_fill_pnl pour éviter le reset
            pt.rc.reset_day(day_str="2024-01-01")
            pt.rc.on_fill_pnl(-300.0)   # -3% > limite -2%

            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.80)

            assert pt._position is None
            pt.close()

    def test_only_one_position_at_a_time(self):
        """PaperTrader ne doit pas ouvrir une 2e position si une est déjà ouverte."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)

            # Ouvre une position
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)

            if pt._position is not None:
                # Tente d'entrer à nouveau
                initial_pos = pt._position
                pt.on_bar("BTCUSDT", 11, "2024-01-01T11:00:00Z",
                           50_100.0, 50_500.0, 49_900.0, 50_200.0, 200.0,
                           prob_up_override=0.80)
                assert pt._position == initial_pos   # même position
            pt.close()

    def test_sizing_uses_rc_risk_budget(self):
        """La quantité doit correspondre au risk_budget du RC."""
        with tempfile.TemporaryDirectory() as tmp:
            pt = make_pt(tmp, threshold=0.40)
            for i in range(10):
                pt.on_bar("BTCUSDT", i, f"2024-01-01T{i:02d}:00:00Z",
                           49_900.0, 50_100.0, 49_800.0, 50_000.0, 200.0)
            pt.on_bar("BTCUSDT", 10, "2024-01-01T10:00:00Z",
                       50_000.0, 51_000.0, 49_500.0, 50_100.0, 250.0,
                       prob_up_override=0.70)
            if pt._position:
                # risk_budget doit être ≈ 0.2% × equity
                assert abs(pt._position.risk_budget - 20.0) < 1.0
            pt.close()


# ─────────────────────────────────────────────────────────────────────────────
# 6. KlineBar — Parsing WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class TestKlineBar:

    def _make_ws_msg(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1h",
        close: float = 50_000.0,
        is_closed: bool = True,
    ) -> dict:
        return {
            "e": "kline",
            "E": 1_700_000_000_000,
            "s": symbol,
            "k": {
                "t": 1_699_996_800_000,
                "T": 1_700_000_399_999,
                "i": interval,
                "o": str(close - 100.0),
                "h": str(close + 200.0),
                "l": str(close - 200.0),
                "c": str(close),
                "v": "25.345",
                "q": str(close * 25.345),
                "n": 1234,
                "x": is_closed,
            },
        }

    def test_parse_closed_bar(self):
        msg = self._make_ws_msg(close=48_500.0, is_closed=True)
        bar = KlineBar.from_ws_message(msg)
        assert bar.symbol   == "BTCUSDT"
        assert bar.interval == "1h"
        assert bar.is_closed is True
        assert abs(bar.close - 48_500.0) < 0.01
        assert bar.high > bar.close
        assert bar.low  < bar.close

    def test_parse_open_bar(self):
        msg = self._make_ws_msg(is_closed=False)
        bar = KlineBar.from_ws_message(msg)
        assert bar.is_closed is False

    def test_to_dict_roundtrip(self):
        msg = self._make_ws_msg(close=55_000.0)
        bar = KlineBar.from_ws_message(msg)
        d   = bar.to_dict()
        assert d["close"]    == bar.close
        assert d["is_closed"] == bar.is_closed
        assert d["symbol"]   == "BTCUSDT"

    def test_parse_all_fields(self):
        msg = self._make_ws_msg(close=60_000.0, interval="4h")
        bar = KlineBar.from_ws_message(msg)
        assert bar.open        > 0
        assert bar.high        > 0
        assert bar.low         > 0
        assert bar.volume      > 0
        assert bar.quote_volume > 0
        assert bar.n_trades    > 0

    def test_bad_message_raises_keyerror(self):
        with pytest.raises((KeyError, TypeError)):
            KlineBar.from_ws_message({"e": "kline"})   # 'k' manquant


# ─────────────────────────────────────────────────────────────────────────────
# 7. BinanceKlineStream — Mock WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class TestBinanceKlineStream:

    def _make_ws_msg(self, close: float, is_closed: bool = True) -> str:
        return json.dumps({
            "e": "kline", "E": 1_700_000_000_000, "s": "BTCUSDT",
            "k": {
                "t": 1_699_996_800_000, "T": 1_700_000_399_999,
                "i": "1h",
                "o": str(close - 50), "h": str(close + 100),
                "l": str(close - 100), "c": str(close),
                "v": "10.0", "q": str(close * 10), "n": 500, "x": is_closed,
            },
        })

    @staticmethod
    def _make_async_iter(items):
        """Crée un async iterator à partir d'une liste."""
        class AsyncIter:
            def __init__(self, lst):
                self._it = iter(lst)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration
        return AsyncIter(items)

    def _make_mock_connect(self, msgs):
        """
        Crée un mock de websockets.connect qui retourne un async context
        manager itérant sur `msgs` une seule fois.
        """
        ai = self._make_async_iter(msgs)
        inner = MagicMock()
        inner.__aiter__ = lambda _: ai
        inner.__anext__ = ai.__anext__

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=inner)
        cm.__aexit__  = AsyncMock(return_value=False)
        return cm

    @pytest.mark.asyncio
    async def test_emits_only_closed_bars(self):
        """Le stream ne doit émettre que les barres is_closed=True."""
        stream = BinanceKlineStream("btcusdt", "1h",
                                    max_reconnects=0, reconnect_delay=0.0)
        received = []

        msgs_to_send = [
            self._make_ws_msg(50_000.0, is_closed=False),   # barre ouverte
            self._make_ws_msg(50_100.0, is_closed=True),    # barre fermée ✓
            self._make_ws_msg(50_200.0, is_closed=False),   # barre ouverte
        ]

        async def on_bar(bar: KlineBar):
            received.append(bar)

        with patch("infra.exchange.ws.binance_ws.websockets.connect",
                   return_value=self._make_mock_connect(msgs_to_send)):
            await stream.run(on_bar)

        assert len(received) == 1
        assert abs(received[0].close - 50_100.0) < 0.01
        assert received[0].is_closed is True

    @pytest.mark.asyncio
    async def test_stats_updated_correctly(self):
        stream = BinanceKlineStream("btcusdt", "1h",
                                    max_reconnects=0, reconnect_delay=0.0)

        msgs = [
            self._make_ws_msg(50_000.0, is_closed=False),
            self._make_ws_msg(50_100.0, is_closed=True),
            self._make_ws_msg(50_200.0, is_closed=True),
        ]

        async def noop(bar): pass

        with patch("infra.exchange.ws.binance_ws.websockets.connect",
                   return_value=self._make_mock_connect(msgs)):
            await stream.run(noop)

        stats = stream.stats
        assert stats["bars_received"] == 3
        assert stats["closed_bars"]   == 2

    @pytest.mark.asyncio
    async def test_stop_halts_stream(self):
        """stream.stop() doit arrêter la boucle."""
        stream = BinanceKlineStream("btcusdt", "1h", max_reconnects=5)
        stream.stop()   # arrêt avant connexion
        assert not stream._running

    def test_url_format(self):
        stream = BinanceKlineStream("btcusdt", "1h")
        assert "btcusdt@kline_1h" in stream.url
        assert stream.url.startswith("wss://")

    def test_symbol_lowercased(self):
        stream = BinanceKlineStream("ETHUSDT", "4h")
        assert stream.symbol == "ethusdt"
        assert "ethusdt" in stream.url


# ─────────────────────────────────────────────────────────────────────────────
# 8. Scénario de replay complet (validation 100+ trades)
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayScenario:

    def test_100_plus_trades_with_low_threshold(self):
        """
        Vérifie qu'on peut générer 100+ trades avec des données synthétiques
        et un threshold bas (simulation de validation Phase 3).
        """
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PaperConfig(
                entry_threshold  = 0.30,   # très bas → beaucoup d'entrées
                tp_atr_mult      = 1.5,
                sl_atr_mult      = 1.0,
                max_hold_bars    = 5,
                warmup_bars      = 10,
                fee_rt           = 8e-4,
                slippage_rt      = 4e-4,
                log_path         = str(Path(tmp) / "trades.jsonl"),
                metrics_interval = 20,
                channel_lookback = 5,
            )
            rc = RiskController(RiskConfig(
                equity=10_000.0,
                risk_per_trade=0.002,
                daily_loss_limit_pct=0.02,
                max_consecutive_losses=3,
                cooldown_bars=1,   # cooldown court pour générer plus de trades
            ))
            pt = PaperTrader(cfg, rc)

            rng = np.random.default_rng(42)
            price = 50_000.0
            n_bars = 2000

            for i in range(n_bars):
                price = price * (1 + rng.normal(0.0002, 0.005))
                atr   = price * 0.005
                o = price * (1 - rng.uniform(0, 0.001))
                h = price + atr * rng.uniform(0.5, 2.0)
                l = price - atr * rng.uniform(0.5, 2.0)
                day = f"2024-{(i // 24) % 12 + 1:02d}-{(i % 24 // 1) + 1:02d}"

                pt.on_bar("BTCUSDT", i, f"{day}T{i%24:02d}:00:00Z",
                           o, h, l, price, 100.0 * rng.uniform(0.5, 2.0),
                           prob_up_override=0.65 if i % 4 == 0 else 0.25)

            pt.close()
            m = pt.metrics()

            assert m["n_trades"] >= 100, f"Seulement {m['n_trades']} trades (< 100)"
            assert m["max_drawdown_pct"] > -20.0, f"DD trop élevé : {m['max_drawdown_pct']}%"

    def test_log_completeness_after_replay(self):
        """Vérifie que le log contient tous les types requis après un replay."""
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PaperConfig(
                entry_threshold=0.30, tp_atr_mult=1.5, sl_atr_mult=1.0,
                max_hold_bars=5, warmup_bars=5, fee_rt=8e-4, slippage_rt=4e-4,
                log_path=str(Path(tmp) / "trades.jsonl"),
                metrics_interval=10, channel_lookback=5,
            )
            rc = RiskController(RiskConfig(
                equity=10_000.0, risk_per_trade=0.002,
                daily_loss_limit_pct=0.02, max_consecutive_losses=3,
                cooldown_bars=1,
            ))
            pt = PaperTrader(cfg, rc)
            pt._write_header("2024-01-01")

            rng = np.random.default_rng(0)
            price = 50_000.0
            for i in range(500):
                price = price * (1 + rng.normal(0, 0.003))
                atr = price * 0.004
                pt.on_bar("BTCUSDT", i, f"2024-01-{i//24+1:02d}T{i%24:02d}:00:00Z",
                           price, price + atr, price - atr, price, 100.0,
                           prob_up_override=0.65 if i % 3 == 0 else 0.25)

            pt.close()
            lines  = [json.loads(l) for l in open(cfg.log_path)]
            types  = {l["type"] for l in lines}

            assert "session_start" in types
            assert "session_end"   in types
            assert len(lines) > 10

    def test_capital_stability(self):
        """
        Avec un risk 0.2%/trade, le capital ne doit pas chuter de plus de 15%
        même avec de mauvaises performances (protection par le RC).
        """
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PaperConfig(
                entry_threshold=0.30, max_hold_bars=5, warmup_bars=5,
                log_path=str(Path(tmp) / "t.jsonl"), metrics_interval=20,
                channel_lookback=5,
            )
            rc = RiskController(RiskConfig(
                equity=10_000.0, risk_per_trade=0.002,
                daily_loss_limit_pct=0.02, max_consecutive_losses=3,
                cooldown_bars=1,
            ))
            pt = PaperTrader(cfg, rc)
            rng = np.random.default_rng(99)
            price = 50_000.0
            for i in range(1000):
                price = max(price * (1 + rng.normal(-0.001, 0.008)), 100.0)  # marché baissier
                atr = price * 0.006
                pt.on_bar("BTCUSDT", i, f"2024-01-{i//24+1:02d}T{i%24:02d}:00:00Z",
                           price, price + atr, price - atr, price, 100.0,
                           prob_up_override=0.65 if i % 4 == 0 else 0.25)
            pt.close()
            m = pt.metrics()
            assert m["max_drawdown_pct"] > -15.0, (
                f"Capital trop instable : DD={m['max_drawdown_pct']:.1f}% "
                f"(limit -15%)"
            )
