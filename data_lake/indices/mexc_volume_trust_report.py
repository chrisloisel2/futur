#!/usr/bin/env python3
"""
mexc_volume_trust_report.py -- applique MEXC_VOLUME_TRUST_V1 (spec : reports/prereg/MEXC_VOLUME_TRUST_V1_SPEC.md) aux
tapes stockees et ecrit reports/wash_volume/. Lit UNIQUEMENT data/pre_binance/** (bougies closes <= t0), l'univers
d'evenements (t0, publication_ts), la decision de precedence P9 et la classe causale P11. Aucun rendement, aucune
donnee Binance post-t0, aucun fichier de resultat. Sorties estampillees code_sha + spec_sha256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.collectors import venue_pre_binance_paths as VP
from data_lake.collectors.mexc_pre_binance_tape import mexc_first_events
from data_lake.collectors.pre_binance_venue_tape import load_candles, other_venue_first_events
from data_lake.collectors.second_venue_pre_binance_tape import manifest_path as sv_manifest_path
from data_lake.indices import mexc_volume_trust as M
from data_lake.indices.pre_binance_features import PostT0Leak

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "wash_volume"
SPEC = ROOT / "reports" / "prereg" / "MEXC_VOLUME_TRUST_V1_SPEC.md"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
CAUSAL = ROOT / "reports" / "data_acquisition" / "H2_CAUSAL_POPULATION_MATRIX.json"
EXCLUDED_CLASSES = ("BAD_TIMESTAMP",)
COLUMNS = ["event_id", "asset", "binance_symbol", "mexc_symbol", "mexc_market", "causal_class", "status", "reason", "announcement_cut", "n_hours_used", "n_post_announcement_dropped",
           "n_before_window_dropped", "multiplier", "integrity_violations", "volume_floor", "volume_cv", "volume_range_coupling", "log10_volume_per_range", "zero_range_share",
           "volume_floor_rank", "volume_cv_rank", "volume_range_coupling_rank", "log10_volume_per_range_rank", "n_features_ranked", "volume_anomaly_rank", "wash_volume_suspect",
           "programme_like", "anticipation_ratio", "anticipation_base_hours", "second_venue", "second_venue_market", "n_common_hours", "log10_venue_volume_multiple", "coupling_gap"]


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _ms(ts: Optional[str]) -> Optional[int]:
    return int(VP.parse_ts(ts).timestamp() * 1000) if ts else None


def code_sha() -> Optional[str]:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def spec_sha256(spec: Optional[Path] = None) -> Optional[str]:
    p = Path(spec) if spec else SPEC
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _universe() -> Dict[str, Dict[str, Any]]:
    return {e["event_id"]: e for e in json.loads(UNIVERSE.read_text())["events"]} if UNIVERSE.exists() else {}


def _causal_class() -> Dict[str, str]:
    if not CAUSAL.exists():
        return {}
    return {r["event_id"]: r.get("class") for r in json.loads(CAUSAL.read_text()).get("rows", [])}


def _hourly(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return load_candles(((manifest.get("files") or {}).get("60m") or {}).get("path"))


def mexc_rows(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Une ligne par evenement MEXC-first : statut, covariables, anticipation. Pas encore de rang."""
    root = Path(root) if root else VP.STORE; uni = _universe(); cls = _causal_class(); rows = []
    for ev in mexc_first_events():
        mp = VP.manifest_path("mexc", ev["event_id"], root); man = json.loads(mp.read_text()) if mp.exists() else {"files": {}}
        t0 = _ms(ev["t0"]); pub = (uni.get(ev["event_id"]) or {}).get("publication_ts_ms"); listed = _ms(ev.get("mexc_listed_ts")); market = man.get("market") or ev.get("mexc_market") or "spot"
        base = {"event_id": ev["event_id"], "asset": ev["asset"], "binance_symbol": ev["binance_symbol"], "mexc_symbol": ev.get("mexc_symbol"), "mexc_market": market,
                "causal_class": cls.get(ev["event_id"]), "announcement_cut": pub is not None, "t0_ms": t0, "publication_ts_ms": pub, "mexc_listed_ms": listed}
        if base["causal_class"] in EXCLUDED_CLASSES:
            r = M.compute_event([], t0, pub, listed, market); r.update({"status": "NOT_COMPUTABLE", "reason": "%s: excluded by rule (manual proof needed)" % base["causal_class"]})
        else:
            try:
                r = M.compute_event(_hourly(man), t0, pub, listed, market)
            except PostT0Leak as e:                                      # jamais avale : signale, l'evenement n'est pas mesure
                r = M.compute_event([], t0, pub, listed, market); r.update({"status": "NOT_COMPUTABLE", "reason": "REJECTED post-t0 candle: " + str(e)})
        r["integrity_violations"] = r["integrity"]["n_violations"]; rows.append({**base, **r})
    return rows


