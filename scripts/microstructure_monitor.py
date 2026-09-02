#!/usr/bin/env python3
"""
scripts/microstructure_monitor.py
─────────────────────────────────────────────────────────────────────────────
Monitoring rolling 1h/6h/24h du collecteur microstructure réduit (item P1,
phase OPERATIONAL HARDENING) : rows/bytes par (venue, symbol, stream_type),
disconnects/errors, missing_intervals, last_event_age, disk_free,
estimated_days_remaining. But explicite : détecter un collecteur VIVANT
mais silencieusement INCOMPLET (ex: une paire venue/symbol arrêtée sans que
le process meure), pas juste "est-ce que le service tourne".

Fichiers bruts : data/microstructure_reduced/raw/{bbo,trades}/
venue={V}/symbol={S}/date=YYYY-MM-DD/events-HH.jsonl.gz -- un fichier par
heure, gzippé, potentiellement TRONQUÉ pour l'heure en cours (collecteur en
train d'écrire) -- lu en best-effort, jamais un crash sur un fichier
partiel.

reconnects/parse_errors du state.json du collecteur sont des compteurs
CUMULATIFS depuis le démarrage, pas déjà windowés -- ce script persiste son
propre snapshot append-only (reports/live_alpha_lab/
microstructure_monitoring.jsonl) pour pouvoir, aux prochaines exécutions,
calculer un delta réel sur la fenêtre demandée par diff contre un snapshot
antérieur. Tant qu'aucun snapshot assez ancien n'existe, le delta est
explicitement "INSUFFICIENT_HISTORY", jamais un chiffre inventé.
"""
from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "microstructure_reduced" / "raw"
COLLECTOR_STATE = ROOT / "data" / "microstructure_reduced" / "state.json"
SNAPSHOT_LOG = ROOT / "reports" / "live_alpha_lab" / "microstructure_monitoring.jsonl"
ROW_COUNT_CACHE_PATH = ROOT / "reports" / "live_alpha_lab" / "microstructure_row_count_cache.json"

WINDOWS_HOURS = {"1h": 1, "6h": 6, "24h": 24}

# décompresser chaque fichier .jsonl.gz pour compter ses lignes coûte
# ~1s pour un fichier binance BBO chargé (18MB/1.5M lignes) -- répéter ça
# pour ~24h * 18 séries à chaque exécution (timer 5min) serait beaucoup
# trop lent. Cache par (path, mtime, size) : un fichier d'heure CLOSE
# (mtime/size stables) n'est jamais redécompressé une fois compté --
# seul le fichier de l'heure EN COURS (mtime/size changeants tant que le
# collecteur écrit dedans) est recompté à chaque run.
_row_count_cache: Dict[str, Dict] = {}


def _load_row_count_cache() -> None:
    global _row_count_cache
    if ROW_COUNT_CACHE_PATH.exists():
        try:
            _row_count_cache = json.loads(ROW_COUNT_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            _row_count_cache = {}


def _save_row_count_cache() -> None:
    ROW_COUNT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROW_COUNT_CACHE_PATH.write_text(json.dumps(_row_count_cache))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _discover_series() -> List[Dict[str, str]]:
    """(stream_type, venue, symbol) triples réellement présents sur disque
    -- jamais une liste codée en dur qui se périmerait si le scope du
    collecteur change."""
    series = []
    if not RAW_DIR.exists():
        return series
    for stream_dir in sorted(RAW_DIR.iterdir()):
        if not stream_dir.is_dir():
            continue
        stream_type = stream_dir.name
        for venue_dir in sorted(stream_dir.glob("venue=*")):
            venue = venue_dir.name.split("=", 1)[1]
            for symbol_dir in sorted(venue_dir.glob("symbol=*")):
                symbol = symbol_dir.name.split("=", 1)[1]
                series.append({"stream_type": stream_type, "venue": venue, "symbol": symbol, "dir": str(symbol_dir)})
    return series


def _hour_files(symbol_dir: Path) -> List[Dict]:
    """Chaque fichier -> {path, hour_start (datetime UTC), size_bytes}."""
    out = []
    for date_dir in sorted(symbol_dir.glob("date=*")):
        date_str = date_dir.name.split("=", 1)[1]
        for f in sorted(date_dir.glob("events-*.jsonl.gz")):
            # f.stem ne retire qu'UN SEUL suffixe ("events-05.jsonl.gz" ->
            # "events-05.jsonl", pas "events-05") -- retirer les deux
            # extensions explicitement plutôt que de dépendre de .stem.
            base = f.name
            for suffix in (".jsonl.gz", ".jsonl"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            try:
                hh = int(base.split("-")[-1])
            except ValueError:
                continue
            try:
                hour_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    hour=hh, tzinfo=timezone.utc)
            except ValueError:
                continue
            out.append({"path": f, "hour_start": hour_start, "size_bytes": f.stat().st_size})
    return out


def _count_rows(path: Path) -> int:
    """Best-effort : le fichier de l'heure en cours peut être tronqué
    (collecteur en train d'écrire) -- ne jamais planter dessus, compter ce
    qui est lisible. Mis en cache par (path, mtime, size) -- voir note plus
    haut sur le coût de décompression."""
    key = str(path)
    st = path.stat()
    cached = _row_count_cache.get(key)
    if cached is not None and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
        return cached["rows"]

    n = 0
    try:
        with gzip.open(path, "rb") as f:
            for _ in f:
                n += 1
    except (EOFError, OSError):
        pass
    _row_count_cache[key] = {"mtime": st.st_mtime, "size": st.st_size, "rows": n}
    return n


def _last_event_ts(path: Path) -> Optional[datetime]:
    last_line = None
    try:
        with gzip.open(path, "rb") as f:
            for line in f:
                if line.strip():
                    last_line = line
    except (EOFError, OSError):
        pass
    if last_line is None:
        return None
    try:
        d = json.loads(last_line)
        return datetime.fromtimestamp(d["event_ts_ns"] / 1e9, tz=timezone.utc)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def analyze_series(entry: Dict[str, str], now: datetime) -> Dict:
    symbol_dir = Path(entry["dir"])
    files = _hour_files(symbol_dir)
    result = {"stream_type": entry["stream_type"], "venue": entry["venue"], "symbol": entry["symbol"]}

    for label, hours in WINDOWS_HOURS.items():
        cutoff = now - timedelta(hours=hours)
        in_window = [f for f in files if f["hour_start"] + timedelta(hours=1) > cutoff]
        result[f"rows_{label}"] = sum(_count_rows(f["path"]) for f in in_window)
        result[f"bytes_{label}"] = sum(f["size_bytes"] for f in in_window)

    if files:
        latest = max(files, key=lambda f: f["hour_start"])
        last_ts = _last_event_ts(latest["path"])
        if last_ts is not None:
            # horloge fraîche, PAS `now` figé au début du run -- ce script
            # décompresse ~1s/fichier * plusieurs dizaines de fichiers, donc
            # `now` peut être minutes en retard au moment où cette série est
            # traitée, alors que le collecteur continue d'écrire en temps
            # réel -- avec `now` figé, ça donnait un âge NÉGATIF (le
            # collecteur avait déjà écrit des lignes "après" le `now` gelé).
            result["last_event_age_s"] = (_now() - last_ts).total_seconds()
        else:
            result["last_event_age_s"] = None
        # missing_intervals : heures des dernières 24h SANS fichier du tout
        # (le collecteur ne devrait jamais s'arrêter d'écrire une heure)
        present_hours = {f["hour_start"] for f in files}
        expected = [now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h) for h in range(24)]
        missing = [h.isoformat() for h in expected if h not in present_hours and h <= now]
        result["missing_intervals_24h"] = missing
    else:
        result["last_event_age_s"] = None
        result["missing_intervals_24h"] = "NO_DATA_AT_ALL"

    return result


