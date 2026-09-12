#!/usr/bin/env python3
"""
mexc_to_binance_migration_v1 / first_look.py -- MEXC_TO_BINANCE_V1 : le regard unique pre-enregistre.

    conditionnement (pre-annonce) -> population (pre-t0 + cout mesure) -> sceller -> controle positif -> regarder UNE fois

Une seule hypothese, direction fixee (short), une seule variable (pre_announcement_return_24h), une seule entree
(t0 + 15 min), un seul horizon primaire (6 h). Tout est dans reports/prereg/MEXC_TO_BINANCE_V1_PREREG.md.

La statistique primaire est un rang : Spearman(variable, rendement short brut 6 h) sur tous les evenements eligibles,
p-value par permutation (seedee) de la variable entre evenements. Elle conditionne sur le multiset des rendements et
reste donc invariante au niveau moyen que le regard seq 8 a deja revele. La porte economique (groupe HIGH : moyenne
nette >= 3 x cout moyen) est la revendication negociable ; elle ne porte pas le seuil de famille.

Modes :
  --build-conditioning  pre_announcement_return_24h depuis les tapes MEXC closes (pre-t0). AUCUN rendement lu.
  --build-population    l'entonnoir de la section 3 du prereg, listes HIGH/LOW figees. AUCUN rendement lu.
  --positive-control    prix synthetiques au regime du design (n, sigma) : effet retrouve, nul rejete, sur plusieurs seeds.
  --run                 le regard : refuse sans temoin orphelin pousse, si un pin differe (ce fichier compris), si le ledger
                        n'a pas le seq 9, si le budget n'a pas sa ligne de credit, si la periode est brulee sans override,
                        ou si un resultat existe ; LOOK_LEDGER + kernel + debit AVANT le premier prix.
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
import statistics
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from research_kernel.multiplicity import MultiplicityLedger, threshold_t  # noqa: E402
from data_lake.indices import pre_binance_features as PF  # noqa: E402
import importlib.util as _ilu  # noqa: E402
_s8 = _ilu.spec_from_file_location("perp_fade_first_look", ROOT / "mechanisms" / "event_listing_perp_fade_v1" / "first_look.py")
S8 = _ilu.module_from_spec(_s8); _s8.loader.exec_module(S8)     # VisionStore4, ER (series, cluster_stats, SyntheticStore)
ER = S8.ER

MECH = ROOT / "mechanisms" / "mexc_to_binance_migration_v1"
RESULTS = MECH / "results"
POPULATION = MECH / "population.json"
CONDITIONING = ROOT / "reports" / "data_acquisition" / "MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json"
PREREG = ROOT / "reports" / "prereg" / "MEXC_TO_BINANCE_V1_PREREG.md"
OVERRIDES = ROOT / "reports" / "prereg" / "MEXC_TO_BINANCE_V1_OVERRIDES.json"
WITNESS_BRANCH = "prereg/mexc-to-binance-v1"
EXCHANGE_INFO = ROOT / "data_lake" / "first_look" / "event_listing_perp_fade_v1" / "exchangeInfo.json"
FUNDING_ROOT = ROOT / "data" / "vision_backfill" / "um" / "fundingRate"
LOOK_LEDGER = ROOT / "reports" / "loop" / "LOOK_LEDGER.jsonl"
BUDGET_LEDGER = ROOT / "reports" / "loop" / "BUDGET_LEDGER.jsonl"
LOOP_STATE = ROOT / "reports" / "loop" / "LOOP_STATE.json"
KERNEL_LEDGER = ROOT / "reports" / "research_kernel" / "multiplicity_ledger.json"
MECHANISM_ID = "mexc_to_binance_migration_effect_v1"
SEQ9_HASH = "033b2ab63d900eea14a9c89f8fc84189f0a96fc29e49fdbe10f2ad6498a227ec"     # regard seq 9 (P3B) : la chaine doit le porter
SEQ9_DEBIT = "__DEBIT__FORCED_LIQUIDATION_FIRST_LOOK"

FAMILY = "news"; N_FAMILY_TESTS = 6                      # sixieme hypothese scellee ; verifie contre le ledger du noyau au regard
ALPHA_FAMILY = 0.05 / N_FAMILY_TESTS                     # 0.008333 unilateral
SIDE = -1                                                # short
ENTRY_OFFSET_MIN = 15; PRIMARY = "6h"; SENSITIVITIES = ["60m", "24h"]
HORIZON_MIN = {"60m": 60, "6h": 360, "24h": 1440}
BAR_TOLERANCE_MIN = 5
WINDOW_MIN = 15; NOTIONAL_USD = 500; FEE_PER_SIDE_BPS = 5.0      # decision P13 : official VIP0, remise BNB non appliquee
HIGH_THRESHOLD = 0.20                                    # HIGH : pre_announcement_return_24h >= +20 % (absolu, fige)
MIN_CLOSES_24H = 20
MIN_N = 30; MIN_N_HIGH = 15; N_EFF_SHARE_MIN = 0.45; MAX_TOP1_SHARE = 0.20; MIN_GROSS_MEDIAN_BPS = 30.0; COST_WALL_X = 3.0
N_PERMUTATIONS = 20_000; PERM_SEED = 20260912
PLACEBO_VETO_SHARE = 0.50; PLACEBO_MIN_N = 30; PLACEBO_MIN_T = 1.0; PLACEBO_T0_SHIFT_DAYS = (7, 30); FAKE_SYMBOL_MIN_AGE_DAYS = 90
WINSOR_BPS = 1000.0
N_FORWARD_MIN = 60; FORWARD_LATEST_LOOK = "2028-09-12"
VERDICTS = ("REJECTED", "INDECIDABLE", "CANDIDATE_ALPHA_REQUIRES_FORWARD")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def pins() -> dict:
    m = re.search(r"```json pins\s*(\{.*?\})\s*```", PREREG.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit("REFUS : pas de bloc pins dans le prereg")
    return json.loads(m.group(1))


def check_pins(require_harness: bool = True) -> dict:
    p = pins()
    for name, pin in p.items():
        path = ROOT / pin["path"]
        if name == "harness":
            if require_harness and sha(Path(__file__)) != pin["sha256"]:
                raise SystemExit("REFUS : ce harnais n'est pas celui que le prereg pinne")
            continue
        if not path.exists():
            if pin.get("optional"):
                continue
            raise SystemExit("REFUS : %s absent" % pin["path"])
        if sha(path) != pin["sha256"]:
            raise SystemExit("REFUS : %s a change depuis le scellement" % pin["path"])
    if (RESULTS / "first_look_results.json").exists():
        raise SystemExit("REFUS : le regard a deja eu lieu. Un seul regard.")
    return p


# ----------------------------------------------------------------------------- conditionnement (pre-annonce, pre-t0)
def build_conditioning() -> dict:
    """pre_announcement_return_24h pour chaque evenement MEXC_FIRST, depuis les tapes MEXC closes <= t0 et coupees a la
    derniere heure complete avant l'annonce. Fonction des fichiers locaux pre-t0 ; aucun rendement Binance."""
    from data_lake.collectors import venue_pre_binance_paths as VP
    from data_lake.collectors.mexc_pre_binance_tape import mexc_first_events
    from data_lake.collectors.pre_binance_venue_tape import load_candles
    uni = {e["event_id"]: e for e in json.loads((ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json").read_text())["events"]}
    rows = []
    for ev in mexc_first_events():
        mp = VP.manifest_path("mexc", ev["event_id"]); man = json.loads(mp.read_text()) if mp.exists() else {"files": {}}
        hourly = load_candles(((man.get("files") or {}).get("60m") or {}).get("path"))
        t0 = int(VP.parse_ts(ev["t0"]).timestamp() * 1000); pub = (uni.get(ev["event_id"]) or {}).get("publication_ts_ms")
        try:
            r = PF.pre_announcement_return(hourly, pub, t0, 24, MIN_CLOSES_24H) if hourly else {"pre_announcement_return_24h": None, "hours_in_window": 0, "window_end_ms": None, "post_announcement_hours_dropped": 0, "announcement_cut": bool(pub)}
        except PF.PostT0Leak as e:
            r = {"pre_announcement_return_24h": None, "hours_in_window": 0, "window_end_ms": None, "post_announcement_hours_dropped": 0, "announcement_cut": bool(pub), "rejected": str(e)}
        rows.append({"event_id": ev["event_id"], "asset": ev["asset"], "binance_symbol": ev["binance_symbol"], "mexc_market": man.get("market") or ev.get("mexc_market"), "t0": ev["t0"],
                     "publication_ts_ms": pub, **r})
    n_ok = sum(1 for r in rows if r["pre_announcement_return_24h"] is not None)
    return {"variable": "pre_announcement_return_24h", "definition": "MEXC close of the last complete hour <= floor_hour(publication_ts) over the close 24 h earlier; >= %d complete hourly closes required; strictly before the announcement and before t0" % MIN_CLOSES_24H,
            "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "n": len(rows), "n_defined": n_ok, "rows": rows, "no_return_computed": True, "no_alpha_test": True}


# ----------------------------------------------------------------------------- population (pre-t0 + cout)
def event_cost(cap_event: dict, median_slippage_rt: float | None = None, window_min: int = WINDOW_MIN, notional: int = NOTIONAL_USD):
    """Cout aller-retour par evenement : 2 x frais + spread effectif + glissement vente + achat a la fenetre nommee.
    Sans instantane de carnet (lacune Vision, pas illiquidite) : glissement impute a la mediane de population, marque."""
    w = next((w for w in cap_event.get("windows", []) if w.get("window_min") == window_min), None)
    if not w or w.get("effective_spread_bps") is None:
        return None, None
    sp = float(w["effective_spread_bps"]); ss, bs = w.get("sell_slippage_%d_usd_bps" % notional), w.get("buy_slippage_%d_usd_bps" % notional)
    if ss is not None and bs is not None:
        return round(2 * FEE_PER_SIDE_BPS + sp + float(ss) + float(bs), 3), False
    if median_slippage_rt is not None:
        return round(2 * FEE_PER_SIDE_BPS + sp + median_slippage_rt, 3), True
    return None, None


def build_population(p: dict | None = None, min_t0_ms: int | None = None) -> dict:
    """L'entonnoir de la section 3, dans l'ordre, sans option. Fonction pure des fichiers pinnes ; aucun rendement."""
    p = p or pins()
    uni = {e["event_id"]: e for e in json.loads((ROOT / p["universe"]["path"]).read_text())["events"]}
    cond = {r["event_id"]: r for r in json.loads((ROOT / p["conditioning"]["path"]).read_text())["rows"]}
    causal = {r["event_id"]: r for r in json.loads((ROOT / p["causal_matrix"]["path"]).read_text())["rows"]}
    cap = {e["event_id"]: e for e in json.loads((ROOT / p["capacity"]["path"]).read_text())["events"]}
    wash = {r["event_id"]: r for r in json.loads((ROOT / p["wash"]["path"]).read_text())["rows"]}
    funnel = []
    s0 = [e for e, r in causal.items() if r.get("population") == "MEXC_FIRST" and (min_t0_ms is None or uni[e]["tradable_start_ms"] > min_t0_ms)]; funnel.append(("MEXC_FIRST", len(s0)))
    s1 = [e for e in s0 if causal[e].get("class") != "BAD_TIMESTAMP"]; funnel.append(("not BAD_TIMESTAMP (announced vs first bar > 15 min)", len(s1)))
    s2 = [e for e in s1 if (cond.get(e) or {}).get("pre_announcement_return_24h") is not None]; funnel.append(("pre_announcement_return_24h defined (>= %d complete closes)" % MIN_CLOSES_24H, len(s2)))
    measured = {e: event_cost(cap.get(e, {}))[0] for e in s2}
    med_slip = None
    slips = []
    for e in s2:
        w = next((w for w in cap.get(e, {}).get("windows", []) if w.get("window_min") == WINDOW_MIN), None)
        if w and w.get("sell_slippage_%d_usd_bps" % NOTIONAL_USD) is not None and w.get("buy_slippage_%d_usd_bps" % NOTIONAL_USD) is not None:
            slips.append(float(w["sell_slippage_%d_usd_bps" % NOTIONAL_USD]) + float(w["buy_slippage_%d_usd_bps" % NOTIONAL_USD]))
    med_slip = statistics.median(slips) if slips else None
    cost = {e: event_cost(cap.get(e, {}), med_slip) for e in s2}
    s3 = [e for e in s2 if cost[e][0] is not None]; funnel.append(("effective spread measured at +%dm (slippage imputed when the book snapshot is missing)" % WINDOW_MIN, len(s3)))
    rows = []
    for e in sorted(s3):
        u = uni[e]; x = float(cond[e]["pre_announcement_return_24h"])
        rows.append({"event_id": e, "asset": u["asset"], "symbol": u["symbol"], "tradable_start_ts": u["tradable_start_ts"], "tradable_start_ms": u["tradable_start_ms"],
                     "publication_ts_ms": u.get("publication_ts_ms"), "pre_announcement_return_24h": x, "hours_in_window": cond[e]["hours_in_window"], "group": "HIGH" if x >= HIGH_THRESHOLD else "LOW",
                     "mexc_market": cond[e].get("mexc_market"), "cost_rt_bps": cost[e][0], "cost_slippage_imputed": cost[e][1], "lead_time_days": causal[e].get("lead_time_days"),
                     "wash_volume_suspect": wash.get(e, {}).get("wash_volume_suspect"), "programme_like": wash.get(e, {}).get("programme_like")})
    lead = [r["lead_time_days"] for r in rows if r["lead_time_days"] is not None]; xs = [r["pre_announcement_return_24h"] for r in rows]
    high = sorted(r["event_id"] for r in rows if r["group"] == "HIGH"); low = sorted(r["event_id"] for r in rows if r["group"] == "LOW")
    return {"mechanism_id": MECHANISM_ID, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "t0_definition": "tradable_start_ts of the pinned universe (open_time of the first 1-min Vision bar)",
            "funnel": funnel, "n": len(rows), "high_threshold": HIGH_THRESHOLD, "n_high": len(high), "n_low": len(low), "high_event_ids": high, "low_event_ids": low,
            "high_sha256": hashlib.sha256(json.dumps(high).encode()).hexdigest(), "low_sha256": hashlib.sha256(json.dumps(low).encode()).hexdigest(),
            "median_return": statistics.median(xs) if xs else None, "mean_cost_rt_bps": statistics.mean(r["cost_rt_bps"] for r in rows) if rows else None,
            "mean_cost_rt_bps_high": statistics.mean(r["cost_rt_bps"] for r in rows if r["group"] == "HIGH") if high else None, "median_slippage_rt_bps_imputed": med_slip,
            "n_cost_imputed": sum(1 for r in rows if r["cost_slippage_imputed"]), "market_split": {"spot": sum(1 for r in rows if r["mexc_market"] == "spot"), "perp": sum(1 for r in rows if r["mexc_market"] == "perp")},
            "median_lead_time_days": statistics.median(lead) if lead else None, "n_launch_days": len({r["tradable_start_ts"][:10] for r in rows}),
            "pre_t0_checks": {"spearman_x_vs_cost": _spearman(xs, [r["cost_rt_bps"] for r in rows]), "spearman_x_vs_lead": _spearman([r["pre_announcement_return_24h"] for r in rows if r["lead_time_days"] is not None], lead)},
            "events": rows, "no_return_computed": True, "no_alpha_test": True}


# ----------------------------------------------------------------------------- mesure (au regard seulement)
def funding_bps_for_short(symbol: str, entry_ms: int, exit_ms: int):
    """Financement encaisse par un short entre l'entree (exclue) et la sortie (incluse), depuis les archives mensuelles
    Vision fundingRate (P6). Un taux positif est recu par le short. None si l'archive manque (compte, jamais devine)."""
    months = sorted({datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m") for ms in (entry_ms, exit_ms)})
    total, found = 0.0, False
    for m in months:
        p = FUNDING_ROOT / symbol / ("%s-fundingRate-%s.zip" % (symbol, m))
        if not p.exists():
            continue
        found = True
        with zipfile.ZipFile(p) as z:
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                t = int(row[0]); t = t // 1000 if t > 10**14 else t
                if entry_ms < t <= exit_ms:
                    total += float(row[2]) * 1e4
    return round(total, 4) if found else None


def measure(store, ev: dict, shift_days: int = 0, symbol: str | None = None, with_funding: bool = True) -> dict:
    start = ev["tradable_start_ms"] + shift_days * 86_400_000; sym = symbol or ev["symbol"]
    t_entry = start + ENTRY_OFFSET_MIN * 60_000; hmax = max(HORIZON_MIN.values())
    days = ER._days_between(start - 3600_000, start + hmax * 60_000 + 3600_000)
    s = ER.series(store, "um", sym, days)
    out = {"event_id": ev["event_id"], "symbol": sym, "group": ev.get("group"), "raw": {}, "funding_bps": None}
    def bar(series, ms):
        m0 = -(-ms // 60_000) * 60_000
        for k in range(BAR_TOLERANCE_MIN + 1):
            v = series.get(m0 + k * 60_000)
            if v:
                return m0 + k * 60_000, v
        return None, None
    e_ot, e_v = bar(s, t_entry)
    if e_v is None:
        out["missing"] = "entry_bar"; return out
    out["entry_lag_s"] = (e_ot - start) / 1000
    for h, mins in HORIZON_MIN.items():
        x_ot, x_v = bar(s, start + mins * 60_000)
        if x_v is None:
            if h == PRIMARY:
                out["missing"] = "exit_bar"; return out                          # sortie manquante = exclu ET compte
            continue
        out["raw"][h] = 1e4 * math.log(x_v[0] / e_v[0])
        if h == PRIMARY:
            out["exit_open_ms"] = x_ot
    if with_funding and shift_days == 0 and symbol is None:
        out["funding_bps"] = funding_bps_for_short(sym, e_ot, out["exit_open_ms"])
    win = {ot: v for ot, v in s.items() if e_ot <= ot < start + HORIZON_MIN[PRIMARY] * 60_000}
    if win:
        out["quote_volume_window_usd"] = sum(v[2] for v in win.values())
    return out


def _stats(rows: list, key: str, h: str, cost: dict | None = None, funding: bool = False) -> dict:
    xs, cl = [], []
    for m in rows:
        if h in m[key]:
            x = SIDE * m[key][h]
            if cost is not None:
                x -= cost.get(m["event_id"], 0.0)
                if funding and m.get("funding_bps") is not None:
                    x += m["funding_bps"]
            xs.append(x); cl.append(m["tradable_start_ts"][:10])
    st = ER.cluster_stats(xs, cl)
    if xs:
        w = [max(-WINSOR_BPS, min(WINSOR_BPS, x)) for x in xs]; st["winsorised_mean_bps"] = sum(w) / len(w)
        if len(xs) > 2:
            big = max(range(len(xs)), key=lambda i: xs[i]); rest = [x for i, x in enumerate(xs) if i != big]
            st["mean_without_largest_bps"] = sum(rest) / len(rest)
    return st


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def _spearman(x, y):
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y); mx, my = statistics.mean(rx), statistics.mean(ry)
    sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sxx * syy) if sxx and syy else None


def permutation_test(x: list, y: list, n_perm: int = N_PERMUTATIONS, seed: int = PERM_SEED) -> dict:
    """Spearman(x, y) observe et p-value unilaterale (rho > 0) par permutation seedee de x entre evenements.
    Conditionne sur le multiset de y : invariant au niveau moyen deja vu au regard seq 8."""
    rho = _spearman(x, y)
    if rho is None:
        return {"rho": None, "p_one_sided": None, "n": len(x), "n_perm": 0}
    ry = _ranks(y); rx = _ranks(x); n = len(x); my = statistics.mean(ry); syy = sum((b - my) ** 2 for b in ry); mx = statistics.mean(rx); sxx = sum((a - mx) ** 2 for a in rx)
    rng = random.Random(seed); ge = 0; perm = list(rx)
    for _ in range(n_perm):
        rng.shuffle(perm)
        r = sum((a - mx) * (b - my) for a, b in zip(perm, ry)) / math.sqrt(sxx * syy)
        if r >= rho:
            ge += 1
    return {"rho": rho, "p_one_sided": (ge + 1) / (n_perm + 1), "n": n, "n_perm": n_perm, "seed": seed, "rho_bar_50pct_power": round(2.394 / math.sqrt(n - 1), 4), "rho_bar_80pct_power": round((2.394 + 0.842) / math.sqrt(n - 1), 4)}


def evaluate(store, pop: dict, workers: int = 8, n_perm: int = N_PERMUTATIONS) -> dict:
    events = pop["events"]; cost = {e["event_id"]: e["cost_rt_bps"] for e in events}; by_id = {e["event_id"]: e for e in events}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        ms = list(ex.map(lambda ev: measure(store, ev), events))
    for m in ms:
        m["tradable_start_ts"] = by_id[m["event_id"]]["tradable_start_ts"]
    ok = [m for m in ms if not m.get("missing")]; missing = defaultdict(int)
    for m in ms:
        if m.get("missing"):
            missing[m["missing"]] += 1
    high = [m for m in ok if m["group"] == "HIGH"]; low = [m for m in ok if m["group"] == "LOW"]
    x = [by_id[m["event_id"]]["pre_announcement_return_24h"] for m in ok]; g = [SIDE * m["raw"][PRIMARY] for m in ok]
    out = {"n_measured": len(ok), "n_high_measured": len(high), "missing": dict(missing), "funding_missing": sum(1 for m in ok if m.get("funding_bps") is None),
           "primary": {"statistic": "spearman(pre_announcement_return_24h, short gross raw %s return) on all eligible, one-sided rho > 0, permutation p" % PRIMARY, **permutation_test(x, g, n_perm)},
           "economic_gate": {"group": "HIGH (>= %.2f)" % HIGH_THRESHOLD, "gross": _stats(high, "raw", PRIMARY), "net": _stats(high, "raw", PRIMARY, cost, funding=True),
                             "net_without_funding": _stats(high, "raw", PRIMARY, cost, funding=False), "mean_cost_rt_bps": statistics.mean(cost[m["event_id"]] for m in high) if high else None,
                             "n_clusters_launch_days": len({m["tradable_start_ts"][:10] for m in high})},
           "complement_LOW": {"gross": _stats(low, "raw", PRIMARY), "net": _stats(low, "raw", PRIMARY, cost, funding=True)},
           "sensitivities": {"horizon": {"HIGH_raw_60m": _stats(high, "raw", "60m"), "HIGH_raw_24h": _stats(high, "raw", "24h")},
                             "robustness": {"HIGH_ex_wash_flags_net": _stats([m for m in high if not (by_id[m["event_id"]].get("wash_volume_suspect") or by_id[m["event_id"]].get("programme_like"))], "raw", PRIMARY, cost, funding=True),
                                            "HIGH_ex_cost_imputed_net": _stats([m for m in high if not by_id[m["event_id"]].get("cost_slippage_imputed")], "raw", PRIMARY, cost, funding=True)}}}
    lead = [(by_id[m["event_id"]]["lead_time_days"], SIDE * m["raw"][PRIMARY]) for m in ok if by_id[m["event_id"]].get("lead_time_days") is not None]
    out["lead_time_placebo"] = {"spearman_lead_vs_short_gross": _spearman([l[0] for l in lead], [l[1] for l in lead]), "n": len(lead), "reported_only": True}
    out["capacity_median_quote_volume_6h_usd_high"] = statistics.median([m["quote_volume_window_usd"] for m in high if "quote_volume_window_usd" in m]) if any("quote_volume_window_usd" in m for m in high) else None
    out["per_event"] = ms
    return out


# ----------------------------------------------------------------------------- placebos
def fake_symbols(pop: dict, exchange_info: dict) -> dict:
    """Pour chaque evenement HIGH : un perp USDT cote >= 90 j avant t0 (onboardDate), candidats tries par symbole,
    index = int(sha256(event_id), 16) mod len. Survivants du snapshot seulement : biais divulgue."""
    perps = [(s["symbol"], int(s.get("onboardDate") or 0)) for s in exchange_info.get("symbols", []) if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"]
    out = {}
    for ev in pop["events"]:
        if ev["group"] != "HIGH":
            continue
        cands = sorted(sym for sym, od in perps if od and od <= ev["tradable_start_ms"] - FAKE_SYMBOL_MIN_AGE_DAYS * 86_400_000 and sym != ev["symbol"])
        if cands:
            out[ev["event_id"]] = cands[int(hashlib.sha256(ev["event_id"].encode()).hexdigest(), 16) % len(cands)]
    return out


def _placebo_block(ms: list, high: list) -> dict:
    for m, ev in zip(ms, high):
        m["tradable_start_ts"] = ev["tradable_start_ts"]
    ok = [m for m in ms if not m.get("missing")]
    return {"n": len(ok), "missing": len(ms) - len(ok), "gross": _stats(ok, "raw", PRIMARY)}


def placebos(store, pop: dict, workers: int = 8) -> dict:
    high = [e for e in pop["events"] if e["group"] == "HIGH"]; out = {}
    for d in PLACEBO_T0_SHIFT_DAYS:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            ms = list(ex.map(lambda ev: measure(store, ev, shift_days=d, with_funding=False), high))
        out["post_listing_drift_t0_plus_%dd" % d] = {**_placebo_block(ms, high), "veto_eligible": d == PLACEBO_T0_SHIFT_DAYS[0]}
    fs = fake_symbols(pop, json.loads(EXCHANGE_INFO.read_text())) if EXCHANGE_INFO.exists() else {}
    if fs:
        sel = [e for e in high if e["event_id"] in fs]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            ms2 = list(ex.map(lambda ev: measure(store, ev, symbol=fs[ev["event_id"]], with_funding=False), sel))
        out["fake_symbol"] = {**_placebo_block(ms2, sel), "drawn": len(fs), "veto_eligible": True}
    else:
        out["fake_symbol"] = {"n": 0, "status": "NOT_COMPUTABLE: exchangeInfo snapshot absent", "veto_eligible": True}
    return out


def other_venue_first_control(store, workers: int = 8) -> dict:
    """Les evenements OKX/Bybit/KuCoin-first avec LEUR rendement pre-annonce 24 h (tapes de controle P12) : meme
    statistique, rapporte seulement (n trop petit pour un veto)."""
    from data_lake.collectors import venue_pre_binance_paths as VP
    from data_lake.collectors.pre_binance_venue_tape import load_candles, other_venue_first_events
    uni = {e["event_id"]: e for e in json.loads((ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json").read_text())["events"]}
    evs = []
    for ev in other_venue_first_events():
        if ev["venue"] == "mexc":
            continue
        mp = VP.manifest_path(ev["venue"], ev["event_id"])
        if not mp.exists():
            continue
        man = json.loads(mp.read_text()); hourly = load_candles(((man.get("files") or {}).get("60m") or {}).get("path"))
        u = uni.get(ev["event_id"]); t0 = int(VP.parse_ts(ev["t0"]).timestamp() * 1000)
        if not u or not hourly:
            continue
        try:
            r = PF.pre_announcement_return(hourly, u.get("publication_ts_ms"), t0, 24, MIN_CLOSES_24H)
        except PF.PostT0Leak:
            continue
        if r["pre_announcement_return_24h"] is None:
            continue
        evs.append({"event_id": ev["event_id"], "asset": ev["asset"], "symbol": u["symbol"], "tradable_start_ts": u["tradable_start_ts"], "tradable_start_ms": u["tradable_start_ms"],
                    "pre_announcement_return_24h": r["pre_announcement_return_24h"], "group": "HIGH" if r["pre_announcement_return_24h"] >= HIGH_THRESHOLD else "LOW", "venue": ev["venue"]})
    if not evs:
        return {"n": 0, "status": "NOT_COMPUTABLE"}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        ms = list(ex.map(lambda ev: measure(store, ev, with_funding=False), evs))
    by = {e["event_id"]: e for e in evs}
    for m in ms:
        m["tradable_start_ts"] = by[m["event_id"]]["tradable_start_ts"]
    ok = [m for m in ms if not m.get("missing")]
    return {"n": len(ok), "n_high": sum(1 for m in ok if m["group"] == "HIGH"), "spearman": _spearman([by[m["event_id"]]["pre_announcement_return_24h"] for m in ok], [SIDE * m["raw"][PRIMARY] for m in ok]),
            "HIGH_gross": _stats([m for m in ok if m["group"] == "HIGH"], "raw", PRIMARY), "reported_only": True}


# ----------------------------------------------------------------------------- verdict (ordre fixe, trois issues)
def verdict_for(res: dict, placebo: dict, alpha: float = ALPHA_FAMILY) -> tuple:
    prim, gate = res["primary"], res["economic_gate"]; g, nt = gate["gross"], gate["net"]
    if res["n_measured"] < MIN_N:
        return "INDECIDABLE", ["n measured %d < %d" % (res["n_measured"], MIN_N)]
    if prim["rho"] is None or prim["rho"] <= 0:
        return "REJECTED", ["spearman rho %s <= 0: the pre-announcement run-up does not order the fade" % (None if prim["rho"] is None else round(prim["rho"], 4))]
    if prim["p_one_sided"] > alpha:
        return "INDECIDABLE", ["permutation p %.4f > alpha/%d = %.4f (rho %.3f)" % (prim["p_one_sided"], N_FAMILY_TESTS, alpha, prim["rho"])]
    if g.get("n", 0) < MIN_N_HIGH:
        return "INDECIDABLE", ["n(HIGH) %d < %d" % (g.get("n", 0), MIN_N_HIGH)]
    if g["median_bps"] < MIN_GROSS_MEDIAN_BPS:
        return "REJECTED", ["HIGH median gross %.1f < %.0f bps" % (g["median_bps"], MIN_GROSS_MEDIAN_BPS)]
    if nt["mean_bps"] < COST_WALL_X * gate["mean_cost_rt_bps"]:
        return "REJECTED", ["HIGH mean net %.1f < %.0f x mean cost %.1f" % (nt["mean_bps"], COST_WALL_X, gate["mean_cost_rt_bps"])]
    reasons = []
    if nt["n_eff"] < N_EFF_SHARE_MIN * nt["n"]:
        reasons.append("n_eff %.1f < %.2f x n(HIGH) %d" % (nt["n_eff"], N_EFF_SHARE_MIN, nt["n"]))
    if nt["top1_share"] > MAX_TOP1_SHARE:
        reasons.append("top1_share %.2f > %.2f (positive contributions, net series)" % (nt["top1_share"], MAX_TOP1_SHARE))
    if nt.get("mean_without_largest_bps", 0.0) <= 0:
        reasons.append("HIGH mean net without its largest event %.1f <= 0" % nt.get("mean_without_largest_bps", 0.0))
    for k, v in placebo.items():
        if not (isinstance(v, dict) and v.get("veto_eligible")):
            continue
        pg = v.get("gross") or {}
        if pg.get("n", 0) >= PLACEBO_MIN_N and pg.get("t", 0) >= PLACEBO_MIN_T and g["mean_bps"] > 0 and pg["mean_bps"] >= PLACEBO_VETO_SHARE * g["mean_bps"]:
            reasons.append("placebo %s gross %.1f (t %.2f, n %d) >= %.0f %% of HIGH gross %.1f" % (k, pg["mean_bps"], pg["t"], pg["n"], 100 * PLACEBO_VETO_SHARE, g["mean_bps"]))
        elif v.get("status", "").startswith("NOT_COMPUTABLE"):
            reasons.append("placebo %s not computable" % k)
    return ("INDECIDABLE", reasons) if reasons else ("CANDIDATE_ALPHA_REQUIRES_FORWARD", ["all criteria met; forward-only collection, no position"])


# ----------------------------------------------------------------------------- controle positif (synthetique, au regime du design)
class SynthStore(ER.SyntheticStore):
    def bars(self, market, symbol, day):
        b = super().bars(market, symbol, day)
        return {ot: (v[0], v[1], v[1] * 5000.0, 20.0) for ot, v in b.items()}


def positive_control(n: int = 84, sigma_6h_bps: float = 1546.0, seeds: tuple = (1, 2, 3, 4, 5, 6, 7, 8), n_perm: int = 2000) -> dict:
    """Au regime declare (n, sigma 6 h) : un effet a 80 % de puissance (rho ~ 0.33, obtenu par une pente sur la variable)
    doit donner CANDIDATE sur la plupart des seeds ; le nul ne doit jamais le donner."""
    sigma_min = sigma_6h_bps / math.sqrt(HORIZON_MIN[PRIMARY]); out = {"seeds": [], "n": n, "sigma_6h_bps": sigma_6h_bps}
    for seed in seeds:
        rng = random.Random(seed); base = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp() * 1000); evs = []
        for i in range(n):
            start = base + rng.randrange(0, 200 * 86400_000); start -= start % 60_000; a = "SYN%04d" % i; x = max(-0.5, rng.gauss(0.25, 0.4))
            evs.append({"event_id": a, "asset": a, "symbol": a + "USDT", "tradable_start_ts": _iso(start), "tradable_start_ms": start, "pre_announcement_return_24h": x, "group": "HIGH" if x >= HIGH_THRESHOLD else "LOW", "cost_rt_bps": 26.0, "lead_time_days": rng.uniform(1, 30)})
        pop = {"events": evs}; rec = {"seed": seed}
        for label, slope in (("effect", 1400.0), ("null", 0.0)):
            eff = {e["symbol"]: (e["tradable_start_ms"] + ENTRY_OFFSET_MIN * 60_000, SIDE, slope * max(0.0, e["pre_announcement_return_24h"]), HORIZON_MIN[PRIMARY]) for e in evs} if slope else {}
            st = SynthStore(eff, sigma_bps=sigma_min, seed=seed); r = evaluate(st, pop, workers=4, n_perm=n_perm)
            v, why = verdict_for(r, {"fake_symbol": {"n": 0, "veto_eligible": False}})
            rec[label] = {"rho": r["primary"]["rho"], "p": r["primary"]["p_one_sided"], "high_n": r["economic_gate"]["gross"]["n"], "high_gross_mean": r["economic_gate"]["gross"]["mean_bps"], "verdict": v, "reasons": why}
        out["seeds"].append(rec)
    eff_c = sum(1 for s in out["seeds"] if s["effect"]["verdict"] == "CANDIDATE_ALPHA_REQUIRES_FORWARD"); null_c = sum(1 for s in out["seeds"] if s["null"]["verdict"] == "CANDIDATE_ALPHA_REQUIRES_FORWARD")
    out["effect_candidate_share"] = eff_c / len(seeds); out["null_candidate_share"] = null_c / len(seeds)
    out["passed"] = bool(out["effect_candidate_share"] >= 0.6 and null_c == 0)
    return out


# ----------------------------------------------------------------------------- ledgers, budget, run
def check_ledgers(pop_window: tuple, mode: str) -> dict:
    """Les preconditions du regard, dans l'ordre : chaine avec seq 9, debit seq 9 present, credit nomme, solde coherent,
    periode brulee -> override enregistre ou mode forward."""
    entries = [json.loads(l) for l in LOOK_LEDGER.read_text().splitlines() if l.strip()]
    if not any(e.get("hash") == SEQ9_HASH for e in entries):
        raise SystemExit("REFUS : le LOOK_LEDGER ne porte pas le regard seq 9 (%s…) : merger PR #10 d'abord" % SEQ9_HASH[:8])
    budget = [json.loads(l) for l in BUDGET_LEDGER.read_text().splitlines() if l.strip()]
    if not any(b.get("source") == SEQ9_DEBIT for b in budget):
        raise SystemExit("REFUS : le BUDGET_LEDGER ne porte pas le debit du seq 9")
    credit = [b for b in budget if b.get("credited", 0) > 0 and "MEXC_TO_BINANCE_V1" in json.dumps(b)]
    if not credit:
        raise SystemExit("REFUS : aucune ligne de credit nommant MEXC_TO_BINANCE_V1 dans le BUDGET_LEDGER (decision utilisateur, hors regle des episodes)")
    st = json.loads(LOOP_STATE.read_text()); bal = sum(b.get("credited", 0) for b in budget)
    if bal != st.get("budget_tests_remaining") or bal < 1:
        raise SystemExit("REFUS : solde ledger %s != LOOP_STATE %s ou < 1" % (bal, st.get("budget_tests_remaining")))
    ml = MultiplicityLedger(KERNEL_LEDGER); burned = ml.is_burned(FAMILY, pop_window[0][:10], pop_window[1][:10])
    ov = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    if burned and mode == "historical" and not ov.get("burn_override"):
        raise SystemExit("REFUS : periode %s brulee pour la famille %s (kernel ledger) et aucun override enregistre : regard historique interdit ; mode forward seulement" % (burned, FAMILY))
    thr_ledger = ml.current_threshold(FAMILY, extra=1)
    if abs(thr_ledger - threshold_t(N_FAMILY_TESTS)) > 1e-6:
        raise SystemExit("REFUS : le ledger du noyau donne threshold %.4f pour la famille %s (+1), le prereg dit %.4f" % (thr_ledger, FAMILY, threshold_t(N_FAMILY_TESTS)))
    return {"seq9": True, "credit": credit[-1], "balance": bal, "burned": burned, "override": ov, "threshold_from_kernel": thr_ledger}


def debit_budget(n: int, note: str, seq: int):
    st = json.loads(LOOP_STATE.read_text())
    if st.get("budget_tests_remaining", 0) < n:
        raise SystemExit("REFUS : budget %s < %d" % (st.get("budget_tests_remaining"), n))
    entry = {"source": "__DEBIT__MEXC_TO_BINANCE_V1", "provenance": "regard unique pre-enregistre (n=1), prereg orphelin %s, ledger seq %d" % (WITNESS_BRANCH, seq),
             "episodes": 0, "credited": -n, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "note": note}
    with open(BUDGET_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    st["budget_tests_remaining"] -= n; st["budget_raw_balance"] = st.get("budget_raw_balance", st["budget_tests_remaining"] + n) - n
    st["tests_consumed_lifetime"] = st.get("tests_consumed_lifetime", 0) + n; st.setdefault("budget_ledger", []).append(entry)
    LOOP_STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def run(workers: int = 8) -> dict:
    import look_ledger
    p = check_pins(require_harness=True)
    ov = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    mode = "historical" if ov.get("burn_override") else "forward"
    if mode == "historical":
        pop = json.loads(POPULATION.read_text())
        if sha(POPULATION) != p["population"]["sha256"]:
            raise SystemExit("REFUS : population.json ne correspond pas au pin")
    else:
        seal_ms = int(datetime.fromisoformat(ov.get("seal_ts", "2026-09-13T00:00:00+00:00")).timestamp() * 1000)
        pop = build_population(p, min_t0_ms=seal_ms)
        if pop["n"] < N_FORWARD_MIN and datetime.now(timezone.utc).strftime("%Y-%m-%d") < FORWARD_LATEST_LOOK:
            raise SystemExit("REFUS : mode forward, %d evenements eligibles < %d et date < %s" % (pop["n"], N_FORWARD_MIN, FORWARD_LATEST_LOOK))
    ts = sorted(e["tradable_start_ts"] for e in pop["events"]); lg = check_ledgers((ts[0], ts[-1]), mode); thr = lg["threshold_from_kernel"]
    cfg = [{"hypothesis": "MEXC_TO_BINANCE_V1", "mechanism_id": MECHANISM_ID, "mode": mode, "side": SIDE, "entry": "t0+%dm" % ENTRY_OFFSET_MIN, "primary": PRIMARY,
            "statistic": "spearman permutation, one-sided", "conditioning": "pre_announcement_return_24h", "high_threshold": HIGH_THRESHOLD, "n_universe": pop["n"], "n_high": pop["n_high"],
            "family": FAMILY, "n_family": N_FAMILY_TESTS, "threshold_t": thr, "alpha_one_sided": ALPHA_FAMILY, "cost_rt_bps_mean": pop["mean_cost_rt_bps"], "burn_override": bool(ov.get("burn_override"))}]
    entry = look_ledger.record("confirm", (ts[0], ts[-1]), cfg, prereg=str(PREREG), require_witness=True, witness_branch=WITNESS_BRANCH,
                               note="MEXC_TO_BINANCE_V1 first look (%s); re-test on a MEXC_FIRST subset of seq 8 with one pre-announcement conditioning; harness pinned" % mode)
    ml = MultiplicityLedger(KERNEL_LEDGER); ml.record_trial(FAMILY, MECHANISM_ID, sha(PREREG), 1, note="LOOK_LEDGER seq %d, prereg orphan %s" % (entry["seq"], WITNESS_BRANCH))
    debit_budget(1, "regard unique MEXC_TO_BINANCE_V1 (MEXC_FIRST, pre_announcement_return_24h, short +15m/6h), famille news n=6", entry["seq"])
    store = S8.VisionStore4(); res = evaluate(store, pop, workers=workers); pl = placebos(store, pop, workers=workers); ctl = other_venue_first_control(store, workers=workers)
    v, why = verdict_for(res, pl)
    out = {"ledger_seq": entry["seq"], "ledger_hash": entry["hash"], "mode": mode, "threshold_t": thr, "alpha_one_sided": ALPHA_FAMILY, "family": FAMILY, "n_family_tests": N_FAMILY_TESTS, "verdict": v, "reasons": why,
           "fee_provenance": "official_published", "fee_status": "official_published_vip0_confirmed_by_spot_tier", "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **{k: v_ for k, v_ in res.items() if k != "per_event"}, "placebos": pl, "other_venue_first_control": ctl, "per_event": res["per_event"],
           "capital_deployable": False, "no_position": True, "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "first_look_results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    (RESULTS / "verdict.json").write_text(json.dumps({"mechanism_id": MECHANISM_ID, "status": v, "reasons": why, "primary": res["primary"], "economic_gate": {k: v_ for k, v_ in res["economic_gate"].items()},
                                                      "ledger_seq": entry["seq"], "first_look": True, "capital_deployable": False, "fee_provenance": "official_published"}, indent=1, default=str))
    pr, g, nt = res["primary"], res["economic_gate"]["gross"], res["economic_gate"]["net"]
    print("n=%d rho=%.3f p=%.4f | HIGH n=%d gross mean=%.1f median=%.1f net mean=%.1f t=%.2f -> %s %s" % (res["n_measured"], pr["rho"] or 0, pr["p_one_sided"] or 1, g.get("n", 0), g.get("mean_bps", 0), g.get("median_bps", 0), nt.get("mean_bps", 0), nt.get("t", 0), v, why))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-conditioning", action="store_true"); ap.add_argument("--build-population", action="store_true"); ap.add_argument("--positive-control", action="store_true"); ap.add_argument("--run", action="store_true")
    ap.add_argument("--workers", type=int, default=8); a = ap.parse_args()
    if a.build_conditioning:
        c = build_conditioning(); CONDITIONING.parent.mkdir(parents=True, exist_ok=True); CONDITIONING.write_text(json.dumps(c, indent=1, ensure_ascii=False))
        with open(CONDITIONING.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["event_id", "asset", "binance_symbol", "mexc_market", "t0", "publication_ts_ms", "pre_announcement_return_24h", "hours_in_window", "window_end_ms", "post_announcement_hours_dropped", "announcement_cut"], extrasaction="ignore"); w.writeheader(); w.writerows(c["rows"])
        print(json.dumps({k: v for k, v in c.items() if k != "rows"}, indent=1, ensure_ascii=False))
    elif a.build_population:
        pop = build_population(); POPULATION.write_text(json.dumps(pop, indent=1, ensure_ascii=False)); print(json.dumps({k: v for k, v in pop.items() if k not in ("events", "high_event_ids", "low_event_ids")}, indent=1, ensure_ascii=False))
    elif a.positive_control:
        r = positive_control(); RESULTS.mkdir(parents=True, exist_ok=True); (RESULTS / "positive_control.json").write_text(json.dumps(r, indent=1, default=str))
        print(json.dumps({k: v for k, v in r.items() if k != "seeds"}, indent=1, default=str)); sys.exit(0 if r["passed"] else 1)
    elif a.run:
        run(a.workers)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
