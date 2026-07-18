#!/usr/bin/env python3
"""
scripts/audit_paper_ledger.py
─────────────────────────────────────────────────────────────────────────────
AUDIT COMPTABLE du portefeuille paper 200 k€ (comptabilité v2 réalisée).

Lecture SEULE (aucune écriture Mongo). Trois volets :
  1. Identité de conservation interne :
     value_eur = capital + (carry + basis + borrow + longs_realized + fees
                            + longs_latent) / fx
  2. Recalcul INDÉPENDANT du carry depuis les événements de funding réels
     (API publique /fapi/v1/fundingRate) sur la trajectoire de notionnels
     reconstruite depuis le journal d'événements (portfolio_events).
  3. Reconstruction des frais depuis la même trajectoire (barème déclaré :
     maker 2 bps/jambe Δ-neutre, taker 5 bps directionnel) + basis accrué
     attendu depuis les klines des contrats trimestriels.

Sorties : reports/paper_audit/AUDIT_<date>.md + .json + snapshot brut du doc.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "reports" / "paper_audit"
MAKER2, TAKER = 0.0004, 0.0005  # 2 bps × 2 jambes ; 5 bps directionnel

# Trajectoire des poids reconstruite depuis futur_ui.portfolio_events
# (OBSERVÉ — événements du 2026-07-17/18) + alloc à l'ouverture (mémoire
# projet + frais d'ouverture -68 € confirmés par l'événement 10:03).
# Chaque étape : (ts ISO, {sleeve: poids cible}), poids en fraction d'equity.
TRAIL = [
    ("2026-07-17T10:02:55Z", {"carry_ETHUSDT": 0.35, "basis_BTCUSDT": 0.28,
                              "basis_ETHUSDT": 0.22}),
    ("2026-07-17T18:02:59Z", {"carry_BTCUSDT": 0.40, "carry_ETHUSDT": 0.35,
                              "basis_BTCUSDT": 0.25, "basis_ETHUSDT": 0.0}),
    ("2026-07-18T02:15:51Z", {"carry_BTCUSDT": 0.40, "carry_ETHUSDT": 0.20,
                              "basis_BTCUSDT": 0.0,
                              "long_BTCUSDT": None, "long_ETHUSDT": None}),
    # 10:15:50 et 10:15:52 : double rebalance (marks concurrents), cibles
    # inchangées → dérive < 2 % → aucun frais attendu.
]


def fapi(path: str):
    req = urllib.request.Request("https://fapi.binance.com" + path,
                                 headers={"User-Agent": "futur-audit/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def funding_between(symbol: str, t0_ms: int, t1_ms: int):
    out, cur = [], t0_ms
    while True:
        rows = fapi(f"/fapi/v1/fundingRate?symbol={symbol}&startTime={cur}"
                    f"&endTime={t1_ms}&limit=1000")
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        if len(rows) < 1000:
            break
        cur = out[-1][0] + 1
    return out


def iso_ms(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    from pymongo import MongoClient
    db = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)["futur_ui"]
    doc = db.paper_portfolio.find_one({"_id": "main"})
    events = list(db.portfolio_events.find().sort("ts", 1))
    if doc is None or doc.get("ledger", {}).get("version") != 2:
        print("DONNÉES_INSUFFISANTES : doc absent ou ledger != v2")
        sys.exit(2)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {k: v for k, v in doc.items()}
    (OUT / f"snapshot_doc_{stamp}.json").write_text(
        json.dumps({"doc": snap, "events": [
            {k: str(v) if k == "_id" else v for k, v in e.items()}
            for e in events]}, indent=1, default=str))

    led = doc["ledger"]
    fx0 = float(doc["eur_usdt_at_init"])
    cap_eur = float(doc["capital_eur"])
    cap0 = cap_eur * fx0
    created_ms = iso_ms(doc["created_at"])
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    # ── 1. identité interne (recalcule le mark sans écrire) ──────────────────
    from src.institutional.live.paper_portfolio import live_prices, eur_usdt
    px = live_prices([c["symbol"] for c in doc.get("carry", [])]
                     + [l["symbol"] for l in doc.get("longs", [])])
    fx_now = eur_usdt()
    longs_unreal = sum(l["notional"] * (px[l["symbol"]] / l["entry"] - 1)
                       for l in doc.get("longs", [])
                       if l.get("active") and px.get(l["symbol"]))
    comp = (led["carry_accrued"] + led["basis_accrued"] + led["borrow_accrued"]
            + led["longs_realized"] + led["fees"])
    value_eur = cap_eur + (comp + longs_unreal) / fx_now
    hist_last = doc["history"][-1] if doc.get("history") else {}
    identity = {
        "carry_eur": round(led["carry_accrued"] / fx_now, 2),
        "basis_eur": round(led["basis_accrued"] / fx_now, 2),
        "borrow_eur": round(led["borrow_accrued"] / fx_now, 6),
        "longs_realized_eur": round(led["longs_realized"] / fx_now, 2),
        "fees_eur": round(led["fees"] / fx_now, 2),
        "longs_latent_eur": round(longs_unreal / fx_now, 2),
        "value_eur_recomputed": round(value_eur, 2),
        "value_eur_last_history": hist_last.get("v"),
        "fx_now": fx_now,
    }

    # ── 2. carry indépendant : funding réel × trajectoire de notionnels ──────
    # Notionnels par période (dérivés de TRAIL ; equity ≈ cap0, tolérance).
    def w_at(sleeve: str, t_ms: int) -> float:
        w = 0.0
        for ts, tgt in TRAIL:
            if iso_ms(ts) <= t_ms and sleeve in tgt and tgt[sleeve] is not None:
                w = tgt[sleeve]
        return w

    carry_expect, carry_detail = 0.0, []
    for sym in ("BTCUSDT", "ETHUSDT"):
        sleeve = f"carry_{sym}"
        opened = min((iso_ms(ts) for ts, tgt in TRAIL if tgt.get(sleeve)),
                     default=None)
        if opened is None:
            continue
        for t, r in funding_between(sym, opened + 1, now_ms):
            notion = cap0 * w_at(sleeve, t - 1)
            carry_expect += notion * r
            carry_detail.append({"symbol": sym,
                                 "funding_time": datetime.fromtimestamp(
                                     t / 1000, tz=timezone.utc).isoformat(),
                                 "rate": r, "notional_usdt": round(notion, 0),
                                 "accrual_usdt": round(notion * r, 3)})
    carry_gap = led["carry_accrued"] - carry_expect

    # ── 3. frais reconstruits depuis la trajectoire ──────────────────────────
    fees_expect, fees_lines = 0.0, []
    prev: dict = {}
    for ts, tgt in TRAIL:
        for sleeve, w in tgt.items():
            old = prev.get(sleeve, 0.0)
            if w is None:                        # longs sizés au rebalance (taker)
                new = next((l["notional"] for l in doc.get("longs", [])
                            if l["symbol"] == sleeve.split("_")[1]), 0.0)
                fee = new * TAKER
                lbl = f"{ts} {sleeve} 0→{new:.0f} USDT (taker 5 bps)"
                prev[sleeve] = new / cap0
            else:
                delta = abs(w - old) * cap0
                if delta <= 0.02 * cap0:         # anti-churn 2 %
                    continue
                rate = MAKER2 if sleeve.startswith(("carry", "basis")) else TAKER
                fee = delta * rate
                lbl = f"{ts} {sleeve} {old:.2f}E→{w:.2f}E (|Δ|={delta:,.0f} USDT)"
                prev[sleeve] = w
            fees_expect += fee
            fees_lines.append({"line": lbl, "fee_usdt": round(fee, 2)})
    fees_gap = led["fees"] + fees_expect         # led négatif, expect positif

    # ── 4. basis attendu (klines quarterly réels) ────────────────────────────
    # basis_entry ≈ (q/spot − 1) au moment de l'ouverture des sleeves (klines 1h)
    basis_expect, basis_lines = 0.0, []
    expiry_ms = iso_ms("2026-09-25T08:00:00Z")
    for sym, w_open, t_open, t_close in [
            ("BTCUSDT", 0.28, TRAIL[0][0], TRAIL[2][0]),
            ("ETHUSDT", 0.22, TRAIL[0][0], TRAIL[1][0])]:
        o_ms, c_ms = iso_ms(t_open), iso_ms(t_close)
        try:
            q = float(fapi(f"/fapi/v1/klines?symbol={sym}_260925&interval=1h"
                           f"&startTime={o_ms}&limit=1")[0][4])
            s = float(fapi(f"/fapi/v1/klines?symbol={sym}&interval=1h"
                           f"&startTime={o_ms}&limit=1")[0][4])
        except Exception:
            basis_lines.append({"symbol": sym, "note": "klines quarterly indisponibles"})
            continue
        be = q / s - 1
        days = (expiry_ms - o_ms) / 86400000
        # resize 0.28→0.25 BTC à 18:03 : approx w moyen pondéré par durée
        if sym == "BTCUSDT":
            mid = iso_ms(TRAIL[1][0])
            acc = (cap0 * 0.28 * be * (mid - o_ms) / (days * 86400000)
                   + cap0 * 0.25 * be * (c_ms - mid) / (days * 86400000))
        else:
            acc = cap0 * w_open * be * (c_ms - o_ms) / (days * 86400000)
        basis_expect += acc
        basis_lines.append({"symbol": sym, "basis_entry": round(be, 5),
                            "days": round(days, 1), "accrual_usdt": round(acc, 2)})
    basis_gap = led["basis_accrued"] - basis_expect

    # ── verdict ──────────────────────────────────────────────────────────────
    id_gap = (abs(value_eur - hist_last.get("v", value_eur))
              if hist_last.get("v") else 0.0)
    checks = {
        "identite_interne_eur": {"gap": round(id_gap, 2), "tol": 30.0,
                                 "ok": id_gap <= 30.0,  # marks non simultanés (prix bougent)
                                 "note": "recalcul vs dernier point d'historique"},
        "carry_vs_funding_api_usdt": {"ledger": round(led["carry_accrued"], 3),
                                      "attendu": round(carry_expect, 3),
                                      "gap": round(carry_gap, 3), "tol": 1.0,
                                      "ok": abs(carry_gap) <= 1.0},
        "fees_vs_bareme_usdt": {"ledger": round(led["fees"], 2),
                                "attendu": round(-fees_expect, 2),
                                "gap": round(fees_gap, 2), "tol": 5.0,
                                "ok": abs(fees_gap) <= 5.0},
        "basis_vs_klines_usdt": {"ledger": round(led["basis_accrued"], 2),
                                 "attendu": round(basis_expect, 2),
                                 "gap": round(basis_gap, 2), "tol": 2.0,
                                 "ok": abs(basis_gap) <= 2.0},
        "borrow_usdt": {"ledger": round(led["borrow_accrued"], 6),
                        "ok": abs(led["borrow_accrued"]) < 1.0,
                        "note": "gross ≤ 1×E sur la période → borrow ≈ 0 attendu"},
        "longs_realized": {"ledger": led["longs_realized"],
                           "ok": led["longs_realized"] == 0.0,
                           "note": "aucun flip OFF depuis l'ouverture (journal)"},
    }
    all_ok = all(c["ok"] for c in checks.values())
    verdict = "COMPTABILITÉ_CONFIRMÉE" if all_ok else "BUG_DE_COMPTABILITÉ"

    out = {"run": now.isoformat(), "verdict": verdict, "identity": identity,
           "checks": checks, "carry_detail": carry_detail,
           "fees_lines": fees_lines, "basis_lines": basis_lines,
           "trail_source": "futur_ui.portfolio_events (journal) + alloc ouverture",
           "anomalies": [
               "Double rebalance 2026-07-18T10:15:50/52 (marks concurrents "
               "PWA 2,5 s + timer 15 min) — cibles inchangées donc 0 frais "
               "constaté, mais lost-update possible (replace_one plein doc).",
               "Pas de journal par écriture dans le ledger (agrégats seuls) — "
               "l'audit repose sur portfolio_events + reconstruction.",
           ]}
    (OUT / f"AUDIT_{stamp}.json").write_text(json.dumps(out, indent=1, default=str))

    L = [f"# Audit comptable paper 200k — {stamp}\n",
         f"## VERDICT : {verdict}\n",
         f"Ledger (USDT) : carry {led['carry_accrued']:+.2f} · basis "
         f"{led['basis_accrued']:+.2f} · borrow {led['borrow_accrued']:+.6f} · "
         f"longs réalisés {led['longs_realized']:+.2f} · frais {led['fees']:+.2f}",
         f"\nIdentité : {json.dumps(identity, ensure_ascii=False)}",
         "\n## Contrôles\n"]
    for k, c in checks.items():
        L.append(f"- {'✅' if c['ok'] else '❌'} {k} : {json.dumps(c, ensure_ascii=False)}")
    L.append("\n## Frais reconstruits (barème déclaré)\n")
    L += [f"- {x['line']} → {x['fee_usdt']} USDT" for x in fees_lines]
    L.append(f"\nTotal attendu : {-fees_expect:.2f} USDT vs ledger {led['fees']:.2f}")
    L.append("\n## Funding encaissé (événements réels)\n")
    L += [f"- {x['funding_time']} {x['symbol']} r={x['rate']:+.6f} × "
          f"{x['notional_usdt']:,.0f} = {x['accrual_usdt']:+.3f} USDT"
          for x in carry_detail]
    L.append("\n## Anomalies / limites\n")
    L += [f"- {a}" for a in out["anomalies"]]
    (OUT / f"AUDIT_{stamp}.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
