#!/usr/bin/env python3
"""
event_listing_perp_fade_v1 / first_look.py -- H2 rendue executable : short du perp Binance apres
listing, sur les listings ou le perp est le PREMIER marche Binance reellement negociable.

    construire l'univers (metadonnees) -> pre-enregistrer -> geler -> regarder UNE fois

Une seule hypothese. Direction fixee (short) -- informee par H2 spot (regard seq 7, +202 bps,
t 3,25), sur des evenements et un marche jamais prices : les 210 listings perp sans spot Binance.

Modes :
  --build-universe   depuis le snapshot gele du tape (sha 6e957851) : selection, heure de lancement
                     (exchangeInfo.onboardDate ; sinon premiere barre Vision ; croisement des deux),
                     exclusion spot-avant-perp, doublons, actifs deja prices par H1/H2. AUCUN rendement.
  --positive-control prix synthetiques : effet injecte retrouve, nul rejete
  --run              le regard : refuse sans FREEZE / pins / temoin pousse / si resultat existe ;
                     LOOK_LEDGER + debit AVANT le premier prix
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from research_kernel.multiplicity import threshold_t  # noqa: E402
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("event_reaction_first_look", ROOT / "mechanisms" / "event_reaction_v1" / "first_look.py")
ER = _ilu.module_from_spec(_spec); _spec.loader.exec_module(ER)   # VisionStore, select, cluster_stats, series, spot_symbol

MECH = ROOT / "mechanisms" / "event_listing_perp_fade_v1"
RESULTS = MECH / "results"
PREREG = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_PREREG.md"
FREEZE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_FREEZE.json"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
FROZEN_TAPE = ROOT / "data_lake" / "first_look" / "event_reaction_v1" / "official_event_tape_frozen.jsonl"
FROZEN_TAPE_SHA = "6e957851e452395f"
PRIOR_RESULTS = ROOT / "mechanisms" / "event_reaction_v1" / "results" / "first_look_results.json"
EXCHANGE_INFO = ROOT / "data_lake" / "first_look" / "event_listing_perp_fade_v1" / "exchangeInfo.json"
BUDGET_LEDGER = ROOT / "reports" / "loop" / "BUDGET_LEDGER.jsonl"
LOOP_STATE = ROOT / "reports" / "loop" / "LOOP_STATE.json"

FAMILY = "news"; N_FAMILY_TESTS_BEFORE = 4; N_FAMILY_TESTS = N_FAMILY_TESTS_BEFORE + 1   # 5e hypothese de la famille
SIDE = -1                                  # short
ENTRY_OFFSET_MIN = 15; PRIMARY = "6h"; SENSITIVITIES = ["60m", "24h"]
HORIZON_MIN = {"60m": 60, "6h": 360, "24h": 1440}
BAR_TOLERANCE_MIN = 5                      # livre neuf : barres manquantes tolerees jusqu'a +5 min
MIN_GROSS_MEDIAN_BPS = 30.0
COST_WALL_X = 3.0                          # net = brut - cout ; mur : net >= 3 x cout (lecture stricte)
MIN_N = 30; MIN_N_EFF = 30; MAX_TOP1_SHARE = 0.20
MAX_TS_UNRELIABLE_SHARE = 0.20
MIN_MEDIAN_QUOTE_VOLUME_6H_USD = 2_000_000  # capacite : 100 k$ <= 5 % du volume de la fenetre
LAUNCH_MAX_DAYS_AFTER_PUB = 7; TS_AGREE_MIN = 5
COST = {"taker": {"fee_per_side": 5.0, "spread": 8.0, "slippage": 6.0}, "maker": {"fee_per_side": 2.0, "spread": 8.0, "slippage": 6.0}}
def cost_rt(mode="taker"):
    c = COST[mode]; return 2 * c["fee_per_side"] + c["spread"] + c["slippage"]
BENCH = ER.BENCH


class VisionStore4(ER.VisionStore):
    """Le lecteur du harnais scelle (seq 7) ne rend que (open, volume) ; celui-ci rend aussi le
    volume en quote (capacite) et l'amplitude 1 min (proxy de spread). Le harnais parent reste intact."""
    def bars(self, market, symbol, day):
        key = (market, symbol, day)
        if key in self._mem:
            return self._mem[key]
        if self.fetch(market, symbol, day) != "ok":
            self._mem[key] = None; return None
        out = {}
        with zipfile.ZipFile(self._path(market, symbol, day)) as z:
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                ot = int(row[0]); ot = ot // 1000 if ot > 10**14 else ot
                o, h, l = float(row[1]), float(row[2]), float(row[3])
                out[ot] = (o, float(row[5]), float(row[7]), 1e4 * (h - l) / o if o else 0.0)   # (open, volume, quote_volume, range_bps)
        self._mem[key] = out; return out


