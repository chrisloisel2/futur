#!/usr/bin/env python3
"""
first_look.py -- le regard unique, pre-enregistre, sur le tape officiel des evenements.

    collecter -> pre-enregistrer -> geler -> regarder UNE fois

Ce fichier est PINNE (sha256) dans reports/first_look/event_reaction_v1_FREEZE.json :
le modifier apres le scellement invalide le regard (--run le verifie et refuse).

Quatre hypotheses, une famille (news / official_event_reaction), un horizon primaire
chacune, les autres horizons sont des sensibilites descriptives :

  H1 event_reaction_v1           listing perp Binance -> continuation   long   [pub+60s, +60m]
  H2 event_listing_reversal_v1   listing perp Binance -> reversion      short  [pub+15m, +6h]
  H3 event_delisting_pressure_v1 delisting Binance    -> pression       short  [pub+60s, +60m]
  H4 event_cross_venue_lag_v1    listing OKX/Bybit    -> retard Binance long   [pub+60s, +15m]

Rendement = 1e4 * ln(open_sortie / open_entree) sur la barre 1 min Binance Vision du marche
mesure, en exces du BTCUSDT spot sur la meme fenetre. Aucune optimisation : les
parametres sont des constantes de ce fichier.

Modes :
  --plan               selection des evenements depuis le snapshot gele, AUCUN prix
  --positive-control   chaine complete sur des prix SYNTHETIQUES (effet injecte / nul)
  --run                le regard : refuse sans FREEZE, sans pins intacts, sans temoin
                       pousse, et refuse un second regard
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import re
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from research_kernel.multiplicity import threshold_t  # noqa: E402

MECH = ROOT / "mechanisms" / "event_reaction_v1"
RESULTS = MECH / "results"
PREREG = ROOT / "reports" / "first_look" / "event_reaction_v1_PREREG.md"
FREEZE = ROOT / "reports" / "first_look" / "event_reaction_v1_FREEZE.json"
FROZEN = ROOT / "data_lake" / "first_look" / "event_reaction_v1" / "official_event_tape_frozen.jsonl"
CACHE = ROOT / "data" / "vision_1m"          # data/* est gitignore
VISION = "https://data.binance.vision/data"
BUDGET_LEDGER = ROOT / "reports" / "loop" / "BUDGET_LEDGER.jsonl"
LOOP_STATE = ROOT / "reports" / "loop" / "LOOP_STATE.json"

# ---- constantes du protocole (aucune n'est un parametre de ligne de commande) ----
FAMILY = "news"
SUBFAMILY = "official_event_reaction"
N_FAMILY_TESTS = 4                 # 4 hypotheses scellees ensemble -> threshold_t(4)
MIN_GROSS_BPS = 30.0
COST_WALL_X = 3.0
ENTRY_DELAY_S = 60                 # lecture + envoi : 60 s apres l'horodatage officiel
MIN_N_EFF = 30
MAX_TOP1_SHARE = 0.10
MIN_TRADABLE_SHARE = 0.80
PRE_WINDOW_MIN = 60                # fenetre de fuite : [pub-60min, pub)
PLACEBO_SHIFT_H = 48               # placebo : meme evenement, 48 h plus tot
BAR_TOLERANCE_MIN = 2              # barre manquante : on accepte jusqu'a +2 min, sinon exclu
MIN_MARKET_AGE_H = 24              # le marche mesure doit exister >= 24 h avant l'evenement
# VIP0 officiel (manifeste P1.1 : perp taker 5.0 / maker 2.0 ; spot Binance publie 10/10)
COST_RT_BPS = {"um": 2 * 5.0 + 4.0 + 4.0, "spot": 2 * 10.0 + 4.0 + 4.0}   # frais x2 + spread + slippage
HORIZON_MIN = {"1m": 1, "5m": 5, "15m": 15, "60m": 60, "6h": 360, "24h": 1440}
BENCH = ("spot", "BTCUSDT")

HYPOTHESES = {
    "H1": {"mechanism_id": "event_reaction_v1", "name": "futures listing continuation", "side": +1,
           "entry_offset_min": 0, "primary": "60m", "sensitivities": ["15m", "6h"],
           "market_pref": ["spot"], "selection": "binance_futures_listing"},
    "H2": {"mechanism_id": "event_listing_reversal_v1", "name": "futures listing mean reversion", "side": -1,
           "entry_offset_min": 15, "primary": "6h", "sensitivities": ["60m", "24h"],
           "market_pref": ["spot"], "selection": "binance_futures_listing"},
    "H3": {"mechanism_id": "event_delisting_pressure_v1", "name": "delisting forced pressure", "side": -1,
           "entry_offset_min": 0, "primary": "60m", "sensitivities": ["6h", "24h"],
           "market_pref": ["um", "spot"], "selection": "binance_delisting"},
    "H4": {"mechanism_id": "event_cross_venue_lag_v1", "name": "cross-venue lag after listing", "side": +1,
           "entry_offset_min": 0, "primary": "15m", "sensitivities": ["5m", "60m"],
           "market_pref": ["um", "spot"], "selection": "okx_bybit_listing"},
}

NONCRYPTO = re.compile(r"TradFi|bStocks|Tokeni[sz]ed|Quanto|\bstocks?\b|\bCFD|Equit|Index Perpetual|\bETF\b", re.I)
PROMO = re.compile(r"Splash|Convert|Savings|\bEarn\b|Launchpool|Launchpad|Pre-?Market|Airdrop|Prize|Collateral|"
                   r"Margin|Trading Bots?|Grid|Copy Trading|Leveraged Token|Staking|Loan|Simple Earn|Auto-Invest|"
                   r"Megadrop|HODLer|Alpha|Options?\b|Dual Investment|Futures Trading Bots|Referral", re.I)
ASSET_OK = re.compile(r"^[A-Z0-9]{2,15}$")
BINANCE_LAUNCH = re.compile(
    r"Will Launch\s+(?:USDⓈ-(?:Margined|M)\s+)?([A-Z0-9]{2,15}?)(?:USDT|USDC)?\s+(?:USDⓈ-(?:Margined|M)\s+)?Perpetual Contract")


# ----------------------------------------------------------------------------- selection
def asset_of(r: dict) -> str | None:
    """Actif d'un enregistrement : celui du tape, sinon (Binance perp) le titre en forme symbole."""
    a = r.get("asset")
    if a and ASSET_OK.match(str(a)):
        return str(a)
    if r.get("source") == "binance" and r.get("event_type") == "futures_listing":
        m = BINANCE_LAUNCH.search(r.get("raw_title", ""))
        if m and ASSET_OK.match(m.group(1)):
            return m.group(1)
    return None


