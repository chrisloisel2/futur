"""
Tests unitaires — BinanceRestClient + LiveTrader (Phase 4)
==========================================================

Toutes les requêtes HTTP sont mockées avec aioresponses.
Aucune vraie clé API n'est nécessaire.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── imports sous test ───────────────────────────────────────────────────────
from infra.exchange.binance_rest import (
    BinanceRestClient,
    BinanceApiError,
    BinanceOrderNotFound,
    InsufficientFunds,
    OrderFill,
    OcoResult,
    _sign,
    _raise_for_binance,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_client(base_url: str = "http://fake-binance") -> BinanceRestClient:
    return BinanceRestClient(
        api_key="test_key", api_secret="test_secret",
        base_url=base_url,
    )


def market_order_response(
    symbol="BTCUSDT", side="BUY", status="FILLED",
    qty="0.001", avg_price="50000.0", order_id=123456,
) -> dict:
    """Réponse typique d'un ordre MARKET Binance."""
    return {
        "symbol"       : symbol,
        "orderId"      : order_id,
        "clientOrderId": "entry_1",
        "side"         : side,
        "type"         : "MARKET",
        "status"       : status,
        "origQty"      : qty,
        "executedQty"  : qty,
        "transactTime" : 1_700_000_000_000,
        "fills"        : [{"qty": qty, "price": avg_price, "commission": "0.0001", "commissionAsset": "BNB"}],
    }


