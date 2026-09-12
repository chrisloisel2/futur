#!/usr/bin/env python3
"""
account_execution_reality.py -- ce que l'execution coute REELLEMENT sur ce compte, par opposition a ce qu'une
spec a declare. Lecture seule, aucun ordre, aucune permission de trading requise.

Sans cle : le module ne contacte aucun endpoint signe, collecte ce qui est public (contraintes de symbole)
et ecrit un rapport qui dit exactement ce qui manque et pourquoi cela bloque. Les tests passent dans ce mode.
Avec une cle en lecture seule : frais constates, brackets de levier, executions, funding paye, marge empruntable.

    --collect [--allow-trading-key]   collecte (public + signe si cle) et ecrit les rapports
    --status                          ce qui est disponible, sans rien appeler de signe
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_lake.collectors import binance_account_readonly as RO
from data_lake.collectors import execution_cost_schema as S

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "execution"
STORE = ROOT / "data" / "account_execution"          # data/* est gitignore : rien de sensible n'est versionne
UNIVERSE_H2 = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
H3_RESULTS = ROOT / "mechanisms" / "event_reaction_v1" / "results" / "first_look_results.json"
FEE_MANIFEST = ROOT / "data_lake" / "manifests" / "published_fee_schedules_2026-09-10.json"
SPECS = {"H2": ROOT / "mechanisms" / "event_listing_perp_fade_v1" / "spec.json",
         "H3": ROOT / "mechanisms" / "event_delisting_pressure_v1" / "spec.json"}


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _write_private(p: Path, text: str) -> None:
    """Dump brut de compte : hors depot ET lisible par l'utilisateur seul (0600, dossier 0700)."""
    p.parent.mkdir(parents=True, exist_ok=True); os.chmod(p.parent, 0o700)
    tmp = p.with_suffix(p.suffix + ".tmp"); fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p); os.chmod(p, 0o600)


def h2_symbols() -> List[str]:
    if not UNIVERSE_H2.exists():
        return []
    return [e["symbol"] for e in json.loads(UNIVERSE_H2.read_text())["events"]]


def h3_symbols() -> List[str]:
    if not H3_RESULTS.exists():
        return []
    return sorted({e["symbol"] for e in json.loads(H3_RESULTS.read_text())["hypotheses"]["H3"]["events"]})


def published_vip0() -> Dict[str, Any]:
    """Frais publies officiels (manifeste P1.1) : la meilleure provenance disponible sans cle."""
    if not FEE_MANIFEST.exists():
        return {}
    m = json.loads(FEE_MANIFEST.read_text())
    v = ((m.get("venues") or {}).get("binance") or {}).get("published_schedule") or {}
    return {"maker_bps": (v.get("vip0") or {}).get("maker"), "taker_bps": (v.get("vip0") or {}).get("taker"),
            "source_class": (v.get("vip0") or {}).get("source_class"), "as_of": m.get("as_of") or m.get("fetched_at_utc")}


#: bareme SPOT officiel (https://www.binance.com/en/fee/trading, VIP0 0,1000 %/0,1000 %, VIP1 0,0900 %/0,1000 %) : sert
#: seulement a identifier le palier depuis les champs makerCommission/takerCommission du compte (en bps, sans remise BNB)
SPOT_PUBLISHED_BPS = {"VIP0": (10.0, 10.0), "VIP1": (9.0, 10.0), "VIP2": (8.0, 10.0), "VIP3": (4.2, 5.4), "VIP4": (4.2, 5.4), "VIP5": (3.6, 4.8), "VIP6": (3.0, 4.2), "VIP7": (2.4, 3.6), "VIP8": (1.8, 3.0), "VIP9": (1.2, 2.4)}
CAPACITY_FEATURES = ROOT / "reports" / "data_acquisition" / "H2_DEPTH_CAPACITY_FEATURES.json"
FEE_DECISION = ROOT / "reports" / "execution" / "FUTURES_FEE_DECISION.json"


def fee_decision(path: Optional[Path] = None) -> Dict[str, Any]:
    """La decision P13 (option B) : bareme officiel VIP0, remise BNB ignoree, fenetre de chaine nommee. Vide si absente."""
    p = Path(path) if path else FEE_DECISION
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}
FAPI_PERMISSION_NOTE = ("Binance serves every signed /fapi endpoint, reads included, only to a key with 'Enable Futures' -- a trading "
                        "permission. There is no futures-read-only key: a read-only key gets -2015 on /fapi and the futures fee cannot be "
                        "read as account_actual without accepting a trading-capable key (IP-restricted, --allow-trading-key, journaled).")


