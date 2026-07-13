"""
src/institutional/live/paper_portfolio.py
─────────────────────────────────────────────────────────────────────────────
PORTEFEUILLE PAPER LIVE — 200 000 € marqués au prix RÉEL, en continu.

Honnêteté : le suivi commence à l'INSTANT du lancement (init). On n'invente
aucun passé — le portefeuille s'ouvre maintenant, prend des positions aux prix
réels de l'instant, puis sa valeur fluctue avec le marché (crypto + EUR/USD).
C'est du forward paper trading authentique, pas la courbe backtest rehabillée.

Positions : livre spot directionnel (« je détiens ces cryptos »), alloué soit
par un cœur liquide, soit selon les prévisions de l'utilisateur (biais haussier).
Marquage : valeur_usdt = cash + Σ qty×prix_live ; valeur_eur = valeur_usdt / EURUSDT.
État + historique d'équité dans MongoDB (futur_ui). Aucun ordre réel.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

CAPITAL_EUR = 200_000.0
BORROW_ANN = 0.08          # coût d'emprunt sur le spot levé au-delà du cash
CORE_WEIGHTS = {
    "BTCUSDT": 0.35, "ETHUSDT": 0.22, "SOLUSDT": 0.10, "BNBUSDT": 0.08,
    "XRPUSDT": 0.06, "DOGEUSDT": 0.04, "ADAUSDT": 0.04, "AVAXUSDT": 0.03,
    "LINKUSDT": 0.03, "LTCUSDT": 0.03, "NEARUSDT": 0.02,
}
HIST_CAP = 4000
MIN_SNAPSHOT_S = 2.0


ROOT = Path(__file__).resolve().parents[3] if False else None


def _binance(path: str):
    req = urllib.request.Request("https://api.binance.com" + path,
                                 headers={"User-Agent": "futur-cc/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=12).read())


def _fapi(path: str):
    req = urllib.request.Request("https://fapi.binance.com" + path,
                                 headers={"User-Agent": "futur-cc/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=12).read())


def live_funding(symbol: str) -> Optional[float]:
    try:
        return float(_fapi(f"/fapi/v1/premiumIndex?symbol={symbol}")["lastFundingRate"])
    except Exception:
        return None


def next_quarterly(symbol: str):
    """(prix_trimestriel_live, jours_échéance) pour le contrat le plus proche >0."""
    from datetime import date
    base = symbol.replace("USDT", "")
    today = date.today()
    for exp in _QUARTERLY_EXPIRIES:
        y, m, d = exp
        e = date(y, m, d)
        if e <= today:
            continue
        sym = f"{symbol}_{y % 100:02d}{m:02d}{d:02d}"
        try:
            px = float(_fapi(f"/fapi/v1/ticker/price?symbol={sym}")["price"])
            return px, (e - today).days, sym
        except Exception:
            continue
    return None, None, None


_QUARTERLY_EXPIRIES = [
    (2026, 3, 27), (2026, 6, 26), (2026, 9, 25), (2026, 12, 25),
    (2027, 3, 26), (2027, 6, 25), (2027, 9, 24), (2027, 12, 31),
]


def btc_regime() -> str:
    """Régime BTC courant (BULL/RECOVERY→longs ON, sinon OFF). Lit le fleet paper."""
    try:
        from pathlib import Path as _P
        import json as _j
        p = _P(__file__).resolve().parents[3] / "reports" / "paper_trading" / "fleet_summary.json"
        return _j.loads(p.read_text()).get("btc_regime", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def eur_usdt() -> float:
    try:
        return float(_binance("/api/v3/ticker/price?symbol=EURUSDT")["price"])
    except Exception:
        return 1.14


def live_prices(symbols: List[str]) -> Dict[str, float]:
    try:
        data = _binance("/api/v3/ticker/price")
        want = set(symbols)
        return {t["symbol"]: float(t["price"]) for t in data if t["symbol"] in want}
    except Exception:
        return {}


class PaperPortfolio:
    def __init__(self, db):
        self.db = db                       # futur_ui (pymongo Database) ou None
        self.col = db.paper_portfolio if db is not None else None

    # ── état ─────────────────────────────────────────────────────────────────
    def get(self) -> Optional[Dict]:
        if self.col is None:
            return None
        return self.col.find_one({"_id": "main"})

    def exists(self) -> bool:
        return self.get() is not None

    # ── init / reset ─────────────────────────────────────────────────────────
    def initialize(self, policy: str = "core",
                   forecasts: Optional[List[Dict]] = None) -> Dict:
        """Ouvre le portefeuille à 200 000 € aux prix RÉELS de l'instant."""
        if self.col is None:
            raise RuntimeError("MongoDB indisponible")
        weights = dict(CORE_WEIGHTS)
        if policy == "forecasts" and forecasts:
            bull = {f["symbol"]: max(1, int(f.get("conviction", 3)))
                    for f in forecasts if f.get("direction") == "up"}
            if bull:
                tot = sum(bull.values())
                weights = {s: w / tot for s, w in bull.items()}
        fx = eur_usdt()
        usdt_cap = CAPITAL_EUR * fx
        px = live_prices(list(weights))
        positions, invested = [], 0.0
        for sym, w in weights.items():
            p = px.get(sym)
            if not p:
                continue
            alloc = usdt_cap * w
            qty = alloc / p
            invested += alloc
            positions.append({"symbol": sym, "qty": qty, "entry_price": p,
                              "weight": w,
                              "entry_ts": datetime.now(timezone.utc).isoformat()})
        doc = {"_id": "main", "capital_eur": CAPITAL_EUR,
               "eur_usdt_at_init": fx, "policy": policy,
               "cash_usdt": usdt_cap - invested,
               "positions": positions,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "history": []}
        self.col.replace_one({"_id": "main"}, doc, upsert=True)
        return self.mark_to_market()

    # ── helpers optimisation (inverse-vol + MA trend) ────────────────────────
    def _daily_close(self, symbol: str):
        from src.institutional.engines.legacy_bridge import load_enriched
        try:
            df = load_enriched(symbol, required_cols=["close"])
            return df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"].resample("D").last().dropna()
        except Exception:
            return None

    def _ma(self, symbol: str, n: int):
        s = self._daily_close(symbol)
        return float(s.tail(n).mean()) if s is not None and len(s) >= n else None

    def _inverse_vol_weights(self, symbols):
        vols = {}
        for s in symbols:
            c = self._daily_close(s)
            if c is not None and len(c) > 20:
                vols[s] = float(c.pct_change().tail(20).std())
        if not vols:
            return {s: 1 / len(symbols) for s in symbols}
        inv = {s: 1 / v for s, v in vols.items() if v > 0}
        tot = sum(inv.values())
        return {s: inv.get(s, 0) / tot for s in symbols}

    # ── STRATÉGIE VALIDÉE : carry Δ-neutre + basis Δ-neutre + longs gatés ─────
    def initialize_strategy(self, aggressive: bool = False,
                            preset: str = None) -> Dict:
        """Reconstruit le portefeuille sur la stratégie validée (V1.2+basis).
        preset 'calm'      : Δ-neutre dominant, longs 10% (~+4.5%/an, DD ~-1%).
        preset 'aggressive': point efficient 40% dir (~+10.4%/an, DD ~-13%).
        preset 'max'       : FULL-STACK +21.8% backtest (carry 1.5× + basis 1.0×
                             + longs) → honnête ~18-19%/an après borrow, ~5× gross.
        preset 'adaptive'  : allocation par rendement LIVE — chaque sleeve Δ-neutre
                             est dimensionné selon son yield live vs le borrow
                             (leçon LEVERAGE_FRONTIER : ne lever que quand ça paie) ;
                             longs par régime (BULL 40% / RECOVERY 25% / sinon 0)."""
        if self.col is None:
            raise RuntimeError("MongoDB indisponible")
        preset = preset or ("aggressive" if aggressive else "calm")
        fx = eur_usdt()
        cap = CAPITAL_EUR * fx                      # en USDT
        now = datetime.now(timezone.utc).isoformat()
        alloc_note = None
        if preset == "adaptive":
            # caps par sleeve = sizing du preset max (frontières mesurées) ;
            # le budget spot ≤ 1×E se remplit par yield live décroissant (0 borrow) ;
            # au-delà de 1×E : levier accordé SEULEMENT si yield > borrow + 2%.
            caps = {("carry", "BTCUSDT"): 0.40, ("carry", "ETHUSDT"): 0.35,
                    ("basis", "BTCUSDT"): 0.28, ("basis", "ETHUSDT"): 0.22}
            px0 = live_prices(["BTCUSDT", "ETHUSDT"])
            yields = {}
            for s in ("BTCUSDT", "ETHUSDT"):
                f = live_funding(s)
                if f is not None:
                    yields[("carry", s)] = f * 3 * 365
                q, days, _qs = next_quarterly(s)
                if q and days and px0.get(s):
                    yields[("basis", s)] = (q / px0[s] - 1) * 365 / days
            regime0 = btc_regime()
            LONG_TOTAL = {"BULL": 0.40, "RECOVERY": 0.25}.get(regime0, 0.0)
            basket = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "AVAXUSDT"]
            long_ivw = self._inverse_vol_weights(basket)
            LONGS = {s: round(LONG_TOTAL * w, 4) for s, w in long_ivw.items()}
            budget_free = max(1.0 - LONG_TOTAL, 0.0)
            CARRY, BASIS, alloc_note = {}, {}, {}
            for (kind, s), y in sorted(yields.items(), key=lambda kv: -kv[1]):
                cap_w = caps[(kind, s)]
                if y <= 0.01:               # plancher 1%/an : sleeve mort
                    alloc_note[f"{kind}_{s}"] = f"{y*100:+.1f}%/an < plancher 1% → 0"
                    continue
                unlev = min(cap_w, budget_free)
                lever = cap_w - unlev
                lever_ok = y > BORROW_ANN + 0.02
                w = unlev + (lever if lever_ok else 0.0)
                budget_free -= unlev
                if w > 0:
                    (CARRY if kind == "carry" else BASIS)[s] = round(w, 4)
                alloc_note[f"{kind}_{s}"] = (
                    f"{y*100:+.1f}%/an → {w:.2f}E"
                    + ("" if (lever <= 0 or lever_ok)
                       else f" (levier {lever:.2f}E refusé : yield < borrow+2%)"))
        elif preset == "max":
            # sizing full-stack qui a backtesté +21.8% (levier gratuit → borrow modélisé)
            CARRY = {"BTCUSDT": 0.40, "ETHUSDT": 0.35}   # ~1.5× equity spot (Δ-neutre)
            BASIS = {"BTCUSDT": 0.28, "ETHUSDT": 0.22}   # ~1.0× equity (Δ-neutre)
            basket = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "AVAXUSDT"]
            long_ivw = self._inverse_vol_weights(basket)
            LONG_TOTAL = 0.30      # longs régime-gatés (V1.2)
            LONGS = {s: round(LONG_TOTAL * w, 4) for s, w in long_ivw.items()}
        elif preset == "aggressive":
            CARRY = {"BTCUSDT": 0.30, "ETHUSDT": 0.25}
            BASIS = {"BTCUSDT": 0.15, "ETHUSDT": 0.10}
            basket = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "AVAXUSDT"]
            long_ivw = self._inverse_vol_weights(basket)
            LONG_TOTAL = 0.40      # point efficient de la frontière (Sharpe 0.96)
            LONGS = {s: round(LONG_TOTAL * w, 4) for s, w in long_ivw.items()}
        else:
            CARRY = {"BTCUSDT": 0.35, "ETHUSDT": 0.30}
            BASIS = {"BTCUSDT": 0.18, "ETHUSDT": 0.12}
            long_ivw = self._inverse_vol_weights(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            LONG_TOTAL = 0.10
            LONGS = {s: round(LONG_TOTAL * w, 4) for s, w in long_ivw.items()}
        ma20 = {s: self._ma(s, 20) for s in LONGS}   # seuil trend (overlay)
        px = live_prices(list(set(CARRY) | set(BASIS) | set(LONGS)))
        carry, basis, longs = [], [], []
        for s, w in CARRY.items():
            f = live_funding(s)
            if px.get(s) and f is not None:
                carry.append({"symbol": s, "notional": cap * w, "spot_entry": px[s],
                              "funding_rate": f})
        for s, w in BASIS.items():
            q, days, qsym = next_quarterly(s)
            if px.get(s) and q and days:
                basis.append({"symbol": s, "notional": cap * w, "spot_entry": px[s],
                              "q_entry": q, "days_to_expiry": days, "q_symbol": qsym,
                              "basis_entry": q / px[s] - 1})
        for s, w in LONGS.items():
            if px.get(s):
                longs.append({"symbol": s, "notional": cap * w, "entry": px[s],
                              "ma20": ma20.get(s)})   # seuil trend-filter
        doc = {"_id": "main", "mode": "strategy", "capital_eur": CAPITAL_EUR,
               "eur_usdt_at_init": fx, "preset": preset,
               "policy": f"preset_{preset}",
               "aggressive": preset in ("aggressive", "max", "adaptive"),
               "carry": carry, "basis": basis, "longs": longs,
               "alloc_note": alloc_note,
               "regime_at_init": btc_regime(),
               "created_at": now, "history": []}
        self.col.replace_one({"_id": "main"}, doc, upsert=True)
        return self.mark_to_market()

    def _mark_strategy(self, doc: Dict) -> Dict:
        now = datetime.now(timezone.utc)
        start = datetime.fromisoformat(doc["created_at"])
        elapsed_s = max((now - start).total_seconds(), 0.0)
        fx = eur_usdt()
        syms = ([c["symbol"] for c in doc["carry"]] + [b["symbol"] for b in doc["basis"]]
                + [l["symbol"] for l in doc["longs"]])
        px = live_prices(list(set(syms)))
        regime = btc_regime()
        longs_on = regime in ("BULL", "RECOVERY")
        sleeves, pos_out = {}, []
        # CARRY : Δ-neutre (prix s'annule) + funding accru pro-rata au taux LIVE
        carry_pnl = 0.0
        for c in doc["carry"]:
            f = live_funding(c["symbol"]) or c["funding_rate"]
            earned = c["notional"] * f * (elapsed_s / (8 * 3600))   # funding 8h
            carry_pnl += earned
            pos_out.append({"symbol": c["symbol"], "sleeve": "carry Δ-neutre",
                            "notional_eur": c["notional"] / fx, "price": px.get(c["symbol"]),
                            "pnl_eur": earned / fx,
                            "detail": f"funding {f*3*365*100:+.1f}%/an"})
        sleeves["carry"] = carry_pnl
        # BASIS : Δ-neutre + convergence linéaire du basis (pro-rata temps)
        basis_pnl = 0.0
        for b in doc["basis"]:
            frac = min(elapsed_s / (b["days_to_expiry"] * 86400), 1.0)
            earned = b["notional"] * b["basis_entry"] * frac
            basis_pnl += earned
            pos_out.append({"symbol": b["symbol"], "sleeve": "basis Δ-neutre",
                            "notional_eur": b["notional"] / fx, "price": px.get(b["symbol"]),
                            "pnl_eur": earned / fx,
                            "detail": f"basis {b['basis_entry']*365/b['days_to_expiry']*100:+.1f}%/an"})
        sleeves["basis"] = basis_pnl
        # LONGS : directionnel, DOUBLE GATE = régime bull ET trend (prix>MA20)
        longs_pnl = 0.0
        for l in doc["longs"]:
            cur = px.get(l["symbol"], l["entry"])
            ma20 = l.get("ma20")
            trend_ok = (ma20 is None) or (cur > ma20)
            active = longs_on and trend_ok
            pnl = l["notional"] * (cur / l["entry"] - 1) if active else 0.0
            longs_pnl += pnl
            why = ("ACTIF" if active else
                   ("FLAT régime" if not longs_on else "FLAT trend<MA20"))
            pos_out.append({"symbol": l["symbol"], "sleeve": "long gaté",
                            "notional_eur": l["notional"] / fx, "price": cur,
                            "pnl_eur": pnl / fx,
                            "detail": f"régime {regime}+trend → {why}"})
        sleeves["longs"] = longs_pnl
        # BORROW réel : le spot long cumulé au-delà du cash s'emprunte (~8%/an).
        # C'est ce qui transforme le "+21.8% levier gratuit" en chiffre honnête.
        gross_spot = (sum(c["notional"] for c in doc["carry"])
                      + sum(b["notional"] for b in doc["basis"])
                      + sum(l["notional"] for l in doc["longs"]))
        excess = max(gross_spot - doc["capital_eur"] * fx, 0.0)
        borrow_pnl = -excess * BORROW_ANN * (elapsed_s / (365 * 86400))
        sleeves["borrow"] = borrow_pnl
        total_pnl_usdt = carry_pnl + basis_pnl + longs_pnl + borrow_pnl
        value_eur = doc["capital_eur"] + total_pnl_usdt / fx
        # snapshot throttlé
        hist = doc.get("history", [])
        last = hist[-1]["t"] if hist else None
        if last is None or (now - datetime.fromisoformat(last)).total_seconds() >= MIN_SNAPSHOT_S:
            hist.append({"t": now.isoformat(), "v": round(value_eur, 2)})
            self.col.update_one({"_id": "main"}, {"$set": {"history": hist[-HIST_CAP:]}})
        pos_out.sort(key=lambda x: -abs(x["pnl_eur"]))
        return {
            "exists": True, "mode": "strategy", "value_eur": round(value_eur, 2),
            "capital_eur": doc["capital_eur"], "pnl_eur": round(value_eur - doc["capital_eur"], 2),
            "pnl_pct": (value_eur - doc["capital_eur"]) / doc["capital_eur"],
            "eur_usdt": fx, "regime": regime, "longs_active": longs_on,
            "sleeves_eur": {k: round(v / fx, 2) for k, v in sleeves.items()},
            "positions": pos_out, "created_at": doc["created_at"],
            "policy": {
                "max": "FULL-STACK +21.8% backtest (levier gratuit) → ~18-19%/an honnête après borrow · ~5× gross · DD backtest -3.2% mais risque queue funding-flip",
                "aggressive": "AGRESSIVE 40% directionnel + cœur Δ-neutre · ~+10%/an cible, DD ~-13%",
                "adaptive": "ADAPTATIF — sleeves Δ-neutre dimensionnés par yield LIVE vs borrow (levier seulement si ça paie), longs par régime · domine max/aggressive à conditions courantes",
            }.get(doc.get("preset"), "stratégie validée V1.2 (carry+basis Δ-neutre + longs gatés)"),
            "alloc_note": doc.get("alloc_note"),
            "gross_leverage": round(gross_spot / (doc["capital_eur"] * fx), 2),
            "ts": now.isoformat(),
        }

    # ── marquage au marché (live) ────────────────────────────────────────────
    def mark_to_market(self) -> Dict:
        doc = self.get()
        if doc is None:
            return {"exists": False}
        if doc.get("mode") == "strategy":
            return self._mark_strategy(doc)
        syms = [p["symbol"] for p in doc["positions"]]
        px = live_prices(syms)
        fx = eur_usdt()
        val_usdt = float(doc.get("cash_usdt", 0.0))
        pos_out, invested_now, cost_basis = [], 0.0, 0.0
        for p in doc["positions"]:
            cur = px.get(p["symbol"], p["entry_price"])
            v = p["qty"] * cur
            val_usdt += v
            invested_now += v
            cost_basis += p["qty"] * p["entry_price"]
            pos_out.append({
                "symbol": p["symbol"], "qty": p["qty"],
                "entry": p["entry_price"], "price": cur,
                "value_eur": v / fx,
                "pnl_pct": cur / p["entry_price"] - 1,
                "weight": p.get("weight"),
            })
        value_eur = val_usdt / fx
        pnl_eur = value_eur - doc["capital_eur"]
        now = datetime.now(timezone.utc)
        # snapshot historique (throttlé)
        hist = doc.get("history", [])
        last_ts = hist[-1]["t"] if hist else None
        if last_ts is None or (now - datetime.fromisoformat(last_ts)).total_seconds() >= MIN_SNAPSHOT_S:
            hist.append({"t": now.isoformat(), "v": round(value_eur, 2)})
            hist = hist[-HIST_CAP:]
            self.col.update_one({"_id": "main"}, {"$set": {"history": hist}})
        pos_out.sort(key=lambda x: -x["value_eur"])
        return {
            "exists": True,
            "value_eur": round(value_eur, 2),
            "capital_eur": doc["capital_eur"],
            "pnl_eur": round(pnl_eur, 2),
            "pnl_pct": pnl_eur / doc["capital_eur"],
            "eur_usdt": fx,
            "cash_eur": round(doc.get("cash_usdt", 0) / fx, 2),
            "positions": pos_out,
            "created_at": doc["created_at"],
            "policy": doc.get("policy"),
            "ts": now.isoformat(),
        }

    def history(self) -> List[Dict]:
        doc = self.get()
        return doc.get("history", []) if doc else []