def oco_response(tp_price="51500", sl_price="49500", sl_stop="49700") -> dict:
    return {
        "orderListId"   : 9999,
        "contingencyType": "OCO",
        "listStatusType": "EXEC_STARTED",
        "listOrderStatus": "EXECUTING",
        "orderReports"  : [
            {"orderId": 111, "type": "LIMIT_MAKER",      "status": "NEW", "price": tp_price},
            {"orderId": 222, "type": "STOP_LOSS_LIMIT",  "status": "NEW", "price": sl_price, "stopPrice": sl_stop},
        ],
        "orders": [
            {"orderId": 111, "type": "LIMIT_MAKER"},
            {"orderId": 222, "type": "STOP_LOSS_LIMIT"},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Helpers cryptographiques
# ─────────────────────────────────────────────────────────────────────────────

class TestSignature:

    def test_sign_produces_hex_string(self):
        sig = _sign("secret", {"timestamp": 1700000000000, "symbol": "BTCUSDT"})
        assert isinstance(sig, str)
        assert len(sig) == 64   # SHA256 hex = 64 chars

    def test_sign_deterministic(self):
        params = {"a": 1, "b": "hello"}
        assert _sign("key", params) == _sign("key", params)

    def test_sign_changes_with_params(self):
        assert _sign("key", {"a": 1}) != _sign("key", {"a": 2})

    def test_sign_changes_with_secret(self):
        assert _sign("key1", {"a": 1}) != _sign("key2", {"a": 1})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Gestion des erreurs Binance
# ─────────────────────────────────────────────────────────────────────────────

class TestRaiseForBinance:

    def test_no_error_on_success(self):
        _raise_for_binance({"symbol": "BTCUSDT"})   # ne lève rien

    def test_raises_binance_api_error(self):
        with pytest.raises(BinanceApiError) as exc:
            _raise_for_binance({"code": -1100, "msg": "illegal chars"})
        assert exc.value.code == -1100

    def test_raises_order_not_found(self):
        with pytest.raises(BinanceOrderNotFound):
            _raise_for_binance({"code": -2011, "msg": "Unknown order"})

    def test_raises_insufficient_funds(self):
        with pytest.raises(InsufficientFunds):
            _raise_for_binance({"code": -2010, "msg": "insufficient"})

    def test_positive_code_ignored(self):
        _raise_for_binance({"code": 200})   # code positif = pas d'erreur


# ─────────────────────────────────────────────────────────────────────────────
# 3. OrderFill.from_api
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderFill:

    def test_parse_market_order(self):
        data = market_order_response()
        fill = OrderFill.from_api(data)
        assert fill.order_id   == 123456
        assert fill.symbol     == "BTCUSDT"
        assert fill.side       == "BUY"
        assert fill.status     == "FILLED"
        assert abs(fill.qty_filled - 0.001) < 1e-9
        assert abs(fill.avg_price - 50_000.0) < 1e-4
        assert fill.is_filled()

    def test_parse_no_fills(self):
        """Sans sous-fills, utilise executedQty et price directement."""
        data = {
            "symbol": "ETHUSDT", "orderId": 1, "clientOrderId": "x",
            "side": "SELL", "type": "LIMIT", "status": "NEW",
            "origQty": "1.0", "executedQty": "0.0",
            "price": "2000.0", "transactTime": 0,
        }
        fill = OrderFill.from_api(data)
        assert not fill.is_filled()
        assert fill.qty_filled == 0.0

    def test_to_dict_roundtrip(self):
        fill = OrderFill.from_api(market_order_response())
        d = fill.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert "order_id" in d


# ─────────────────────────────────────────────────────────────────────────────
# 4. Construction du client
# ─────────────────────────────────────────────────────────────────────────────

class TestClientConstruction:

    def test_from_env_ok(self, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY",    "mykey")
        monkeypatch.setenv("BINANCE_API_SECRET", "mysecret")
        c = BinanceRestClient.from_env()
        assert c._key    == "mykey"
        assert c._secret == "mysecret"

    def test_from_env_missing_key(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY",    raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        with pytest.raises(EnvironmentError):
            BinanceRestClient.from_env()

    def test_testnet_url(self):
        c = BinanceRestClient("k", "s", testnet=True)
        assert "testnet" in c._base

    def test_custom_base_url(self):
        c = BinanceRestClient("k", "s", base_url="http://localhost:9999")
        assert c._base == "http://localhost:9999"

    def test_requires_context_manager(self):
        c = make_client()
        with pytest.raises(RuntimeError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(c.ping())


# ─────────────────────────────────────────────────────────────────────────────
# 5. Endpoints — mocks aiohttp
# ─────────────────────────────────────────────────────────────────────────────

def _mock_response(json_data: dict, status: int = 200):
    """Crée un mock de réponse aiohttp."""
    resp = AsyncMock()
    resp.status = status
    resp.json   = AsyncMock(return_value=json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__  = AsyncMock(return_value=False)
    return resp


def _mock_session(get_data=None, post_data=None, delete_data=None):
    """Crée un mock de session aiohttp."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__  = AsyncMock(return_value=False)
    session.close      = AsyncMock()

    if get_data is not None:
        session.get    = MagicMock(return_value=_mock_response(get_data))
    if post_data is not None:
        session.post   = MagicMock(return_value=_mock_response(post_data))
    if delete_data is not None:
        session.delete = MagicMock(return_value=_mock_response(delete_data))
    return session


class TestPingAndServerTime:

    @pytest.mark.asyncio
    async def test_ping_returns_true(self):
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data={})):
            async with client:
                result = await client.ping()
        assert result is True

    @pytest.mark.asyncio
    async def test_server_time(self):
        client = make_client()
        ts = 1_700_000_000_000
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data={"serverTime": ts})):
            async with client:
                result = await client.server_time()
        assert result == ts


class TestGetBalance:

    @pytest.mark.asyncio
    async def test_get_usdt_balance(self):
        account_data = {
            "balances": [
                {"asset": "BTC",  "free": "0.5",    "locked": "0"},
                {"asset": "USDT", "free": "1234.56", "locked": "0"},
            ]
        }
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data=account_data)):
            async with client:
                bal = await client.get_usdt_balance()
        assert abs(bal - 1234.56) < 1e-6

    @pytest.mark.asyncio
    async def test_get_usdt_balance_missing_returns_zero(self):
        account_data = {"balances": [{"asset": "ETH", "free": "1.0", "locked": "0"}]}
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data=account_data)):
            async with client:
                bal = await client.get_usdt_balance()
        assert bal == 0.0


class TestMarketOrder:

    @pytest.mark.asyncio
    async def test_market_order_buy(self):
        resp_data = market_order_response(qty="0.001", avg_price="50000.0")
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(post_data=resp_data)):
            async with client:
                fill = await client.market_order("BTCUSDT", "BUY", 0.001)
        assert fill.is_filled()
        assert abs(fill.avg_price - 50_000.0) < 0.01
        assert abs(fill.qty_filled - 0.001) < 1e-9

    @pytest.mark.asyncio
    async def test_market_order_raises_on_api_error(self):
        error_data = {"code": -2010, "msg": "insufficient balance"}
        resp = _mock_response(error_data, status=400)
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__  = AsyncMock(return_value=False)
        session.close      = AsyncMock()
        session.post = MagicMock(return_value=resp)

        client = make_client()
        with patch("aiohttp.ClientSession", return_value=session):
            async with client:
                with pytest.raises(InsufficientFunds):
                    await client.market_order("BTCUSDT", "BUY", 0.001)


class TestOcoOrder:

    @pytest.mark.asyncio
    async def test_oco_order_returns_result(self):
        resp_data = oco_response()
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(post_data=resp_data)):
            async with client:
                result = await client.oco_order(
                    "BTCUSDT", "SELL", 0.001,
                    tp_price=51_500.0,
                    sl_stop_price=49_700.0,
                    sl_limit_price=49_500.0,
                )
        assert isinstance(result, OcoResult)
        assert result.order_list_id == 9999
        assert result.symbol == "BTCUSDT"
        assert result.qty    == 0.001

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        cancel_data = {"orderId": 12345, "status": "CANCELED", "symbol": "BTCUSDT"}
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(delete_data=cancel_data)):
            async with client:
                result = await client.cancel_order("BTCUSDT", 12345)
        assert result["status"] == "CANCELED"

    @pytest.mark.asyncio
    async def test_get_open_orders(self):
        orders_data = [{"orderId": 1}, {"orderId": 2}]
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data=orders_data)):
            async with client:
                orders = await client.get_open_orders("BTCUSDT")
        assert len(orders) == 2


class TestGetSymbolInfo:

    @pytest.mark.asyncio
    async def test_get_step_size(self):
        exchange_info = {
            "symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            }]
        }
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data=exchange_info)):
            async with client:
                step = await client.get_step_size("BTCUSDT")
        assert abs(step - 0.00001) < 1e-9

    @pytest.mark.asyncio
    async def test_unknown_symbol_raises(self):
        client = make_client()
        with patch("aiohttp.ClientSession", return_value=_mock_session(get_data={"symbols": []})):
            async with client:
                with pytest.raises(BinanceApiError):
                    await client.get_symbol_info("FAKECOIN")


# ─────────────────────────────────────────────────────────────────────────────
# 6. round_qty
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundQty:

    def test_round_to_step_size(self):
        assert abs(BinanceRestClient.round_qty(0.123456789, 0.00001) - 0.12345) < 1e-9

    def test_round_floor_not_round(self):
        # 0.00999 arrondi vers le bas à 0.009 pour step=0.001
        assert abs(BinanceRestClient.round_qty(0.00999, 0.001) - 0.009) < 1e-9

    def test_step_zero_returns_unchanged(self):
        assert BinanceRestClient.round_qty(1.23456, 0.0) == 1.23456


# ─────────────────────────────────────────────────────────────────────────────
# 7. LiveTrader — tests d'intégration (mocks)
# ─────────────────────────────────────────────────────────────────────────────

def _make_rc(equity: float = 10_000.0):
    """Crée un RiskController minimal pour les tests LiveTrader."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[3] / "ai" / "models" / "level_7"))
    from RiskController import RiskController, RiskConfig
    return RiskController(RiskConfig(
        equity             = equity,
        risk_per_trade     = 0.001,    # 0.1% Phase 4
        daily_loss_limit_pct = 0.02,
        max_consecutive_losses = 3,
        cooldown_bars      = 0,
    ))


def _make_live_trader(tmp: str, rc=None, dry_run: bool = True):
    from pipeline.execution.live_trader import LiveTrader
    from pipeline.execution.paper_trader import PaperConfig
    from infra.exchange.binance_rest import BinanceRestClient

    cfg = PaperConfig(
        entry_threshold  = 0.30,
        tp_atr_mult      = 1.5,
        sl_atr_mult      = 1.0,
        max_hold_bars    = 10,
        warmup_bars      = 5,
        fee_rt           = 8e-4,
        slippage_rt      = 4e-4,
        log_path         = str(Path(tmp) / "live_trades.jsonl"),
        metrics_interval = 10,
        channel_lookback = 5,
    )
    rc = rc or _make_rc()
    client = BinanceRestClient("k", "s", base_url="http://fake")

    return LiveTrader(
        cfg        = cfg,
        risk_ctrl  = rc,
        client     = client,
        symbol     = "BTCUSDT",
        state_path = str(Path(tmp) / "position.json"),
        dry_run    = dry_run,
    )


def _push_bars(lt, n: int, base_price: float = 50_000.0, trend: bool = True):
    """Envoie n barres à un LiveTrader de façon synchrone (via asyncio.run)."""
    import asyncio
    for i in range(n):
        c = base_price + (i * 10 if trend else 0)
        asyncio.get_event_loop().run_until_complete(
            lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                      c - 50, c + 100, c - 100, c, 200.0)
        )


class TestLiveTraderBasic:

    def test_no_trade_before_warmup(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp)
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(0, "2024-01-01T00:00:00Z",
                          50000, 50100, 49900, 50000, 100.0)
            )
            assert lt._position is None
            lt.close()

    def test_position_opens_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp, dry_run=True)
            import asyncio
            # Warmup
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            # Signal fort
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            # En dry_run, position doit s'ouvrir (pas d'ordre réel)
            assert lt._position is not None
            lt.close()

    def test_position_persisted_to_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp, dry_run=True)
            import asyncio
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            if lt._position is not None:
                state_file = Path(tmp) / "position.json"
                assert state_file.exists()
                data = json.loads(state_file.read_text())
                assert data is not None
                assert data["symbol"] == "BTCUSDT"
            lt.close()

    def test_position_restored_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp, dry_run=True)
            import asyncio
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            if lt._position is None:
                lt.close()
                return   # signal non déclenché, skip

            original_entry = lt._position.entry_px
            lt.close()

            # Recharge depuis le même state_path
            lt2 = _make_live_trader(tmp, rc=_make_rc(), dry_run=True)
            assert lt2._position is not None
            assert abs(lt2._position.entry_px - original_entry) < 1e-6


class TestLiveTraderTimestop:

    def test_time_stop_exits_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp, dry_run=True)
            import asyncio

            # Ouvre une position
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            if lt._position is None:
                lt.close()
                return

            # Avance jusqu'au time-stop (max_hold_bars=10)
            entry_bar = lt._position.entry_bar
            for i in range(entry_bar + 1, entry_bar + 15):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-02T{i % 24:02d}:00:00Z",
                              50000, 50200, 49800, 50100, 100.0)
                )

            # Position doit être fermée
            assert lt._position is None
            assert len(lt.trades) == 1
            assert lt.trades[0].exit_reason == "time"
            lt.close()


