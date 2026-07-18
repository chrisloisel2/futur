"""
src/institutional/live/paper_portfolio.py
─────────────────────────────────────────────────────────────────────────────
PORTEFEUILLE PAPER LIVE — 200 000 € marqués au prix RÉEL, en continu.

Honnêteté (v2 — comptabilité RÉALISÉE, plus aucun P&L recalculé rétroactivement) :
  • le suivi commence à l'INSTANT du lancement (init) — aucun passé inventé ;
  • CARRY : funding ENCAISSÉ événement par événement (taux réels 8 h de l'API
    Binance), jamais re-taux-é rétroactivement au taux courant ;
  • LONGS gatés : le P&L est RÉALISÉ au prix du flip (sortie taker) et la
    ré-entrée se fait au prix courant — rien n'est effacé ni téléporté ;
  • BASIS : convergence accruée linéairement (bornée à 100 %), livrée à
    l'échéance puis ROLLÉE sur le trimestriel suivant si le yield ≥ plancher ;
  • FRAIS déclarés : maker 2 bps/jambe (Δ-neutre, validé maker-probe), taker
    5 bps (directionnel) — prélevés à l'ouverture, aux flips et aux resizes ;
  • preset adaptive : RE-ALLOCATION toutes les 8 h (pas du funding) selon les
    yields LIVE vs borrow ; resize seulement si dérive > 2 % d'equity
    (anti-churn — leçon exit-engine/CARRY_GATE_V2 : le churn est un impôt) ;
  • historique d'équité COMPRESSÉ (30 s récent / 1 h ≤ 30 j / 1 j au-delà),
    jamais tronqué aveuglément.

État + historique dans MongoDB (futur_ui.paper_portfolio). Aucun ordre réel.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Sérialise lecture→décision→écriture du mark : le poll PWA (2,5 s) et le
# timer 15 min frappent le même process — sans ce lock, deux marks concurrents
# peuvent rejouer le même rebalance ou s'écraser (observé 2026-07-18 10:15:50/52).
_MARK_LOCK = threading.Lock()

CAPITAL_EUR = 200_000.0
BORROW_ANN = 0.08          # coût d'emprunt sur le spot levé au-delà du cash
MAKER_FEE = 0.0002         # par jambe, post-only (jambes Δ-neutres)
TAKER_FEE = 0.0005         # jambe directionnelle (flips de gate)
YIELD_FLOOR = 0.01         # sleeve mort sous 1 %/an
DRIFT_MIN = 0.02           # anti-churn : resize si |Δ| > 2 % de l'equity
REBALANCE_S = 8 * 3600     # au pas du funding (00/08/16 UTC)
MA_TTL_S = 6 * 3600        # rafraîchissement du seuil trend MA20
FUNDING_8H_MS = 8 * 3600 * 1000
CORE_WEIGHTS = {
    "BTCUSDT": 0.35, "ETHUSDT": 0.22, "SOLUSDT": 0.10, "BNBUSDT": 0.08,
    "XRPUSDT": 0.06, "DOGEUSDT": 0.04, "ADAUSDT": 0.04, "AVAXUSDT": 0.03,
    "LINKUSDT": 0.03, "LTCUSDT": 0.03, "NEARUSDT": 0.02,
}
LONG_BASKET = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "AVAXUSDT"]
SLEEVE_CAPS = {("carry", "BTCUSDT"): 0.40, ("carry", "ETHUSDT"): 0.35,
               ("basis", "BTCUSDT"): 0.28, ("basis", "ETHUSDT"): 0.22}
HIST_CAP = 5000
MIN_SNAPSHOT_S = 30.0
NOTIFY_STEP_EUR = 500.0    # alerte quand le P&L total bouge de ±500 € (0,25 % du capital)
NOTIFY_FLIP_EUR = 250.0    # alerte quand un flip de gate réalise ±250 €
EVENTS_CAP = 200           # rétention des événements de notification


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


def funding_events(symbol: str, start_ms: int, end_ms: int) -> List[Tuple[int, float]]:
    """Événements de funding RÉELS (payés toutes les 8 h) — API publique.
    C'est la source de vérité du carry : on encaisse les taux effectivement
    fixés, pas le taux courant appliqué rétroactivement."""
    out, cur = [], start_ms
    try:
        while True:
            data = _fapi(f"/fapi/v1/fundingRate?symbol={symbol}"
                         f"&startTime={cur}&endTime={end_ms}&limit=1000")
            if not data:
                break
            out += [(int(d["fundingTime"]), float(d["fundingRate"])) for d in data]
            if len(data) < 1000:
                break
            cur = out[-1][0] + 1
    except Exception:
        pass
    return out


def next_quarterly(symbol: str):
    """(prix_trimestriel_live, jours_échéance, symbole) du contrat le plus proche >0."""
    from datetime import date
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


def _iso_ms(s: str) -> int:
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def _fresh_ledger(now: datetime) -> Dict:
    return {"version": 2, "last_mark": now.isoformat(),
            "carry_accrued": 0.0, "basis_accrued": 0.0, "borrow_accrued": 0.0,
            "longs_realized": 0.0, "fees": 0.0}


def _compress_history(hist: List[Dict], now: datetime) -> List[Dict]:
    """Pleine résolution 2 h, 1 pt/5 min ≤ 48 h, 1 pt/h ≤ 30 j, 1 pt/j
    au-delà. Premier point de chaque seau conservé — la courbe garde toute
    sa trajectoire (fini la troncature aveugle à N points)."""
    out, seen = [], set()
    for p in hist:
        try:
            t = datetime.fromisoformat(p["t"])
        except Exception:
            continue
        age = (now - t).total_seconds()
        if age <= 2 * 3600:
            key = p["t"]
        elif age <= 48 * 3600:
            key = t.strftime("%Y-%m-%dT%H:") + f"{t.minute // 5}"
        elif age <= 30 * 86400:
            key = t.strftime("%Y-%m-%dT%H")
        else:
            key = t.strftime("%Y-%m-%d")
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[-HIST_CAP:]


class PaperPortfolio:
    def __init__(self, db):
        self.db = db                       # futur_ui (pymongo Database) ou None
        self.col = db.paper_portfolio if db is not None else None
        self.events = db.portfolio_events if db is not None else None

    # ── notifications (gros gains / grosses pertes / événements de gestion) ──
    def _notify(self, level: str, title: str, body: str,
                value_eur: Optional[float] = None,
                pnl_eur: Optional[float] = None) -> None:
        """level : 'gain' | 'perte' | 'info'. Consommé par le PWA
        (GET /api/portfolio/events) — toast in-app + notification navigateur."""
        if self.events is None:
            return
        try:
            self.events.insert_one({
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level, "title": title, "body": body,
                "value_eur": None if value_eur is None else round(value_eur, 2),
                "pnl_eur": None if pnl_eur is None else round(pnl_eur, 2)})
            n = self.events.count_documents({})
            if n > EVENTS_CAP:
                for d in self.events.find().sort("ts", 1).limit(n - EVENTS_CAP):
                    self.events.delete_one({"_id": d["_id"]})
        except Exception:
            pass

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

    # ── allocation adaptative (partagée init + rebalance 8 h) ────────────────
    def _adaptive_targets(self, regime: str, px: Dict[str, float]):
        """Poids cibles par yield LIVE décroissant : budget spot ≤ 1×E sans
        borrow, levier accordé SEULEMENT si yield > borrow + 2 %, plancher
        1 %/an. Longs par régime (BULL 40 % / RECOVERY 25 % / sinon 0)."""
        yields, qmeta = {}, {}
        for s in ("BTCUSDT", "ETHUSDT"):
            f = live_funding(s)
            if f is not None:
                yields[("carry", s)] = f * 3 * 365
            q, days, qs = next_quarterly(s)
            if q and days and px.get(s):
                yields[("basis", s)] = (q / px[s] - 1) * 365 / days
                qmeta[s] = {"q": q, "days": days, "qsym": qs}
        long_total = {"BULL": 0.40, "RECOVERY": 0.25}.get(regime, 0.0)
        longs_w = {s: round(long_total * w, 4)
                   for s, w in self._inverse_vol_weights(LONG_BASKET).items()}
        budget_free = max(1.0 - long_total, 0.0)
        carry_w, basis_w, note = {}, {}, {}
        for (kind, s), y in sorted(yields.items(), key=lambda kv: -kv[1]):
            cap_w = SLEEVE_CAPS[(kind, s)]
            if y <= YIELD_FLOOR:
                note[f"{kind}_{s}"] = f"{y*100:+.1f}%/an < plancher 1% → 0"
                continue
            unlev = min(cap_w, budget_free)
            lever = cap_w - unlev
            lever_ok = y > BORROW_ANN + 0.02
            w = unlev + (lever if lever_ok else 0.0)
            budget_free -= unlev
            if w > 0:
                (carry_w if kind == "carry" else basis_w)[s] = round(w, 4)
            note[f"{kind}_{s}"] = (
                f"{y*100:+.1f}%/an → {w:.2f}E"
                + ("" if (lever <= 0 or lever_ok)
                   else f" (levier {lever:.2f}E refusé : yield < borrow+2%)"))
        return carry_w, basis_w, longs_w, note, qmeta

    # ── STRATÉGIE VALIDÉE : carry Δ-neutre + basis Δ-neutre + longs gatés ─────
    def initialize_strategy(self, aggressive: bool = False,
                            preset: str = None) -> Dict:
        """Reconstruit le portefeuille sur la stratégie validée (V1.2+basis).
        preset 'calm'      : Δ-neutre dominant, longs 10% (~+4.5%/an, DD ~-1%).
        preset 'aggressive': point efficient 40% dir (~+10.4%/an, DD ~-13%).
        preset 'max'       : FULL-STACK +21.8% backtest → ~18-19%/an après borrow.
        preset 'adaptive'  : allocation par yield LIVE vs borrow, re-allouée
                             toutes les 8 h (voir _rebalance_adaptive)."""
        if self.col is None:
            raise RuntimeError("MongoDB indisponible")
        preset = preset or ("aggressive" if aggressive else "calm")
        fx = eur_usdt()
        cap = CAPITAL_EUR * fx                      # en USDT
        now = datetime.now(timezone.utc)
        alloc_note, qmeta = None, {}
        regime0 = btc_regime()
        px = live_prices(list(set(["BTCUSDT", "ETHUSDT"] + LONG_BASKET)))
        if preset == "adaptive":
            CARRY, BASIS, LONGS, alloc_note, qmeta = self._adaptive_targets(regime0, px)
        elif preset == "max":
            CARRY = {"BTCUSDT": 0.40, "ETHUSDT": 0.35}   # ~1.5× equity spot (Δ-neutre)
            BASIS = {"BTCUSDT": 0.28, "ETHUSDT": 0.22}   # ~1.0× equity (Δ-neutre)
            long_ivw = self._inverse_vol_weights(LONG_BASKET)
            LONGS = {s: round(0.30 * w, 4) for s, w in long_ivw.items()}
        elif preset == "aggressive":
            CARRY = {"BTCUSDT": 0.30, "ETHUSDT": 0.25}
            BASIS = {"BTCUSDT": 0.15, "ETHUSDT": 0.10}
            long_ivw = self._inverse_vol_weights(LONG_BASKET)
            LONGS = {s: round(0.40 * w, 4) for s, w in long_ivw.items()}
        else:
            CARRY = {"BTCUSDT": 0.35, "ETHUSDT": 0.30}
            BASIS = {"BTCUSDT": 0.18, "ETHUSDT": 0.12}
            long_ivw = self._inverse_vol_weights(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            LONGS = {s: round(0.10 * w, 4) for s, w in long_ivw.items()}
        ma20 = {s: self._ma(s, 20) for s in LONGS}   # seuil trend (overlay)
        led = _fresh_ledger(now)
        now_ms = int(now.timestamp() * 1000)
        longs_on = regime0 in ("BULL", "RECOVERY")
        carry, basis, longs = [], [], []
        for s, w in CARRY.items():
            f = live_funding(s)
            if px.get(s) and f is not None and w > 0:
                carry.append({"symbol": s, "notional": cap * w, "spot_entry": px[s],
                              "funding_rate": f, "funding_paid_until": now_ms})
                led["fees"] -= cap * w * MAKER_FEE * 2
        for s, w in BASIS.items():
            m = qmeta.get(s)
            if m is None:
                q, days, qsym = next_quarterly(s)
            else:
                q, days, qsym = m["q"], m["days"], m["qsym"]
            if px.get(s) and q and days and w > 0:
                basis.append({"symbol": s, "notional": cap * w, "spot_entry": px[s],
                              "q_entry": q, "days_to_expiry": days, "q_symbol": qsym,
                              "basis_entry": q / px[s] - 1, "accrued_frac": 0.0})
                led["fees"] -= cap * w * MAKER_FEE * 2
        for s, w in LONGS.items():
            if px.get(s) and w > 0:
                cur = px[s]
                trend_ok = ma20.get(s) is None or cur > ma20[s]
                active = longs_on and trend_ok
                longs.append({"symbol": s, "notional": cap * w, "entry": cur,
                              "ma20": ma20.get(s), "ma20_ts": now_ms,
                              "active": active, "realized": 0.0})
                if active:
                    led["fees"] -= cap * w * TAKER_FEE
        doc = {"_id": "main", "mode": "strategy", "capital_eur": CAPITAL_EUR,
               "eur_usdt_at_init": fx, "preset": preset,
               "policy": f"preset_{preset}",
               "aggressive": preset in ("aggressive", "max", "adaptive"),
               "carry": carry, "basis": basis, "longs": longs,
               "alloc_note": alloc_note,
               "regime_at_init": regime0,
               "ledger": led,
               "notify": {"last_pnl": 0.0},
               "next_rebalance_ms": now_ms + REBALANCE_S * 1000,
               "created_at": now.isoformat(), "history": []}
        self.col.replace_one({"_id": "main"}, doc, upsert=True)
        self._notify("info", f"🚀 Portefeuille ouvert à {CAPITAL_EUR:,.0f} €",
                     f"Préset {preset} · régime {regime0} · "
                     f"{len(carry)} carry + {len(basis)} basis + {len(longs)} longs · "
                     f"frais d'ouverture {led['fees'] / fx:,.0f} €")
        return self.mark_to_market()

    # ── migration v1 → v2 (comptabilité réalisée) ────────────────────────────
    def _migrate_v2(self, doc: Dict, now: datetime) -> Dict:
        """Reconstruit un ledger HONNÊTE pour un doc v1 : funding réellement
        encaissé depuis l'ouverture (API), basis accrué linéaire, longs
        considérés détenus depuis l'entrée (le prochain mark réalise au prix
        courant si le gate est OFF), frais d'ouverture prélevés rétroactivement."""
        if doc.get("ledger"):
            return doc
        now_ms = int(now.timestamp() * 1000)
        created_ms = _iso_ms(doc["created_at"])
        led = _fresh_ledger(now)
        for c in doc.get("carry", []):
            led["fees"] -= c["notional"] * MAKER_FEE * 2
            evs = funding_events(c["symbol"], created_ms, now_ms)
            if evs:
                led["carry_accrued"] += sum(c["notional"] * r for _, r in evs)
                c["funding_paid_until"] = evs[-1][0]
                c["funding_rate"] = evs[-1][1]
            else:
                c["funding_paid_until"] = created_ms
        el_s = max((now_ms - created_ms) / 1000.0, 0.0)
        for b in doc.get("basis", []):
            led["fees"] -= b["notional"] * MAKER_FEE * 2
            frac = min(el_s / (b["days_to_expiry"] * 86400), 1.0)
            b["accrued_frac"] = frac
            led["basis_accrued"] += b["notional"] * b["basis_entry"] * frac
        for l in doc.get("longs", []):
            led["fees"] -= l["notional"] * TAKER_FEE
            l.setdefault("active", True)      # détenu depuis l'entrée
            l.setdefault("realized", 0.0)
            l.setdefault("ma20_ts", 0)
        doc["ledger"] = led
        doc.setdefault("next_rebalance_ms", now_ms + REBALANCE_S * 1000)
        return doc

    # ── rebalance adaptatif toutes les 8 h ───────────────────────────────────
    def _claim_rebalance(self, now_ms: int) -> bool:
        """Garde ATOMIQUE de la fenêtre de rebalance : un seul appelant
        (thread ou processus) peut avancer next_rebalance_ms ; les autres
        voient le filtre échouer et sautent le rebalance."""
        if self.col is None:
            return True
        prev = self.col.find_one_and_update(
            {"_id": "main", "next_rebalance_ms": {"$lte": now_ms}},
            {"$set": {"next_rebalance_ms": now_ms + REBALANCE_S * 1000}})
        return prev is not None

    def _rebalance_adaptive(self, doc: Dict, led: Dict, px: Dict[str, float],
                            eq_usdt: float, regime: str, now_ms: int) -> None:
        carry_w, basis_w, longs_w, note, qmeta = self._adaptive_targets(regime, px)
        # CARRY : resize/create/close par yield live (frais maker 2 jambes sur |Δ|)
        old = {c["symbol"]: c for c in doc.get("carry", [])}
        new_carry = []
        for s, w in carry_w.items():
            tgt = eq_usdt * w
            c = old.pop(s, None)
            if c is None:
                f = live_funding(s)
                if not px.get(s) or f is None:
                    continue
                c = {"symbol": s, "notional": 0.0, "spot_entry": px[s],
                     "funding_rate": f, "funding_paid_until": now_ms}
            if abs(tgt - c["notional"]) > DRIFT_MIN * eq_usdt:
                led["fees"] -= abs(tgt - c["notional"]) * MAKER_FEE * 2
                c["notional"] = tgt
            new_carry.append(c)
        for s, c in old.items():             # sleeve coupé (yield < plancher)
            if c["notional"] > 0:
                led["fees"] -= c["notional"] * MAKER_FEE * 2
        doc["carry"] = new_carry
        # BASIS : resize/create/close (le P&L de convergence est déjà accrué)
        old = {b["symbol"]: b for b in doc.get("basis", [])}
        new_basis = []
        for s, w in basis_w.items():
            tgt = eq_usdt * w
            b = old.pop(s, None)
            if b is None or b.get("delivered"):
                m = qmeta.get(s)
                if not m or not px.get(s):
                    continue
                b = {"symbol": s, "notional": 0.0, "spot_entry": px[s],
                     "q_entry": m["q"], "days_to_expiry": m["days"],
                     "q_symbol": m["qsym"], "basis_entry": m["q"] / px[s] - 1,
                     "accrued_frac": 0.0}
            if abs(tgt - b["notional"]) > DRIFT_MIN * eq_usdt:
                led["fees"] -= abs(tgt - b["notional"]) * MAKER_FEE * 2
                b["notional"] = tgt
            new_basis.append(b)
        for s, b in old.items():
            if b["notional"] > 0 and not b.get("delivered"):
                led["fees"] -= b["notional"] * MAKER_FEE * 2
        doc["basis"] = new_basis
        # LONGS : nouvelles cibles inverse-vol × régime ; resize d'une position
        # ACTIVE = réalisation partielle au prix courant (entry rebasé)
        by_sym = {l["symbol"]: l for l in doc.get("longs", [])}
        for s, w in longs_w.items():
            tgt = eq_usdt * w
            l = by_sym.get(s)
            if l is None:
                if not px.get(s):
                    continue
                l = {"symbol": s, "notional": tgt, "entry": px[s],
                     "ma20": self._ma(s, 20), "ma20_ts": now_ms,
                     "active": False, "realized": 0.0}
                doc["longs"].append(l)
                continue
            if abs(tgt - l["notional"]) <= DRIFT_MIN * eq_usdt:
                continue
            cur = px.get(l["symbol"])
            if l.get("active") and cur:
                led["longs_realized"] += l["notional"] * (cur / l["entry"] - 1)
                led["fees"] -= abs(tgt - l["notional"]) * TAKER_FEE
                l["entry"] = cur
            l["notional"] = tgt
        doc["alloc_note"] = note
        doc["rebalanced_at"] = datetime.fromtimestamp(
            now_ms / 1000, tz=timezone.utc).isoformat()

    # ── marquage stratégie (v2 : accruals incrémentaux + réalisations) ───────
    def _mark_strategy(self, doc: Dict) -> Dict:
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        fx = eur_usdt()
        doc = self._migrate_v2(doc, now)
        led = doc["ledger"]
        try:
            last_mark = datetime.fromisoformat(led["last_mark"])
        except Exception:
            last_mark = now
        dt_s = max((now - last_mark).total_seconds(), 0.0)
        syms = ([c["symbol"] for c in doc["carry"]]
                + [b["symbol"] for b in doc["basis"]]
                + [l["symbol"] for l in doc["longs"]])
        px = live_prices(list(set(syms)))
        regime = btc_regime()
        longs_on = regime in ("BULL", "RECOVERY")
        pos_out = []

        # CARRY : encaisse les événements de funding RÉELS depuis le dernier payé
        for c in doc["carry"]:
            due = now_ms >= c.get("funding_paid_until", now_ms) + FUNDING_8H_MS
            if due:
                evs = funding_events(c["symbol"], c["funding_paid_until"] + 1, now_ms)
                if evs:
                    led["carry_accrued"] += sum(c["notional"] * r for _, r in evs)
                    c["funding_paid_until"] = evs[-1][0]
                    c["funding_rate"] = evs[-1][1]
            pos_out.append({"symbol": c["symbol"], "sleeve": "carry Δ-neutre",
                            "notional_eur": c["notional"] / fx,
                            "price": px.get(c["symbol"]),
                            "pnl_eur": None,   # rempli après (part du sleeve)
                            "detail": f"funding {c['funding_rate']*3*365*100:+.1f}%/an "
                                      f"(dernier taux encaissé)"})

        # BASIS : accrual linéaire borné + livraison/ROLL à l'échéance
        for b in doc["basis"]:
            if b.get("delivered"):
                continue
            frac0 = float(b.get("accrued_frac", 0.0))
            frac1 = min(frac0 + dt_s / (b["days_to_expiry"] * 86400), 1.0)
            if frac1 > frac0:
                led["basis_accrued"] += b["notional"] * b["basis_entry"] * (frac1 - frac0)
                b["accrued_frac"] = frac1
            if frac1 >= 1.0:
                # livraison (2 bps) puis ROLL sur le trimestriel suivant si yield OK
                led["fees"] -= b["notional"] * 0.0002
                q, days, qsym = next_quarterly(b["symbol"])
                spot = px.get(b["symbol"])
                ann = (q / spot - 1) * 365 / days if (q and days and spot) else None
                if ann is not None and ann >= YIELD_FLOOR:
                    b.update({"spot_entry": spot, "q_entry": q,
                              "days_to_expiry": days, "q_symbol": qsym,
                              "basis_entry": q / spot - 1, "accrued_frac": 0.0})
                    led["fees"] -= b["notional"] * MAKER_FEE * 2
                    self._notify("info", f"🔁 Basis {b['symbol'].replace('USDT', '')} "
                                 f"livré et rollé sur {qsym}",
                                 f"Nouveau basis {ann * 100:+.1f}%/an, "
                                 f"notional {b['notional'] / fx:,.0f} €")
                else:
                    b["delivered"] = True
                    self._notify("info", f"⏸ Basis {b['symbol'].replace('USDT', '')} "
                                 f"livré, pas de roll",
                                 f"Yield trimestriel suivant sous le plancher 1%/an")
            pos_out.append({"symbol": b["symbol"], "sleeve": "basis Δ-neutre",
                            "notional_eur": b["notional"] / fx,
                            "price": px.get(b["symbol"]), "pnl_eur": None,
                            "detail": f"basis {b['basis_entry']*365/max(b['days_to_expiry'],1)*100:+.1f}%/an "
                                      f"· accrué {b.get('accrued_frac', 0)*100:.0f}%"
                                      + (" · LIVRÉ" if b.get("delivered") else "")})

        # LONGS : gate double (régime + trend) → RÉALISATION aux flips
        longs_unreal = 0.0
        for l in doc["longs"]:
            cur = px.get(l["symbol"])
            if cur is None:
                continue
            if now_ms - int(l.get("ma20_ts", 0)) > MA_TTL_S * 1000:
                ma = self._ma(l["symbol"], 20)
                if ma is not None:
                    l["ma20"] = ma
                l["ma20_ts"] = now_ms
            trend_ok = (l.get("ma20") is None) or (cur > l["ma20"])
            want = longs_on and trend_ok
            if l.get("active") and not want:      # SORTIE : réalise au prix du flip
                realized = l["notional"] * (cur / l["entry"] - 1)
                led["longs_realized"] += realized
                led["fees"] -= l["notional"] * TAKER_FEE
                l["active"] = False
                if abs(realized / fx) >= NOTIFY_FLIP_EUR:
                    self._notify("gain" if realized > 0 else "perte",
                                 f"{'📈' if realized > 0 else '📉'} Long "
                                 f"{l['symbol'].replace('USDT', '')} clôturé (gate)",
                                 f"Réalisé {realized / fx:+,.0f} € au flip "
                                 f"({'régime ' + regime if not longs_on else 'trend<MA20'})",
                                 pnl_eur=realized / fx)
            elif (not l.get("active")) and want:  # RÉ-ENTRÉE au prix courant
                l["entry"] = cur
                l["active"] = True
                led["fees"] -= l["notional"] * TAKER_FEE
                self._notify("info",
                             f"▶ Long {l['symbol'].replace('USDT', '')} ré-ouvert",
                             f"Gate {regime}+trend OK — notional "
                             f"{l['notional'] / fx:,.0f} € @ {cur:g}")
            unreal = l["notional"] * (cur / l["entry"] - 1) if l.get("active") else 0.0
            longs_unreal += unreal
            why = ("ACTIF" if l.get("active") else
                   ("FLAT régime" if not longs_on else "FLAT trend<MA20"))
            pos_out.append({"symbol": l["symbol"], "sleeve": "long gaté",
                            "notional_eur": l["notional"] / fx, "price": cur,
                            "pnl_eur": unreal / fx,
                            "detail": f"régime {regime}+trend → {why}"})

        # BORROW : incrémental sur l'excès de spot RÉELLEMENT détenu
        gross_spot = (sum(c["notional"] for c in doc["carry"])
                      + sum(b["notional"] for b in doc["basis"]
                            if not b.get("delivered"))
                      + sum(l["notional"] for l in doc["longs"] if l.get("active")))
        excess = max(gross_spot - doc["capital_eur"] * fx, 0.0)
        led["borrow_accrued"] -= excess * BORROW_ANN * dt_s / (365 * 86400)

        # REBALANCE adaptatif (au pas du funding, 8 h)
        eq_usdt = (doc["capital_eur"] * fx + led["carry_accrued"]
                   + led["basis_accrued"] + led["borrow_accrued"]
                   + led["longs_realized"] + led["fees"] + longs_unreal)
        if (doc.get("preset") == "adaptive"
                and now_ms >= int(doc.get("next_rebalance_ms", 0))):
            if self._claim_rebalance(now_ms):
                self._rebalance_adaptive(doc, led, px, eq_usdt, regime, now_ms)
                doc["next_rebalance_ms"] = now_ms + REBALANCE_S * 1000
                self._notify("info", "⚖ Rebalance adaptatif (8h)",
                             " · ".join(f"{k}: {v}" for k, v in
                                        (doc.get("alloc_note") or {}).items())[:400])
                longs_unreal = sum(
                    l["notional"] * (px[l["symbol"]] / l["entry"] - 1)
                    for l in doc["longs"] if l.get("active") and px.get(l["symbol"]))
                eq_usdt = (doc["capital_eur"] * fx + led["carry_accrued"]
                           + led["basis_accrued"] + led["borrow_accrued"]
                           + led["longs_realized"] + led["fees"] + longs_unreal)
            else:
                # fenêtre déjà prise ailleurs : ne pas la réarmer via notre
                # replace_one plein-doc plus bas
                cur = self.get() or {}
                doc["next_rebalance_ms"] = int(cur.get(
                    "next_rebalance_ms", now_ms + REBALANCE_S * 1000))

        led["last_mark"] = now.isoformat()
        pnl_usdt = eq_usdt - doc["capital_eur"] * fx
        value_eur = doc["capital_eur"] + pnl_usdt / fx

        # alerte GROS GAIN / GROSSE PERTE : à chaque pas de ±500 € du P&L total
        ns = doc.setdefault("notify", {"last_pnl": 0.0})
        pnl_eur_now = value_eur - doc["capital_eur"]
        delta = pnl_eur_now - float(ns.get("last_pnl", 0.0))
        if abs(delta) >= NOTIFY_STEP_EUR:
            self._notify("gain" if delta > 0 else "perte",
                         f"{'📈 GAIN' if delta > 0 else '📉 PERTE'} "
                         f"{delta:+,.0f} € sur le portefeuille",
                         f"Valeur {value_eur:,.0f} € · P&L total "
                         f"{pnl_eur_now:+,.0f} € "
                         f"({pnl_eur_now / doc['capital_eur'] * 100:+.2f} %)",
                         value_eur=value_eur, pnl_eur=pnl_eur_now)
            ns["last_pnl"] = pnl_eur_now

        # sleeves affichés (carry/basis = encaissé ; longs = réalisé + latent)
        sleeves = {"carry": led["carry_accrued"], "basis": led["basis_accrued"],
                   "longs": led["longs_realized"] + longs_unreal,
                   "borrow": led["borrow_accrued"], "fees": led["fees"]}
        for p in pos_out:       # P&L par position Δ-neutre = quote-part du sleeve
            if p["pnl_eur"] is None:
                key = "carry" if "carry" in p["sleeve"] else "basis"
                tot = sum(q["notional_eur"] for q in pos_out if q["sleeve"] == p["sleeve"])
                p["pnl_eur"] = (sleeves[key] / fx) * (p["notional_eur"] / tot if tot else 0)

        # snapshot compressé (30 s / 1 h / 1 j)
        hist = doc.get("history", [])
        last_t = hist[-1]["t"] if hist else None
        if last_t is None or (now - datetime.fromisoformat(last_t)).total_seconds() >= MIN_SNAPSHOT_S:
            hist.append({"t": now.isoformat(), "v": round(value_eur, 2)})
            hist = _compress_history(hist, now)
        doc["history"] = hist
        self.col.replace_one({"_id": "main"}, doc, upsert=True)

        pos_out.sort(key=lambda x: -abs(x["pnl_eur"] or 0))
        return {
            "exists": True, "mode": "strategy", "value_eur": round(value_eur, 2),
            "capital_eur": doc["capital_eur"],
            "pnl_eur": round(value_eur - doc["capital_eur"], 2),
            "pnl_pct": (value_eur - doc["capital_eur"]) / doc["capital_eur"],
            "eur_usdt": fx, "regime": regime, "longs_active": longs_on,
            "sleeves_eur": {k: round(v / fx, 2) for k, v in sleeves.items()},
            "breakdown_eur": {
                "funding_encaissé": round(led["carry_accrued"] / fx, 2),
                "basis_accrué": round(led["basis_accrued"] / fx, 2),
                "longs_réalisé": round(led["longs_realized"] / fx, 2),
                "longs_latent": round(longs_unreal / fx, 2),
                "frais": round(led["fees"] / fx, 2),
                "borrow": round(led["borrow_accrued"] / fx, 2),
            },
            "positions": pos_out, "created_at": doc["created_at"],
            "rebalanced_at": doc.get("rebalanced_at"),
            "policy": {
                "max": "FULL-STACK +21.8% backtest → ~18-19%/an honnête après borrow · ~5× gross",
                "aggressive": "AGRESSIVE 40% directionnel + cœur Δ-neutre · ~+10%/an cible, DD ~-13%",
                "adaptive": "ADAPTATIF — re-alloué toutes les 8h par yield LIVE vs borrow · "
                            "comptabilité v2 : funding réel encaissé, P&L réalisé aux flips, "
                            "frais déclarés, anti-churn 2%",
            }.get(doc.get("preset"), "stratégie validée V1.2 (carry+basis Δ-neutre + longs gatés)"),
            "alloc_note": doc.get("alloc_note"),
            "gross_leverage": round(gross_spot / (doc["capital_eur"] * fx), 2),
            "accounting": "v2_realized",
            "ts": now.isoformat(),
        }

    # ── marquage au marché (live) ────────────────────────────────────────────
    def mark_to_market(self) -> Dict:
        """Lecture→décision→écriture sous _MARK_LOCK : deux appels concurrents
        (poll 2,5 s + timer) sont sérialisés, le second voit l'état écrit par
        le premier."""
        with _MARK_LOCK:
            return self._mark_unlocked()

    def _mark_unlocked(self) -> Dict:
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
        # snapshot historique (throttlé + compressé)
        hist = doc.get("history", [])
        last_ts = hist[-1]["t"] if hist else None
        if last_ts is None or (now - datetime.fromisoformat(last_ts)).total_seconds() >= MIN_SNAPSHOT_S:
            hist.append({"t": now.isoformat(), "v": round(value_eur, 2)})
            hist = _compress_history(hist, now)
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