def same_asset_rows(rows: List[Dict[str, Any]], root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Pour chaque evenement MEASURED avec un second venue utilisable : multiple de volume et ecart de couplage."""
    root = Path(root) if root else VP.STORE; out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mp = sv_manifest_path(r["event_id"], root)
        if not mp.exists():
            continue
        man = json.loads(mp.read_text()); usable = [a for a in man.get("attempts", []) if a.get("usable")]
        if not usable:
            out[r["event_id"]] = {"second_venue": None, "second_venue_market": None, "n_common_hours": 0, "status": man.get("status"), "attempts": len(man.get("attempts", []))}; continue
        mexc_man = json.loads(VP.manifest_path("mexc", r["event_id"], root).read_text()) if VP.manifest_path("mexc", r["event_id"], root).exists() else {"files": {}}
        try:
            mx = M.select_hours(_hourly(mexc_man), r["t0_ms"], r["window_start_ms"], r["window_end_ms"])["rows"]
        except PostT0Leak:
            mx = []
        best = None
        for a in usable:
            try:
                ot = M.select_hours(load_candles(a["path"]), r["t0_ms"], r["window_start_ms"], r["window_end_ms"])["rows"]
            except PostT0Leak:
                continue
            c = M.same_asset_control(mx, ot); c.update({"second_venue": a["venue"], "second_venue_market": a["market"], "second_venue_symbol": a["symbol"], "dated": a.get("dated")})
            if best is None or (c["status"] == "MEASURED" and (best["status"] != "MEASURED" or c["n_common_hours"] > best["n_common_hours"])):
                best = c
        out[r["event_id"]] = best or {"second_venue": None, "second_venue_market": None, "n_common_hours": 0, "status": "NOT_COMPUTABLE"}
    return out


def control_rows(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Les 24 evenements autre-place-premiere, memes definitions, pour la seule difference de place (F4)."""
    root = Path(root) if root else VP.STORE; uni = _universe(); out = []
    for ev in other_venue_first_events():
        if ev["venue"] == "mexc":
            continue
        mp = VP.manifest_path(ev["venue"], ev["event_id"], root)
        if not mp.exists():
            continue
        man = json.loads(mp.read_text())
        if man.get("role") != "control":
            continue
        t0 = _ms(ev["t0"]); pub = (uni.get(ev["event_id"]) or {}).get("publication_ts_ms")
        try:
            r = M.compute_event(_hourly(man), t0, pub, _ms(ev.get("listed_ts")), man.get("market") or ev.get("market") or "spot")
        except PostT0Leak as e:
            r = {"status": "NOT_COMPUTABLE", "reason": "REJECTED post-t0 candle: " + str(e), "log10_volume_per_range": None}
        out.append({"event_id": ev["event_id"], "asset": ev["asset"], "venue": ev["venue"], "market": man.get("market"), "status": r["status"], "reason": r.get("reason"),
                    "n_hours_used": r.get("n_hours_used"), **{k: r.get(k) for k in M.FEATURES}})
    return out


def build(root: Optional[Path] = None) -> Dict[str, Any]:
    rows = mexc_rows(root); ranks = M.rank_events({r["event_id"]: r for r in rows}); sa = same_asset_rows(rows, root); ctrl = control_rows(root)
    for r in rows:
        r.update(ranks[r["event_id"]]); s = sa.get(r["event_id"]) or {}
        r.update({"second_venue": s.get("second_venue"), "second_venue_market": s.get("second_venue_market"), "n_common_hours": s.get("n_common_hours", 0),
                  "log10_venue_volume_multiple": s.get("log10_venue_volume_multiple"), "coupling_gap": s.get("coupling_gap"), "second_venue_status": s.get("status")})
    vl = M.venue_level_difference([r["log10_volume_per_range"] for r in rows if r["status"] == "MEASURED"], [c["log10_volume_per_range"] for c in ctrl if c["status"] == "MEASURED"])
    meas = [r for r in rows if r["status"] == "MEASURED"]
    summ = {"n_events": len(rows), "by_status": dict(Counter(r["status"] for r in rows)), "reasons": dict(Counter(r["reason"] for r in rows if r["status"] != "MEASURED")),
            "n_measured": len(meas), "n_wash_volume_suspect": sum(1 for r in rows if r.get("wash_volume_suspect")), "n_programme_like": sum(1 for r in rows if r.get("programme_like")),
            "n_same_asset_measured": sum(1 for r in rows if r.get("log10_venue_volume_multiple") is not None), "same_asset_by_venue": dict(Counter("%s %s" % (r["second_venue"], r["second_venue_market"]) for r in rows if r.get("log10_venue_volume_multiple") is not None)),
            "controls_measured": sum(1 for c in ctrl if c["status"] == "MEASURED"), "controls_by_status": dict(Counter(c["status"] for c in ctrl)),
            "feature_medians_measured": {k: _median([r[k] for r in meas if r.get(k) is not None]) for k in M.FEATURES + ("anticipation_ratio", "log10_venue_volume_multiple", "coupling_gap")}}
    return {"version": M.VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_sha": code_sha(), "spec_sha256": spec_sha256(), "spec_path": "reports/prereg/MEXC_VOLUME_TRUST_V1_SPEC.md",
            "parameters": {"WINDOW_H": M.WINDOW_H, "LISTING_BURN_IN_H": M.LISTING_BURN_IN_H, "MIN_HOURS": M.MIN_HOURS, "TOP_FRACTION": M.TOP_FRACTION, "MIN_RANKED": M.MIN_RANKED, "PROGRAMME_FLOOR": M.PROGRAMME_FLOOR,
                           "PROGRAMME_CV": M.PROGRAMME_CV, "INTEGRITY_TOL": M.INTEGRITY_TOL, "WINSOR_PCT": M.WINSOR_PCT, "excluded_classes": list(EXCLUDED_CLASSES)},
            "summary": summ, "venue_level_difference": vl, "rows": rows, "controls": ctrl, "feature_policy": M.FEATURE_POLICY, "declared_limits": M.DECLARED_LIMITS,
            "no_post_t0_data": True, "no_alpha_test": True, "no_verdict": True, "no_return_computed": True}


def _median(xs: List[float]) -> Optional[float]:
    import statistics
    return round(statistics.median(xs), 4) if xs else None


def _f(x: Any) -> str:
    return "" if x is None else ("%.4f" % x if isinstance(x, float) else str(x))


def write_reports(doc: Optional[Dict[str, Any]] = None, out: Optional[Path] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    doc = doc or build(root); out = Path(out) if out else OUT; out.mkdir(parents=True, exist_ok=True); s = doc["summary"]; vl = doc["venue_level_difference"]
    _write_atomic(out / "MEXC_VOLUME_TRUST.json", json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n")
    with open(out / "MEXC_VOLUME_TRUST.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore"); w.writeheader()
        for r in doc["rows"]:
            w.writerow({k: _f(r.get(k)) for k in COLUMNS})
    md = ["# MEXC_VOLUME_TRUST_V1 — covariables de wash sur le volume MEXC d'avant Binance", "",
          "Généré %s · code `%s` · spec sha256 `%s` · descriptif, non validé, sans verdict, sans alpha, sans budget." % (doc["generated_at_utc"], (doc["code_sha"] or "?")[:12], (doc["spec_sha256"] or "?")[:12]), "",
          "## Support", "", "| statut | n |", "|---|---|"] + ["| %s | %d |" % (k, v) for k, v in sorted(s["by_status"].items())] + ["",
          "Raisons de non‑mesure : " + "; ".join("%s ×%d" % (k, v) for k, v in sorted(s["reasons"].items(), key=lambda kv: -kv[1])) if s["reasons"] else "", "",
          "## Drapeaux (fraction pré‑déclarée, jamais un filtre)", "",
          "- `wash_volume_suspect` (décile supérieur du rang d'anomalie, sur %d MEASURED) : **%d**" % (s["n_measured"], s["n_wash_volume_suspect"]),
          "- `programme_like` (plancher ≥ %.1f et CV ≤ %.1f) : **%d**" % (M.PROGRAMME_FLOOR, M.PROGRAMME_CV, s["n_programme_like"]), "",
          "## Médianes des événements MEASURED", "", "| covariable | médiane |", "|---|---|"] + ["| %s | %s |" % (k, _f(v)) for k, v in s["feature_medians_measured"].items()] + ["",
          "## Contrôle même actif, second venue, mêmes heures", "",
          "- événements avec un multiple mesuré : **%d** (%s)" % (s["n_same_asset_measured"], ", ".join("%s %d" % kv for kv in sorted(s["same_asset_by_venue"].items()))),
          "- `log10_venue_volume_multiple` médian : %s (0 = même volume horaire médian que l'autre place ; 1 = 10×)" % _f(s["feature_medians_measured"].get("log10_venue_volume_multiple")),
          "- `coupling_gap` médian : %s (négatif = le volume MEXC est moins couplé à l'amplitude que sur l'autre place)" % _f(s["feature_medians_measured"].get("coupling_gap")), "",
          "## Différence de place (F4, MEXC − 24 contrôles autre‑place‑première)", "",
          "- n : MEXC %s, contrôles %s (%s)" % (vl["n_mexc"], vl["n_control"], ", ".join("%s %d" % kv for kv in sorted(s["controls_by_status"].items()))),
          "- diff log10 : %s, SE %s, IC 95 %% %s, demi‑largeur ≈ ×%s" % (_f(vl["diff_log10"]), _f(vl["se"]), vl["ci95_log10"], vl["ci95_half_width_ratio"]),
          "- étiquette : %s" % vl["label"], "",
          "## Événements MEASURED, du plus anormal au moins anormal", "",
          "| asset | marché | h | F1 plancher | F2 CV | F3 couplage | F4 log10 vol/amp | rang | suspect | programme | anticipation | 2ᵉ place | log10 multiple | écart couplage |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted([r for r in doc["rows"] if r["status"] == "MEASURED"], key=lambda r: -(r.get("volume_anomaly_rank") or 0)):
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r["asset"], r["mexc_market"], r["n_hours_used"], _f(r["volume_floor"]), _f(r["volume_cv"]), _f(r["volume_range_coupling"]),
                  _f(r["log10_volume_per_range"]), _f(r["volume_anomaly_rank"]), r["wash_volume_suspect"], r["programme_like"], _f(r["anticipation_ratio"]),
                  ("%s %s" % (r["second_venue"], r["second_venue_market"])) if r.get("second_venue") else "—", _f(r.get("log10_venue_volume_multiple")), _f(r.get("coupling_gap"))))
    md += ["", "## Événements non mesurés", "", "| asset | statut | raison | h utilisées |", "|---|---|---|---|"] + \
          ["| %s | %s | %s | %s |" % (r["asset"], r["status"], r["reason"], r["n_hours_used"]) for r in doc["rows"] if r["status"] != "MEASURED"] + \
          ["", "## Limites déclarées", ""] + ["- " + l for l in doc["declared_limits"]] + ["", "Politique des features : `FEATURE_POLICY.md`. Spec : `%s`." % doc["spec_path"]]
    _write_atomic(out / "MEXC_VOLUME_TRUST.md", "\n".join(md) + "\n")
    pol = ["# Politique globale des features (par feature, jamais par événement)", "",
           "Un ban par événement fondé sur un score bruité serait un filtre dépendant des données : la surface survivante serait biaisée. La règle est donc globale.", "",
           "| feature | classe | autorisée comme | pourquoi |", "|---|---|---|---|"] + ["| %s | %s | **%s** | %s |" % (p["feature"], p["class"], p["allowed_as"], p["why"]) for p in doc["feature_policy"]] + \
          ["", "- `conditioning` : peut être variable de conditionnement d'une prereg (jamais plus d'une par prereg).", "- `covariate` : descriptif / contrôle de robustesse ; jamais un filtre d'inclusion.",
           "- `banned_cross_venue` : jamais comparé entre places ; rangs intra‑MEXC seulement.", "", "Aucun événement n'est exclu par cette politique. Les événements `BAD_TIMESTAMP` sont exclus par la règle P10/P11, pas par le volume."]
    _write_atomic(out / "FEATURE_POLICY.md", "\n".join(pol) + "\n")
    ctl = ["# Contrôle même actif, second venue, mêmes heures", "", "Le seul contrôle contrôlé par l'actif : pour un même jeton, les mêmes heures de W sur MEXC et sur une autre place.", "",
           "| asset | MEXC | 2ᵉ place | datée | h communes | log10 multiple | couplage MEXC | couplage autre | écart |", "|---|---|---|---|---|---|---|---|---|"]
    sa = same_asset_rows(doc["rows"], root)
    for r in sorted(doc["rows"], key=lambda r: -(r.get("log10_venue_volume_multiple") if r.get("log10_venue_volume_multiple") is not None else -99)):
        c = sa.get(r["event_id"]) or {}
        if c.get("status") == "MEASURED":
            ctl.append("| %s | %s | %s %s | %s | %d | %s | %s | %s | %s |" % (r["asset"], r["mexc_market"], c["second_venue"], c["second_venue_market"], c.get("dated"), c["n_common_hours"],
                                                                       _f(c["log10_venue_volume_multiple"]), _f(c["coupling_mexc"]), _f(c["coupling_other"]), _f(c["coupling_gap"])))
    ctl += ["", "Sans second venue utilisable : %d événements (%s)." % (sum(1 for r in doc["rows"] if (sa.get(r["event_id"]) or {}).get("status") != "MEASURED"),
            ", ".join("%s ×%d" % kv for kv in Counter((sa.get(r["event_id"]) or {}).get("status") or "no_manifest" for r in doc["rows"] if (sa.get(r["event_id"]) or {}).get("status") != "MEASURED").items())),
            "", "Lecture : un multiple de 1 (10×) avec un écart de couplage négatif est ce qu'un programme produirait ; c'est aussi ce qu'une place à frais nuls et à base d'utilisateurs différente produit. Descriptif."]
    _write_atomic(out / "SAME_ASSET_CROSS_VENUE_CONTROL.md", "\n".join(ctl) + "\n")
    return {"n": len(doc["rows"]), "by_status": s["by_status"], "suspect": s["n_wash_volume_suspect"], "programme_like": s["n_programme_like"], "same_asset": s["n_same_asset_measured"], "out": str(out)}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--reports", action="store_true"); a = ap.parse_args()
    if a.reports:
        print(json.dumps(write_reports(), indent=1, ensure_ascii=False))
    else:
        d = build(); print(json.dumps({"summary": d["summary"], "venue_level_difference": d["venue_level_difference"]}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