# ----------------------------------------------------------------------------- metadonnees
def _head_exists(url: str) -> str:
    """'ok' | '404' | 'error' -- HEAD seulement, rien n'est lu."""
    for attempt in range(3):
        try:
            req = Request(url, method="HEAD", headers={"User-Agent": "futur-first-look"})
            with urlopen(req, timeout=30):
                return "ok"
        except HTTPError as e:
            if e.code == 404:
                return "404"
        except (URLError, TimeoutError, OSError):
            pass
    return "error"


def _first_bar_open_ms(store: ER.VisionStore, market: str, symbol: str, day: str):
    """open_time de la premiere barre du fichier journalier (horodatage seulement)."""
    if store.fetch(market, symbol, day) != "ok":
        return None
    with zipfile.ZipFile(store._path(market, symbol, day)) as z:
        for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
            if row and row[0].isdigit():
                ot = int(row[0]); return ot // 1000 if ot > 10**14 else ot
    return None


def _iso(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")
def _day(ms): return ER._day(ms)


def load_exchange_info() -> dict:
    if not EXCHANGE_INFO.exists():
        with urlopen(Request("https://fapi.binance.com/fapi/v1/exchangeInfo", headers={"User-Agent": "futur-first-look"}), timeout=60) as r:
            EXCHANGE_INFO.parent.mkdir(parents=True, exist_ok=True); EXCHANGE_INFO.write_bytes(r.read())
    d = json.loads(EXCHANGE_INFO.read_text())
    return {s["symbol"]: s for s in d["symbols"]}


def build_universe(workers: int = 8) -> dict:
    """Univers gele : evenements, symbole du perp, heure de lancement, raisons d'exclusion. AUCUN rendement."""
    if hashlib.sha256(FROZEN_TAPE.read_bytes()).hexdigest()[:16] != FROZEN_TAPE_SHA:
        raise SystemExit("REFUS : le snapshot gele du tape n'est pas celui du regard seq 7")
    rows = ER.load_rows(FROZEN_TAPE)
    sel, why = ER.select(rows, "binance_futures_listing")
    prior = json.loads(PRIOR_RESULTS.read_text())
    priced_assets = {e["asset"] for k in ("H1", "H2") for e in prior["hypotheses"][k]["events"]}
    info = load_exchange_info(); store = VisionStore4()
    out, reasons, seen = [], defaultdict(int), set()
    def one(r):
        asset = ER.asset_of(r); pub = r["publication_ts_exchange_ms"]
        rec = {"event_id": r["event_id"], "asset": asset, "publication_ts": r["publication_ts_exchange"], "publication_ts_ms": pub, "raw_title": r["raw_title"]}
        if asset in priced_assets:
            rec["exclude"] = "asset_priced_in_h1_h2_spot"; return rec
        cands = [f"{asset}USDT"] + ([f"1000{asset}USDT"] if not asset.startswith("1000") else []) + ([asset] if asset.endswith("USDT") else [])  # tickers a 1 lettre : le titre porte le symbole
        if "Will Convert" in r["raw_title"]:
            rec["exclude"] = "conversion_not_listing"; return rec
        # 1) heure de lancement : onboardDate si le symbole est encore liste ; sinon premiere barre Vision
        sym = onboard = None
        for c in cands:
            if c in info and info[c].get("onboardDate"):
                sym, onboard = c, int(info[c]["onboardDate"]); break
        first_bar = None; sym_v = None
        for c in ([sym] if sym else cands):
            for k in range(0, LAUNCH_MAX_DAYS_AFTER_PUB + 1):
                day = _day(pub + k * 86400_000)
                st = _head_exists(store._url("um", c, day))
                if st == "ok":
                    first_bar = _first_bar_open_ms(store, "um", c, day); sym_v = c; break
                if st == "error":
                    rec["exclude"] = "vision_error"; return rec
            if first_bar is not None:
                break
        if sym is None and sym_v is None:
            rec["exclude"] = "no_binance_perp_within_7d_of_publication"; return rec
        sym = sym or sym_v; rec["symbol"] = sym
        if onboard is not None and onboard < pub - 86400_000:
            rec["exclude"] = "perp_listed_before_announcement"; rec["onboard_ts"] = _iso(onboard); return rec
        if first_bar is None and onboard is not None:
            day = _day(onboard); first_bar = _first_bar_open_ms(store, "um", sym, day)
        rec["onboard_ts"] = _iso(onboard) if onboard else None; rec["vision_first_bar_ts"] = _iso(first_bar) if first_bar else None
        # la premiere barre Vision est la PREUVE de cotation ; onboardDate est le controle croise
        if first_bar is None:
            rec["exclude"] = "no_vision_first_bar"; return rec
        if onboard is not None:
            rec["ts_disagreement_min"] = round((first_bar - onboard) / 60_000, 1)
            if first_bar < onboard - TS_AGREE_MIN * 60_000:
                rec["exclude"] = "launch_ts_unreliable"; return rec          # des barres avant l'onboarding officiel : incoherent
            rec["launch_ts_source"] = "vision_first_bar (onboardDate agrees)" if abs(first_bar - onboard) <= TS_AGREE_MIN * 60_000 else "vision_first_bar (onboardDate earlier: date-only field)"
        else:
            rec["launch_ts_source"] = "vision_first_bar (symbol delisted since, no onboardDate)"
        start = first_bar
        if start < pub - 3600_000 or start > pub + LAUNCH_MAX_DAYS_AFTER_PUB * 86400_000:
            rec["exclude"] = "launch_not_within_[-1h,+7d]_of_publication"; return rec
        rec["tradable_start_ts"] = _iso(start); rec["tradable_start_ms"] = start
        # 2) spot Binance negociable AVANT le perp ? (fichier de la veille, ou barre spot avant le lancement le jour meme)
        ssym = asset if asset.endswith("USDT") else ER.spot_symbol(asset)   # tickers a 1 lettre : le symbole spot est le meme
        if _head_exists(store._url("spot", ssym, _day(start - 86400_000))) == "ok":
            rec["exclude"] = "spot_tradable_before_perp"; return rec
        if store.fetch("spot", ssym, _day(start)) == "ok":
            fb = _first_bar_open_ms(store, "spot", ssym, _day(start))
            if fb is not None and fb < start:
                rec["exclude"] = "spot_tradable_before_perp"; return rec
        return rec
    with ThreadPoolExecutor(max_workers=workers) as ex:
        recs = list(ex.map(one, sel))
    for rec in sorted(recs, key=lambda x: x["publication_ts_ms"]):
        if rec.get("exclude"):
            reasons[rec["exclude"]] += 1; continue
        if rec["asset"] in seen:
            reasons["duplicate_asset"] += 1; rec["exclude"] = "duplicate_asset"; continue
        seen.add(rec["asset"]); out.append(rec)
    by_year = defaultdict(int)
    for r in out:
        by_year[r["tradable_start_ts"][:4]] += 1
    uni = {"built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frozen_tape_sha256_prefix": FROZEN_TAPE_SHA,
           "selection_counts": why, "exclusion_counts": dict(reasons), "n_events": len(out), "by_launch_year": dict(sorted(by_year.items())),
           "launch_ts_sources": dict(sorted(((s, sum(1 for r in out if r["launch_ts_source"] == s)) for s in {r["launch_ts_source"] for r in out}))),
           "events": out, "excluded": [r for r in recs if r.get("exclude")]}
    UNIVERSE.write_text(json.dumps(uni, indent=1, ensure_ascii=False) + "\n")
    return uni


# ----------------------------------------------------------------------------- mesure
def measure(store, ev: dict) -> dict:
    start = ev["tradable_start_ms"]; sym = ev["symbol"]
    t_entry = start + ENTRY_OFFSET_MIN * 60_000
    hmax = max(HORIZON_MIN[h] for h in [PRIMARY, *SENSITIVITIES])
    days = ER._days_between(start - 3600_000, start + hmax * 60_000 + 3600_000)
    s = ER.series(store, "um", sym, days); b = ER.series(store, BENCH[0], BENCH[1], days)
    out = {"event_id": ev["event_id"], "asset": ev["asset"], "symbol": sym, "tradable_start_ts": ev["tradable_start_ts"], "excess": {}, "raw": {}}
    def bar(series, ms):
        m0 = -(-ms // 60_000) * 60_000
        for k in range(BAR_TOLERANCE_MIN + 1):
            v = series.get(m0 + k * 60_000)
            if v:
                return m0 + k * 60_000, v
        return None, None
    e_ot, e_v = bar(s, t_entry); be_ot, be_v = bar(b, t_entry)
    if e_v is None or be_v is None:
        out["missing"] = "entry_bar"; return out
    out["entry_lag_s"] = (e_ot - start) / 1000
    for h in [PRIMARY, *SENSITIVITIES]:
        x_ot, x_v = bar(s, start + HORIZON_MIN[h] * 60_000); bx_ot, bx_v = bar(b, start + HORIZON_MIN[h] * 60_000)
        if x_v is None or bx_v is None:
            continue
        ra = 1e4 * math.log(x_v[0] / e_v[0]); rb = 1e4 * math.log(bx_v[0] / be_v[0])
        out["raw"][h] = ra; out["excess"][h] = ra - rb
    # capacite : volume quote (USD) sur [entree, sortie primaire] ; proxy de spread : (high-low)/open median
    win = {ot: v for ot, v in s.items() if e_ot <= ot < start + HORIZON_MIN[PRIMARY] * 60_000}
    if win:
        qv = [v[2] for v in win.values()]; rg = [v[3] for v in win.values()]
        out["quote_volume_window_usd"] = sum(qv); out["quote_volume_per_min_median_usd"] = sorted(qv)[len(qv) // 2]
        out["range_1m_bps_median"] = sorted(rg)[len(rg) // 2]
    # premiere quinzaine (descriptif) : du lancement a l'entree
    f_ot, f_v = bar(s, start); bf_ot, bf_v = bar(b, start)
    if f_v is not None and bf_v is not None:
        out["first15_excess_bps"] = 1e4 * (math.log(e_v[0] / f_v[0]) - math.log(be_v[0] / bf_v[0]))
    return out


def verdict_for(st: dict, n_ts_unreliable_share: float, cost: float, thr: float) -> tuple:
    if st.get("n", 0) < MIN_N:
        return "INDECIDABLE", [f"N {st.get('n', 0)} < {MIN_N}"]
    if n_ts_unreliable_share > MAX_TS_UNRELIABLE_SHARE:
        return "INDECIDABLE", [f"launch timestamps unreliable share {n_ts_unreliable_share:.2f} > {MAX_TS_UNRELIABLE_SHARE}"]
    if st["capacity_median_quote_volume_6h_usd"] is None:
        return "INDECIDABLE", ["capacity not measurable"]
    if st["median_bps"] < MIN_GROSS_MEDIAN_BPS:
        return "REJECTED_NO_GROSS", [f"median gross {st['median_bps']:.1f} < {MIN_GROSS_MEDIAN_BPS}"]
    net = st["mean_bps"] - cost
    if net < COST_WALL_X * cost:
        return "REJECTED_COST_WALL", [f"net {net:.1f} (mean {st['mean_bps']:.1f} - cost {cost:.1f}) < {COST_WALL_X} x cost"]
    reasons = []
    if not (st["t"] >= thr):
        reasons.append(f"t {st['t']:.2f} < threshold {thr:.4f}")
    if st["n_eff"] < MIN_N_EFF:
        reasons.append(f"n_eff {st['n_eff']:.1f} < {MIN_N_EFF}")
    if st["top1_share"] > MAX_TOP1_SHARE:
        reasons.append(f"top1_share {st['top1_share']:.2f} > {MAX_TOP1_SHARE}")
    if st["capacity_median_quote_volume_6h_usd"] < MIN_MEDIAN_QUOTE_VOLUME_6H_USD:
        reasons.append(f"capacity: median 6h quote volume {st['capacity_median_quote_volume_6h_usd']:.0f} USD < {MIN_MEDIAN_QUOTE_VOLUME_6H_USD}")
    return ("INDECIDABLE", reasons) if reasons else ("FORWARD_SEAL_REQUIRED", ["all criteria met"])


def evaluate(store, events: list, thr: float, workers: int = 8) -> dict:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        ms = list(ex.map(lambda ev: measure(store, ev), events))
    per_h = {h: ([], []) for h in [PRIMARY, *SENSITIVITIES]}
    missing = defaultdict(int); qv6, rng, f15, rows = [], [], [], []
    for m in ms:
        if m.get("missing"):
            missing[m["missing"]] += 1; continue
        day = m["tradable_start_ts"][:10]
        for h, (xs, cl) in per_h.items():
            if h in m["excess"]:
                xs.append(SIDE * m["excess"][h]); cl.append(day)
        if PRIMARY in m["excess"]:
            rows.append(m)
            if "quote_volume_window_usd" in m:
                qv6.append(m["quote_volume_window_usd"]); rng.append(m["range_1m_bps_median"])
            if "first15_excess_bps" in m:
                f15.append(m["first15_excess_bps"])
    stats = {h: ER.cluster_stats(xs, cl) for h, (xs, cl) in per_h.items()}
    prim = stats[PRIMARY]
    prim["win_rate"] = prim.get("share_positive")
    prim["capacity_median_quote_volume_6h_usd"] = sorted(qv6)[len(qv6) // 2] if qv6 else None
    prim["range_1m_bps_median"] = sorted(rng)[len(rng) // 2] if rng else None
    prim["first15_excess_mean_bps"] = (sum(f15) / len(f15)) if f15 else None
    by_year = defaultdict(list)
    for m in rows:
        by_year[m["tradable_start_ts"][:4]].append(SIDE * m["excess"][PRIMARY])
    return {"stats_by_horizon": stats, "primary_stats": prim, "missing_bars": dict(missing), "n_measured": prim.get("n", 0),
            "by_year_mean_bps": {y: (sum(v) / len(v), len(v)) for y, v in sorted(by_year.items())},
            "events": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items() if k != "raw"} for m in rows]}


# ----------------------------------------------------------------------------- scellement
def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()


def check_seal() -> dict:
    if not FREEZE.exists():
        raise SystemExit("REFUS : pas de FREEZE")
    fz = json.loads(FREEZE.read_text())
    if sha(UNIVERSE) != fz["universe"]["sha256"]:
        raise SystemExit("REFUS : l'univers gele ne correspond plus au sha256 scelle")
    if hashlib.sha256(FROZEN_TAPE.read_bytes()).hexdigest() != fz["snapshot"]["sha256"]:
        raise SystemExit("REFUS : le snapshot du tape ne correspond plus")
    for rel, expected in fz["pins"].items():
        if sha(ROOT / rel) != expected:
            raise SystemExit(f"REFUS : {rel} a change depuis le scellement")
    if (RESULTS / "first_look_results.json").exists():
        raise SystemExit("REFUS : le regard a deja eu lieu. Un seul regard.")
    return fz


def debit_budget(n: int, note: str, seq: int):
    st = json.loads(LOOP_STATE.read_text())
    if st.get("budget_tests_remaining", 0) < n:
        raise SystemExit(f"REFUS : budget {st.get('budget_tests_remaining')} < {n}")
    entry = {"source": "__DEBIT__EVENT_LISTING_PERP_FADE_V1", "provenance": f"regard unique pre-enregistre (n=1), branche p3-h2-perp-first-executable, ledger seq {seq}",
             "episodes": 0, "credited": -n, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "note": note}
    with open(BUDGET_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    st["budget_tests_remaining"] -= n; st["budget_raw_balance"] = st.get("budget_raw_balance", st["budget_tests_remaining"] + n) - n
    st["tests_consumed_lifetime"] = st.get("tests_consumed_lifetime", 0) + n; st.setdefault("budget_ledger", []).append(entry)
    LOOP_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


# ----------------------------------------------------------------------------- controle positif
class SynthStore(ER.SyntheticStore):
    """Barres synthetiques a 4 champs (open, volume, quote_volume, range_bps) comme VisionStore.bars."""
    def bars(self, market, symbol, day):
        b = super().bars(market, symbol, day)
        return {ot: (v[0], v[1], v[1] * 5000.0, 20.0) for ot, v in b.items()}


def positive_control(n: int = 600, effect_bps: float = 80.0, seed: int = 5) -> dict:
    rng = random.Random(seed); base = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)
    evs, eff = [], {}
    for i in range(n):
        start = base + rng.randrange(0, 200 * 86400_000); start -= start % 60_000
        a = f"SYN{i:04d}"; evs.append({"event_id": a, "asset": a, "symbol": f"{a}USDT", "tradable_start_ts": _iso(start), "tradable_start_ms": start})
        eff[f"{a}USDT"] = (start + ENTRY_OFFSET_MIN * 60_000, SIDE, effect_bps, HORIZON_MIN[PRIMARY])
    thr = threshold_t(N_FAMILY_TESTS); cost = cost_rt("taker"); res = {}
    for label, e in (("effect_injected", eff), ("null", {})):
        r = evaluate(SynthStore(e), evs, thr, workers=4); p = r["primary_stats"]
        v, why = verdict_for(p, 0.0, cost, thr)
        res[label] = {"n": p["n"], "mean_bps": p["mean_bps"], "median_bps": p["median_bps"], "t": p["t"], "verdict": v, "reasons": why}
    rec = res["effect_injected"]["mean_bps"] - res["null"]["mean_bps"]
    res["recovered_bps"] = rec; res["threshold_t"] = thr
    res["passed"] = 0.75 * effect_bps <= rec <= 1.15 * effect_bps and res["effect_injected"]["t"] >= thr and res["null"]["verdict"] != "FORWARD_SEAL_REQUIRED"
    return res


# ----------------------------------------------------------------------------- run
def run(workers: int = 8) -> dict:
    import look_ledger
    fz = check_seal(); uni = json.loads(UNIVERSE.read_text()); events = uni["events"]
    thr = threshold_t(N_FAMILY_TESTS); cost = cost_rt("taker")
    ts = sorted(e["tradable_start_ts"] for e in events)
    cfg = [{"hypothesis": "H2-exec", "mechanism_id": "event_listing_perp_fade_v1", "side": SIDE, "entry": f"launch+{ENTRY_OFFSET_MIN}m", "primary": PRIMARY,
            "n_universe": len(events), "threshold_t": thr, "cost_rt_bps": cost}]
    entry = look_ledger.record("confirm", (ts[0], ts[-1]), cfg, prereg=str(PREREG), require_witness=True,
                               note=f"event_listing_perp_fade_v1 first look, universe sha {fz['universe']['sha256'][:16]}, harness pinned")
    debit_budget(1, "regard unique event_listing_perp_fade_v1 (H2 executable, perp-first), famille news n=5", entry["seq"])
    store = VisionStore4(); res = evaluate(store, events, thr, workers=workers); p = res["primary_stats"]
    unrel = uni["exclusion_counts"].get("launch_ts_unreliable", 0); share_unrel = unrel / max(1, unrel + len(events))
    v, why = verdict_for(p, share_unrel, cost, thr)
    p["net_bps_taker"] = p["mean_bps"] - cost; p["net_bps_maker_sensitivity"] = p["mean_bps"] - cost_rt("maker")
    out = {"ledger_seq": entry["seq"], "ledger_hash": entry["hash"], "threshold_t": thr, "family": FAMILY, "n_family_tests": N_FAMILY_TESTS,
           "cost_rt_bps": {"taker": cost, "maker_sensitivity": cost_rt("maker")}, "cost_wall_rule": "net = mean - cost_taker >= 3 x cost_taker",
           "launch_ts_unreliable_share": share_unrel, "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "verdict": v, "reasons": why, **res, "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "first_look_results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    (RESULTS / "verdict.json").write_text(json.dumps({"mechanism_id": "event_listing_perp_fade_v1", "status": v, "reasons": why, "primary": p, "ledger_seq": entry["seq"], "first_look": True}, indent=1, default=str))
    (RESULTS / "verdict.md").write_text(f"# event_listing_perp_fade_v1 — verdict (un seul regard)\n\nledger seq {entry['seq']} · threshold_t({N_FAMILY_TESTS}) = {thr:.4f} · cost taker {cost:.0f} bps\n\n"
                                        f"| n | mean | median | t | win | N_eff | top-1 | net taker | cap. median 6h vol | verdict |\n|---|---|---|---|---|---|---|---|---|---|\n"
                                        f"| {p['n']} | {p['mean_bps']:.1f} | {p['median_bps']:.1f} | {p['t']:.2f} | {p['win_rate']:.2f} | {p['n_eff']:.1f} | {p['top1_share']:.2f} | {p['net_bps_taker']:.1f} | {p['capacity_median_quote_volume_6h_usd'] or 0:.0f} $ | **{v}** |\n\n{'; '.join(why)}\n")
    print(f"n={p['n']} mean={p['mean_bps']:.1f} median={p['median_bps']:.1f} t={p['t']:.2f} win={p['win_rate']:.2f} n_eff={p['n_eff']:.1f} top1={p['top1_share']:.2f} net_taker={p['net_bps_taker']:.1f} -> {v} {why}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-universe", action="store_true"); ap.add_argument("--positive-control", action="store_true"); ap.add_argument("--run", action="store_true")
    ap.add_argument("--workers", type=int, default=8); a = ap.parse_args()
    if a.build_universe:
        u = build_universe(a.workers); print(json.dumps({k: v for k, v in u.items() if k not in ("events", "excluded")}, indent=1, ensure_ascii=False))
    elif a.positive_control:
        r = positive_control(); RESULTS.mkdir(parents=True, exist_ok=True); (RESULTS / "positive_control.json").write_text(json.dumps(r, indent=1, default=str))
        print(json.dumps(r, indent=1, default=str)); sys.exit(0 if r["passed"] else 1)
    elif a.run:
        run(a.workers)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
