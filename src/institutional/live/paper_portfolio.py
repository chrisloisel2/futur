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
  • règle NET (2026-07-19, pré-enregistrée) : une jambe ne S'OUVRE que si son
    yield NET — brut moins coûts d'aller-retour amortis sur la détention visée
    (30 j) moins borrow sur la part levée — dépasse la marge de sécurité ;
    une jambe DÉJÀ ouverte est GARDÉE tant que son brut reste > 0 (hystérésis)
    et ne peut être coupée/réduite avant 72 h (min-hold, sauf yield toxique).
    Le doc trace en continu le CONTREFACTUEL de l'ancienne règle (churn_guard :
    frais, accruals et borrow qu'elle aurait générés) — c'est la mesure LIVE
    de la différence entre les deux règles ;
  • v1.1 (2026-07-19, décision humaine, incrément anti-oscillation) : un
    resize ne peut pas INVERSER la direction du resize précédent d'un sleeve
    avant 72 h (même échappatoire yield toxique) — le min-hold ne protégeait
    que les sleeves jeunes, les cibles oscillaient sur du bruit de yield 8 h
    et payaient les frais dans les deux sens ; les seuils jugés de la v1
    (marge, bande 2 %, min-hold) sont inchangés ;
  • historique d'équité COMPRESSÉ (30 s récent / 1 h ≤ 30 j / 1 j au-delà),
    jamais tronqué aveuglément.

État + historique dans MongoDB (futur_ui.paper_portfolio). Aucun ordre réel.
"""
from __future__ import annotations

import json
import threading
import time
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
# ── règle NET 2026-07-19 (anti-churn structurel, pré-enregistrée) ────────────
MIN_HOLD_S = 72 * 3600     # coupe/réduction interdite avant 72 h : garder le
                           # borrow 72 h coûte au plus ~6,6 bps là où l'aller-
                           # retour coupe+réouverture coûte 8 bps
HOLD_AMORT_D = 30          # amortissement des coûts d'A/R du score net sur la
                           # détention minimale VISÉE (30 j), pas sur 72 h
COST_RT = 4 * MAKER_FEE    # aller-retour Δ-neutre complet (2 jambes × 2)
NET_MARGIN = 0.01          # marge de sécurité du score net (1 %/an)
HARD_EXIT_ANN = -BORROW_ANN  # sous ce yield le min-hold saute (sleeve toxique)
GUARD_RULE = "net_v1.1_2026-07-19"  # v1.1 = v1 + veto de réversion 72 h
GUARD_EVENTS_CAP = 120     # rétention du journal contrefactuel churn_guard
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


def _alpha20_emit(rows: list) -> None:
    """Émission SOFT vers le ledger append-only alpha20 — la vérité comptable
    est enregistrée au moment de l'EXÉCUTION (fin des trails reconstruits a
    posteriori, leçon de l'audit du 2026-07-19). Jamais bloquant : le paper
    survit à toute erreur côté alpha20."""
    try:
        import sys
        from pathlib import Path as _P
        root = str(_P(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)
        from src.alpha20.accounting.event_ledger import append as _append
        from src.alpha20.contracts import LedgerEvent as _LE
        _append([_LE(**r) for r in rows])
    except Exception:
        pass


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


_QUARTERLIES_CACHE = {"ts": 0.0, "by_pair": {}}
_QUARTERLIES_TTL_S = 6 * 3600


def _discover_quarterlies():
    """Contrats livrables ACTIFS via exchangeInfo (cache 6 h) — remplace la
    liste d'échéances figée (2026-07-19). {pair: [(delivery_ms, symbol), …]}."""
    now = time.time()
    if now - _QUARTERLIES_CACHE["ts"] < _QUARTERLIES_TTL_S:
        return _QUARTERLIES_CACHE["by_pair"]
    by_pair: Dict[str, list] = {}
    for s in _fapi("/fapi/v1/exchangeInfo").get("symbols", []):
        if (s.get("contractType") in ("CURRENT_QUARTER", "NEXT_QUARTER")
                and s.get("status") == "TRADING"
                and int(s.get("deliveryDate", 0)) > now * 1000):
            by_pair.setdefault(s["pair"], []).append(
                (int(s["deliveryDate"]), s["symbol"]))
    for v in by_pair.values():
        v.sort()
    _QUARTERLIES_CACHE.update(ts=now, by_pair=by_pair)
    return by_pair


