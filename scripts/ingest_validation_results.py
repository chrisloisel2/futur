#!/usr/bin/env python3
"""
scripts/ingest_validation_results.py
─────────────────────────────────────────────────────────────────────────────
Alpha Validation Factory — WAVE 2 (2026-09-03). Enrichit
configs/validation_registry.yaml depuis les RESULTS.json écrits par les
validateurs sous reports/edge_discovery/validation_2026-09/<CANDIDATE_ID>/.

Édition TEXTUELLE bloc par bloc (le registre est un YAML très commenté : un
dump yaml perdrait tous les commentaires). Pour chaque candidat trouvé :
  - `current_status` remplacé par le verdict (vocabulaire registre, mapping
    documenté ci-dessous pour les tags secondaires round 4) ;
  - anciens champs de résultat retirés puis ré-écrits (idempotent) ;
  - jamais un champ inventé : seuls les champs PRÉSENTS dans RESULTS.json
    sont écrits, les absents restent absents (rendus « — » par le scoreboard).

Vocabulaire `current_status` du registre : VALIDATED_FOR_FORWARD / REJECTED /
NEEDS_MORE_RESEARCH / DATA_BLOCKED / IMPLEMENTATION_BLOCKED. Tags round 4
mappés : UNCONFIRMABLE_IN_HORIZON -> NEEDS_MORE_RESEARCH (edge peut-être réel,
ETA rédhibitoire), COST_FRAGILE / REGIME_DEPENDENT / WEAK / DEAD -> REJECTED,
DATA_LIMITED -> DATA_BLOCKED. Le verdict brut est conservé dans
`validation_verdict_raw`.

Usage : .venv/bin/python scripts/ingest_validation_results.py [--dry-run]
        [--only CANDIDATE_ID ...] [--scoreboard]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs" / "validation_registry.yaml"
VALIDATION_DIR = ROOT / "reports" / "edge_discovery" / "validation_2026-09"

STATUS_MAP = {
    "VALIDATED_FOR_FORWARD": "VALIDATED_FOR_FORWARD",
    "REJECTED": "REJECTED",
    "NEEDS_MORE_RESEARCH": "NEEDS_MORE_RESEARCH",
    "DATA_BLOCKED": "DATA_BLOCKED",
    "IMPLEMENTATION_BLOCKED": "IMPLEMENTATION_BLOCKED",
    "UNCONFIRMABLE_IN_HORIZON": "NEEDS_MORE_RESEARCH",
    "PROMISING_NEEDS_VALIDATION": "NEEDS_MORE_RESEARCH",
    "COST_FRAGILE": "REJECTED",
    "REGIME_DEPENDENT": "REJECTED",
    "WEAK": "REJECTED",
    "DEAD": "REJECTED",
    "DATA_LIMITED": "DATA_BLOCKED",
}

# Champs copiés tels quels (ordre d'écriture). Tout autre champ est ignoré.
RESULT_FIELDS = [
    "validated_for_forward", "confirmable_in_horizon", "sign_correction_required",
    "validation_verdict_raw", "secondary_tag",
    "discovery_net_bps", "validation_net_bps", "validation_net_bps_stress28", "pf",
    "n_raw", "n_independent_L1", "n_independent_L2", "n_independent_L3",
    "n_validation_independent", "t_stat_declustered", "bootstrap_ci95",
    "ex_best_year_net_bps", "year_by_year",
    "historical_event_rate", "recent_event_rate", "conservative_event_rate",
    "n_required_statistical", "minimum_calendar_days", "eta_p50", "eta_conservative",
    "capacity_note", "overlap_with_existing_live", "long_only_leg",
    "recommended_next_step", "validation_caveats", "validation_report",
    "validation_ingested_at",
]
MANAGED_KEYS = set(RESULT_FIELDS)

BLOCK_START = re.compile(r"^  - candidate_id: (\S+)\s*$")
BLOCK_END = re.compile(r"^  - candidate_id: |^  # ═")


def _fmt_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return json.dumps(json.dumps(v, ensure_ascii=False, default=str), ensure_ascii=False)


def load_results() -> Dict[str, dict]:
    """RESULTS.json peut être un dict (1 candidat), une liste de dicts, ou un dict
    keyed par candidate_id. Retourne {candidate_id: result}."""
    out: Dict[str, dict] = {}
    for p in sorted(VALIDATION_DIR.glob("*/RESULTS.json")):
        try:
            raw = json.loads(p.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[ingest] SKIP {p}: JSON invalide ({e})", file=sys.stderr)
            continue
        items: List[dict] = []
        if isinstance(raw, list):
            items = [x for x in raw if isinstance(x, dict)]
        elif isinstance(raw, dict):
            if "candidate_id" in raw:
                items = [raw]
            else:
                for k, v in raw.items():
                    if isinstance(v, dict):
                        v = dict(v)
                        v.setdefault("candidate_id", k)
                        items.append(v)
        for it in items:
            cid = it.get("candidate_id")
            if not cid:
                continue
            it.setdefault("validation_report",
                          str(p.parent.relative_to(ROOT) / "REPORT.md"))
            out[str(cid)] = it
    return out


def split_blocks(text: str) -> List[Tuple[Optional[str], List[str]]]:
    """Découpe le fichier en segments (candidate_id ou None, lignes)."""
    lines = text.splitlines(keepends=True)
    segments: List[Tuple[Optional[str], List[str]]] = []
    cur_id: Optional[str] = None
    cur: List[str] = []
    for ln in lines:
        m = BLOCK_START.match(ln)
        if m or (cur_id is not None and BLOCK_END.match(ln)):
            if cur:
                segments.append((cur_id, cur))
            cur = [ln]
            cur_id = m.group(1) if m else None
        else:
            cur.append(ln)
    if cur:
        segments.append((cur_id, cur))
    return segments


def _strip_managed(block: List[str]) -> List[str]:
    """Retire les lignes de champs gérés (et leurs continuations indentées > 4)."""
    out: List[str] = []
    skipping = False
    for ln in block:
        m = re.match(r"^    ([A-Za-z_][A-Za-z0-9_]*):", ln)
        if m:
            skipping = m.group(1) in MANAGED_KEYS
            if skipping:
                continue
            out.append(ln)
            continue
        if skipping and (ln.startswith("      ") or ln.strip() == ""):
            # continuation d'un scalaire plié / ligne vide interne
            if ln.strip() == "" and out and out[-1].strip() == "":
                continue
            if ln.strip() == "":
                out.append(ln)
            continue
        skipping = False
        out.append(ln)
    return out


def apply_result(block: List[str], res: dict) -> List[str]:
    verdict_raw = str(res.get("verdict", "")).strip().upper()
    status = STATUS_MAP.get(verdict_raw)
    if status is None:
        raise ValueError(f"verdict inconnu {verdict_raw!r} pour {res.get('candidate_id')}")
    res = dict(res)
    res["validation_verdict_raw"] = verdict_raw
    res.setdefault("validated_for_forward", status == "VALIDATED_FOR_FORWARD")
    res["validation_ingested_at"] = datetime.now(timezone.utc).isoformat()

    new_block: List[str] = []
    for ln in _strip_managed(block):
        if re.match(r"^    current_status:", ln):
            ln = f"    current_status: {status}   # wave 2, verdict validateur {verdict_raw}\n"
        new_block.append(ln)
    # trailing blank lines -> réinsérées après les champs
    trailing: List[str] = []
    while new_block and new_block[-1].strip() == "":
        trailing.insert(0, new_block.pop())
    for k in RESULT_FIELDS:
        if k in res and res[k] is not None:
            new_block.append(f"    {k}: {_fmt_scalar(res[k])}\n")
    new_block.extend(trailing or ["\n"])
    return new_block


def ingest(only: Optional[List[str]] = None, dry_run: bool = False) -> List[str]:
    results = load_results()
    if only:
        results = {k: v for k, v in results.items() if k in set(only)}
    text = REGISTRY.read_text()
    segments = split_blocks(text)
    known = {cid for cid, _ in segments if cid}
    applied: List[str] = []
    new_segments = []
    for cid, block in segments:
        if cid in results:
            block = apply_result(block, results[cid])
            applied.append(cid)
        new_segments.append((cid, block))
    missing = sorted(set(results) - known)
    for m in missing:
        print(f"[ingest] RESULTS.json pour {m} mais aucun bloc dans le registre -> ignoré", file=sys.stderr)
    new_text = "".join("".join(b) for _, b in new_segments)
    yaml.safe_load(new_text)  # garde-fou : YAML valide avant d'écrire
    if not dry_run and new_text != text:
        REGISTRY.write_text(new_text)
    return applied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--scoreboard", action="store_true")
    a = ap.parse_args()
    applied = ingest(only=a.only, dry_run=a.dry_run)
    print(f"[ingest] {len(applied)} candidat(s) enrichi(s){' (dry-run)' if a.dry_run else ''}: {', '.join(applied)}")
    if a.scoreboard and not a.dry_run:
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "compute_validation_scoreboard.py")], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