def _load_collector_state() -> Dict:
    if not COLLECTOR_STATE.exists():
        return {}
    try:
        return json.loads(COLLECTOR_STATE.read_text())
    except json.JSONDecodeError:
        return {}


def _find_prior_snapshot(target_age_hours: float, now: datetime) -> Optional[Dict]:
    """Le snapshot le plus proche de `target_age_hours` dans le passé, sans
    dépasser cette ancienneté de plus de 50% (sinon le delta ne représente
    pas vraiment la fenêtre demandée)."""
    if not SNAPSHOT_LOG.exists():
        return None
    target = now - timedelta(hours=target_age_hours)
    best = None
    best_diff = None
    for line in SNAPSHOT_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["timestamp"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        diff = abs((ts - target).total_seconds())
        if diff <= target_age_hours * 3600 * 0.5 and (best_diff is None or diff < best_diff):
            best, best_diff = rec, diff
    return best


def windowed_counter_deltas(current_counters: Dict, now: datetime) -> Dict:
    out = {}
    for label, hours in (("1h", 1), ("24h", 24)):
        prior = _find_prior_snapshot(hours, now)
        if prior is None:
            out[label] = "INSUFFICIENT_HISTORY"
            continue
        prior_counters = prior.get("collector_counters", {})
        delta = {}
        for k, v in current_counters.items():
            if isinstance(v, (int, float)):
                delta[k] = v - prior_counters.get(k, 0)
            elif isinstance(v, dict):
                delta[k] = {kk: vv - prior_counters.get(k, {}).get(kk, 0) for kk, vv in v.items()}
        out[label] = delta
    return out


def main() -> int:
    now = _now()
    _load_row_count_cache()
    series = _discover_series()
    per_series = [analyze_series(e, now) for e in series]
    _save_row_count_cache()

    collector_state = _load_collector_state()
    counters = collector_state.get("counters", {})
    disk = collector_state.get("disk", {})

    total_bytes_24h = sum(s["bytes_24h"] for s in per_series)
    gb_per_day_current = total_bytes_24h / (1024 ** 3)
    free_gb = disk.get("free_gb")
    estimated_days_remaining = (free_gb / gb_per_day_current) if (free_gb and gb_per_day_current > 0) else None

    record = {
        "timestamp": now.isoformat(),
        "series": per_series,
        "gb_per_day_current": round(gb_per_day_current, 4),
        "disk_free_gb": free_gb,
        "estimated_days_remaining": round(estimated_days_remaining, 1) if estimated_days_remaining else None,
        "collector_counters": counters,
        "collector_disk": disk,
        "windowed_counter_deltas": windowed_counter_deltas(counters, now),
    }

    SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    print(f"[microstructure_monitor] {len(per_series)} séries, "
         f"{gb_per_day_current:.3f}GB/j, disk_free={free_gb}, "
         f"est_days_remaining={record['estimated_days_remaining']}", flush=True)
    for s in per_series:
        gap = f" MISSING={s['missing_intervals_24h']}" if s["missing_intervals_24h"] not in ([], "NO_DATA_AT_ALL") else ""
        print(f"  {s['venue']}/{s['symbol']}/{s['stream_type']}: "
             f"rows_1h={s['rows_1h']} rows_24h={s['rows_24h']} "
             f"last_event_age_s={s['last_event_age_s']}{gap}", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
