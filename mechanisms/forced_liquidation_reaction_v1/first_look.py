#!/usr/bin/env python3
"""
forced_liquidation_reaction_v1 / first_look.py -- le regard unique sur le tape forced-flow (P3B).

    geler (first_look_gate --freeze forced_flow) -> univers par regles pures -> pre-enregistrer -> regarder UNE fois

Deux hypotheses, famille `liquidation` (0 scellee avant) -> threshold_t(2) = 1,96 unilateral :
  H1 forced_liquidation_reaction_v1    grosse liquidation (>= 50 k$)   -> continuation, primaire 30 s (5 s, 5 min)
  H2 forced_liquidation_exhaustion_v1  liquidation extreme (>= 250 k$) -> retournement,  primaire 5 min (30 s, 30 min)

Direction : une position LONGUE liquidee est une vente forcee -> continuation = baisse (on vend) ;
une SHORT liquidee est un achat force -> continuation = hausse (on achete). H2 = l'oppose.
Entree : premier trade >= t0 + 2 s (latence mesuree ~1,1 s + marge). Sortie : dernier trade <= entree + h.
Prix : Binance REST fapi aggTrades (fenetres courtes, cache disque), aucune donnee live, aucun ordre.
Statistique : signe x 1e4 x ln(P_sortie / P_entree), brut ; SE robuste par grappe de 5 minutes
(une cascade touche plusieurs symboles a la fois : la grappe est l'unite d'independance).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import threading
import time
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
ER = _ilu.module_from_spec(_spec); _spec.loader.exec_module(ER)          # cluster_stats (harnais pinne du regard 7, lecture seule)

MECH = ROOT / "mechanisms" / "forced_liquidation_reaction_v1"; RESULTS = MECH / "results"
FROZEN_DIR = ROOT / "data_lake" / "first_look" / "forced_liquidation_reaction_v1"
FROZEN = FROZEN_DIR / "liquidations_frozen.jsonl"; MANIFEST = FROZEN_DIR / "SNAPSHOT_MANIFEST.json"
PREREG = ROOT / "reports" / "first_look" / "forced_liquidation_reaction_v1_PREREG.md"
FREEZE = ROOT / "reports" / "first_look" / "forced_liquidation_reaction_v1_FREEZE.json"
UNIVERSE = ROOT / "reports" / "first_look" / "forced_liquidation_reaction_v1_UNIVERSE.json"
CACHE = ROOT / "data" / "forced_flow_prices"
BUDGET_LEDGER = ROOT / "reports" / "loop" / "BUDGET_LEDGER.jsonl"; LOOP_STATE = ROOT / "reports" / "loop" / "LOOP_STATE.json"
FAPI = "https://fapi.binance.com/fapi/v1"

FAMILY = "liquidation"; N_FAMILY_TESTS = 2
ENTRY_DELAY_MS = 2000
HORIZON_MS = {"5s": 5_000, "30s": 30_000, "5m": 300_000, "30m": 1_800_000}
HYPOTHESES = {
    "H1": {"mechanism_id": "forced_liquidation_reaction_v1", "name": "cascade continuation after a large liquidation", "min_notional_usd": 50_000.0,
           "direction": "continuation", "primary": "30s", "sensitivities": ["5s", "5m"]},
    "H2": {"mechanism_id": "forced_liquidation_exhaustion_v1", "name": "exhaustion reversal after an extreme liquidation", "min_notional_usd": 250_000.0,
           "direction": "reversal", "primary": "5m", "sensitivities": ["30s", "30m"]},
}
MIN_GROSS_BPS = 10.0; COST_WALL_X = 3.0; FEE_TAKER_BPS = 5.0; SLIPPAGE_BPS = 4.0
MIN_N = 30; MIN_N_EFF = 30; MAX_TOP1_SHARE = 0.20; MIN_CLUSTERS = 20; MAX_TOP_CLUSTER_SHARE = 0.30
CLUSTER_S = 300; HORIZON_MARGIN_MIN = 32
NOTIONAL_BUCKETS = [(50e3, 100e3, "50k-100k"), (100e3, 250e3, "100k-250k"), (250e3, 1e6, "250k-1M"), (1e6, float("inf"), ">=1M")]


# ----------------------------------------------------------------------------- univers (regles pures)
def sign_of(r: dict) -> int:
    """+1 : continuation = hausse (short liquide = achat force) ; -1 : continuation = baisse (long liquide = vente forcee)."""
    return -1 if r["liquidated_position"] == "long" else +1


def cost_bps(r: dict) -> float:
    return 2 * FEE_TAKER_BPS + float(r.get("spread_before_bps") or 0.0) + SLIPPAGE_BPS


def build_universe() -> dict:
    man = json.loads(MANIFEST.read_text())
    frozen_at = datetime.fromisoformat(man["frozen_at_utc"]); cutoff = frozen_at - timedelta(minutes=HORIZON_MARGIN_MIN)
    rows = [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    why, out = defaultdict(int), []
    for r in rows:
        if r["venue"] != "binance":
            why["not_binance (bybit: no sub-minute history)"] += 1; continue
        if not r["symbol"].endswith("USDT"):
            why["not_usdt_quote"] += 1; continue
        if (r.get("notional_usd") or 0) < HYPOTHESES["H1"]["min_notional_usd"]:
            why["notional_lt_50k"] += 1; continue
        if datetime.fromisoformat(r["event_ts_exchange"]) > cutoff:
            why["horizon_30m_not_observable_at_freeze"] += 1; continue
        if r.get("spread_before_bps") is None or (r.get("bbo_age_ms") is None) or r["bbo_age_ms"] < 0:
            why["not_enriched_or_negative_age"] += 1; continue
        if r.get("liquidated_position") not in ("long", "short"):
            why["no_side"] += 1; continue
        if any(r.get(k) is not None for k in ("return_5s_bps", "return_30s_bps", "return_5m_bps", "return_30m_bps")):
            why["post_event_contamination"] += 1; continue
        t0 = r["event_ts_exchange_ns"] // 1_000_000
        out.append({"event_id": hashlib.sha256(f"{r['symbol']}|{r['event_ts_exchange_ns']}|{r['side']}|{r['qty']}".encode()).hexdigest()[:20],
                    "symbol": r["symbol"], "t0_ms": t0, "event_ts": r["event_ts_exchange"], "liquidated_position": r["liquidated_position"], "sign": sign_of(r),
                    "notional_usd": r["notional_usd"], "price": r["price"], "spread_before_bps": r["spread_before_bps"], "book_imbalance_before": r.get("book_imbalance_before"),
                    "bbo_age_ms": r["bbo_age_ms"], "stream_delay_ms": r.get("stream_delay_ms"), "latency_ms": r.get("latency_ms"), "cluster": t0 // (CLUSTER_S * 1000),
                    "cost_bps": cost_bps(r), "in_H1": True, "in_H2": (r["notional_usd"] or 0) >= HYPOTHESES["H2"]["min_notional_usd"]})
    out.sort(key=lambda e: e["t0_ms"])
    u = {"built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frozen_sha256": man["sha256"], "frozen_at_utc": man["frozen_at_utc"],
         "exclusion_counts": dict(why), "n_H1": len(out), "n_H2": sum(1 for e in out if e["in_H2"]),
         "clusters_H1": len({e["cluster"] for e in out}), "clusters_H2": len({e["cluster"] for e in out if e["in_H2"]}),
         "symbols_H1": len({e["symbol"] for e in out}), "longs_liquidated_H1": sum(1 for e in out if e["liquidated_position"] == "long"),
         "by_hour_utc_H1": dict(sorted(defaultdict(int, {}).items())), "events": out}
    bh = defaultdict(int)
    for e in out:
        bh[e["event_ts"][11:13]] += 1
    u["by_hour_utc_H1"] = dict(sorted(bh.items()))
    UNIVERSE.write_text(json.dumps(u, indent=1, ensure_ascii=False) + "\n"); return u


# ----------------------------------------------------------------------------- prix : REST aggTrades, cache, cadence
class RestTrades:
    """Trades agreges Binance USDS-M par fenetres courtes. Cache disque par (symbole, fenetre).
    Cadence : <= 1 500 poids / min (aggTrades = 20). Aucune donnee live, aucun ordre."""
    WEIGHT = 20; BUDGET_PER_MIN = 1500

    def __init__(self, cache: Path = CACHE):
        self.cache = cache; self.cache.mkdir(parents=True, exist_ok=True); self._lock = threading.Lock(); self._spent = []

    def _pace(self):
        with self._lock:
            now = time.time(); self._spent = [t for t in self._spent if now - t < 60]
            while len(self._spent) * self.WEIGHT >= self.BUDGET_PER_MIN:
                time.sleep(0.5); now = time.time(); self._spent = [t for t in self._spent if now - t < 60]
            self._spent.append(time.time())

    def window(self, symbol: str, start_ms: int, end_ms: int) -> list:
        p = self.cache / symbol / f"{start_ms}_{end_ms}.json"
        if p.exists():
            return json.loads(p.read_text())
        url = f"{FAPI}/aggTrades?symbol={symbol}&startTime={start_ms}&endTime={end_ms}&limit=1000"
        for attempt in range(5):
            self._pace()
            try:
                with urlopen(Request(url, headers={"User-Agent": "futur-first-look"}), timeout=30) as r:
                    data = json.loads(r.read())
                out = [(int(t["T"]), float(t["p"])) for t in data]
                p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(out)); return out
            except HTTPError as e:
                if e.code in (418, 429):
                    time.sleep(30 * (attempt + 1)); continue
                if e.code == 400:
                    return []
                time.sleep(2 * (attempt + 1))
            except (URLError, TimeoutError, OSError):
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"aggTrades indisponible {symbol} {start_ms}")

    def first_at_or_after(self, symbol, ts):
        for span in (10_000, 60_000):
            w = [t for t in self.window(symbol, ts, ts + span) if t[0] >= ts]
            if w:
                return w[0]
        return None

    def last_at_or_before(self, symbol, ts):
        for span in (2_000, 15_000, 120_000):
            w = [t for t in self.window(symbol, ts - span, ts) if t[0] <= ts]
            if w and len(w) < 1000:
                return w[-1]
            if w and len(w) >= 1000:      # fenetre saturee : la derniere des 1000 n'est pas la derniere de la fenetre -> resserrer
                w2 = [t for t in self.window(symbol, ts - 500, ts) if t[0] <= ts]
                if w2:
                    return w2[-1]
        return None


class SynthTrades:
    """Trades synthetiques : marche aleatoire (1 trade / 250 ms, sigma par trade) + derive injectee apres t0.
    Sert au controle positif : la chaine doit retrouver un effet connu et rien sous le nul."""

    def __init__(self, effects: dict, sigma_bps_per_trade: float = 1.0, seed: int = 9):
        self.effects = effects; self.sigma = sigma_bps_per_trade; self.seed = seed; self._paths = {}

    def _path(self, symbol):
        if symbol not in self._paths:
            rng = random.Random(f"{self.seed}|{symbol}"); self._paths[symbol] = (rng, {})
        return self._paths[symbol]

    def _price(self, symbol, ts):
        rng, memo = self._path(symbol); k = ts // 250
        if k not in memo:
            base = 0.0 if not memo else memo[max(memo)]
            lo = max(memo) + 1 if memo else k
            for j in range(lo, k + 1):
                base += random.Random(f"{self.seed}|{symbol}|{j}").gauss(0, self.sigma) / 1e4; memo[j] = base
        lp = memo[k]; eff = self.effects.get(symbol)
        if eff:
            t0, sign, bps, hms = eff
            if ts >= t0:
                lp += sign * bps / 1e4 * min(1.0, (ts - t0) / hms)
        return 100.0 * math.exp(lp)

    def first_at_or_after(self, symbol, ts):
        t = (ts // 250 + 1) * 250; return (t, self._price(symbol, t))

    def last_at_or_before(self, symbol, ts):
        t = (ts // 250) * 250; return (t, self._price(symbol, t))


# ----------------------------------------------------------------------------- mesure
def measure(src, e: dict) -> dict:
    out = {"event_id": e["event_id"], "symbol": e["symbol"], "t0_ms": e["t0_ms"], "ret": {}}
    ent = src.first_at_or_after(e["symbol"], e["t0_ms"] + ENTRY_DELAY_MS)
    if ent is None:
        out["missing"] = "entry"; return out
    t_e, p_e = ent; out["entry_lag_ms"] = t_e - e["t0_ms"]; out["entry_price"] = p_e
    for h, ms in HORIZON_MS.items():
        x = src.last_at_or_before(e["symbol"], t_e + ms)
        if x is None or x[0] <= t_e:
            continue
        out["ret"][h] = 1e4 * math.log(x[1] / p_e)
    return out


def bucket_of(e: dict) -> dict:
    nb = next(lab for lo, hi, lab in NOTIONAL_BUCKETS if lo <= e["notional_usd"] < hi)
    imb = e.get("book_imbalance_before")
    return {"side": e["liquidated_position"], "notional": nb, "group": "BTC/ETH" if e["symbol"] in ("BTCUSDT", "ETHUSDT") else "alts",
            "spread": "<=0.1bps" if e["spread_before_bps"] <= 0.1 else ">0.1bps",
            "imbalance": "n/a" if imb is None else ("with_flow" if (imb * e["sign"]) > 0 else "against_flow")}


def evaluate(src, events: list, key: str, thr: float, workers: int = 4) -> dict:
    h = HYPOTHESES[key]; dsign = 1 if h["direction"] == "continuation" else -1
    evs = [e for e in events if e["in_" + key]]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        ms = list(ex.map(lambda e: measure(src, e), evs))
    per_h = {hz: ([], []) for hz in [h["primary"], *h["sensitivities"]]}
    missing = defaultdict(int); costs, rows, buckets = [], [], defaultdict(list); lags = []
    for e, m in zip(evs, ms):
        if m.get("missing"):
            missing[m["missing"]] += 1; continue
        lags.append(m["entry_lag_ms"])
        for hz, (xs, cl) in per_h.items():
            if hz in m["ret"]:
                xs.append(dsign * e["sign"] * m["ret"][hz]); cl.append(e["cluster"])
        if h["primary"] in m["ret"]:
            x = dsign * e["sign"] * m["ret"][h["primary"]]; costs.append(e["cost_bps"])
            rows.append({**{k: e[k] for k in ("event_id", "symbol", "event_ts", "liquidated_position", "notional_usd", "spread_before_bps", "book_imbalance_before", "cluster", "cost_bps")},
                         "entry_lag_ms": m["entry_lag_ms"], "x_bps": {hz: round(dsign * e["sign"] * v, 2) for hz, v in m["ret"].items()}})
            for bk, bv in bucket_of(e).items():
                buckets[f"{bk}={bv}"].append(x)
    stats = {hz: ER.cluster_stats(xs, cl) for hz, (xs, cl) in per_h.items()}
    prim = stats[h["primary"]]
    xs, cl = per_h[h["primary"]]
    S = defaultdict(float)
    for x, c in zip(xs, cl):
        S[c] += max(x, 0.0)
    prim["top_cluster_share"] = (max(S.values()) / sum(S.values())) if S and sum(S.values()) > 0 else 0.0
    prim["win_rate"] = prim.get("share_positive")
    cost = (sum(costs) / len(costs)) if costs else 2 * FEE_TAKER_BPS + SLIPPAGE_BPS
    return {"hypothesis": key, **h, "n_universe": len(evs), "missing": dict(missing), "n_measured": prim.get("n", 0), "stats_by_horizon": stats, "primary_stats": prim,
            "cost_rt_bps_mean": cost, "cost_wall_bps": COST_WALL_X * cost, "entry_lag_ms_median": (sorted(lags)[len(lags) // 2] if lags else None),
            "buckets_primary": {k: {"n": len(v), "mean_bps": sum(v) / len(v), "median_bps": sorted(v)[len(v) // 2]} for k, v in sorted(buckets.items())},
            "threshold_t": thr, "events": rows}


def verdict_for(prim: dict, cost: float, thr: float) -> tuple:
    if prim.get("n", 0) < MIN_N:
        return "INDECIDABLE", [f"N {prim.get('n', 0)} < {MIN_N}"]
    gross = prim["mean_bps"]
    if gross < MIN_GROSS_BPS:
        return "REJECTED_NO_GROSS", [f"gross {gross:.1f} < {MIN_GROSS_BPS}"]
    if gross < COST_WALL_X * cost:
        return "REJECTED_COST_WALL", [f"gross {gross:.1f} < {COST_WALL_X} x cost {cost:.1f}"]
    reasons = []
    if not (prim["t"] >= thr):
        reasons.append(f"t {prim['t']:.2f} < threshold {thr:.4f}")
    if prim["n_eff"] < MIN_N_EFF:
        reasons.append(f"n_eff {prim['n_eff']:.1f} < {MIN_N_EFF}")
    if prim["top1_share"] > MAX_TOP1_SHARE:
        reasons.append(f"top1_share {prim['top1_share']:.2f} > {MAX_TOP1_SHARE}")
    if prim["n_clusters"] < MIN_CLUSTERS:
        reasons.append(f"n_clusters {prim['n_clusters']} < {MIN_CLUSTERS}")
    if prim["top_cluster_share"] > MAX_TOP_CLUSTER_SHARE:
        reasons.append(f"top_cluster_share {prim['top_cluster_share']:.2f} > {MAX_TOP_CLUSTER_SHARE}")
    return ("INDECIDABLE", reasons) if reasons else ("FORWARD_SEAL_REQUIRED", ["all criteria met"])


# ----------------------------------------------------------------------------- scellement / budget
def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()


def check_seal() -> dict:
    if not FREEZE.exists():
        raise SystemExit("REFUS : pas de FREEZE")
    fz = json.loads(FREEZE.read_text())
    if sha(FROZEN) != fz["snapshot"]["sha256"]:
        raise SystemExit("REFUS : le snapshot gele ne correspond plus au sha256 scelle")
    if sha(UNIVERSE) != fz["universe"]["sha256"]:
        raise SystemExit("REFUS : l'univers ne correspond plus au sha256 scelle")
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
    entry = {"source": "__DEBIT__FORCED_LIQUIDATION_FIRST_LOOK", "provenance": f"regard unique pre-enregistre (n=1, 2 hypotheses famille liquidation), branche p3-forced-flow-first-look, ledger seq {seq}",
             "episodes": 0, "credited": -n, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "note": note}
    with open(BUDGET_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    st["budget_tests_remaining"] -= n; st["budget_raw_balance"] = st.get("budget_raw_balance", st["budget_tests_remaining"] + n) - n
    st["tests_consumed_lifetime"] = st.get("tests_consumed_lifetime", 0) + n; st.setdefault("budget_ledger", []).append(entry)
    LOOP_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


# ----------------------------------------------------------------------------- controle positif
def positive_control(n: int = 200, effect_bps: float = 40.0, seed: int = 9) -> dict:
    rng = random.Random(seed); base = int(datetime(2026, 9, 10, 12, tzinfo=timezone.utc).timestamp() * 1000)
    evs, eff = [], {}
    for i in range(n):
        t0 = base + rng.randrange(0, 8 * 3600_000); sym = f"SYN{i:04d}USDT"; pos = rng.choice(["long", "short"]); sign = -1 if pos == "long" else 1
        big = rng.random() < 0.4
        evs.append({"event_id": sym, "symbol": sym, "t0_ms": t0, "event_ts": datetime.fromtimestamp(t0 / 1000, tz=timezone.utc).isoformat(), "liquidated_position": pos, "sign": sign,
                    "notional_usd": 600e3 if big else 80e3, "spread_before_bps": 0.1, "book_imbalance_before": 0.0, "cluster": t0 // 300_000, "cost_bps": 14.1, "in_H1": True, "in_H2": big})
        eff[sym] = (t0 + ENTRY_DELAY_MS, sign, effect_bps, HORIZON_MS["30s"])          # continuation injectee sur 30 s (H1)
    thr = threshold_t(N_FAMILY_TESTS); res = {}
    for label, e in (("effect_injected", eff), ("null", {})):
        r = evaluate(SynthTrades(e), evs, "H1", thr, workers=4); p = r["primary_stats"]; v, why = verdict_for(p, r["cost_rt_bps_mean"], thr)
        res[label] = {"n": p["n"], "mean_bps": p["mean_bps"], "median_bps": p["median_bps"], "t": p["t"], "n_clusters": p["n_clusters"], "verdict": v, "reasons": why}
    rec = res["effect_injected"]["mean_bps"] - res["null"]["mean_bps"]; res["recovered_bps"] = rec; res["threshold_t"] = thr
    res["passed"] = 0.75 * effect_bps <= rec <= 1.15 * effect_bps and res["effect_injected"]["t"] >= thr and res["null"]["verdict"] != "FORWARD_SEAL_REQUIRED"
    return res


# ----------------------------------------------------------------------------- run
def run(workers: int = 4) -> dict:
    import look_ledger
    from research_kernel.multiplicity import MultiplicityLedger
    from research_kernel.mechanism_spec import MechanismSpec
    fz = check_seal(); u = json.loads(UNIVERSE.read_text()); events = u["events"]; thr = threshold_t(N_FAMILY_TESTS)
    cfg = [{"hypothesis": k, "mechanism_id": h["mechanism_id"], "direction": h["direction"], "min_notional_usd": h["min_notional_usd"], "primary": h["primary"],
            "n_universe": sum(1 for e in events if e["in_" + k]), "threshold_t": thr} for k, h in HYPOTHESES.items()]
    entry = look_ledger.record("confirm", (u["events"][0]["event_ts"], u["events"][-1]["event_ts"]), cfg, prereg=str(PREREG), require_witness=True,
                               note=f"forced-flow first look, frozen tape {fz['snapshot']['sha256'][:16]}, universe {fz['universe']['sha256'][:16]}, harness pinned")
    debit_budget(1, "regard unique forced-flow : H1 continuation (>= 50 k$), H2 exhaustion (>= 250 k$), famille liquidation n=2", entry["seq"])
    src = RestTrades(); out = {"ledger_seq": entry["seq"], "ledger_hash": entry["hash"], "threshold_t": thr, "family": FAMILY, "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hypotheses": {}}
    for k in HYPOTHESES:
        print(f"== {k} {HYPOTHESES[k]['name']}", flush=True)
        r = evaluate(src, events, k, thr, workers=workers); p = r["primary_stats"]; v, why = verdict_for(p, r["cost_rt_bps_mean"], thr)
        r["verdict"], r["reasons"] = v, why; out["hypotheses"][k] = r
        print(f"   n={r['n_measured']} clusters={p.get('n_clusters')} gross={p.get('mean_bps', float('nan')):.1f} med={p.get('median_bps', float('nan')):.1f} t={p.get('t', float('nan')):.2f} cost={r['cost_rt_bps_mean']:.1f} -> {v} {why}", flush=True)
    out["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RESULTS.mkdir(parents=True, exist_ok=True); (RESULTS / "first_look_results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    lines = ["# forced-flow first look — verdicts (un seul regard)", "", f"ledger seq {entry['seq']} · threshold_t({N_FAMILY_TESTS}) = {thr:.4f} · tape {fz['snapshot']['sha256'][:16]}", "",
             "| H | mechanism | n | clusters | gross | median | t | cost wall | verdict | reasons |", "|---|---|---|---|---|---|---|---|---|---|"]
    L = MultiplicityLedger()
    for k, r in out["hypotheses"].items():
        p = r["primary_stats"]
        lines.append(f"| {k} | {r['mechanism_id']} | {r['n_measured']} | {p.get('n_clusters', 0)} | {p.get('mean_bps', float('nan')):.1f} | {p.get('median_bps', float('nan')):.1f} | {p.get('t', float('nan')):.2f} | {r['cost_wall_bps']:.0f} | **{r['verdict']}** | {'; '.join(r['reasons'])} |")
        d = ROOT / "mechanisms" / r["mechanism_id"] / "results"; d.mkdir(parents=True, exist_ok=True)
        (d / "verdict.json").write_text(json.dumps({"mechanism_id": r["mechanism_id"], "status": r["verdict"], "reasons": r["reasons"], "primary": p, "ledger_seq": entry["seq"], "first_look": True}, indent=1, default=str))
        sp = MechanismSpec.from_json(ROOT / "mechanisms" / r["mechanism_id"] / "spec.json")
        L.record_trial(FAMILY, r["mechanism_id"], sp.rules_hash(), 1, recorded_at=out["started_utc"], note=f"sealed first look, LOOK_LEDGER seq {entry['seq']}")
    L.record_contamination(FAMILY, [[u["events"][0]["event_ts"][:10], u["events"][-1]["event_ts"][:10]]], f"forced-flow first look seq {entry['seq']} on the frozen liquidation tape")
    (RESULTS / "verdict.md").write_text("\n".join(lines) + "\n"); return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-universe", action="store_true"); ap.add_argument("--positive-control", action="store_true"); ap.add_argument("--run", action="store_true"); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.build_universe:
        u = build_universe(); print(json.dumps({k: v for k, v in u.items() if k != "events"}, indent=1, ensure_ascii=False))
    elif a.positive_control:
        r = positive_control(); RESULTS.mkdir(parents=True, exist_ok=True); (RESULTS / "positive_control.json").write_text(json.dumps(r, indent=1, default=str))
        print(json.dumps(r, indent=1, default=str)); sys.exit(0 if r["passed"] else 1)
    elif a.run:
        run(a.workers)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
