#!/usr/bin/env python3
"""
scripts/compute_validation_scoreboard.py
─────────────────────────────────────────────────────────────────────────────
VALIDATION_AND_FORWARD_SCOREBOARD (mission ALPHA VALIDATION FACTORY, section
26, 2026-09-02). Lit configs/validation_registry.yaml (inventaire + résultats
de validation indépendante, enrichis candidat par candidat au fur et à
mesure que chaque validation atterrit -- champs absents = pas encore
validé, jamais un zéro inventé) + configs/live_alpha_registry.yaml (pour
les alphas déjà frozen/forward) + reports/live_alpha_lab/<alpha_id>/
decisions.parquet (pour forward_age_days/forward_N_independent/
forward_net_bps réels).

Champs scoreboard PAR candidat (colonnes demandées, section 26) : alpha_id,
family, discovery_net_bps, validation_net_bps, N_validation_independent,
validated_for_forward, freeze_timestamp, historical_event_rate,
recent_event_rate, N_required, minimum_calendar_days, ETA_P50,
ETA_conservative, forward_age_days, forward_N_independent, forward_net_bps,
edge_retention, scientific_status, operational_status.

edge_retention calculé UNIQUEMENT si forward_N_independent est suffisant
(>= item 14 : 30 EARLY minimum) -- sinon INSUFFICIENT_EVIDENCE explicite,
jamais un ratio calculé sur un N trop faible pour être honnête.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VALIDATION_REGISTRY = ROOT / "configs" / "validation_registry.yaml"
LIVE_REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
OUT_PATH = ROOT / "reports" / "edge_discovery" / "validation_2026-09" / "VALIDATION_AND_FORWARD_SCOREBOARD.md"

MIN_EDGE_RETENTION_N = 30   # floor EARLY (item 14) -- en dessous, jamais de ratio calculé


def _load_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _forward_stats(alpha_id: str) -> Dict[str, Any]:
    """N_independent forward, âge, net_bps -- None si pas encore de données ou
    pas assez pour honnêtement estimer un net_bps (edge_retention gate)."""
    p = LAB_DIR / alpha_id / "decisions.parquet"
    if not p.exists():
        return {"forward_age_days": None, "forward_N_independent": None,
               "forward_net_bps": None, "forward_N_raw": 0}
    df = pd.read_parquet(p)
    if "provenance" not in df.columns:
        return {"forward_age_days": None, "forward_N_independent": None,
               "forward_net_bps": None, "forward_N_raw": 0}
    fwd = df[df["provenance"] == "FORWARD_LIVE"]
    if fwd.empty:
        return {"forward_age_days": None, "forward_N_independent": None,
               "forward_net_bps": None, "forward_N_raw": 0}

    time_col = next((c for c in ("event_time", "timestamp", "date") if c in fwd.columns), None)
    forward_age_days = None
    n_independent = None
    if time_col is not None:
        ts = pd.to_datetime(fwd[time_col], utc=True)
        earliest = ts.min()
        forward_age_days = (pd.Timestamp.now(tz="UTC") - earliest).total_seconds() / 86400.0
        try:
            from src.institutional.live_alpha_lab.episodes import decluster, summarize
            symbol_col = next((c for c in ("symbol", "asset") if c in fwd.columns), None)
            if symbol_col is not None:
                declustered = decluster(fwd.assign(**{time_col: ts}), time_col, symbol_col)
                summary = summarize(declustered)
                n_independent = summary.independent_episodes
        except Exception:
            n_independent = None

    # net_bps forward : PAS calculé ici (nécessite un label de résultat/MTM
    # réel par décision, cf note existante du scoreboard live_alpha_lab --
    # même limite documentée, pas dupliquée en silence ici).
    return {"forward_age_days": forward_age_days, "forward_N_independent": n_independent,
           "forward_net_bps": None, "forward_N_raw": len(fwd)}


def _edge_retention(historical_net_bps: Optional[float], forward_net_bps: Optional[float],
                    forward_n_independent: Optional[int]) -> str:
    if forward_n_independent is None or forward_n_independent < MIN_EDGE_RETENTION_N:
        return "INSUFFICIENT_EVIDENCE"
    if historical_net_bps in (None, 0) or forward_net_bps is None:
        return "INSUFFICIENT_EVIDENCE"
    return f"{forward_net_bps / historical_net_bps:.2f}"


def build_rows() -> list:
    validation = _load_yaml(VALIDATION_REGISTRY).get("candidates", [])
    live = {a["alpha_id"]: a for a in _load_yaml(LIVE_REGISTRY).get("alphas", [])}

    rows = []
    for cand in validation:
        cid = cand["candidate_id"]
        existing = cand.get("existing_live_alpha")
        alpha_id_for_forward = cand.get("frozen_alpha_id") or existing

        fwd = _forward_stats(alpha_id_for_forward) if alpha_id_for_forward else {
            "forward_age_days": None, "forward_N_independent": None,
            "forward_net_bps": None, "forward_N_raw": 0,
        }

        live_entry = live.get(alpha_id_for_forward, {}) if alpha_id_for_forward else {}

        historical_net_bps = cand.get("validation_net_bps") or cand.get("discovery_net_bps")
        edge_retention = _edge_retention(historical_net_bps, fwd["forward_net_bps"], fwd["forward_N_independent"])

        rows.append({
            "alpha_id": cand.get("frozen_alpha_id") or cid,
            "family": cand.get("family"),
            "discovery_net_bps": cand.get("discovery_net_bps"),
            "validation_net_bps": cand.get("validation_net_bps"),
            "N_validation_independent": cand.get("n_validation_independent"),
            "validated_for_forward": cand.get("validated_for_forward", cand.get("current_status") == "VALIDATED_FOR_FORWARD"),
            "freeze_timestamp": cand.get("freeze_timestamp") or live_entry.get("freeze_timestamp"),
            "historical_event_rate": cand.get("historical_event_rate"),
            "recent_event_rate": cand.get("recent_event_rate") or cand.get("conservative_event_rate"),
            "N_required": cand.get("n_required_statistical"),
            "minimum_calendar_days": cand.get("minimum_calendar_days"),
            "ETA_P50": cand.get("eta_p50"),
            "ETA_conservative": cand.get("eta_conservative"),
            "forward_age_days": round(fwd["forward_age_days"], 1) if fwd["forward_age_days"] else None,
            "forward_N_independent": fwd["forward_N_independent"],
            "forward_net_bps": fwd["forward_net_bps"],
            "edge_retention": edge_retention,
            "scientific_status": cand.get("scientific_status", cand.get("current_status")),
            "operational_status": cand.get("operational_status", "CODE_MISSING" if not alpha_id_for_forward else live_entry.get("operational_status")),
        })
    return rows


def render_markdown(rows: list) -> str:
    cols = ["alpha_id", "family", "discovery_net_bps", "validation_net_bps", "N_validation_independent",
           "validated_for_forward", "freeze_timestamp", "historical_event_rate", "recent_event_rate",
           "N_required", "minimum_calendar_days", "ETA_P50", "ETA_conservative", "forward_age_days",
           "forward_N_independent", "forward_net_bps", "edge_retention", "scientific_status", "operational_status"]
    lines = [
        "# VALIDATION_AND_FORWARD_SCOREBOARD",
        "",
        f"Généré : {datetime.now(timezone.utc).isoformat()}",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) if r.get(c) is not None else "—" for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = build_rows()
    md = render_markdown(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(md)
    print(f"[validation_scoreboard] {len(rows)} candidats -> {OUT_PATH}", flush=True)
    n_validated = sum(1 for r in rows if r["validated_for_forward"])
    print(f"[validation_scoreboard] {n_validated} VALIDATED_FOR_FORWARD", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