def selection_reason(kind: str, r: dict) -> str:
    """'' si l'evenement est retenu pour cette selection, sinon la raison de l'exclusion.
    Fonction pure de l'enregistrement : aucune donnee de prix."""
    t = r.get("raw_title", "") or ""
    src, et = r.get("source"), r.get("event_type")
    if not r.get("publication_ts_exchange_ms"):
        return "no_publication_ts"
    if str(r.get("raw_url", "")).startswith("snapshot://"):
        return "snapshot_baseline"
    if NONCRYPTO.search(t):
        return "non_crypto"
    if kind == "binance_futures_listing":
        if not (src == "binance" and et == "futures_listing" and r.get("market_type") == "perp"):
            return "not_binance_futures_listing"
        if re.search(r"\bMultiple\b", t):
            return "multi_asset_title_without_assets"
        if "Coin-Margined" in t or "COIN-M" in t:
            return "coin_margined"
    elif kind == "binance_delisting":
        if src != "binance":
            return "not_binance"
        if not ((et == "delisting" and t.startswith("Binance Will Delist")) or
                (et == "futures_delisting" and t.startswith("Binance Futures Will Delist"))):
            return "not_asset_delisting"
    elif kind == "okx_bybit_listing":
        if src not in ("okx", "bybit") or et not in ("listing", "futures_listing"):
            return "not_okx_bybit_listing"
        if PROMO.search(t):
            return "promo_or_non_orderbook"
    else:
        raise ValueError(kind)
    if not asset_of(r):
        return "asset_unmapped"
    return ""


def select(rows: list[dict], kind: str) -> tuple[list[dict], dict]:
    """Applique la selection + dedoublonnage (raw_body_hash, puis (source, type, actif, jour))."""
    seen_hash, seen_key, out, why = set(), set(), [], defaultdict(int)
    for r in sorted(rows, key=lambda x: x.get("publication_ts_exchange_ms") or 0):
        reason = selection_reason(kind, r)
        if reason:
            why[reason] += 1; continue
        if r["raw_body_hash"] in seen_hash:
            why["duplicate_raw_hash"] += 1; continue
        key = (r["source"], r["event_type"], asset_of(r), r["publication_ts_exchange"][:10])
        if key in seen_key:
            why["duplicate_asset_day"] += 1; continue
        seen_hash.add(r["raw_body_hash"]); seen_key.add(key)
        out.append(r)
    why["selected"] = len(out)
    return out, dict(why)


