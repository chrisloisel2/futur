#!/usr/bin/env python3
"""
p1_payer_discovery/run.py -- ou le cout reel devient-il assez bas pour qu'un edge brut survive ?

Mesure autonome. N'appelle PAS research_kernel.run_mechanism, n'inscrit aucun essai :
il n'y a pas de cible, donc pas de regard. Sorties :
    mechanisms/p1_payer_discovery/results/metrics.json, verdict.md
    reports/mechanisms/p1_payer_discovery/venue_cost_table.{md,json}
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mechanisms"))
from _common.microstructure import load_bbo_grid, raw_dir  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "mechanisms" / "p1_payer_discovery_v1" / "results"
REP = ROOT / "reports" / "mechanisms" / "p1_payer_discovery"
VENUES = ["binance", "okx", "hyperliquid"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DATES = ["2026-09-07", "2026-09-08", "2026-09-09"]
CTVAL = {"okx": {"BTCUSDT": 0.01, "ETHUSDT": 0.1, "SOLUSDT": 1.0}}   # endpoint public /api/v5/public/instruments
FILL_WINDOWS = [5, 30, 60]
AS_WINDOWS = [5, 30]
STEP = 10                     # echantillonnage des instants de pose, en secondes
BPS = 1e4

# Baremes PUBLIES : charges depuis le manifeste d'audit (P1.1), jamais codes en dur.
# Trois classes y sont separees : official / account_actual / third_party_fallback.
FEE_MANIFEST = ROOT / "data_lake" / "manifests" / "published_fee_schedules_2026-09-10.json"
_MAN = json.loads(FEE_MANIFEST.read_text())
FEES, THRESHOLDS, SOURCES, FEE_CLASS = {}, {}, {}, {}
for _v, _d in _MAN["venues"].items():
    _ps = _d["published_schedule"]
    FEES[_v] = {"vip0": {"maker": _ps["vip0"]["maker"], "taker": _ps["vip0"]["taker"]},
                "best": {"maker": _ps["best"]["maker"], "taker": _ps["best"]["taker"], "tier": _ps["best"]["tier"]}}
    FEE_CLASS[_v] = {"vip0": _ps["vip0"]["source_class"], "best": _ps["best"]["source_class"], "best_final": bool(_ps["best"].get("final"))}
    _th = _d.get("thresholds", {})
    THRESHOLDS[_v] = _th.get("summary") or "; ".join(f"{k}: {v.get('value', v) if isinstance(v, dict) else v}" for k, v in _th.items() if k != "source_class")[:220]
    SOURCES[_v] = sorted({u for blk in (_ps["vip0"], _ps["best"]) for u in blk.get("sources", [])})
# Les bruts observes par P0 (verdicts COST_WALL), la reference de la decision.
GROSS_P0 = {"microstructure": 0.78, "cross_exchange": 1.97}
WALL = 3.0


def read_trades(venue, symbol, date):
    d = raw_dir(DATA, "trades", venue, symbol) / ("date=%s" % date)
    frames = []
    for f in sorted(d.glob("events-*.jsonl.gz")):
        try:
            frames.append(pd.read_json(f, lines=True, compression="gzip")[["event_ts_ns", "price", "qty", "side"]])
        except Exception:
            continue
    if not frames:
        return None
    t = pd.concat(frames, ignore_index=True).sort_values("event_ts_ns").reset_index(drop=True)
    t["price"] = t["price"].astype(float); t["qty"] = t["qty"].astype(float)
    return t


EXACT_HOURS = [12, 13]      # echantillon par jour pour la mesure contre la cote EXACTE


def exact_sample(venue, symbol):
    """Demi-spread effectif contre la cote prevalant a l'instant du trade, sur EXACT_HOURS
    de chaque jour. Calibre le 2026-09-10 sur Binance BTC : la methode mid-de-grille-1s
    donnait 0,52 bps de mediane la ou la cote exacte donne 0,07 -- un artefact de
    resolution x7. Le plancher taker utilise la MOYENNE exacte : la queue (balayages,
    59 % des trades seulement au touch) est un cout reel pour un taker."""
    eff, touch, half = [], [], []
    for date in DATES:
        bdir = raw_dir(DATA, "bbo", venue, symbol) / ("date=%s" % date)
        tdir = raw_dir(DATA, "trades", venue, symbol) / ("date=%s" % date)
        for h in EXACT_HOURS:
            fb, ft = bdir / ("events-%02d.jsonl.gz" % h), tdir / ("events-%02d.jsonl.gz" % h)
            if not (fb.exists() and ft.exists()):
                continue
            try:
                b = pd.read_json(fb, lines=True, compression="gzip")[["event_ts_ns", "bid_price", "ask_price"]].sort_values("event_ts_ns")
                t = pd.read_json(ft, lines=True, compression="gzip")[["event_ts_ns", "price"]].sort_values("event_ts_ns")
            except Exception:
                continue
            bt = b["event_ts_ns"].to_numpy(np.int64); bid = b["bid_price"].to_numpy(float); ask = b["ask_price"].to_numpy(float)
            tt = t["event_ts_ns"].to_numpy(np.int64); px = t["price"].to_numpy(float)
            i = np.clip(np.searchsorted(bt, tt, side="right") - 1, 0, len(bt) - 1)
            mid = 0.5 * (bid[i] + ask[i]); ok = np.isfinite(mid) & (mid > 0)
            eff.append(np.abs(px[ok] - mid[ok]) / mid[ok] * BPS)
            half.append((ask[i][ok] - bid[i][ok]) / 2 / mid[ok] * BPS)
            touch.append(np.isclose(px[ok], bid[i][ok]) | np.isclose(px[ok], ask[i][ok]))
    if not eff:
        return {}
    e, hs, tc = np.concatenate(eff), np.concatenate(half), np.concatenate(touch)
    return {"exact_sample_hours": EXACT_HOURS, "exact_n_trades": int(len(e)),
            "quoted_half_spread_bps_median": float(np.median(hs)),
            "effective_half_spread_exact_bps_median": float(np.median(e)),
            "effective_half_spread_exact_bps_mean": float(e.mean()),
            "effective_half_spread_exact_bps_p90": float(np.percentile(e, 90)),
            "share_trades_at_touch": float(tc.mean())}


def measure(venue, symbol):
    grid = load_bbo_grid(DATA, venue, symbol, DATES, grid_ms=1000)
    if grid is None or grid.empty:
        return None
    grid = grid.sort_values("timestamp").reset_index(drop=True)
    g_ns = grid["timestamp"].astype("int64").to_numpy()
    bid, ask, bq, aq, mid = (grid[c].to_numpy(float) for c in ("bid", "ask", "bid_qty", "ask_qty", "mid"))
    mult = CTVAL.get(venue, {}).get(symbol, 1.0)
    out = {
        "venue": venue, "symbol": symbol, "dates": DATES, "grid_rows": int(len(grid)),
        "spread_bps_median": float(np.nanmedian(grid["spread_bps"])), "spread_bps_p95": float(np.nanpercentile(grid["spread_bps"], 95)),
        "latency_ms_median": float(np.nanmedian(grid["latency_ms"])), "latency_ms_p95": float(np.nanpercentile(grid["latency_ms"], 95)),
        "top_notional_usd_median": float(np.nanmedian(0.5 * (bq * bid + aq * ask) * mult)),
        "size_unit": "contracts x ctVal" if venue == "okx" else "coin",
    }
    # ---- trades : half-spread effectif, adverse selection, probabilite de fill
    eff, adv, fills = [], {w: [] for w in AS_WINDOWS}, {w: [] for w in FILL_WINDOWS}
    n_tr = 0
    for date in DATES:
        tr = read_trades(venue, symbol, date)
        if tr is None or tr.empty:
            continue
        n_tr += len(tr)
        ts = tr["event_ts_ns"].to_numpy(np.int64); px = tr["price"].to_numpy(); qty = tr["qty"].to_numpy()
        sell = (tr["side"].astype(str).str.lower() == "sell").to_numpy()
        # mid au moment de la transaction (derniere cote <= t)
        i0 = np.clip(np.searchsorted(g_ns, ts, side="right") - 1, 0, len(g_ns) - 1)
        m0 = mid[i0]
        ok = np.isfinite(m0) & (m0 > 0)
        eff.append(np.abs(px[ok] - m0[ok]) / m0[ok] * BPS)
        # adverse selection du passif : perte = mouvement du mid dans le sens de l'agresseur
        half0 = (ask[i0] - bid[i0]) / 2 / m0 * BPS                 # demi-spread cote au fill
        for w in AS_WINDOWS:
            i1 = np.clip(np.searchsorted(g_ns, ts + w * 1_000_000_000, side="right") - 1, 0, len(g_ns) - 1)
            m1 = mid[i1]; k = ok & np.isfinite(m1)
            # perte nette du passif = (prix de fill vs mid futur) : elle INCLUT le demi-spread
            # gagne au fill. On la decompose : derive du mid (perdue) - demi-spread (gagne).
            loss = np.where(sell[k], px[k] - m1[k], m1[k] - px[k]) / px[k] * BPS
            adv[w].append(np.column_stack([loss, loss + half0[k]]))   # [net, derive]
        # probabilite de fill d'un ordre passif au bid, FIN DE FILE : rempli si le volume
        # vendeur au prix <= bid(t) dans (t, t+T] atteint la taille en file bid_qty(t)
        day0 = int(pd.Timestamp(date, tz="UTC").value); day1 = day0 + 86_400_000_000_000
        sample = np.arange(day0, day1, STEP * 1_000_000_000, dtype=np.int64)
        gi = np.clip(np.searchsorted(g_ns, sample, side="right") - 1, 0, len(g_ns) - 1)
        valid = (g_ns[gi] >= day0) & np.isfinite(bid[gi]) & (bq[gi] > 0)
        sample, gi = sample[valid], gi[valid]
        sell_ts = ts[sell]; sell_px = px[sell]; sell_q = qty[sell]
        for w in FILL_WINDOWS:
            lo = np.searchsorted(sell_ts, sample, side="right"); hi = np.searchsorted(sell_ts, sample + w * 1_000_000_000, side="right")
            got = np.zeros(len(sample), bool)
            for j in range(len(sample)):
                if hi[j] > lo[j]:
                    seg = slice(lo[j], hi[j])
                    got[j] = sell_q[seg][sell_px[seg] <= bid[gi[j]]].sum() >= bq[gi[j]]
            fills[w].append(got)
    if n_tr:
        e = np.concatenate(eff)
        out.update({"n_trades": int(n_tr),
                    "effective_half_spread_bps_median": float(np.median(e)), "effective_half_spread_bps_mean": float(e.mean())})
        for w in AS_WINDOWS:
            a = np.concatenate(adv[w])
            out[f"maker_net_loss_bps_mean_{w}s"] = float(a[:, 0].mean())        # derive - demi-spread gagne
            out[f"maker_drift_loss_bps_mean_{w}s"] = float(a[:, 1].mean())      # derive seule
            out[f"adverse_selection_bps_mean_{w}s"] = out[f"maker_net_loss_bps_mean_{w}s"]   # compat
        out["half_spread_earned_at_fill_bps_mean"] = float(out["maker_drift_loss_bps_mean_30s"] - out["maker_net_loss_bps_mean_30s"])
        for w in FILL_WINDOWS:
            f = np.concatenate(fills[w]); out[f"fill_prob_end_of_queue_{w}s"] = float(f.mean()); out[f"n_samples_fill_{w}s"] = int(len(f))
    out.update(exact_sample(venue, symbol))
    return out


def _f(x, nd=2):
    """Formatage tolerant : None / NaN -> tiret, jamais une exception dans un rapport."""
    try:
        return "—" if x is None or not np.isfinite(float(x)) else f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def floors(m):
    """Couts aller-retour (bps) par mode, au VIP0 et au meilleur tier publie."""
    v = m["venue"]; f = FEES[v]; res = {}
    eff2 = 2 * m.get("effective_half_spread_exact_bps_mean",
                     m.get("effective_half_spread_bps_median", m["spread_bps_median"] / 2))   # taker : deux traversees, moyenne EXACTE
    as30 = 2 * m.get("adverse_selection_bps_mean_30s", float("nan"))                    # maker : subi sur les deux jambes
    for tier in ("vip0", "best"):
        mk, tk = f[tier]["maker"], f[tier]["taker"]
        res[tier] = {"taker_round_trip_bps": None if tk is None else 2 * tk + eff2,
                     "maker_round_trip_bps": 2 * mk + as30,
                     "maker_fill_prob_30s": m.get("fill_prob_end_of_queue_30s")}
    return res


def main():
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True); REP.mkdir(parents=True, exist_ok=True)
    rows = []
    if "--recompute" in sys.argv:
        # Les mesures sont couteuses, les hypotheses de frais ne le sont pas : on reprend les
        # lignes mesurees et on recalcule planchers, decision et tables avec les FEES courants.
        rows = json.loads((OUT / "metrics.json").read_text())["rows"]
        for m in rows:
            m["floors"] = floors(m)
        print(f"  --recompute : {len(rows)} lignes mesurees reprises, frais et decision recalcules", flush=True)
    for v in ([] if rows else VENUES):
        for s in SYMBOLS:
            print(f"  mesure {v} {s} ...", flush=True)
            m = measure(v, s)
            if m is None:
                print("    (aucune donnee)"); continue
            m["floors"] = floors(m); rows.append(m)
            print(f"    spread {m['spread_bps_median']:.2f} bps | lat {m['latency_ms_median']:.0f} ms | eff half-spread "
                  f"{m.get('effective_half_spread_bps_median', float('nan')):.2f} | AS30 {m.get('adverse_selection_bps_mean_30s', float('nan')):.2f} "
                  f"| P(fill 30s) {m.get('fill_prob_end_of_queue_30s', float('nan')):.2f}  [{time.time()-t0:.0f}s]", flush=True)
    # ---- decision, mecanique
    need = {k: g / WALL for k, g in GROSS_P0.items()}
    verdict = {"reopen": {}, "gross_required_bps": {}, "conditions": []}
    # cross-exchange : la jambe disloquee se PREND (taker), l'autre peut se poser (maker)
    xex = {}
    for tier in ("vip0", "best"):
        for r in rows:
            f = FEES[r["venue"]][tier]
            if f["taker"] is None:
                continue
            tk_leg = f["taker"] + r.get("effective_half_spread_exact_bps_mean", r["spread_bps_median"] / 2)
            mk_leg = f["maker"] + r.get("maker_net_loss_bps_mean_30s", float("nan"))
            xex[f"{r['venue']}/{r['symbol']}/{tier}"] = {"taker_leg_bps": tk_leg, "maker_leg_bps": mk_leg,
                                                           "round_trip_two_legs_bps": 2 * (tk_leg + mk_leg)}
    verdict["cross_exchange_two_legs"] = xex
    for r in rows:
        for tier in ("vip0", "best"):
            for mode in ("taker", "maker"):
                c = r["floors"][tier][f"{mode}_round_trip_bps"]
                if c is None or not np.isfinite(c):
                    continue
                key = f"{r['venue']}/{r['symbol']}/{tier}/{mode}"
                verdict["gross_required_bps"][key] = round(WALL * c, 3)
                verdict["reopen"][key] = {"microstructure": bool(c <= need["microstructure"]),
                                          "cross_exchange_one_leg": bool(c <= need["cross_exchange"] / 2)}
    micro_vip0 = [k for k, v in verdict["reopen"].items() if "/vip0/" in k and v["microstructure"]]
    micro_best = [k for k, v in verdict["reopen"].items() if "/best/" in k and v["microstructure"]]
    # verdict FINAL : seules les venues dont le tier 'best' est de source OFFICIELLE y entrent
    micro_best_final = [k for k in micro_best if FEE_CLASS[k.split("/")[0]]["best_final"]]
    micro_best_fallback = [k for k in micro_best if k not in micro_best_final]
    xex_ok = [k for k, v in xex.items() if np.isfinite(v["round_trip_two_legs_bps"]) and v["round_trip_two_legs_bps"] <= GROSS_P0["cross_exchange"] / WALL]
    any_micro, any_xex = bool(micro_best), bool(xex_ok)
    for k in micro_best:
        r = next(x for x in rows if k.startswith(f"{x['venue']}/{x['symbol']}/"))
        verdict["conditions"].append({"key": k, "tier": FEES[r["venue"]]["best"]["tier"],
                                      "fill_prob_30s_end_of_queue": r.get("fill_prob_end_of_queue_30s"),
                                      "half_spread_earned_bps": r.get("half_spread_earned_at_fill_bps_mean"),
                                      "drift_loss_bps_30s": r.get("maker_drift_loss_bps_mean_30s")})
    best = sorted(((r["floors"]["best"]["maker_round_trip_bps"], r["venue"], r["symbol"]) for r in rows if np.isfinite(r["floors"]["best"]["maker_round_trip_bps"])))[:3]
    metrics = {"measured_at": pd.Timestamp.utcnow().isoformat(), "dates": DATES, "step_seconds": STEP,
               "fees_published_bps": FEES, "fee_tier_thresholds": THRESHOLDS, "fee_sources": SOURCES, "fees_measured_on_account": False,
               "bybit": "no BBO/trades on disk: published fees only, no measured spread/latency",
               "gross_reference_bps": GROSS_P0, "cost_wall_multiple": WALL, "floor_needed_bps": need,
               "fee_manifest": {"path": str(FEE_MANIFEST.relative_to(ROOT)), "sha256": hashlib.sha256(FEE_MANIFEST.read_bytes()).hexdigest(),
                                "source_class": FEE_CLASS},
               "rows": rows, "decision": {"microstructure_reopen_vip0": bool(micro_vip0), "microstructure_reopen_best_tier": any_micro,
                                          "microstructure_reopen_best_tier_FINAL_official_only": bool(micro_best_final),
                                          "microstructure_reopen_keys_best_final": micro_best_final,
                                          "microstructure_reopen_keys_best_fallback_non_final": micro_best_fallback,
                                          "microstructure_reopen_keys_best": micro_best, "cross_exchange_reopen": any_xex,
                                          "cross_exchange_reopen_keys": xex_ok, "conditions": verdict["conditions"],
                                          "cross_exchange_two_legs": xex,
                                          "lowest_maker_round_trip_best_tier": best}, "elapsed_s": round(time.time() - t0)}
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    (REP / "venue_cost_table.json").write_text(json.dumps(metrics, indent=2, default=float))
    # ---- table markdown
    L = ["# P1 — table des coûts par venue (mesurée sur L1 + trades, 3 jours : 2026-09-07 → 09-09)\n",
         "Frais : barèmes **publiés** chargés depuis `data_lake/manifests/published_fee_schedules_2026-09-10.json` (bps par côté ; classes official / third_party_fallback ; les frais **réels du compte** ne sont pas encore lus — `scripts/fetch_account_fees.py`). Bybit : frais seuls, aucune donnée sur disque.\n",
         "| venue | symbole | spread coté méd. | lat. méd. | ½-spread eff. exact méd. / moy. | au touch | maker : dérive − ½-spread gagné (30 s) | P(fill 30 s) | RT taker VIP0 / best | RT maker VIP0 / best |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f0, f1 = r["floors"]["vip0"], r["floors"]["best"]
        fmt = lambda x: "—" if x is None or not np.isfinite(x) else f"{x:.2f}"
        L.append(f"| {r['venue']} | {r['symbol']} | {r['spread_bps_median']:.2f} bps | {r['latency_ms_median']:.0f} ms | "
                 f"{fmt(r.get('effective_half_spread_exact_bps_median'))} / {fmt(r.get('effective_half_spread_exact_bps_mean'))} | "
                 f"{fmt(r.get('share_trades_at_touch'))} | {fmt(r.get('maker_drift_loss_bps_mean_30s'))} − {fmt(r.get('half_spread_earned_at_fill_bps_mean'))} = {fmt(r.get('maker_net_loss_bps_mean_30s'))} | "
                 f"{fmt(r.get('fill_prob_end_of_queue_30s'))} | {fmt(f0['taker_round_trip_bps'])} / {fmt(f1['taker_round_trip_bps'])} | "
                 f"{fmt(f0['maker_round_trip_bps'])} / {fmt(f1['maker_round_trip_bps'])} |")
    L += ["", "| venue | maker VIP0 / best | taker VIP0 / best | tier « best » | source VIP0 / best | ce que le tier exige |", "|---|---|---|---|---|---|"]
    for v, f in FEES.items():
        bt = "—" if f["best"]["taker"] is None else "%.2f" % f["best"]["taker"]
        L.append(f"| {v} | {f['vip0']['maker']:.1f} / {f['best']['maker']:.2f} | {f['vip0']['taker']:.1f} / {bt} | {f['best']['tier']} | {FEE_CLASS[v]['vip0']} / {FEE_CLASS[v]['best']}{'' if FEE_CLASS[v]['best_final'] else ' (NON FINAL)'} | {THRESHOLDS[v]} |")
    L += ["", f"## Décision (mur ×{WALL:.0f}, bruts de référence P0 : microstructure {GROSS_P0['microstructure']} bps, cross-exchange {GROSS_P0['cross_exchange']} bps)", "",
          f"- plancher requis : ≤ **{need['microstructure']:.2f} bps** aller-retour (microstructure), ≤ **{need['cross_exchange']/2:.2f} bps par jambe** (cross-exchange)",
          (f"- **microstructure au VIP0 : NON** — aucun mode, aucune venue ne passe sous {need['microstructure']:.2f} bps à frais publics de base" if not micro_vip0 else f"- microstructure au VIP0 : OUI ({', '.join(micro_vip0)})"),
          f"- **microstructure au meilleur tier publié, verdict FINAL (sources officielles seules) : {'OUI' if micro_best_final else 'NON'}**" + (f" — {', '.join(micro_best_final)}" if micro_best_final else ""),
          (f"- non final (tier 'best' de source tierce, non confirmé officiellement) : {', '.join(micro_best_fallback)}" if micro_best_fallback else "- aucune configuration de repli tiers en jeu"),
          ("- conditions de la réouverture : maker-only, tiers à rebate (" + "; ".join(f"{c['key']} : {c['tier']}, P(fill 30 s) {_f(c['fill_prob_30s_end_of_queue'])}, ½-spread gagné {_f(c['half_spread_earned_bps'])} bps contre dérive perdue {_f(c['drift_loss_bps_30s'])} bps" for c in verdict["conditions"]) + ")") if verdict["conditions"] else "- conditions : aucune configuration ne rouvre",
          f"- **cross-exchange (jambe disloquée prise en taker + jambe posée en maker) : {'OUI' if any_xex else 'NON'}** — plus bas aller-retour deux jambes : " + ", ".join(f"{k} {v['round_trip_two_legs_bps']:.2f} bps" for k, v in sorted(((k, v) for k, v in xex.items() if np.isfinite(v['round_trip_two_legs_bps'])), key=lambda kv: kv[1]['round_trip_two_legs_bps'])[:3]),
          "- **lecture** : un plancher maker négatif n'est pas un edge, c'est la marge du market maker — le demi-spread gagné au fill — qui n'existe que si l'ordre est servi et n'est disponible qu'aux tiers à rebate ; la réouverture change l'objet testé (exécution passive), elle ne ressuscite pas le signal de P0.",
          "- plus bas aller-retour maker au meilleur tier publié : " + ", ".join(f"{v}/{s} {c:.2f} bps" for c, v, s in best),
          "", "## Brut requis pour passer le mur, par venue / mode (bps)", "", "| clé | brut requis |", "|---|---|"]
    L += [f"| {k} | {x:.2f} |" for k, x in sorted(verdict["gross_required_bps"].items(), key=lambda kv: kv[1])]
    L += ["", "Méthode : demi-spread effectif contre la cote EXACTE prévalant au trade, sur 2 h par jour (12-13 h UTC) ; la méthode mid-de-grille-1s le gonflait ×7 (calibré). Limites : L1 seulement (pas de profondeur au-delà du touch) ; fin de file supposée pour P(fill) ; 3 jours ; tailles OKX converties par ctVal public.", ""]
    (REP / "venue_cost_table.md").write_text("\n".join(L))
    (OUT / "verdict.md").write_text("# p1_payer_discovery — MEASUREMENT\n\n" + "\n".join(L[-14:]))
    print(f"-> {REP/'venue_cost_table.md'}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
