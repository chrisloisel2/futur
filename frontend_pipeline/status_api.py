#!/usr/bin/env python3
"""
frontend_pipeline/status_api.py
─────────────────────────────────────────────────────────────────────────────
GET /api/status — vivacité des services hôte MESURÉE PAR LA FRAÎCHEUR DE LEURS
ARTEFACTS (le conteneur ne peut pas interroger systemd sur l'hôte).

  state (expected == "running") : fresh si âge <= fresh_max_min, sinon stale ;
                                  unknown si l'artefact n'existe pas.
  state (expected == "stopped") : stopped si l'artefact a > STOPPED_GRACE_MIN,
                                  error s'il est FRAIS (le moteur arrêté a
                                  réécrit !), unknown si absent / Mongo indispo.

Tout est lecture seule ; cache TTL 15 s. Toutes les constantes de chemin sont
des variables de module (monkeypatchées par les tests). Les chemins dérivent
de ROOT (= /home/qbee/futur sur l'hôte, /app dans le conteneur bind-monté).
Compatibilité Python 3.8 ET 3.11.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
DATA_DIR = ROOT / "data"

# ── artefacts (module-level → monkeypatchables) ──────────────────────────────
CYCLE_STATE_PATH = REPORTS_DIR / "live_alpha_lab" / "CYCLE_STATE.json"
DERIVATIVES_RAW_DIR = DATA_DIR / "derivatives_raw"
HYPERLIQUID_DIR = DATA_DIR / "hyperliquid"
MICROSTRUCTURE_PATH = REPORTS_DIR / "live_alpha_lab" / "microstructure_monitoring.jsonl"
DISK_WATCHDOG_PATH = REPORTS_DIR / "ops" / "disk_watchdog.jsonl"
NEWS_RAW_DIR = DATA_DIR / "news_raw"
POSITIONING_DIR = DATA_DIR / "positioning"
OPTIONS_DERIBIT_DIR = DATA_DIR / "options_backfill" / "deribit"
EVENT_SHADOW_STATE_PATH = REPORTS_DIR / "liq_cascade" / "shadow" / "state.json"
PAPER_V1_PATH = REPORTS_DIR / "paper_trading" / "fleet_summary.json"
TOURNAMENT_LEDGER_DIR = DATA_DIR / "alpha20" / "tournament" / "ledger"
DISK_PATH = REPORTS_DIR

LAB_LIVE_MAX_AGE_MIN = 30.0
STOPPED_GRACE_MIN = 60.0
STATUS_TTL_S = 15.0
NEWEST_WALK_CAP = 200_000     # garde-fou : jamais plus de N fichiers stat()és
PARTITION_KEEP = 2            # partitions date=* parcourues par répertoire (les plus récentes)

router = APIRouter(prefix="/api/status")
_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _cache_lock:
        _cache[key] = (time.time(), v)
    return v


# ── mtime helpers ────────────────────────────────────────────────────────────

def _mtime(p: Path) -> Optional[float]:
    try:
        return os.stat(str(p)).st_mtime
    except OSError:
        return None


def _newest_in_dir(d: Path, recursive: bool = True, suffixes: Optional[tuple] = None,
                   cap: int = NEWEST_WALK_CAP) -> Optional[float]:
    """mtime max des fichiers sous d (None si aucun). Parcours plafonné."""
    d = Path(d)
    if not d.is_dir():
        return None
    newest: Optional[float] = None
    n = 0
    stack: List[Path] = [d]
    while stack and n < cap:
        cur = stack.pop()
        try:
            with os.scandir(str(cur)) as it:
                subdirs: List[str] = []
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if recursive:
                            subdirs.append(e.path)
                        continue
                    if suffixes and not e.name.endswith(suffixes):
                        continue
                    n += 1
                    try:
                        m = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if newest is None or m > newest:
                        newest = m
                    if n >= cap:
                        break
            # partitions date=YYYY-MM-DD : seules les PARTITION_KEEP plus
            # récentes (ordre lexical = chronologique) sont parcourues —
            # data/hyperliquid compte ~93 000 fichiers, le tout-parcourir
            # coûtait 0,75 s par appel.
            dated = sorted(x for x in subdirs if os.path.basename(x).startswith("date="))
            others = [x for x in subdirs if not os.path.basename(x).startswith("date=")]
            stack.extend(Path(x) for x in others + dated[-PARTITION_KEEP:])
        except OSError:
            continue
    return newest


def _newest_derivatives_manifest(root: Path, now: datetime) -> Optional[float]:
    """Manifest le plus récent dans les partitions date=aujourd'hui (puis hier
    si rien) : exchange=*/market=*/stream=*/symbol=*/date=YYYY-MM-DD/*.manifest.json.
    Le parcours est borné à ces deux dates (jamais tout l'historique)."""
    root = Path(root)
    if not root.is_dir():
        return None
    for day in (now.date(), (now - timedelta(days=1)).date()):
        part = "date=%s" % day.isoformat()
        newest: Optional[float] = None
        for date_dir in root.glob("exchange=*/market=*/stream=*/symbol=*/" + part):
            m = _newest_in_dir(date_dir, recursive=False, suffixes=(".manifest.json",))
            if m is not None and (newest is None or m > newest):
                newest = m
        if newest is not None:
            return newest
    return None