def infer_tier_from_spot(spot_fees: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Le palier VIP depuis les commissions spot du compte (account_actual) confrontees au bareme spot officiel.
    Le palier est commun au spot et aux futures USDS-M ; le frais futures reste ensuite un chiffre PUBLIE a ce palier."""
    if not spot_fees or spot_fees.get("maker_bps") is None or spot_fees.get("taker_bps") is None:
        return {"tier": None, "source": "no spot commission in the account payload"}
    pair = (float(spot_fees["maker_bps"]), float(spot_fees["taker_bps"]))
    for tier, ref in SPOT_PUBLISHED_BPS.items():
        if abs(pair[0] - ref[0]) < 1e-9 and abs(pair[1] - ref[1]) < 1e-9:
            return {"tier": tier, "source": "spot makerCommission/takerCommission (account_actual) matched to the official spot schedule", "spot_bps": pair}
    return {"tier": None, "source": "spot commission %s matches no published tier (BNB discount or promotion applied?)" % (pair,)}


def futures_permission_diagnosis(acct: Dict[str, Any]) -> Optional[str]:
    """Explique -2015 sur /fapi quand la cle est read-only sans 'Enable Futures' : ce n'est pas une panne, c'est le modele de permissions."""
    pc = acct.get("permission_check") or {}
    if pc.get("status") != "ok":
        return None
    fapi = [v for k, v in (acct.get("endpoints") or {}).items() if v.get("endpoint", "").startswith("GET /fapi")]
    if fapi and all(v.get("binance_code") == -2015 for v in fapi) and not (pc.get("restrictions") or {}).get("enableFutures"):
        return "futures_read_requires_enable_futures"
    return None


def capacity_links(window_min: int, notional_usd: int, events: Optional[List[str]] = None, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Spread et glissement MESURES par P11 (Binance Vision : archives officielles) pour une fenetre et un notionnel
    nommes, en ALLER-RETOUR comme le schema l'attend : spread effectif complet, glissement vente + achat (entree short,
    sortie achat) au notionnel donne. Medianes sur les evenements H2 (ou la liste donnee) ; la borne haute (bande du
    carnet) est donnee a cote ; un ordre qui depasse le carnet observe est compte, pas estime. La preregistration nomme
    la fenetre et le notionnel ; ce module ne choisit pas."""
    p = Path(path) if path else CAPACITY_FEATURES
    if not p.exists():
        return None
    import statistics
    doc = json.loads(p.read_text()); sp, rt, ub, n, no_fill = [], [], [], 0, 0
    for e in doc.get("events", []):
        if events is not None and e.get("event_id") not in events:
            continue
        w = next((w for w in e.get("windows", []) if w.get("window_min") == window_min), None)
        if not w:
            continue
        n += 1
        if w.get("effective_spread_bps") is not None:
            sp.append(float(w["effective_spread_bps"]))
        ks, kb = "sell_slippage_%d_usd_bps" % notional_usd, "buy_slippage_%d_usd_bps" % notional_usd
        if w.get(ks) is not None and w.get(kb) is not None:
            rt.append(float(w[ks]) + float(w[kb]))
            if w.get(ks + "_ub") is None and w.get(ks.replace("_bps", "_ub_bps")) is not None and w.get(kb.replace("_bps", "_ub_bps")) is not None:
                ub.append(float(w[ks.replace("_bps", "_ub_bps")]) + float(w[kb.replace("_bps", "_ub_bps")]))
        elif w.get("book_ok"):
            no_fill += 1
    if not sp or not rt:
        return None
    return {"window_min": window_min, "notional_usd": notional_usd, "n_events": n, "n_spread": len(sp), "n_slippage": len(rt), "n_order_exceeds_book": no_fill,
            "spread_bps_median": round(statistics.median(sp), 2), "slippage_rt_bps_median": round(statistics.median(rt), 2),
            "slippage_rt_upper_bound_bps_median": round(statistics.median(ub), 1) if ub else None,
            "provenance": "official_published", "source": "P11 H2_DEPTH_CAPACITY_FEATURES (Binance Vision bookDepth + aggTrades); slippage = linear estimate within the book band, uniform-within-band assumption"}


def spec_cost(which: str) -> Optional[Dict[str, Any]]:
    p = SPECS.get(which)
    if not p or not p.exists():
        return None
    c = json.loads(p.read_text())["cost_model"]
    fee = c.get("taker_fee_bps", 0.0) if c.get("maker_ratio", 0.0) == 0.0 else (c.get("maker_fee_bps") or 0.0)
    return {"fee_bps_per_side": fee, "spread_bps": c.get("spread_bps", 0.0), "slippage_bps": c.get("slippage_bps", 0.0),
            "n_legs": c.get("n_legs", 1), "declared_round_trip_bps": S.round_trip(fee, c.get("spread_bps", 0.0), c.get("slippage_bps", 0.0), c.get("n_legs", 1)),
            "notes": c.get("notes", "")}


def public_constraints(symbols: List[str]) -> Dict[str, Any]:
    """Contraintes de symbole depuis l'exchangeInfo public : aucune cle necessaire."""
    cl = RO.ReadOnlyClient()
    r = cl.get("/fapi/v1/exchangeInfo")
    if r["status"] != "ok":
        return {"status": r["status"], "error": r.get("error"), "constraints": {}, "missing": symbols}
    by = {}
    for s in r["data"]["symbols"]:
        f = {x["filterType"]: x for x in s.get("filters", [])}
        by[s["symbol"]] = S.validate("constraint", {
            "venue": "binance", "market_type": "perp" if s.get("contractType") == "PERPETUAL" else "futures", "symbol": s["symbol"],
            "status": s.get("status"), "tick_size": (f.get("PRICE_FILTER") or {}).get("tickSize"), "step_size": (f.get("LOT_SIZE") or {}).get("stepSize"),
            "min_qty": (f.get("LOT_SIZE") or {}).get("minQty"), "min_notional": (f.get("MIN_NOTIONAL") or {}).get("notional"),
            "max_market_qty": (f.get("MARKET_LOT_SIZE") or {}).get("maxQty"), "market_take_bound": s.get("marketTakeBound"),
            "max_move_order_limit": s.get("maxMoveOrderLimit"), "liquidation_fee": s.get("liquidationFee"),
            "maint_margin_percent": s.get("maintMarginPercent"), "required_margin_percent": s.get("requiredMarginPercent"),
            "max_leverage": None, "margin_borrowable": None, "source": "GET /fapi/v1/exchangeInfo (public)",
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    present = [s for s in symbols if s in by]
    return {"status": "ok", "constraints": {s: by[s] for s in present}, "missing": [s for s in symbols if s not in by], "universe_size": len(by)}


def account_snapshot(allow_trading_key: bool = False, fee_symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Tout ce qui demande une cle. Sans cle : chaque entree vaut 'no_credentials' et rien n'est appele."""
    cl = RO.ReadOnlyClient(allow_trading_key=allow_trading_key)
    perm = cl.check_permissions()
    out: Dict[str, Any] = {"has_credentials": RO.has_credentials(), "env_var": cl.env_var, "key_redacted": RO.redact(cl._key),
                           "permission_check": perm, "usable": bool(perm.get("usable")), "endpoints": {}}
    plan = [("commission_rate", "/fapi/v1/commissionRate", {"symbol": (fee_symbols or ["BTCUSDT"])[0]}),
            ("leverage_brackets", "/fapi/v1/leverageBracket", None),
            ("futures_account", "/fapi/v2/account", None),
            ("spot_account", "/api/v3/account", None),
            ("funding_income", "/fapi/v1/income", {"incomeType": "FUNDING_FEE", "limit": 1000}),
            ("own_fills", "/fapi/v1/userTrades", {"symbol": (fee_symbols or ["BTCUSDT"])[0], "limit": 500}),
            ("margin_pairs", "/sapi/v1/margin/allPairs", None)]
    for name, path, params in plan:
        if not out["usable"]:
            pc = out["permission_check"]                                    # l'etat courant, jamais la sonde d'origine
            out["endpoints"][name] = {"status": pc.get("status", "no_credentials") if pc.get("status") != "ok" else "refused", "endpoint": "GET " + path,
                                      "reason": pc.get("reason"), "would_give": RO.ALLOWED[path][2]}
            continue
        r = cl.get(path, params)
        out["endpoints"][name] = {"status": r["status"], "endpoint": "GET " + path, "error": RO.sanitise_error(r.get("error")), "binance_code": r.get("binance_code"),
                                  "has_rows": (len(r["data"]) > 0) if isinstance(r.get("data"), list) else None}
        if r["status"] == "ok":
            _write_private(STORE / ("%s.json" % name), json.dumps(r["data"], indent=1, default=str))   # hors depot, 0600
            if name == "commission_rate":
                d = r["data"] if isinstance(r["data"], dict) else {}
                try:
                    out["actual_fees"] = {"symbol": params["symbol"], "maker_bps": float(d["makerCommissionRate"]) * 1e4, "taker_bps": float(d["takerCommissionRate"]) * 1e4}
                except (KeyError, TypeError, ValueError):
                    out["endpoints"][name]["status"] = "schema_unexpected"; out["endpoints"][name]["error"] = "commissionRate payload without maker/takerCommissionRate"
            if name == "futures_account":
                out["fee_tier"] = r["data"].get("feeTier") if isinstance(r["data"], dict) else None
            if name in ("futures_account", "spot_account") and isinstance(r["data"], dict):
                out.setdefault("account_flags", {})[name] = RO.cross_check_account_flags(r["data"])["account_flags"]   # informatif, jamais un refus
            if name == "spot_account" and isinstance(r["data"], dict):
                mk, tk = r["data"].get("makerCommission"), r["data"].get("takerCommission")
                if mk is not None and tk is not None:
                    out["spot_fees"] = {"maker_bps": float(mk), "taker_bps": float(tk), "unit": "makerCommission=10 means 10 bps"}
    return out


def build_costs(pub: Dict[str, Any], acct: Dict[str, Any], capacity: Optional[Dict[str, Any]] = None, decision: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """La chaine de cout, avec la provenance de chaque maillon, et la comparaison aux hypotheses H2/H3.
    Frais : account_actual si lu, sinon bareme officiel au palier INFERE du spot (official_published, palier confirme),
    sinon bareme VIP0. Spread/glissement : mesures P11 si une fenetre est nommee (`capacity`), sinon declares."""
    actual = acct.get("actual_fees"); tier = infer_tier_from_spot(acct.get("spot_fees"))
    if actual:
        fee, prov, fee_note = actual["taker_bps"], "account_actual", "GET /fapi/v1/commissionRate"
    elif pub.get("taker_bps") is not None:
        fee, prov = float(pub["taker_bps"]), "official_published"
        fee_note = ("official VIP0 schedule; tier %s confirmed from the account's spot commission; BNB discount not applied (status unknown: conservative)" % tier["tier"]
                    if tier.get("tier") == "VIP0" else "official VIP0 schedule; account tier %s" % (tier.get("tier") or "unknown"))
    else:
        fee, prov, fee_note = 5.0, "declared", "no schedule available"
    decision = fee_decision() if decision is None else decision; dw = (decision.get("cost_chain_window") or {}) if decision else {}
    out = {"fee_bps_per_side": fee, "fee_provenance": prov, "fee_note": fee_note, "fee_tier_inferred": tier, "comparisons": {}, "capacity_links": capacity,
           "decision": decision.get("decision") if decision else None, "decision_window": dw or None}
    for which in ("H2", "H3"):
        sc = spec_cost(which)
        if not sc:
            continue
        cl = capacity if capacity else (capacity_links(int(dw["window_min"]), int(dw["notional_usd"])) if dw and dw.get("hypothesis") == which else None)
        if cl:
            out.setdefault("capacity_links_used", {})[which] = cl
            cost = S.build_cost("perp", fee, prov, cl["spread_bps_median"], cl["provenance"], cl["slippage_rt_bps_median"], cl["provenance"], sc["n_legs"],
                                note="spread and round-trip slippage are P11 medians at window +%dm for %d USD (band upper bound %s bps, %d orders exceed the observed book)" % (
                                    cl["window_min"], cl["notional_usd"], cl.get("slippage_rt_upper_bound_bps_median"), cl["n_order_exceeds_book"]))
        else:
            cost = S.build_cost("perp", fee, prov, sc["spread_bps"], "declared", sc["slippage_bps"], "declared", sc["n_legs"],
                                note="spread and slippage are still the values declared in the spec; only the fee has a stronger provenance")
        out["comparisons"][which] = {"spec_declared_round_trip_bps": sc["declared_round_trip_bps"], "cost_chain": cost,
                                     **S.compare_to_assumption(cost, sc["declared_round_trip_bps"])}
    return out


def chains_by_window(pub: Dict[str, Any], acct: Dict[str, Any], which: str = "H2", notionals: Tuple[int, ...] = (100, 500, 1000), windows: Tuple[int, ...] = (0, 1, 5, 15, 30, 60)) -> List[Dict[str, Any]]:
    """Table DESCRIPTIVE : la chaine de cout mesuree pour chaque fenetre x notionnel que la preregistration pourrait nommer.
    Aucune fenetre n'est choisie ici ; aucun rendement n'entre dans ce calcul."""
    rows = []
    for w in windows:
        for n in notionals:
            cap = capacity_links(w, n)
            if not cap:
                continue
            c = build_costs(pub, acct, cap)["comparisons"].get(which)
            if c:
                rows.append({"window_min": w, "notional_usd": n, "n_events": cap["n_events"], "fee_bps": c["cost_chain"]["fee_bps_per_side"], "fee_provenance": c["cost_chain"]["fee_provenance"],
                             "spread_bps": cap["spread_bps_median"], "slippage_rt_bps": cap["slippage_rt_bps_median"], "slippage_rt_ub_bps": cap["slippage_rt_upper_bound_bps_median"], "n_order_exceeds_book": cap["n_order_exceeds_book"],
                             "round_trip_bps": c["measured_round_trip_bps"], "spec_declared_round_trip_bps": c["assumed_round_trip_bps"], "status_vs_spec": c["status"], "weakest_provenance": c["weakest_provenance"]})
    return rows


def write_reports(pub: Dict[str, Any], acct: Dict[str, Any], cons: Dict[str, Any], costs: Dict[str, Any], out: Optional[Path] = None) -> Dict[str, Any]:
    import data_lake.collectors.account_execution_reality as _self   # racine resolue a l'appel
    out = Path(out) if out is not None else _self.OUT
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    has = acct.get("has_credentials"); usable = acct.get("usable")
    answers = {
        "1_actual_futures_taker_fee_bps": (acct.get("actual_fees") or {}).get("taker_bps"),
        "2_actual_futures_maker_fee_bps": (acct.get("actual_fees") or {}).get("maker_bps"),
        "3_funding_payment_availability": acct["endpoints"]["funding_income"]["status"],
        "4_leverage_bracket_availability": acct["endpoints"]["leverage_brackets"]["status"],
        "5_h2_h3_cost_assumptions": {k: v["status"] for k, v in costs["comparisons"].items()},
        "6_what_remains_theoretical": [],
    }
    theo = answers["6_what_remains_theoretical"]; diag = futures_permission_diagnosis(acct)
    answers["7_fee_tier_inferred_from_spot"] = costs.get("fee_tier_inferred"); answers["8_futures_permission_diagnosis"] = diag
    answers["9_cost_chain_by_window"] = chains_by_window(pub, acct, "H2") if usable or not has else []
    if not has:
        theo.append("every account figure: no read-only API key in the environment (%s or %s)" % (RO.ENV_KEY, RO.FALLBACK_ENV[0]))
    elif not usable:
        theo.append("every account figure: the key was refused — %s" % acct["permission_check"].get("reason"))
    if diag == "futures_read_requires_enable_futures":
        theo.append("actual futures fee (account_actual): " + FAPI_PERMISSION_NOTE)
    if not (acct.get("permission_check") or {}).get("ip_restricted", True) and has:
        theo.append("the key has NO IP restriction (ipRestrict=false): restrict it to this machine before any further use")
    if not costs.get("capacity_links"):
        theo.append("spread and slippage in both cost chains are the values declared in the specs; P11 measured them per window (see 9_cost_chain_by_window): "
                    "the chain leaves 'unknown' only once a preregistration names the window and notional")
    theo.append("borrow availability and borrow cost for spot delisting shorts (sapi margin endpoints, key required)")
    doc = {"generated_at_utc": now, "published_vip0": pub, "account": {k: v for k, v in acct.items() if k != "key_redacted"},
           "key_redacted": acct.get("key_redacted"), "costs": costs, "constraints_coverage": {"h2_symbols": len(h2_symbols()), "h3_symbols": len(h3_symbols()),
           "with_public_constraints": len(cons.get("constraints", {})), "missing": cons.get("missing", [])[:40], "n_missing": len(cons.get("missing", []))},
           "answers": answers, "no_orders": True, "no_trading_permission_used": True, "no_secrets_stored": True, "no_alpha_test": True}
    _write_atomic(out / "ACCOUNT_EXECUTION_REALITY.json", json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n")

    md = [f"# ACCOUNT EXECUTION REALITY — P8 ({now[:19]} UTC)", "",
          "What execution actually costs on this account, as opposed to what a spec declared. Read-only: the module "
          "implements GET and nothing else, calls only a whitelisted set of endpoints, and refuses a key that carries "
          "trading permission. No order, no signal, no verdict, no budget. Credentials come from the environment and are "
          "never written, logged or returned.", "",
          f"**Credentials: {'present' if has else 'absent'}** ({acct.get('key_redacted')}).",
          "" if has else f"Set `{RO.ENV_KEY}` and `{RO.ENV_SECRET}` (or `{RO.FALLBACK_ENV[0]}` / `{RO.FALLBACK_ENV[1]}`) to a key with **no** trading permission, then re-run `--collect`.", "",
          "## The six questions", "",
          "| # | question | answer |", "|---|---|---|",
          f"| 1 | actual futures taker fee | {answers['1_actual_futures_taker_fee_bps'] if answers['1_actual_futures_taker_fee_bps'] is not None else '**unknown** — no usable key'} |",
          f"| 2 | actual futures maker fee | {answers['2_actual_futures_maker_fee_bps'] if answers['2_actual_futures_maker_fee_bps'] is not None else '**unknown** — no usable key'} |",
          f"| 3 | funding payments available | {answers['3_funding_payment_availability']} |",
          f"| 4 | leverage brackets available | {answers['4_leverage_bracket_availability']} |",
          f"| 5 | H2 / H3 cost assumptions | {answers['5_h2_h3_cost_assumptions']} |",
          "| 6 | what remains theoretical | see below |", "",
          "### 6. What remains theoretical", ""] + [f"- {t}" for t in theo] + ["", "## Cost chains", "",
          "A cost chain is worth its weakest link. `confirmed` and `contradicted` are only possible when every link is "
          "measured or officially published; otherwise the answer is `unknown`, which is not a failure — it is the correct "
          "statement about a number nobody measured.", "",
          "| hypothesis | spec declared round trip | chain round trip | weakest provenance | status |", "|---|---|---|---|---|"]
    for k, v in costs["comparisons"].items():
        c = v["cost_chain"]
        md.append(f"| {k} | {v['spec_declared_round_trip_bps']:.1f} bps | {c['round_trip_bps']:.1f} bps | `{c['weakest_provenance']}` | **{v['status']}** |")
    md += ["", f"Fee link: {costs['fee_bps_per_side']:.2f} bps per side, provenance `{costs['fee_provenance']}`"
           + (f" (published VIP0, {pub.get('source_class')}, as of {pub.get('as_of')})" if costs['fee_provenance'] == 'official_published' else "") + ".", "",
           "## Endpoints", "", "| name | endpoint | status | what it would give |", "|---|---|---|---|"]
    for name, e in acct["endpoints"].items():
        md.append(f"| {name} | `{e['endpoint']}` | {e['status']} | {e.get('would_give') or RO.ALLOWED[e['endpoint'].split(' ',1)[1]][2]} |")
    md += ["", "## Safety properties of this module", "",
           "- Only GET exists in `binance_account_readonly.py`; there is no order, cancel or transfer code path to disable.",
           "- Every call goes through a whitelist of ten read endpoints; anything else raises before a request is built.",
           f"- A key granting any of {', '.join(RO.FORBIDDEN_PERMISSIONS)} is refused unless the caller passes `--allow-trading-key`, and the refusal is reported.",
           "- Raw account responses are written under `data/account_execution/` (gitignored). Nothing account-specific is versioned.", ""]
    _write_atomic(out / "ACCOUNT_EXECUTION_REALITY.md", "\n".join(md) + "\n")

    ft = [f"# ACCOUNT FEE TABLE — P8 ({now[:19]} UTC)", "",
          "Every fee carries its provenance. `account_actual` is what this account is charged; `official_published` is the "
          "venue's schedule; `declared` is a number a spec wrote down. Only the first two can settle a cost-wall question.", "",
          "| market | maker (bps) | taker (bps) | provenance | source |", "|---|---|---|---|---|"]
    a = acct.get("actual_fees")
    if a:
        ft.append(f"| USDS-M perp ({a['symbol']}) | {a['maker_bps']:.2f} | {a['taker_bps']:.2f} | `account_actual` | GET /fapi/v1/commissionRate |")
    if pub.get("taker_bps") is not None:
        ft.append(f"| USDS-M perp (VIP0) | {pub['maker_bps']} | {pub['taker_bps']} | `official_published` | {FEE_MANIFEST.name} ({pub.get('source_class')}) |")
    for which, v in costs["comparisons"].items():
        sc = spec_cost(which)
        ft.append(f"| {which} spec assumption | — | {sc['fee_bps_per_side']} | `declared` | mechanisms/{'event_listing_perp_fade_v1' if which=='H2' else 'event_delisting_pressure_v1'}/spec.json |")
    ft += ["", f"Account fee tier: {acct.get('fee_tier') if acct.get('fee_tier') is not None else '**unknown** (needs a usable key)'}.", "",
           "Until a read-only key exists, the strongest available figure is the published VIP0 schedule, and every cost wall "
           "computed from it inherits that provenance: strong enough to reject, not strong enough to promote.", ""]
    _write_atomic(out / "ACCOUNT_FEE_TABLE.md", "\n".join(ft) + "\n")

    cs = cons.get("constraints", {}); miss = cons.get("missing", [])
    h2s, h3s = set(h2_symbols()), set(h3_symbols())
    sc_md = [f"# ACCOUNT SYMBOL CONSTRAINTS — P8 ({now[:19]} UTC)", "",
             "What the exchange lets an order be, per symbol: tick size, lot step, minimum notional, market-order bound, "
             "liquidation fee, maintenance margin. Public `exchangeInfo`, no key needed. These are the constraints any "
             "capacity or slippage estimate has to respect.", "",
             f"Coverage: **{len(cs)}** of {len(h2s | h3s)} H2 + H3 symbols are still listed; **{len(miss)}** are not in "
             "`exchangeInfo` any more (delisted contracts disappear from it, which is itself the fact that a delisting happened).", ""]
    if cs:
        sc_md += ["| symbol | scope | status | tick | step | min qty | min notional | market take bound | liq. fee | maint. margin |", "|---|---|---|---|---|---|---|---|---|---|"]
        for s in sorted(cs)[:40]:
            c = cs[s]; scope = "H2" if s in h2s else "H3"
            sc_md.append(f"| {s} | {scope} | {c['status']} | {c['tick_size']} | {c['step_size']} | {c['min_qty']} | {c['min_notional']} | {c['market_take_bound']} | {c['liquidation_fee']} | {c['maint_margin_percent']} |")
        sc_md += ["", f"(first 40 of {len(cs)}; the full set is in `ACCOUNT_EXECUTION_REALITY.json` inputs and can be regenerated with `--collect`)", ""]
    if miss:
        sc_md += ["## Symbols no longer listed", "", ", ".join(sorted(miss)), "",
                  "For these, constraints at the time of the event can only come from a historical source; `exchangeInfo` is a snapshot of now.", ""]
    sc_md += ["## What still needs a key", "",
              "- `maxLeverage` per notional bracket (`GET /fapi/v1/leverageBracket`): decides the margin a short actually costs.",
              "- Margin borrowability and borrow rate per asset (`sapi` margin endpoints): decides whether a spot short exists at all.", ""]
    _write_atomic(out / "ACCOUNT_SYMBOL_CONSTRAINTS.md", "\n".join(sc_md) + "\n")
    return doc


def collect(allow_trading_key: bool = False, out: Optional[Path] = None) -> Dict[str, Any]:
    syms = h2_symbols() + h3_symbols()
    pub = published_vip0(); acct = account_snapshot(allow_trading_key, fee_symbols=syms or ["BTCUSDT"])
    cons = public_constraints(sorted(set(syms))); costs = build_costs(pub, acct)
    doc = write_reports(pub, acct, cons, costs, out); write_collected_reports(doc, out)
    return doc


def snapshot_from_store(store: Optional[Path] = None, collected: Optional[Path] = None) -> Dict[str, Any]:
    """Reconstruit l'instantane de compte depuis les dumps bruts deja collectes (0600, hors depot) et le dernier
    permission_check publie : AUCUN appel reseau, aucune cle lue. Sert a regenerer les rapports apres un correctif."""
    import data_lake.collectors.account_execution_reality as _self
    store = Path(store) if store else _self.STORE; last = json.loads(Path(collected or (_self.OUT / "ACCOUNT_EXECUTION_REALITY_COLLECTED.json")).read_text()) if (collected or (_self.OUT / "ACCOUNT_EXECUTION_REALITY_COLLECTED.json")).exists() else {}
    perm = last.get("permission_check") or {"status": "unknown", "usable": False, "reason": "no previous collection"}
    out: Dict[str, Any] = {"has_credentials": bool(last.get("key_redacted") and last.get("key_redacted") != "<absent>"), "env_var": last.get("env_var_used"), "key_redacted": last.get("key_redacted"),
                           "permission_check": perm, "usable": bool(perm.get("usable")), "endpoints": dict(last.get("endpoints") or {}), "rebuilt_from_store": True}
    for name in ("commission_rate", "leverage_brackets", "futures_account", "spot_account", "funding_income", "own_fills", "margin_pairs"):
        p = store / ("%s.json" % name)
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        if name == "commission_rate" and isinstance(data, dict) and "makerCommissionRate" in data:
            out["actual_fees"] = {"symbol": data.get("symbol"), "maker_bps": float(data["makerCommissionRate"]) * 1e4, "taker_bps": float(data["takerCommissionRate"]) * 1e4}
        if name == "futures_account" and isinstance(data, dict):
            out["fee_tier"] = data.get("feeTier")
        if name in ("futures_account", "spot_account") and isinstance(data, dict):
            out.setdefault("account_flags", {})[name] = RO.cross_check_account_flags(data)["account_flags"]
        if name == "spot_account" and isinstance(data, dict) and data.get("makerCommission") is not None:
            out["spot_fees"] = {"maker_bps": float(data["makerCommission"]), "taker_bps": float(data["takerCommission"]), "unit": "makerCommission=10 means 10 bps"}
    return out


def rebuild_from_store(out: Optional[Path] = None) -> Dict[str, Any]:
    syms = h2_symbols() + h3_symbols(); pub = published_vip0(); acct = snapshot_from_store()
    cons = public_constraints(sorted(set(syms))); costs = build_costs(pub, acct)
    doc = write_reports(pub, acct, cons, costs, out); write_collected_reports(doc, out)
    return doc



# ----------------------------------------------------------------------------- P11 : rapports complementaires
def write_collected_reports(doc: Dict[str, Any], out: Optional[Path] = None) -> Dict[str, Path]:
    """ACCOUNT_EXECUTION_REALITY_COLLECTED (l'instantane tel que collecte, ou 'no_credentials'),
    H2_H3_COST_CHAIN_STATUS (confirmed / unknown / contradicted, maillon par maillon), READONLY_KEY_SAFETY_AUDIT."""
    import data_lake.collectors.account_execution_reality as _self
    out = Path(out) if out is not None else _self.OUT; out.mkdir(parents=True, exist_ok=True)
    now = doc.get("generated_at_utc") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    acct, costs, ans = doc["account"], doc["costs"], doc["answers"]
    has, usable = acct.get("has_credentials"), acct.get("usable")
    mode = "collected" if usable else ("refused" if has else "no_credentials")
    coll = {"generated_at_utc": now, "mode": mode, "env_var_used": acct.get("env_var"), "key_redacted": doc.get("key_redacted"),
            "permission_check": acct.get("permission_check"), "endpoints": acct["endpoints"],
            "actual_futures_taker_fee_bps": ans["1_actual_futures_taker_fee_bps"], "actual_futures_maker_fee_bps": ans["2_actual_futures_maker_fee_bps"],
            "actual_spot_fee_bps": acct.get("spot_fees"), "funding_payments_available": ans["3_funding_payment_availability"] == "ok",
            "user_fills_available": acct["endpoints"].get("own_fills", {}).get("status") == "ok", "leverage_brackets_available": ans["4_leverage_bracket_availability"] == "ok",
            "margin_pairs_available": acct["endpoints"].get("margin_pairs", {}).get("status") == "ok", "fee_tier": acct.get("fee_tier"),
            "symbol_constraints_public": doc.get("constraints_coverage"), "cost_chain_status": ans["5_h2_h3_cost_assumptions"],
            "no_orders": True, "no_secrets_stored": True, "raw_account_data_location": "data/account_execution/ (gitignored)"}
    _write_atomic(out / "ACCOUNT_EXECUTION_REALITY_COLLECTED.json", json.dumps(coll, indent=1, ensure_ascii=False, default=str) + "\n")
    md = [f"# ACCOUNT EXECUTION REALITY — collected ({now[:19]} UTC)", "", f"Mode: **{mode}**.", ""]
    if mode == "no_credentials":
        md += ["No API key was found in the environment (`BINANCE_READONLY_API_KEY` / `BINANCE_READONLY_API_SECRET`, fallback "
               "`BINANCE_API_KEY` / `BINANCE_API_SECRET`). No signed request was made. Every account figure below is therefore "
               "`unknown`, which is the correct output, not a failure. The published VIP0 schedule remains the strongest figure "
               "available and it is only strong enough to reject, never to promote.", ""]
    elif mode == "refused":
        md += [f"A key exists but was **refused**: {acct.get('permission_check', {}).get('reason')}. A research repository uses a key that cannot trade, withdraw or transfer.", ""]
    md += ["| item | value |", "|---|---|",
           f"| actual futures taker fee | {coll['actual_futures_taker_fee_bps'] if coll['actual_futures_taker_fee_bps'] is not None else 'unknown'} |",
           f"| actual futures maker fee | {coll['actual_futures_maker_fee_bps'] if coll['actual_futures_maker_fee_bps'] is not None else 'unknown'} |",
           f"| actual spot fee | {coll['actual_spot_fee_bps'] or 'unknown'} |",
           f"| funding payments available | {coll['funding_payments_available']} |", f"| user fills available | {coll['user_fills_available']} |",
           f"| leverage brackets available | {coll['leverage_brackets_available']} |", f"| margin pairs / borrowability | {coll['margin_pairs_available']} |",
           f"| symbol constraints (public) | {(doc.get('constraints_coverage') or {}).get('with_public_constraints')} symbols |",
           f"| H2 cost chain | **{ans['5_h2_h3_cost_assumptions'].get('H2')}** |", f"| H3 cost chain | **{ans['5_h2_h3_cost_assumptions'].get('H3')}** |", ""]
    _write_atomic(out / "ACCOUNT_EXECUTION_REALITY_COLLECTED.md", "\n".join(md) + "\n")

    cc = [f"# H2 / H3 COST CHAIN STATUS ({now[:19]} UTC)", "",
          "A cost chain has three links — fee, spread, slippage — and is worth its weakest. `confirmed` and `contradicted` "
          "require every link measured (`account_actual`) or officially published; a chain that rests on a declared number "
          "is `unknown`. `unknown` is an honest state, not a defect.", "",
          "| hypothesis | spec declared round trip | chain round trip | fee | spread | slippage | weakest | status |", "|---|---|---|---|---|---|---|---|"]
    for k, v in costs["comparisons"].items():
        c = v["cost_chain"]
        cc.append(f"| {k} | {v['spec_declared_round_trip_bps']:.1f} bps | {c['round_trip_bps']:.1f} bps | `{c['fee_provenance']}` | `{c['spread_provenance']}` | `{c['slippage_provenance']}` | `{c['weakest_provenance']}` | **{v['status']}** |")
    tier = (doc["costs"].get("fee_tier_inferred") or {}); diag = ans.get("8_futures_permission_diagnosis"); pc = acct.get("permission_check") or {}
    if doc["costs"].get("decision"):
        dwd = doc["costs"].get("decision_window") or {}
        cc += ["", "## Decision in force", "", f"`{doc['costs']['decision']}` (FUTURES_FEE_DECISION.md): official VIP0 futures fee at the tier confirmed by the spot account, BNB discount not applied, "
               f"no futures trading key. Cost-chain window for {dwd.get('hypothesis')}: **+{dwd.get('window_min')} min, {dwd.get('notional_usd')} $** — chosen on cost/capacity only."]
    cc += ["", "## Fee link", "",
           f"- provenance now: `{doc['costs'].get('fee_provenance')}` — {doc['costs'].get('fee_note')}",
           f"- tier inferred from the account's spot commission: **{tier.get('tier') or 'unknown'}** ({tier.get('source')})"]
    if diag == "futures_read_requires_enable_futures":
        cc += ["- `account_actual` for the futures fee is **not reachable with a read-only key**: " + FAPI_PERMISSION_NOTE,
               "- consequence: the fee link stays `official_published` at the confirmed tier; on VIP0 the only residual is the 10 % BNB discount (0.5 bps taker), not applied here (conservative)."]
    if acct.get("has_credentials") and not pc.get("ip_restricted", True):
        cc += ["- **the key has no IP restriction** (`ipRestrict=false`): restrict it to this machine before any further use."]
    rows = ans.get("9_cost_chain_by_window") or []
    if rows:
        cc += ["", "## Measured chain by window and notional (H2, P11 depth archives) — descriptive, no window chosen here", "",
               "Spread = median effective spread (full, round trip); slippage = median of sell + buy linear estimates within the book band "
               "(band upper bound alongside); fee = 2 × per-side fee. `status` compares to the spec's declared round trip at ±0.5 bps.", "",
               "| window | notional | n | fee/side | spread | slippage rt | band ub | round trip | spec declared | status | weakest |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            cc.append(f"| +{r['window_min']} min | {r['notional_usd']} $ | {r['n_events']} | {r['fee_bps']:.1f} | {r['spread_bps']:.1f} | {r['slippage_rt_bps']:.1f} | {r['slippage_rt_ub_bps'] if r['slippage_rt_ub_bps'] is not None else '—'} | **{r['round_trip_bps']:.1f}** | {r['spec_declared_round_trip_bps']:.1f} | {r['status_vs_spec']} | `{r['weakest_provenance']}` |")
        cc += ["", "The chain leaves `unknown` the moment a preregistration names one window and one notional from this table; it then reads `confirmed` "
               "or `contradicted` against the spec at `official_published` grade. This table chooses nothing and reads no return."]
    cc += ["", "## What would move each chain", "",
           "- fee → `account_actual`: only a key with 'Enable Futures' (a trading permission) can read `GET /fapi/v1/commissionRate`; the repository does not require it.",
           "- spread, slippage → measured: P11 `depth_capacity_features` derives an effective-spread proxy and slippage bounds from the "
           "Vision archives; they enter the chain as `official_published`-grade links once a preregistration names which window it uses.",
           "- The official fee may serve to **reject** a hypothesis whose gross is below 3 × the published cost. It may never serve to promote one.", ""]
    _write_atomic(out / "H2_H3_COST_CHAIN_STATUS.md", "\n".join(cc) + "\n")

    au = [f"# READ-ONLY KEY SAFETY AUDIT ({now[:19]} UTC)", "",
          "What `binance_account_readonly.py` can and cannot do, verified by tests on its own source, not by promise.", "",
          "| property | how it is enforced | test |", "|---|---|---|",
          "| no order, cancel or transfer code path | only GET is implemented; the source contains no POST / DELETE / PUT and no order endpoint (checked on the AST with docstrings removed) | `test_the_module_contains_no_order_path` |",
          f"| whitelist | {len(RO.ALLOWED)} endpoints; anything else raises `ReadOnlyViolation` before a URL is built | `test_any_endpoint_outside_the_whitelist_raises_before_a_request` |",
          "| the seven prescribed account endpoints | " + ", ".join("`GET %s`" % e for e in RO.ACCOUNT_ENDPOINTS) + " | `test_whitelist_is_exactly_the_prescribed_set_plus_two_reads` |",
          "| two extra reads, justified | `GET /fapi/v1/exchangeInfo` is public and unsigned (symbol constraints); `GET /sapi/v1/account/apiRestrictions` is the **safety probe** that tells us what the key may do — without it a trading key could not be refused | same test |",
          f"| trading / withdrawal / transfer key refused | `check_permissions` reads the probe first; any of {', '.join(RO.FORBIDDEN_PERMISSIONS)} set → refused, and every later signed call returns `refused` | `test_a_key_that_can_trade_is_refused`, `test_withdrawal_and_transfer_keys_are_refused` |",
          "| account flags are informational | `canWithdraw` / `canTrade` are account capabilities, true on any normal account whatever the key: recorded, never a refusal | `test_account_flags_are_cross_checked` |",
          "| deny-by-default permissions | any `enable*` / `permits*` flag that is true and is not a read permission refuses the key (enableFixApiTrade, future flags) | `test_unknown_permission_flags_refuse_by_default` |",
          "| structural gate | an account endpoint cannot be called before a successful apiRestrictions probe on the same client | `test_account_endpoint_before_probe_is_refused` |",
          "| no redirect | the opener refuses every 3xx: the API-key header is never re-sent to another host | `test_redirects_are_not_followed` |",
          "| sanitised errors | Binance error text loses any IP address before it reaches a report (`-2015` carries the caller IP) | `test_error_messages_lose_ip_addresses` |",
          "| half-set credentials | `BINANCE_READONLY_API_KEY` without its secret (or the reverse) is an error, never a fallback to the legacy pair | `test_half_set_readonly_pair_never_falls_back` |",
          "| no credentials → inert | signed endpoints return `no_credentials` without any request | `test_signed_endpoints_are_inert_without_credentials` |",
          "| secrets never logged, written or returned | credentials live only in the client instance; reports carry a redacted form (`abc…yz (n chars)`) | `test_credentials_are_never_exposed` |",
          "| raw account data | written under `data/account_execution/` which is gitignored; nothing account-specific is versioned | `.gitignore` `data/*` |", "",
          f"Credentials at audit time: {'present' if has else 'absent'} ({doc.get('key_redacted')}). Mode: **{mode}**.", ""]
    _write_atomic(out / "READONLY_KEY_SAFETY_AUDIT.md", "\n".join(au) + "\n")
    return {"collected": out / "ACCOUNT_EXECUTION_REALITY_COLLECTED.md", "chain": out / "H2_H3_COST_CHAIN_STATUS.md", "audit": out / "READONLY_KEY_SAFETY_AUDIT.md"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true"); ap.add_argument("--status", action="store_true"); ap.add_argument("--allow-trading-key", action="store_true")
    ap.add_argument("--from-store", action="store_true", help="regenere les rapports depuis les dumps deja collectes, sans reseau ni cle")
    a = ap.parse_args()
    if a.from_store:
        d = rebuild_from_store(); print(json.dumps(d["answers"], indent=1, ensure_ascii=False, default=str)); return
    if a.status:
        cl = RO.ReadOnlyClient()
        print(json.dumps({"has_credentials": RO.has_credentials(), "env_var": cl.env_var, "key": RO.redact(cl._key),
                          "whitelisted_endpoints": len(RO.ALLOWED), "h2_symbols": len(h2_symbols()), "h3_symbols": len(h3_symbols()),
                          "published_vip0": published_vip0()}, indent=1))
    if a.collect:
        d = collect(a.allow_trading_key); print(json.dumps(d["answers"], indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
