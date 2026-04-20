"""
binance_rest.py — Client REST Binance (Spot) async
===================================================

Couvre les endpoints nécessaires au live trading minimal :
  - Authentification HMAC-SHA256 (API key + secret depuis env)
  - Ping / server time
  - Solde USDT (GET /api/v3/account)
  - Place order MARKET / LIMIT / OCO
  - Cancel order
  - Get open orders
  - Get order status

Usage :
    client = BinanceRestClient.from_env()   # lit BINANCE_API_KEY / BINANCE_API_SECRET
    async with client:
        balance = await client.get_usdt_balance()
        fill    = await client.market_order("BTCUSDT", "BUY", qty=0.001)

Notes de sécurité :
  - Les clés ne sont jamais loggées
  - Le paramètre recvWindow est limité à 5000 ms
  - Toutes les erreurs Binance propagent BinanceApiError avec le code/msg
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

BINANCE_REST_BASE  = "https://api.binance.com"
RECV_WINDOW        = 5_000   # ms, max fenêtre de validité d'une requête signée
_MIN_ORDER_USDT    = 10.0    # notionnel minimum Binance Spot BTC/USDT


# ─────────────────────────────────────────────────────────────────────────────
# Erreurs
# ─────────────────────────────────────────────────────────────────────────────

class BinanceApiError(Exception):
    """Exception propagée sur tout code d'erreur Binance (< 0) ou HTTP ≥ 400."""
    def __init__(self, code: int, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg  = msg


class BinanceOrderNotFound(BinanceApiError):
    """Levée quand l'ordre n'existe pas (code -2011)."""


class InsufficientFunds(BinanceApiError):
    """Levée quand le solde est insuffisant (code -2010, -1013)."""


# ─────────────────────────────────────────────────────────────────────────────
# Structures de retour
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderFill:
    """Résultat d'un ordre exécuté (market ou limit rempli)."""
    order_id      : int
    client_oid    : str
    symbol        : str
    side          : str          # "BUY" | "SELL"
    order_type    : str          # "MARKET" | "LIMIT" | "STOP_LOSS_LIMIT" …
    status        : str          # "FILLED" | "PARTIALLY_FILLED" | "NEW" …
    qty_ordered   : float
    qty_filled    : float
    avg_price     : float
    notional      : float        # qty_filled × avg_price
    commission    : float        # total frais payés (USDT ou BNB)
    commission_asset: str
    transact_time : int          # ms timestamp

    @classmethod
    def from_api(cls, d: Dict) -> "OrderFill":
        fills = d.get("fills", [])
        total_qty   = sum(float(f["qty"])        for f in fills) if fills else float(d.get("executedQty", 0))
        total_comm  = sum(float(f["commission"])  for f in fills) if fills else 0.0
        comm_asset  = fills[0]["commissionAsset"] if fills else "USDT"
        avg_px      = float(d.get("price", 0))
        if fills:
            wsum = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            denom = total_qty or 1e-9
            avg_px = wsum / denom

        return cls(
            order_id       = int(d["orderId"]),
            client_oid     = d.get("clientOrderId", ""),
            symbol         = d["symbol"],
            side           = d["side"],
            order_type     = d["type"],
            status         = d["status"],
            qty_ordered    = float(d.get("origQty",    d.get("executedQty", 0))),
            qty_filled     = float(d.get("executedQty", 0)),
            avg_price      = round(avg_px, 8),
            notional       = round(total_qty * avg_px, 4),
            commission     = round(total_comm, 8),
            commission_asset = comm_asset,
            transact_time  = int(d.get("transactTime", d.get("time", 0))),
        )

    def is_filled(self) -> bool:
        return self.status in ("FILLED",)

    def to_dict(self) -> Dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class OcoResult:
    """Résultat d'un ordre OCO (TP + SL simultanés)."""
    order_list_id : int
    symbol        : str
    list_status   : str          # "EXEC_STARTED", "ALL_DONE"
    tp_order_id   : int
    sl_order_id   : int
    qty           : float
    tp_price      : float
    sl_price      : float
    sl_stop_price : float


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de signature
# ─────────────────────────────────────────────────────────────────────────────

def _sign(secret: str, params: Dict) -> str:
    """Retourne la signature HMAC-SHA256 hexadécimale des paramètres."""
    qs = urllib.parse.urlencode(params)
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _ts() -> int:
    """Timestamp serveur local en ms."""
    return int(time.time() * 1000)


def _raise_for_binance(data) -> None:
    """Lève BinanceApiError si le corps JSON contient un code d'erreur."""
    if not isinstance(data, dict):
        return   # liste ou autre : pas d'erreur Binance
    code = data.get("code", 0)
    if isinstance(code, int) and code < 0:
        msg = data.get("msg", "unknown error")
        if code in (-2011,):
            raise BinanceOrderNotFound(code, msg)
        if code in (-2010, -1013, -1100):
            raise InsufficientFunds(code, msg)
        raise BinanceApiError(code, msg)


# ─────────────────────────────────────────────────────────────────────────────
# Client principal
# ─────────────────────────────────────────────────────────────────────────────

class BinanceRestClient:
    """
    Client REST Binance Spot (async aiohttp).

    Paramètres :
        api_key    : clé API Binance (lecture seule si pas d'ordres)
        api_secret : secret API Binance (pour signer les requêtes)
        testnet    : True → utilise testnet.binance.vision
        base_url   : override de l'URL de base (utile pour les tests)
    """

    TESTNET_BASE = "https://testnet.binance.vision"

    def __init__(
        self,
        api_key   : str,
        api_secret: str,
        testnet   : bool = False,
        base_url  : Optional[str] = None,
    ):
        self._key    = api_key
        self._secret = api_secret
        self._base   = base_url or (self.TESTNET_BASE if testnet else BINANCE_REST_BASE)
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def from_env(cls, testnet: bool = False) -> "BinanceRestClient":
        """
        Construit un client depuis les variables d'environnement.
        Lève EnvironmentError si les variables sont absentes.
        """
        key    = os.getenv("BINANCE_API_KEY",    "")
        secret = os.getenv("BINANCE_API_SECRET", "")
        if not key or not secret:
            raise EnvironmentError(
                "Variables manquantes : BINANCE_API_KEY et/ou BINANCE_API_SECRET. "
                "Créez un fichier .env ou exportez ces variables."
            )
        return cls(api_key=key, api_secret=secret, testnet=testnet)

    async def __aenter__(self) -> "BinanceRestClient":
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self._key},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Requêtes internes ─────────────────────────────────────────────────────

    def _session_check(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError(
                "BinanceRestClient doit être utilisé dans un bloc 'async with'."
            )
        return self._session

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        s = self._session_check()
        async with s.get(f"{self._base}{path}", params=params or {}) as r:
            data = await r.json()
            if r.status >= 400:
                _raise_for_binance(data)
                raise BinanceApiError(-r.status, str(data))
            _raise_for_binance(data)
            return data

    async def _get_signed(self, path: str, params: Optional[Dict] = None) -> Any:
        p = dict(params or {})
        p["timestamp"]  = _ts()
        p["recvWindow"] = RECV_WINDOW
        p["signature"]  = _sign(self._secret, p)
        return await self._get(path, p)

    async def _post_signed(self, path: str, params: Optional[Dict] = None) -> Any:
        s   = self._session_check()
        p   = dict(params or {})
        p["timestamp"]  = _ts()
        p["recvWindow"] = RECV_WINDOW
        p["signature"]  = _sign(self._secret, p)
        async with s.post(f"{self._base}{path}", params=p) as r:
            data = await r.json()
            if r.status >= 400:
                _raise_for_binance(data)
                raise BinanceApiError(-r.status, str(data))
            _raise_for_binance(data)
            return data

    async def _delete_signed(self, path: str, params: Optional[Dict] = None) -> Any:
        s   = self._session_check()
        p   = dict(params or {})
        p["timestamp"]  = _ts()
        p["recvWindow"] = RECV_WINDOW
        p["signature"]  = _sign(self._secret, p)
        async with s.delete(f"{self._base}{path}", params=p) as r:
            data = await r.json()
            if r.status >= 400:
                _raise_for_binance(data)
                raise BinanceApiError(-r.status, str(data))
            _raise_for_binance(data)
            return data

    # ── Endpoints publics ─────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Vérifie la connexion à l'API. Retourne True si OK."""
        await self._get("/api/v3/ping")
        return True

    async def server_time(self) -> int:
        """Retourne le timestamp serveur Binance en ms."""
        data = await self._get("/api/v3/time")
        return int(data["serverTime"])

    async def get_symbol_info(self, symbol: str) -> Dict:
        """Retourne les informations de filtre pour un symbole (LOT_SIZE, MIN_NOTIONAL…)."""
        data = await self._get("/api/v3/exchangeInfo", {"symbol": symbol.upper()})
        syms = data.get("symbols", [])
        if not syms:
            raise BinanceApiError(-1, f"Symbole inconnu : {symbol}")
        return syms[0]

    async def get_ticker_price(self, symbol: str) -> float:
        """Retourne le dernier prix (bid/ask midpoint) pour un symbole."""
        data = await self._get("/api/v3/ticker/price", {"symbol": symbol.upper()})
        return float(data["price"])

    # ── Endpoints authentifiés — Compte ───────────────────────────────────────

    async def get_account(self) -> Dict:
        """Retourne les informations du compte (balances, permissions…)."""
        return await self._get_signed("/api/v3/account")

    async def get_usdt_balance(self) -> float:
        """Retourne le solde USDT libre (non engagé dans des ordres)."""
        account = await self.get_account()
        for b in account.get("balances", []):
            if b["asset"] == "USDT":
                return float(b["free"])
        return 0.0

    async def get_asset_balance(self, asset: str) -> float:
        """Retourne le solde libre d'un asset (ex. 'BTC')."""
        account = await self.get_account()
        for b in account.get("balances", []):
            if b["asset"] == asset.upper():
                return float(b["free"])
        return 0.0

    # ── Endpoints authentifiés — Ordres ───────────────────────────────────────

    async def market_order(
        self,
        symbol   : str,
        side     : str,       # "BUY" | "SELL"
        qty      : float,
        client_oid: Optional[str] = None,
    ) -> OrderFill:
        """
        Place un ordre au marché et retourne le fill.
        `qty` est en unité de base (ex. BTC pour BTCUSDT).
        """
        p: Dict[str, Any] = {
            "symbol"  : symbol.upper(),
            "side"    : side.upper(),
            "type"    : "MARKET",
            "quantity": f"{qty:.8f}",
            "newOrderRespType": "FULL",
        }
        if client_oid:
            p["newClientOrderId"] = client_oid
        data = await self._post_signed("/api/v3/order", p)
        return OrderFill.from_api(data)

    async def limit_order(
        self,
        symbol    : str,
        side      : str,
        qty       : float,
        price     : float,
        time_in_force: str = "GTC",
        client_oid : Optional[str] = None,
    ) -> OrderFill:
        """Place un ordre LIMIT GTC."""
        p: Dict[str, Any] = {
            "symbol"      : symbol.upper(),
            "side"        : side.upper(),
            "type"        : "LIMIT",
            "timeInForce" : time_in_force,
            "quantity"    : f"{qty:.8f}",
            "price"       : f"{price:.2f}",
            "newOrderRespType": "FULL",
        }
        if client_oid:
            p["newClientOrderId"] = client_oid
        data = await self._post_signed("/api/v3/order", p)
        return OrderFill.from_api(data)

    async def oco_order(
        self,
        symbol       : str,
        side         : str,     # "SELL" pour sortir d'un LONG
        qty          : float,
        tp_price     : float,   # prix LIMIT de prise de profit
        sl_stop_price: float,   # prix déclencheur du stop
        sl_limit_price: float,  # prix LIMIT du stop (≤ sl_stop pour SELL)
        client_oid   : Optional[str] = None,
    ) -> OcoResult:
        """
        Place un ordre OCO (One-Cancels-the-Other).
        Crée simultanément un LIMIT (TP) et un STOP_LOSS_LIMIT (SL).
        Quand l'un est rempli, l'autre est automatiquement annulé.
        """
        p: Dict[str, Any] = {
            "symbol"           : symbol.upper(),
            "side"             : side.upper(),
            "quantity"         : f"{qty:.8f}",
            "price"            : f"{tp_price:.2f}",
            "stopPrice"        : f"{sl_stop_price:.2f}",
            "stopLimitPrice"   : f"{sl_limit_price:.2f}",
            "stopLimitTimeInForce": "GTC",
        }
        if client_oid:
            p["listClientOrderId"] = client_oid
        data = await self._post_signed("/api/v3/order/oco", p)

        orders = data.get("orders", []) or data.get("orderReports", [])
        tp_id  = next((int(o["orderId"]) for o in orders if o.get("type") == "LIMIT_MAKER" or o.get("type") == "LIMIT"), 0)
        sl_id  = next((int(o["orderId"]) for o in orders if "STOP" in o.get("type", "")), 0)

        return OcoResult(
            order_list_id  = int(data.get("orderListId", 0)),
            symbol         = symbol.upper(),
            list_status    = data.get("listStatusType", ""),
            tp_order_id    = tp_id,
            sl_order_id    = sl_id,
            qty            = qty,
            tp_price       = tp_price,
            sl_price       = sl_limit_price,
            sl_stop_price  = sl_stop_price,
        )

    async def cancel_order(
        self,
        symbol  : str,
        order_id: int,
    ) -> Dict:
        """Annule un ordre par son ID."""
        return await self._delete_signed("/api/v3/order", {
            "symbol" : symbol.upper(),
            "orderId": order_id,
        })

    async def cancel_oco(
        self,
        symbol       : str,
        order_list_id: int,
    ) -> Dict:
        """Annule un groupe d'ordres OCO."""
        return await self._delete_signed("/api/v3/orderList", {
            "symbol"      : symbol.upper(),
            "orderListId" : order_list_id,
        })

    async def get_order(
        self,
        symbol  : str,
        order_id: int,
    ) -> Dict:
        """Retourne l'état courant d'un ordre."""
        return await self._get_signed("/api/v3/order", {
            "symbol" : symbol.upper(),
            "orderId": order_id,
        })

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Liste les ordres ouverts (tous ou pour un symbole)."""
        p = {}
        if symbol:
            p["symbol"] = symbol.upper()
        return await self._get_signed("/api/v3/openOrders", p)

    async def cancel_all_open_orders(self, symbol: str) -> List[Dict]:
        """Annule TOUS les ordres ouverts pour un symbole."""
        return await self._delete_signed("/api/v3/openOrders", {
            "symbol": symbol.upper(),
        })

    async def get_oco_order(self, order_list_id: int) -> Dict:
        """Retourne l'état d'un groupe OCO."""
        return await self._get_signed("/api/v3/orderList", {
            "orderListId": order_list_id,
        })

    # ── Utilitaire : arrondi de quantité au LOT_SIZE ──────────────────────────

    @staticmethod
    def round_qty(qty: float, step_size: float) -> float:
        """Arrondit qty au step_size du filtre LOT_SIZE de Binance."""
        if step_size <= 0:
            return qty
        import math
        precision = max(0, -int(math.floor(math.log10(step_size))))
        factor    = 10 ** precision
        return math.floor(qty * factor) / factor

    async def get_step_size(self, symbol: str) -> float:
        """Retourne le stepSize du filtre LOT_SIZE pour un symbole."""
        info = await self.get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
        return 1e-8   # fallback