def _legacy_paper_rebalanced_at() -> Optional[Any]:
    """rebalanced_at du doc Mongo legacy via command_center._paper() (import
    paresseux : évite l'import circulaire). Lève si Mongo indisponible."""
    from frontend_pipeline import command_center
    pp = command_center._paper()
    if getattr(pp, "col", None) is None:
        raise RuntimeError("mongo indisponible")
    doc = pp.get()
    if not isinstance(doc, dict):
        return None
    return doc.get("rebalanced_at")


def _parse_ts(s: Any) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, datetime):
        dt = s
    else:
        txt = str(s).strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── spécification des services ───────────────────────────────────────────────

def _services_spec(now: datetime) -> List[dict]:
    """Construit la liste à l'appel (les constantes peuvent être monkeypatchées).
    resolver() → mtime epoch (float) | None (absent) ; lève → unknown."""
    spec: List[dict] = [
        {"key": "lab_timer", "label": "Live Alpha Lab (15 min)", "expected": "running",
         "fresh_max_min": 30, "artefact": str(CYCLE_STATE_PATH),
         "resolver": lambda: _mtime(CYCLE_STATE_PATH)},
        {"key": "derivatives", "label": "collecteur derivatives", "expected": "running",
         "fresh_max_min": 30, "artefact": str(DERIVATIVES_RAW_DIR) + "/**/*.manifest.json",
         "resolver": lambda: _newest_derivatives_manifest(DERIVATIVES_RAW_DIR, now)},
    ]
    if Path(HYPERLIQUID_DIR).is_dir():
        spec.append({"key": "hyperliquid", "label": "collecteur Hyperliquid", "expected": "running",
                     "fresh_max_min": 30, "artefact": str(HYPERLIQUID_DIR),
                     "resolver": lambda: _newest_in_dir(HYPERLIQUID_DIR)})
    spec += [
        {"key": "microstructure", "label": "microstructure réduit", "expected": "running",
         "fresh_max_min": 30, "artefact": str(MICROSTRUCTURE_PATH),
         "resolver": lambda: _mtime(MICROSTRUCTURE_PATH)},
        {"key": "disk_watchdog", "label": "disk watchdog", "expected": "running",
         "fresh_max_min": 30, "artefact": str(DISK_WATCHDOG_PATH),
         "resolver": lambda: _mtime(DISK_WATCHDOG_PATH)},
        {"key": "news", "label": "news/F&G", "expected": "running",
         "fresh_max_min": 120, "artefact": str(NEWS_RAW_DIR),
         "resolver": lambda: _newest_in_dir(NEWS_RAW_DIR)},
        {"key": "positioning", "label": "positioning archiver", "expected": "running",
         "fresh_max_min": 720, "artefact": str(POSITIONING_DIR),
         "resolver": lambda: _newest_in_dir(POSITIONING_DIR, suffixes=(".parquet",))},
        {"key": "options", "label": "Deribit options", "expected": "running",
         "fresh_max_min": 1560, "artefact": str(OPTIONS_DERIBIT_DIR),
         "resolver": lambda: _newest_in_dir(OPTIONS_DERIBIT_DIR)},
        {"key": "event_shadow", "label": "event shadow (quotidien)", "expected": "running",
         "fresh_max_min": 1560, "artefact": str(EVENT_SHADOW_STATE_PATH),
         "resolver": lambda: _mtime(EVENT_SHADOW_STATE_PATH)},
        # ── moteurs ARRÊTÉS : un artefact frais = anomalie ────────────────────
        {"key": "paper_v1", "label": "paper V1.1 (arrêté 03/09)", "expected": "stopped",
         "fresh_max_min": STOPPED_GRACE_MIN, "artefact": str(PAPER_V1_PATH),
         "resolver": lambda: _mtime(PAPER_V1_PATH)},
        {"key": "tournament", "label": "tournoi ALPHA_20 (arrêté 03/09)", "expected": "stopped",
         "fresh_max_min": STOPPED_GRACE_MIN, "artefact": str(TOURNAMENT_LEDGER_DIR),
         "resolver": lambda: _newest_in_dir(TOURNAMENT_LEDGER_DIR)},
        {"key": "legacy_paper", "label": "paper Mongo (gelé 03/09)", "expected": "stopped",
         "fresh_max_min": STOPPED_GRACE_MIN, "artefact": "mongo:futur_ui.paper.rebalanced_at",
         "resolver": _legacy_paper_mtime},
    ]
    return spec


