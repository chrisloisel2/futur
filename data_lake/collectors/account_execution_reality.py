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
from typing import Any, Dict, List, Optional

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
            out["endpoints"][name] = {"status": perm.get("status", "no_credentials"), "endpoint": "GET " + path,
                                      "reason": perm.get("reason"), "would_give": RO.ALLOWED[path][2]}
            continue
        r = cl.get(path, params)
        out["endpoints"][name] = {"status": r["status"], "endpoint": "GET " + path, "error": r.get("error"),
                                  "n_rows": (len(r["data"]) if isinstance(r.get("data"), list) else None)}
        if r["status"] == "ok":
            STORE.mkdir(parents=True, exist_ok=True)
            _write_atomic(STORE / ("%s.json" % name), json.dumps(r["data"], indent=1, default=str))   # hors depot
            if name == "commission_rate":
                out["actual_fees"] = {"symbol": params["symbol"], "maker_bps": float(r["data"]["makerCommissionRate"]) * 1e4,
                                      "taker_bps": float(r["data"]["takerCommissionRate"]) * 1e4}
            if name == "futures_account":
                out["fee_tier"] = r["data"].get("feeTier")
    return out


def build_costs(pub: Dict[str, Any], acct: Dict[str, Any]) -> Dict[str, Any]:
    """La chaine de cout, avec la provenance de chaque maillon, et la comparaison aux hypotheses H2/H3."""
    actual = acct.get("actual_fees")
    if actual:
        fee, prov = actual["taker_bps"], "account_actual"
    elif pub.get("taker_bps") is not None:
        fee, prov = float(pub["taker_bps"]), "official_published"
    else:
        fee, prov = 5.0, "declared"
    out = {"fee_bps_per_side": fee, "fee_provenance": prov, "comparisons": {}}
    for which in ("H2", "H3"):
        sc = spec_cost(which)
        if not sc:
            continue
        cost = S.build_cost("perp", fee, prov, sc["spread_bps"], "declared", sc["slippage_bps"], "declared", sc["n_legs"],
                            note="spread and slippage are still the values declared in the spec; only the fee has a stronger provenance")
        out["comparisons"][which] = {"spec_declared_round_trip_bps": sc["declared_round_trip_bps"], "cost_chain": cost,
                                     **S.compare_to_assumption(cost, sc["declared_round_trip_bps"])}
    return out


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
    theo = answers["6_what_remains_theoretical"]
    if not has:
        theo.append("every account figure: no read-only API key in the environment (%s or %s)" % (RO.ENV_KEY, RO.FALLBACK_ENV[0]))
    elif not usable:
        theo.append("every account figure: the key was refused — %s" % acct["permission_check"].get("reason"))
    theo.append("spread and slippage in both cost chains are the values declared in the specs; P6 depth archives can measure them, this phase does not")
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
    return write_reports(pub, acct, cons, costs, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true"); ap.add_argument("--status", action="store_true"); ap.add_argument("--allow-trading-key", action="store_true")
    a = ap.parse_args()
    if a.status:
        cl = RO.ReadOnlyClient()
        print(json.dumps({"has_credentials": RO.has_credentials(), "env_var": cl.env_var, "key": RO.redact(cl._key),
                          "whitelisted_endpoints": len(RO.ALLOWED), "h2_symbols": len(h2_symbols()), "h3_symbols": len(h3_symbols()),
                          "published_vip0": published_vip0()}, indent=1))
    if a.collect:
        d = collect(a.allow_trading_key); print(json.dumps(d["answers"], indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