def next_quarterly(symbol: str):
    """(prix_trimestriel_live, jours_échéance, symbole) du contrat le plus
    proche — découverte dynamique, fallback liste statique si l'API échoue."""
    now_ms = time.time() * 1000
    try:
        contracts = _discover_quarterlies().get(symbol, [])
    except Exception:
        contracts = []
    if not contracts:                       # fallback : anciennes échéances figées
        from datetime import date
        today = date.today()
        contracts = [
            (int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000),
             f"{symbol}_{y % 100:02d}{m:02d}{d:02d}")
            for y, m, d in _QUARTERLY_EXPIRIES_FALLBACK
            if date(y, m, d) > today]
    for delivery_ms, sym in contracts:
        try:
            px = float(_fapi(f"/fapi/v1/ticker/price?symbol={sym}")["price"])
            return px, int((delivery_ms - now_ms) / 86_400_000), sym
        except Exception:
            continue
    return None, None, None


_QUARTERLY_EXPIRIES_FALLBACK = [
    (2026, 9, 25), (2026, 12, 25),
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
    def _alloc_from_yields(self, yields: Dict, budget_free: float,
                           held: Dict, legacy: bool = False):
        """Répartit le budget Δ-neutre par yield décroissant.
        legacy=False — règle NET 2026-07-19 : OUVRIR exige yield net (brut −
        coûts A/R amortis sur HOLD_AMORT_D) > NET_MARGIN, levier d'ouverture
        si net > borrow + marge ; GARDER (sleeve dans `held`) exige seulement
        brut > 0, levier gardé si brut > borrow (hystérésis : couper coûte
        l'aller-retour, on ne l'exige donc pas pour rester).
        legacy=True — ancienne règle (plancher brut 1 %, levier si brut >
        borrow + 2 %), conservée UNIQUEMENT pour le contrefactuel churn_guard."""
        cost_ann = COST_RT * 365 / HOLD_AMORT_D
        carry_w, basis_w, note = {}, {}, {}
        for (kind, s), y in sorted(yields.items(), key=lambda kv: -kv[1]):
            cap_w = SLEEVE_CAPS[(kind, s)]
            key = f"{kind}_{s}"
            if legacy:
                if y <= YIELD_FLOOR:
                    note[key] = f"{y*100:+.1f}%/an < plancher 1% → 0"
                    continue
                lever_need = BORROW_ANN + 0.02
            elif (kind, s) in held:
                if y <= 0.0:
                    note[key] = f"{y*100:+.1f}%/an brut ≤ 0 → coupe (hystérésis)"
                    continue
                lever_need = BORROW_ANN
            else:
                if y - cost_ann <= NET_MARGIN:
                    note[key] = (f"{y*100:+.1f}%/an brut, net "
                                 f"{(y - cost_ann)*100:+.1f}% ≤ marge "
                                 f"{NET_MARGIN*100:.0f}% → reste dormant")
                    continue
                lever_need = BORROW_ANN + cost_ann + NET_MARGIN
            unlev = min(cap_w, budget_free)
            lever = cap_w - unlev
            lever_ok = y > lever_need
            w = unlev + (lever if lever_ok else 0.0)
            budget_free -= unlev
            if w > 0:
                (carry_w if kind == "carry" else basis_w)[s] = round(w, 4)
            note[key] = (
                f"{y*100:+.1f}%/an → {w:.2f}E"
                + ("" if (lever <= 0 or lever_ok)
                   else f" (levier {lever:.2f}E refusé : yield < "
                        f"{lever_need*100:.1f}%)"))
        return carry_w, basis_w, note

    def _adaptive_targets(self, regime: str, px: Dict[str, float],
                          held: Optional[Dict] = None):
        """Poids cibles : budget spot ≤ 1×E sans borrow, gates de la règle NET
        (voir _alloc_from_yields). Longs par régime (BULL 40 % / RECOVERY 25 %
        / sinon 0). `held` = sleeves carry/basis actuellement ouverts
        {(kind, sym): opened_ms} — active l'hystérésis garder/ouvrir."""
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
        carry_w, basis_w, note = self._alloc_from_yields(
            yields, budget_free, held or {})
        return carry_w, basis_w, longs_w, note, qmeta, yields

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
            CARRY, BASIS, LONGS, alloc_note, qmeta, _ = self._adaptive_targets(regime0, px)
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
                              "funding_rate": f, "funding_paid_until": now_ms,
                              "opened_ms": now_ms})
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
                              "basis_entry": q / px[s] - 1, "accrued_frac": 0.0,
                              "opened_ms": now_ms})
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

    def _min_hold_veto(self, kind: str, sym: str, pos: Dict,
                       yields: Dict, now_ms: int) -> bool:
        """True si le min-hold interdit de couper/réduire ce sleeve : ouvert
        depuis < 72 h et yield pas toxique (> -borrow). Économie : garder le
        gross 72 h coûte au plus ~6,6 bps de borrow là où couper puis rouvrir
        coûte 8 bps d'aller-retour."""
        age_ms = now_ms - int(pos.get("opened_ms", 0))
        if age_ms >= MIN_HOLD_S * 1000:
            return False
        y = yields.get((kind, sym))
        return y is None or y > HARD_EXIT_ANN

    def _reversal_veto(self, kind: str, sym: str, pos: Dict, yields: Dict,
                       now_ms: int, direction: int) -> bool:
        """v1.1 : True si ce resize INVERSERAIT la direction du resize
        précédent moins de 72 h après lui. Le min-hold ne protège que les
        sleeves jeunes ; sans ceci, les cibles oscillent sur du bruit de
        yield 8 h et paient les frais dans les deux sens. Même échappatoire
        que le min-hold : yield toxique (< -borrow) → le veto saute."""
        last_dir = int(pos.get("last_resize_dir", 0))
        if not last_dir or direction == last_dir:
            return False
        if now_ms - int(pos.get("last_resize_ms", 0)) >= MIN_HOLD_S * 1000:
            return False
        y = yields.get((kind, sym))
        return y is None or y > HARD_EXIT_ANN

    def _guard_accrue(self, doc: Dict, yields: Dict, eq_usdt: float,
                      cap_usdt: float, regime: str, now_ms: int) -> Dict:
        """CONTREFACTUEL de l'ancienne règle (churn_guard) — la mesure live de
        « la différence ». Un état shadow suit les notionals que l'ancienne
        règle détiendrait ; on cumule séparément ses frais, le différentiel
        d'accrual (approximé aux yields courants de la fenêtre) et le
        différentiel de borrow. Rien ici ne touche le ledger réel."""
        now_iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
        # le shadow démarre SUR les positions réelles : la différence ne
        # s'accumule qu'à partir de la première divergence de décision
        shadow0 = {f"carry_{c['symbol']}": c["notional"]
                   for c in doc.get("carry", []) if c["notional"] > 0}
        shadow0.update({f"basis_{b['symbol']}": b["notional"]
                        for b in doc.get("basis", [])
                        if b["notional"] > 0 and not b.get("delivered")})
        guard = doc.setdefault("churn_guard", {
            "since": now_iso, "rule": GUARD_RULE,
            "fees_new_usdt": 0.0, "fees_legacy_usdt": 0.0,
            "rev_diff_usdt": 0.0, "borrow_diff_usdt": 0.0,
            "shadow": shadow0, "shadow_ms": now_ms, "blocks": 0, "events": []})
        shadow = guard["shadow"]
        dt_s = max((now_ms - int(guard["shadow_ms"])) / 1000.0, 0.0)
        real = {f"carry_{c['symbol']}": c["notional"] for c in doc.get("carry", [])}
        real.update({f"basis_{b['symbol']}": b["notional"]
                     for b in doc.get("basis", []) if not b.get("delivered")})
        if dt_s > 0:
            for (kind, s), y in yields.items():
                key = f"{kind}_{s}"
                guard["rev_diff_usdt"] += ((real.get(key, 0.0) - shadow.get(key, 0.0))
                                           * y * dt_s / (365 * 86400))
            longs_g = sum(l["notional"] for l in doc.get("longs", [])
                          if l.get("active"))
            exc_real = max(sum(real.values()) + longs_g - cap_usdt, 0.0)
            exc_shadow = max(sum(shadow.values()) + longs_g - cap_usdt, 0.0)
            guard["borrow_diff_usdt"] += ((exc_real - exc_shadow)
                                          * BORROW_ANN * dt_s / (365 * 86400))
        # décisions que l'ancienne règle prendrait CETTE fenêtre (memoryless)
        budget_free = max(1.0 - {"BULL": 0.40, "RECOVERY": 0.25}.get(regime, 0.0), 0.0)
        lc, lb, _ = self._alloc_from_yields(yields, budget_free, {}, legacy=True)
        legacy_tgt = {f"carry_{s}": eq_usdt * w for s, w in lc.items()}
        legacy_tgt.update({f"basis_{s}": eq_usdt * w for s, w in lb.items()})
        for key in set(shadow) | set(legacy_tgt):
            tgt = legacy_tgt.get(key, 0.0)
            prev = shadow.get(key, 0.0)
            if abs(tgt - prev) > DRIFT_MIN * eq_usdt:
                guard["fees_legacy_usdt"] += abs(tgt - prev) * MAKER_FEE * 2
                if tgt > 0:
                    shadow[key] = tgt
                else:
                    shadow.pop(key, None)
        guard["shadow_ms"] = now_ms
        return guard

    def _guard_block(self, guard: Dict, kind: str, sym: str, kept: float,
                     tgt: float, y: Optional[float], now_ms: int, fx: float,
                     reason: str = "min-hold") -> None:
        """Journalise un resize DIFFÉRÉ (min-hold ou réversion 72 h — les frais
        ne sont pas « gagnés » tant que le resize peut encore arriver après
        72 h ; la vraie économie se lit dans fees_legacy vs fees_new)."""
        guard["blocks"] += 1
        fee = abs(kept - tgt) * MAKER_FEE * 2
        guard["events"].append({
            "ts": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            "sleeve": f"{kind}_{sym}", "kept_usdt": round(kept, 2),
            "target_usdt": round(tgt, 2), "reason": reason,
            "y_ann": None if y is None else round(y, 4),
            "fee_deferred_usdt": round(fee, 2)})
        guard["events"] = guard["events"][-GUARD_EVENTS_CAP:]
        _alpha20_emit([{
            "ts": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            "kind": "decision", "sleeve": f"{kind}_{sym}",
            "venue": "binance_usdm", "amount_usdt": 0.0, "ref": "deferral",
            "meta": {"reason": reason, "kept_usdt": round(kept, 2),
                     "target_usdt": round(tgt, 2),
                     "fee_deferred_usdt": round(fee, 2)}}])
        action = "hausse" if tgt > kept else "coupe"
        self._notify("info", f"🛡 {reason.capitalize()} : {action} {kind} "
                     f"{sym.replace('USDT', '')} différée",
                     f"Cible {tgt / fx:,.0f} €, position {kept / fx:,.0f} € "
                     f"conservée ({reason} < 72 h) — frais différés "
                     f"~{fee / fx:,.0f} €")

    def _guard_summary(self, doc: Dict, fx: float) -> Optional[Dict]:
        """Résumé € du contrefactuel pour l'API/PWA. edge > 0 = la règle NET
        fait mieux que l'ancienne (frais évités + accrual gardé − borrow subi)."""
        g = doc.get("churn_guard")
        if not g:
            return None
        edge = (g["fees_legacy_usdt"] - g["fees_new_usdt"]
                + g["rev_diff_usdt"] - g["borrow_diff_usdt"])
        return {"since": g["since"], "rule": g["rule"], "blocks": g["blocks"],
                "fees_new_eur": round(g["fees_new_usdt"] / fx, 2),
                "fees_legacy_eur": round(g["fees_legacy_usdt"] / fx, 2),
                "rev_diff_eur": round(g["rev_diff_usdt"] / fx, 2),
                "borrow_diff_eur": round(g["borrow_diff_usdt"] / fx, 2),
                "edge_vs_legacy_eur": round(edge / fx, 2),
                "note": "contrefactuel de l'ancienne règle (plancher brut 1%) ; "
                        "accruals approximés aux yields courants de chaque fenêtre"}

    def _rebalance_adaptive(self, doc: Dict, led: Dict, px: Dict[str, float],
                            eq_usdt: float, regime: str, now_ms: int,
                            fx: float) -> None:
        created_ms = _iso_ms(doc["created_at"])
        held = {}
        for c in doc.get("carry", []):
            if c["notional"] > 0:
                c.setdefault("opened_ms", created_ms)
                held[("carry", c["symbol"])] = int(c["opened_ms"])
        for b in doc.get("basis", []):
            if b["notional"] > 0 and not b.get("delivered"):
                b.setdefault("opened_ms", created_ms)
                held[("basis", b["symbol"])] = int(b["opened_ms"])
        carry_w, basis_w, longs_w, note, qmeta, yields = self._adaptive_targets(
            regime, px, held=held)
        guard = self._guard_accrue(doc, yields, eq_usdt,
                                   doc["capital_eur"] * fx, regime, now_ms)
        guard["rule"] = GUARD_RULE
        fees_new = 0.0
        now_iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
        emit = []

        def _fee_row(sleeve: str, fee: float, before: float, after: float):
            emit.append({"ts": now_iso, "kind": "fee", "sleeve": sleeve,
                         "venue": "binance_usdm", "amount_usdt": -fee,
                         "ref": "rebalance",
                         "meta": {"from_usdt": round(before, 2),
                                  "to_usdt": round(after, 2)}})
        # CARRY : resize/create/close par score net (frais maker 2 jambes sur |Δ|)
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
                     "funding_rate": f, "funding_paid_until": now_ms,
                     "opened_ms": now_ms}
            if abs(tgt - c["notional"]) > DRIFT_MIN * eq_usdt:
                sense = 1 if tgt > c["notional"] else -1
                mh = (sense < 0
                      and self._min_hold_veto("carry", s, c, yields, now_ms))
                if mh or self._reversal_veto("carry", s, c, yields, now_ms, sense):
                    self._guard_block(guard, "carry", s, c["notional"], tgt,
                                      yields.get(("carry", s)), now_ms, fx,
                                      reason="min-hold" if mh else "réversion")
                else:
                    fee = abs(tgt - c["notional"]) * MAKER_FEE * 2
                    led["fees"] -= fee
                    fees_new += fee
                    _fee_row(f"carry_{s}", fee, c["notional"], tgt)
                    if c["notional"] <= 0 < tgt:
                        c["opened_ms"] = now_ms
                    c["last_resize_ms"], c["last_resize_dir"] = now_ms, sense
                    c["notional"] = tgt
            new_carry.append(c)
        for s, c in old.items():             # cible 0 → coupe totale
            if c["notional"] > 0:
                mh = self._min_hold_veto("carry", s, c, yields, now_ms)
                if mh or self._reversal_veto("carry", s, c, yields, now_ms, -1):
                    self._guard_block(guard, "carry", s, c["notional"], 0.0,
                                      yields.get(("carry", s)), now_ms, fx,
                                      reason="min-hold" if mh else "réversion")
                    new_carry.append(c)      # conservé malgré la cible 0
                else:
                    fee = c["notional"] * MAKER_FEE * 2
                    led["fees"] -= fee
                    fees_new += fee
                    _fee_row(f"carry_{s}", fee, c["notional"], 0.0)
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
                     "accrued_frac": 0.0, "opened_ms": now_ms}
            if abs(tgt - b["notional"]) > DRIFT_MIN * eq_usdt:
                sense = 1 if tgt > b["notional"] else -1
                mh = (sense < 0
                      and self._min_hold_veto("basis", s, b, yields, now_ms))
                if mh or self._reversal_veto("basis", s, b, yields, now_ms, sense):
                    self._guard_block(guard, "basis", s, b["notional"], tgt,
                                      yields.get(("basis", s)), now_ms, fx,
                                      reason="min-hold" if mh else "réversion")
                else:
                    fee = abs(tgt - b["notional"]) * MAKER_FEE * 2
                    led["fees"] -= fee
                    fees_new += fee
                    _fee_row(f"basis_{s}", fee, b["notional"], tgt)
                    if b["notional"] <= 0 < tgt:
                        b["opened_ms"] = now_ms
                    b["last_resize_ms"], b["last_resize_dir"] = now_ms, sense
                    b["notional"] = tgt
            new_basis.append(b)
        for s, b in old.items():
            if b["notional"] > 0 and not b.get("delivered"):
                mh = self._min_hold_veto("basis", s, b, yields, now_ms)
                if mh or self._reversal_veto("basis", s, b, yields, now_ms, -1):
                    self._guard_block(guard, "basis", s, b["notional"], 0.0,
                                      yields.get(("basis", s)), now_ms, fx,
                                      reason="min-hold" if mh else "réversion")
                    new_basis.append(b)
                else:
                    fee = b["notional"] * MAKER_FEE * 2
                    led["fees"] -= fee
                    fees_new += fee
                    _fee_row(f"basis_{s}", fee, b["notional"], 0.0)
        doc["basis"] = new_basis
        guard["fees_new_usdt"] += fees_new
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
                fee = abs(tgt - l["notional"]) * TAKER_FEE
                led["fees"] -= fee
                _fee_row(f"long_{s}", fee, l["notional"], tgt)
                l["entry"] = cur
            l["notional"] = tgt
        doc["alloc_note"] = note
        doc["rebalanced_at"] = datetime.fromtimestamp(
            now_ms / 1000, tz=timezone.utc).isoformat()
        # événement de DÉCISION : état exécuté + cumuls Mongo (gate forward de
        # live_reconciliation : Δ cumuls Mongo ≡ Σ événements du ledger)
        emit.append({
            "ts": now_iso, "kind": "decision", "sleeve": "portfolio",
            "venue": "binance_usdm", "amount_usdt": 0.0, "ref": "rebalance",
            "meta": {
                "exec_usdt": dict(
                    {f"carry_{c['symbol']}": round(c["notional"], 2)
                     for c in doc["carry"] if c["notional"] > 0},
                    **{f"basis_{b['symbol']}": round(b["notional"], 2)
                       for b in doc["basis"]
                       if b["notional"] > 0 and not b.get("delivered")}),
                "mongo_fees_cum": round(float(led["fees"]), 6),
                "mongo_carry_cum": round(float(led["carry_accrued"]), 6),
                "regime": regime, "equity_usdt": round(eq_usdt, 2)}})
        _alpha20_emit(emit)

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
                    _alpha20_emit([{
                        "ts": datetime.fromtimestamp(
                            t / 1000, tz=timezone.utc).isoformat(),
                        "kind": "funding", "sleeve": f"carry_{c['symbol']}",
                        "venue": "binance_usdm",
                        "amount_usdt": c["notional"] * r, "ref": "settlement",
                        "meta": {"rate": r, "notional_usdt": c["notional"]}}
                        for t, r in evs])
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
                _alpha20_emit([{
                    "ts": now.isoformat(), "kind": "fee",
                    "sleeve": f"basis_{b['symbol']}", "venue": "binance_usdm",
                    "amount_usdt": -b["notional"] * 0.0002, "ref": "delivery",
                    "meta": {"notional_usdt": round(b["notional"], 2)}}])
                q, days, qsym = next_quarterly(b["symbol"])
                spot = px.get(b["symbol"])
                ann = (q / spot - 1) * 365 / days if (q and days and spot) else None
                if ann is not None and ann >= YIELD_FLOOR:
                    b.update({"spot_entry": spot, "q_entry": q,
                              "days_to_expiry": days, "q_symbol": qsym,
                              "basis_entry": q / spot - 1, "accrued_frac": 0.0})
                    led["fees"] -= b["notional"] * MAKER_FEE * 2
                    _alpha20_emit([{
                        "ts": now.isoformat(), "kind": "fee",
                        "sleeve": f"basis_{b['symbol']}", "venue": "binance_usdm",
                        "amount_usdt": -b["notional"] * MAKER_FEE * 2,
                        "ref": "roll", "meta": {"to": qsym}}])
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
                self._rebalance_adaptive(doc, led, px, eq_usdt, regime, now_ms, fx)
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
                "adaptive": "ADAPTATIF — re-alloué toutes les 8h par SCORE NET (yield − coûts A/R "
                            "amortis − borrow) · hystérésis garder si brut > 0 · min-hold 72h · "
                            "comptabilité v2 : funding réel encaissé, P&L réalisé aux flips, "
                            "frais déclarés, anti-churn 2% · contrefactuel churn_guard",
            }.get(doc.get("preset"), "stratégie validée V1.2 (carry+basis Δ-neutre + longs gatés)"),
            "alloc_note": doc.get("alloc_note"),
            "churn_guard": self._guard_summary(doc, fx),
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