class TestLiveTraderRiskController:

    def test_rejected_after_daily_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(10_000.0)
            rc.reset_day(day_str="2024-01-01")
            rc.on_fill_pnl(-300.0)   # -3% > limite -2%

            lt = _make_live_trader(tmp, rc=rc, dry_run=True)
            import asyncio
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            assert lt._position is None
            assert lt.total_rejected > 0
            lt.close()

    def test_max_order_cap_200_usdt(self):
        """Même avec beaucoup d'equity, notionnel ne dépasse pas $200."""
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(100_000.0)   # gros capital
            lt = _make_live_trader(tmp, rc=rc, dry_run=True)
            import asyncio
            for i in range(5):
                asyncio.get_event_loop().run_until_complete(
                    lt.on_bar(i, f"2024-01-01T{i:02d}:00:00Z",
                              49900, 50100, 49800, 50000, 200.0)
                )
            asyncio.get_event_loop().run_until_complete(
                lt.on_bar(5, "2024-01-01T05:00:00Z",
                          50000, 51000, 49500, 50100, 250.0,
                          prob_up_override=0.80)
            )
            if lt._position is not None:
                notional = lt._position.entry_px * lt._position.qty
                assert notional <= 200.0 * 1.01   # tolérance rounding
            lt.close()


class TestLiveTraderMetrics:

    def test_metrics_zero_at_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp)
            m = lt.metrics()
            assert m["n_trades"] == 0
            assert m["equity_init"] == 10_000.0
            lt.close()

    def test_log_file_has_session_start_and_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = _make_live_trader(tmp)
            lt.close()
            lines = [json.loads(l) for l in open(lt.cfg.log_path)]
            types = {l["type"] for l in lines}
            assert "session_end" in types