def _legacy_paper_mtime() -> Optional[float]:
    ts = _parse_ts(_legacy_paper_rebalanced_at())
    return ts.timestamp() if ts is not None else None


def _service_row(s: dict, now: datetime) -> dict:
    age_min: Optional[float] = None
    try:
        m = s["resolver"]()
    except Exception:
        m = None
        state = "unknown"
    else:
        if m is None:
            state = "unknown"
        else:
            age_min = round((now.timestamp() - float(m)) / 60.0, 1)
            if s["expected"] == "running":
                state = "fresh" if age_min <= float(s["fresh_max_min"]) else "stale"
            else:
                state = "error" if age_min <= STOPPED_GRACE_MIN else "stopped"
    return {
        "key": s["key"], "label": s["label"], "state": state,
        "artefact": s["artefact"], "age_min": age_min, "expected": s["expected"],
        "fresh_max_min": float(s["fresh_max_min"]),
        "last_write": datetime.fromtimestamp(float(m), tz=timezone.utc).isoformat() if m else None,
    }


# ── lab (CYCLE_STATE) ────────────────────────────────────────────────────────

def _lab(now: datetime) -> dict:
    import json
    cs: dict = {}
    try:
        with open(str(CYCLE_STATE_PATH), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        cs = d if isinstance(d, dict) else {}
    except Exception:
        cs = {}
    finished_at = cs.get("cycle_finished_at")
    finished_at = str(finished_at) if finished_at is not None else None
    status = cs.get("status")
    status = str(status) if status is not None else None
    ts = _parse_ts(finished_at)
    age_min: Optional[float] = None
    if ts is not None:
        age_min = round((now - ts).total_seconds() / 60.0, 1)
    failed_raw = cs.get("producers_failed") or []
    failed: List[str] = []
    for x in failed_raw if isinstance(failed_raw, list) else []:
        if isinstance(x, dict):
            failed.append(str(x.get("name") or x.get("alpha_id") or x))
        else:
            failed.append(str(x))

    def _int(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return {
        "finished_at": finished_at,
        "status": status,
        "producers_ok": _int(cs.get("producers_ok")),
        "producers_run": _int(cs.get("producers_run")),
        "producers_failed": failed,
        "age_min": age_min,
        "live": bool(status == "OK" and age_min is not None and abs(age_min) <= LAB_LIVE_MAX_AGE_MIN),
    }


def _disk() -> dict:
    try:
        du = shutil.disk_usage(str(DISK_PATH))
        return {"free_gb": round(du.free / 1e9, 2), "total_gb": round(du.total / 1e9, 2),
                "path": str(DISK_PATH)}
    except OSError:
        return {"free_gb": None, "total_gb": None, "path": str(DISK_PATH)}


def build_status() -> dict:
    now = _now()
    return {
        "ts": now.isoformat(),
        "lab": _lab(now),
        "services": [_service_row(s, now) for s in _services_spec(now)],
        "disk": _disk(),
    }


@router.get("")
def api_status():
    return _cached("status", STATUS_TTL_S, build_status)