# ----------------------------------------------------------------------------- prix
def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _days_between(ms_a: int, ms_b: int) -> list[str]:
    a = datetime.fromtimestamp(ms_a / 1000, tz=timezone.utc).date()
    b = datetime.fromtimestamp(ms_b / 1000, tz=timezone.utc).date()
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


class VisionStore:
    """Barres 1 min Binance Vision (fichiers journaliers), cache disque + manifeste a trois etats.
    Un 404 est une reponse (le marche n'existe pas ce jour-la), une erreur reseau n'en est pas une."""

    def __init__(self, cache: Path = CACHE):
        self.cache = cache; self.cache.mkdir(parents=True, exist_ok=True)
        self.manifest = cache / "manifest.jsonl"
        self.status: dict[tuple, str] = {}
        if self.manifest.exists():
            for l in self.manifest.read_text().splitlines():
                if l.strip():
                    e = json.loads(l); self.status[(e["market"], e["symbol"], e["day"])] = e["status"]
        self._mem: dict[tuple, dict | None] = {}

    def _path(self, market, symbol, day):
        return self.cache / market / symbol / f"{symbol}-1m-{day}.zip"

    def _url(self, market, symbol, day):
        seg = "spot" if market == "spot" else "futures/um"
        return f"{VISION}/{seg}/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"

    def _note(self, market, symbol, day, status):
        self.status[(market, symbol, day)] = status
        with open(self.manifest, "a") as f:
            f.write(json.dumps({"market": market, "symbol": symbol, "day": day, "status": status,
                                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")

    def fetch(self, market, symbol, day) -> str:
        """-> 'ok' | '404' | 'error'"""
        key = (market, symbol, day); p = self._path(market, symbol, day)
        if p.exists():
            return "ok"
        st = self.status.get(key)
        if st == "404":
            return "404"
        for attempt in range(4):
            try:
                with urlopen(Request(self._url(market, symbol, day), headers={"User-Agent": "futur-first-look"}), timeout=60) as r:
                    data = r.read()
                p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
                self._note(market, symbol, day, "ok"); return "ok"
            except HTTPError as e:
                if e.code == 404:
                    self._note(market, symbol, day, "404"); return "404"
                time.sleep(1.5 * (attempt + 1))
            except (URLError, TimeoutError, OSError):
                time.sleep(1.5 * (attempt + 1))
        self._note(market, symbol, day, "error"); return "error"

    def bars(self, market, symbol, day) -> dict | None:
        key = (market, symbol, day)
        if key in self._mem:
            return self._mem[key]
        st = self.fetch(market, symbol, day)
        if st != "ok":
            self._mem[key] = None; return None
        out = {}
        with zipfile.ZipFile(self._path(market, symbol, day)) as z:
            name = z.namelist()[0]
            for row in csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue                                     # en-tete (fichiers recents)
                ot = int(row[0]); ot = ot // 1000 if ot > 10**14 else ot   # µs -> ms (piege Vision)
                out[ot] = (float(row[1]), float(row[5]))         # (open, volume)
        self._mem[key] = out; return out

    def exists(self, market, symbol, day) -> bool:
        return self.fetch(market, symbol, day) == "ok"


class SyntheticStore:
    """Prix synthetiques deterministes : marche aleatoire 1 min CONTINUE d'un jour a l'autre
    (pont brownien vers un total journalier seede) + effet injecte apres t0.
    Sert au controle positif : la chaine doit retrouver un effet connu, et rien quand il n'y en a pas."""

    BASE_DAY = datetime(2023, 11, 1, tzinfo=timezone.utc).date()

    def __init__(self, effects: dict, sigma_bps=15.0, bench_sigma_bps=8.0, seed=7):
        self.effects = effects; self.sigma = sigma_bps; self.bsig = bench_sigma_bps; self.seed = seed
        self._mem = {}

    def exists(self, market, symbol, day):
        return True

    def _daily_total(self, symbol, d, sig):
        return random.Random(f"{self.seed}|{symbol}|{d.isoformat()}|total").gauss(0, sig * math.sqrt(1440)) / 1e4

    def _level_at_day_start(self, symbol, d, sig):
        lv, cur = 0.0, self.BASE_DAY
        while cur < d:
            lv += self._daily_total(symbol, cur, sig); cur += timedelta(days=1)
        return lv

    def bars(self, market, symbol, day):
        key = (market, symbol, day)
        if key in self._mem:
            return self._mem[key]
        d = datetime.fromisoformat(day).date()
        d0 = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        sig = self.bsig if symbol == BENCH[1] else self.sigma
        rng = random.Random(f"{self.seed}|{symbol}|{day}")
        inc = [rng.gauss(0, sig) / 1e4 for _ in range(1440)]
        cum, acc = [], 0.0
        for x in inc:
            acc += x; cum.append(acc)
        total = self._daily_total(symbol, d, sig)
        start = self._level_at_day_start(symbol, d, sig)
        eff = self.effects.get(symbol)
        out = {}
        for i in range(1440):
            ot = d0 + i * 60_000
            lp = start + cum[i] - (i / 1440.0) * (cum[-1] - total)      # pont brownien : continu au jour suivant
            e = 0.0
            if eff:
                t0, side, bps, hmin = eff
                if ot >= t0:
                    e = side * bps / 1e4 * min(1.0, (ot - t0) / (hmin * 60_000))
            out[ot] = (100.0 * math.exp(lp + e), 1.0 + rng.random())
        self._mem[key] = out; return out


def series(store, market, symbol, days) -> dict:
    out = {}
    for d in days:
        b = store.bars(market, symbol, d)
        if b:
            out.update(b)
    return out


def bar_at(s: dict, ms: int):
    """Open de la premiere barre dont l'ouverture est >= ms (tolerance BAR_TOLERANCE_MIN)."""
    m0 = -(-ms // 60_000) * 60_000
    for k in range(BAR_TOLERANCE_MIN + 1):
        v = s.get(m0 + k * 60_000)
        if v:
            return m0 + k * 60_000, v[0]
    return None, None


def spot_symbol(asset: str) -> str:
    base = re.sub(r"^(1000000|1000|1M)", "", asset)
    return f"{base}USDT"


def resolve_market(store, asset: str, pref: list, t0_ms: int) -> tuple[str, str] | None:
    """Premier marche de la preference qui existe depuis >= MIN_MARKET_AGE_H avant t0.
    Existence = le fichier Vision du jour precedent existe et contient une barre <= t0 - 24 h."""
    dm1 = _day(t0_ms - MIN_MARKET_AGE_H * 3600_000)
    for market in pref:
        cands = [f"{asset}USDT"] if market == "um" else [spot_symbol(asset)]
        if market == "um" and not asset.startswith("1000"):
            cands.append(f"1000{asset}USDT")
        for sym in cands:
            if store.exists(market, sym, dm1):
                b = store.bars(market, sym, dm1) or {}
                if any(ot <= t0_ms - MIN_MARKET_AGE_H * 3600_000 for ot in b):
                    return market, sym
    return None


# ----------------------------------------------------------------------------- mesure
def measure_event(store, hyp: dict, r: dict, market: str, symbol: str, shift_ms: int = 0) -> dict:
    """Rendements en exces (bps) pour un evenement a tous les horizons, + fenetre de fuite + volume.
    shift_ms < 0 = placebo (meme evenement, plus tot)."""
    pub = r["publication_ts_exchange_ms"] + shift_ms
    t_entry = pub + ENTRY_DELAY_S * 1000 + hyp["entry_offset_min"] * 60_000
    hmax = max(HORIZON_MIN[h] for h in [hyp["primary"], *hyp["sensitivities"]])
    days = _days_between(pub - PRE_WINDOW_MIN * 60_000 - 3_600_000, t_entry + hmax * 60_000 + 3_600_000)
    s = series(store, market, symbol, days); b = series(store, BENCH[0], BENCH[1], days)
    out = {"entry_bar": None, "excess": {}, "raw": {}, "pre_excess": None, "volume_ratio": None}
    e_ot, e_px = bar_at(s, t_entry); be_ot, be_px = bar_at(b, t_entry)
    if e_px is None or be_px is None:
        out["missing"] = "entry_bar"; return out
    out["entry_bar"] = e_ot; out["entry_lag_s"] = (e_ot - pub) / 1000
    for h in [hyp["primary"], *hyp["sensitivities"]]:
        x_ot, x_px = bar_at(s, e_ot + HORIZON_MIN[h] * 60_000); bx_ot, bx_px = bar_at(b, be_ot + HORIZON_MIN[h] * 60_000)
        if x_px is None or bx_px is None:
            continue
        ra = 1e4 * math.log(x_px / e_px); rb = 1e4 * math.log(bx_px / be_px)
        out["raw"][h] = ra; out["excess"][h] = ra - rb
    p_ot, p_px = bar_at(s, pub - PRE_WINDOW_MIN * 60_000); q_ot, q_px = bar_at(s, pub - 60_000)
    bp_ot, bp_px = bar_at(b, pub - PRE_WINDOW_MIN * 60_000); bq_ot, bq_px = bar_at(b, pub - 60_000)
    if None not in (p_px, q_px, bp_px, bq_px) and q_ot > p_ot:
        out["pre_excess"] = 1e4 * (math.log(q_px / p_px) - math.log(bq_px / bp_px))
    vb = [v[1] for ot, v in s.items() if pub - PRE_WINDOW_MIN * 60_000 <= ot < pub]
    va = [v[1] for ot, v in s.items() if e_ot <= ot < e_ot + 60 * 60_000]
    if vb and va and sum(vb) > 0:
        out["volume_ratio"] = (sum(va) / len(va)) / (sum(vb) / len(vb))
    return out


def cluster_stats(xs: list[float], clusters: list) -> dict:
    """Moyenne, mediane, SE robuste par grappe (jour de publication), t, concentration."""
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n; srt = sorted(xs); med = srt[n // 2] if n % 2 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
    S, C = defaultdict(float), defaultdict(int)
    for x, c in zip(xs, clusters):
        S[c] += x; C[c] += 1
    var = sum((S[c] - C[c] * mean) ** 2 for c in S)
    se = math.sqrt(var) / n if n > 1 else float("nan")
    pos = [x for x in xs if x > 0]
    top1 = (max(pos) / sum(pos)) if pos else 0.0
    n_eff = (sum(abs(x) for x in xs) ** 2 / sum(x * x for x in xs)) if any(xs) else 0.0
    return {"n": n, "n_clusters": len(S), "mean_bps": mean, "median_bps": med, "se_bps": se,
            "t": (mean / se) if se and se > 0 else float("nan"), "top1_share": top1, "n_eff": n_eff,
            "share_positive": sum(1 for x in xs if x > 0) / n}


def verdict_for(h: dict, prim: dict, cost_bps: float, tradable_share: float, leak_bps, placebo_mean, thr: float) -> tuple[str, list]:
    """Ordre fixe des criteres. Un verdict, des raisons."""
    reasons = []
    if prim.get("n", 0) == 0:
        return "INDECIDABLE", ["no_events_measured"]
    gross = prim["mean_bps"]
    if gross < MIN_GROSS_BPS:
        return "REJECTED_NO_GROSS", [f"gross {gross:.1f} < {MIN_GROSS_BPS}"]
    if gross < COST_WALL_X * cost_bps:
        return "REJECTED_COST_WALL", [f"gross {gross:.1f} < {COST_WALL_X} x cost {cost_bps:.1f}"]
    if not (prim["t"] >= thr):
        reasons.append(f"t {prim['t']:.2f} < threshold {thr:.4f}")
    if prim["n_eff"] < MIN_N_EFF:
        reasons.append(f"n_eff {prim['n_eff']:.1f} < {MIN_N_EFF}")
    if prim["top1_share"] > MAX_TOP1_SHARE:
        reasons.append(f"top1_share {prim['top1_share']:.2f} > {MAX_TOP1_SHARE}")
    if tradable_share < MIN_TRADABLE_SHARE:
        reasons.append(f"tradable_share {tradable_share:.2f} < {MIN_TRADABLE_SHARE}")
    if leak_bps is not None and leak_bps >= gross:
        reasons.append(f"pre-publication drift {leak_bps:.1f} >= gross {gross:.1f} (already-open trading)")
    if placebo_mean is not None and placebo_mean >= gross:
        reasons.append(f"placebo {placebo_mean:.1f} >= gross {gross:.1f}")
    return ("INDECIDABLE", reasons) if reasons else ("FORWARD_SEAL_REQUIRED", ["all criteria met"])


def run_hypothesis(store, key: str, rows: list[dict], thr: float, workers: int = 8, placebo: bool = True) -> dict:
    hyp = HYPOTHESES[key]; side = hyp["side"]
    selected, why = select(rows, hyp["selection"])
    resolved, unres = [], defaultdict(int)
    for r in selected:
        m = resolve_market(store, asset_of(r), hyp["market_pref"], r["publication_ts_exchange_ms"])
        if m:
            resolved.append((r, m))
        else:
            unres["no_binance_market_24h_before"] += 1
    def _one(item):
        r, (market, sym) = item
        m = measure_event(store, hyp, r, market, sym)
        p = measure_event(store, hyp, r, market, sym, shift_ms=-PLACEBO_SHIFT_H * 3600_000) if placebo else {"excess": {}}
        return r, market, sym, m, p
    with ThreadPoolExecutor(max_workers=workers) as ex:
        measured = list(ex.map(_one, resolved))
    per_h = {h: ([], []) for h in [hyp["primary"], *hyp["sensitivities"]]}
    plc, pre, trad, costs, missing, events, by_year = [], [], [], [], defaultdict(int), [], defaultdict(list)
    for r, market, sym, m, p in measured:
        if m.get("missing"):
            missing[m["missing"]] += 1; continue
        day = r["publication_ts_exchange"][:10]
        for h, (xs, cl) in per_h.items():
            if h in m["excess"]:
                xs.append(side * m["excess"][h]); cl.append(day)
        if hyp["primary"] in m["excess"]:
            x = side * m["excess"][hyp["primary"]]
            by_year[day[:4]].append(x)
            trad.append(1.0 if (market == "um" or side > 0) else 0.0)
            costs.append(COST_RT_BPS[market])
            if m["pre_excess"] is not None:
                pre.append(side * m["pre_excess"])
            if hyp["primary"] in p.get("excess", {}):
                plc.append(side * p["excess"][hyp["primary"]])
            events.append({"event_id": r["event_id"], "asset": asset_of(r), "market": market, "symbol": sym,
                           "publication_ts": r["publication_ts_exchange"], "entry_lag_s": m.get("entry_lag_s"),
                           "excess_bps": {k: round(v, 2) for k, v in m["excess"].items()},
                           "pre_excess_bps": None if m["pre_excess"] is None else round(m["pre_excess"], 2),
                           "volume_ratio": None if m["volume_ratio"] is None else round(m["volume_ratio"], 3)})
    stats = {h: cluster_stats(xs, cl) for h, (xs, cl) in per_h.items()}
    prim = stats[hyp["primary"]]
    cost = (sum(costs) / len(costs)) if costs else COST_RT_BPS[hyp["market_pref"][0]]
    tradable = (sum(trad) / len(trad)) if trad else 0.0
    leak = (sum(pre) / len(pre)) if pre else None
    plc_mean = (sum(plc) / len(plc)) if plc else None
    verdict, reasons = verdict_for(hyp, prim, cost, tradable, leak, plc_mean, thr)
    return {"hypothesis": key, **{k: v for k, v in hyp.items()}, "selection_counts": why,
            "unresolved": dict(unres), "missing_bars": dict(missing), "n_measured": prim.get("n", 0),
            "stats_by_horizon": stats, "primary_stats": prim, "cost_rt_bps_mean": cost,
            "cost_wall_bps": COST_WALL_X * cost, "tradable_share": tradable,
            "pre_publication_drift_bps": leak, "placebo_48h_mean_bps": plc_mean, "placebo_n": len(plc),
            "by_year_mean_bps": {y: (sum(v) / len(v), len(v)) for y, v in sorted(by_year.items())},
            "threshold_t": thr, "verdict": verdict, "reasons": reasons, "events": events}


# ----------------------------------------------------------------------------- integrite
def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def check_seal() -> dict:
    """Le regard n'a lieu que si tout ce qui a ete scelle est intact."""
    if not FREEZE.exists():
        raise SystemExit("REFUS : pas de FREEZE (reports/first_look/event_reaction_v1_FREEZE.json)")
    fz = json.loads(FREEZE.read_text())
    if not FROZEN.exists():
        raise SystemExit(f"REFUS : snapshot gele absent : {FROZEN}")
    if sha(FROZEN) != fz["snapshot"]["sha256"]:
        raise SystemExit("REFUS : le snapshot gele ne correspond plus au sha256 scelle")
    pins = fz["pins"]
    for rel, expected in pins.items():
        actual = sha(ROOT / rel)
        if actual != expected:
            raise SystemExit(f"REFUS : {rel} a change depuis le scellement ({actual[:12]} != {expected[:12]})")
    if (RESULTS / "first_look_results.json").exists():
        raise SystemExit("REFUS : le regard a deja eu lieu (results/first_look_results.json existe). Un seul regard.")
    return fz


def debit_budget(n: int, note: str, ledger_seq: int) -> None:
    st = json.loads(LOOP_STATE.read_text())
    if st.get("budget_tests_remaining", 0) < n:
        raise SystemExit(f"REFUS : budget {st.get('budget_tests_remaining')} < {n} tests requis")
    entry = {"source": "__DEBIT__EVENT_FIRST_LOOK_V1", "provenance": f"regard unique pre-enregistre (n={n}), branche p3-event-first-look-prereg, ledger seq {ledger_seq}",
             "episodes": 0, "credited": -n, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "note": note}
    with open(BUDGET_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    st["budget_tests_remaining"] -= n; st["budget_raw_balance"] = st.get("budget_raw_balance", st["budget_tests_remaining"] + n) - n
    st["tests_consumed_lifetime"] = st.get("tests_consumed_lifetime", 0) + n
    st.setdefault("budget_ledger", []).append(entry)
    LOOP_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


# ----------------------------------------------------------------------------- modes
def plan(rows: list[dict]) -> dict:
    out = {}
    for k, h in HYPOTHESES.items():
        sel, why = select(rows, h["selection"])
        years = defaultdict(int)
        for r in sel:
            years[r["publication_ts_exchange"][:4]] += 1
        out[k] = {"mechanism_id": h["mechanism_id"], "selection": h["selection"], "side": h["side"],
                  "entry": f"publication + {ENTRY_DELAY_S}s + {h['entry_offset_min']}min", "primary": h["primary"],
                  "sensitivities": h["sensitivities"], "market_pref": h["market_pref"], "counts": why,
                  "by_year": dict(sorted(years.items())), "note": "avant resolution de marche (existence Vision au regard)"}
    return out


def positive_control(n_per_h: int = 200, effect_bps: float = 80.0, seed: int = 11) -> dict:
    """Prix synthetiques : un effet injecte de effect_bps doit etre retrouve, un effet nul rejete."""
    rng = random.Random(seed)
    rows, effects_on = [], {}
    base = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    templates = {"binance_futures_listing": ("binance", "futures_listing", "perp", "Binance Futures Will Launch USDⓈ-Margined {a}USDT Perpetual Contract"),
                 "binance_delisting": ("binance", "delisting", "spot", "Binance Will Delist {a} on 2024-06-01"),
                 "okx_bybit_listing": ("bybit", "futures_listing", "perp", "New listing: {a}USDT Perpetual Contract, with up to 25x leverage")}
    i = 0
    for k, h in HYPOTHESES.items():
        src, et, mt, tpl = templates[h["selection"]]
        for j in range(n_per_h):
            a = f"SYN{i:04d}"; i += 1
            pub = base + rng.randrange(0, 300 * 86400_000)
            pub -= pub % 1000
            rows.append({"event_id": a, "source": src, "event_type": et, "market_type": mt, "asset": a, "raw_title": tpl.format(a=a),
                         "raw_url": f"https://synthetic/{a}", "raw_body_hash": hashlib.sha256(a.encode()).hexdigest(),
                         "publication_ts_exchange": datetime.fromtimestamp(pub / 1000, tz=timezone.utc).isoformat(),
                         "publication_ts_exchange_ms": pub})
            t0 = pub + ENTRY_DELAY_S * 1000 + h["entry_offset_min"] * 60_000
            for sym in (f"{a}USDT", spot_symbol(a)):
                effects_on[sym] = (t0, h["side"], effect_bps, HORIZON_MIN[h["primary"]])
    thr = threshold_t(N_FAMILY_TESTS)
    res = {}
    for label, effects in (("effect_injected", effects_on), ("null", {})):
        store = SyntheticStore(effects)
        # les evenements synthetiques de H1 (selection binance_futures_listing) sont aussi ceux de H2 :
        # l'effet injecte pour H2 (reversion) a un autre horizon : on separe les jeux par hypothese
        res[label] = {}
        for k in HYPOTHESES:
            sub = [r for r in rows if HYPOTHESES[k]["selection"] == HYPOTHESES[k]["selection"] and r["asset"] in
                   {rr["asset"] for rr in rows[list(HYPOTHESES).index(k) * n_per_h:(list(HYPOTHESES).index(k) + 1) * n_per_h]}]
            out = run_hypothesis(store, k, sub, thr, workers=4, placebo=True)
            res[label][k] = {"verdict": out["verdict"], "reasons": out["reasons"], "n": out["n_measured"],
                             "gross_bps": out["primary_stats"].get("mean_bps"), "t": out["primary_stats"].get("t"),
                             "placebo_mean_bps": out["placebo_48h_mean_bps"], "threshold_t": thr}
    # meme bruit dans les deux passes (meme seed) : la difference effet - nul DOIT etre l'effet injecte
    rec = {k: res["effect_injected"][k]["gross_bps"] - res["null"][k]["gross_bps"] for k in HYPOTHESES}
    res["recovered_bps"] = rec
    res["expected"] = {"recovery": f"effect - null within [0.75, 1.15] x {effect_bps} bps for every hypothesis (chain fidelity, slope ~ 1)",
                       "effect_injected": "t >= threshold for every hypothesis",
                       "null": "never FORWARD_SEAL_REQUIRED"}
    res["passed"] = (all(0.75 * effect_bps <= rec[k] <= 1.15 * effect_bps for k in HYPOTHESES)
                     and all(v["t"] is not None and v["t"] >= thr for v in res["effect_injected"].values())
                     and all(v["verdict"] != "FORWARD_SEAL_REQUIRED" for v in res["null"].values()))
    res["params"] = {"n_per_hypothesis": n_per_h, "effect_bps": effect_bps, "seed": seed}
    return res


def run(workers: int = 8) -> dict:
    import look_ledger  # tools/look_ledger.py : le ledger est ecrit AVANT le premier prix
    fz = check_seal()
    rows = load_rows(FROZEN)
    thr = threshold_t(N_FAMILY_TESTS)
    configs = [{"hypothesis": k, "mechanism_id": h["mechanism_id"], "side": h["side"], "primary": h["primary"],
                "entry": f"pub+{ENTRY_DELAY_S}s+{h['entry_offset_min']}m", "selection": h["selection"], "threshold_t": thr}
               for k, h in HYPOTHESES.items()]
    entry = look_ledger.record("confirm", (fz["snapshot"]["min_event_ts"], fz["snapshot"]["max_event_ts"]), configs,
                               prereg=str(PREREG), require_witness=True,
                               note=f"event first look, snapshot sha256 {fz['snapshot']['sha256'][:16]}, harness pinned")
    debit_budget(N_FAMILY_TESTS, "regard unique event_reaction_v1 : 4 hypotheses (H1-H4), famille news/official_event_reaction", entry["seq"])
    store = VisionStore()
    results = {"ledger_seq": entry["seq"], "ledger_hash": entry["hash"], "threshold_t": thr, "family": FAMILY, "subfamily": SUBFAMILY,
               "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hypotheses": {}}
    for k in HYPOTHESES:
        print(f"== {k} {HYPOTHESES[k]['name']}", flush=True)
        results["hypotheses"][k] = run_hypothesis(store, k, rows, thr, workers=workers)
        r = results["hypotheses"][k]; p = r["primary_stats"]
        print(f"   n={r['n_measured']} gross={p.get('mean_bps', float('nan')):.1f} t={p.get('t', float('nan')):.2f} -> {r['verdict']} {r['reasons']}", flush=True)
    results["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "first_look_results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    lines = ["# event first look — verdicts (un seul regard)", "",
             f"ledger seq {entry['seq']} · threshold_t({N_FAMILY_TESTS}) = {thr:.4f} · snapshot {fz['snapshot']['sha256'][:16]}", "",
             "| H | mechanism | n | gross bps | t | cost wall | verdict | reasons |", "|---|---|---|---|---|---|---|---|"]
    for k, r in results["hypotheses"].items():
        p = r["primary_stats"]
        lines.append(f"| {k} | {r['mechanism_id']} | {r['n_measured']} | {p.get('mean_bps', float('nan')):.1f} | {p.get('t', float('nan')):.2f} | {r['cost_wall_bps']:.0f} | **{r['verdict']}** | {'; '.join(r['reasons'])} |")
    (RESULTS / "verdict.md").write_text("\n".join(lines) + "\n")
    for k, r in results["hypotheses"].items():
        d = ROOT / "mechanisms" / r["mechanism_id"] / "results"; d.mkdir(parents=True, exist_ok=True)
        (d / "verdict.json").write_text(json.dumps({"mechanism_id": r["mechanism_id"], "status": r["verdict"], "reasons": r["reasons"],
                                                    "primary": r["primary_stats"], "ledger_seq": entry["seq"], "first_look": True}, indent=1, default=str))
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true"); ap.add_argument("--positive-control", action="store_true")
    ap.add_argument("--run", action="store_true"); ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.plan:
        src = FROZEN if FROZEN.exists() else ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
        print(json.dumps({"source": str(src.relative_to(ROOT)), "plan": plan(load_rows(src))}, indent=1, ensure_ascii=False))
    elif a.positive_control:
        res = positive_control(); RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "positive_control.json").write_text(json.dumps(res, indent=1, default=str))
        print(json.dumps({k: v for k, v in res.items() if k != "expected"}, indent=1, default=str)); print("PASSED" if res["passed"] else "FAILED")
        sys.exit(0 if res["passed"] else 1)
    elif a.run:
        run(a.workers)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
