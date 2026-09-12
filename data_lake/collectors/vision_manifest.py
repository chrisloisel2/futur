#!/usr/bin/env python3
"""
vision_manifest.py -- un manifeste par fenetre d'evenement : fichiers attendus, statut (ok / 404 / error /
skipped / not_yet_published), octets, sha256 (verifie contre le .CHECKSUM de Vision), lignes, premier /
dernier horodatage (colonne temps seulement : aucun prix n'est lu). Ecriture atomique et versionnee :
un manifeste existant n'est jamais ecrase en silence, le journal global est append-only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.collectors.vision_paths import DATASETS, LOCAL_ROOT, window, days_for_window

MANIFEST_ROOT = LOCAL_ROOT / "manifests"
LOG = LOCAL_ROOT / "backfill_log.jsonl"

#: Colonne d'horodatage de chaque jeu Vision (UM futures). Aucune autre colonne n'est lue.
#: aggTrades : agg_id,price,qty,first_id,last_id,transact_time,is_buyer_maker    -> 5
#: trades    : id,price,qty,quote_qty,time,is_buyer_maker                        -> 4
#: klines/*Klines : open_time en colonne 0
#: bookTicker: update_id,bid_px,bid_qty,ask_px,ask_qty,transaction_time,event_ts -> 5
#: bookDepth : timestamp,percentage,depth,notional  (ISO naif, UTC)              -> 0
#: metrics   : create_time,symbol,sum_open_interest,...  (ISO naif, UTC)         -> 0
#: fundingRate : calc_time,funding_interval_hours,last_funding_rate              -> 0
TS_COL = {"aggTrades": 5, "trades": 4, "klines": 0, "markPriceKlines": 0, "indexPriceKlines": 0, "premiumIndexKlines": 0,
          "metrics": 0, "fundingRate": 0, "bookDepth": 0, "bookTicker": 5}

#: Reference de prix externe : l'index lui-meme, ou le premium (index = mark - premium, identite du contrat).
INDEX_REFERENCE = ("indexPriceKlines", "premiumIndexKlines")
#: Sans ces trois familles la fenetre n'est pas "couverte" : reference mark, reference externe, trades.
CORE_MARK, CORE_TRADES = "markPriceKlines", "aggTrades"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _write_atomic(p: Path, text: str) -> None:
    """Ecriture atomique : un arret au milieu ne laisse jamais un fichier tronque en place."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _ts_norm(v: str) -> Optional[str]:
    """Horodatage d'une colonne temps Vision : ms, µs (piege connu) ou ISO NAIF (toujours UTC chez Vision).
    Retourne ISO UTC. Aucune valeur de prix n'est lue ni convertie."""
    v = v.strip()
    if not v:
        return None
    if v.isdigit():
        n = int(v); n = n // 1000 if n > 10**14 else n
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)   # naif = UTC, PAS l'heure locale
    return d.isoformat(timespec="milliseconds")


def inspect_zip(p: Path, dataset: str) -> Dict[str, Any]:
    """Lignes, premier / dernier horodatage. Lit UNE colonne (temps). Ne lit aucun prix."""
    out: Dict[str, Any] = {"rows": 0, "first_ts": None, "last_ts": None, "members": []}
    col = TS_COL.get(dataset, 0)
    try:
        with zipfile.ZipFile(p) as z:
            out["members"] = z.namelist()
            for name in z.namelist():
                first = last = None; n = 0
                with z.open(name) as fh:
                    for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                        if not row or len(row) <= col:
                            continue
                        t = _ts_norm(row[col])
                        if t is None:
                            continue        # en-tete
                        n += 1
                        if first is None:
                            first = t
                        last = t
                out["rows"] += n
                out["first_ts"] = min(out["first_ts"] or first, first) if first else out["first_ts"]
                out["last_ts"] = max(out["last_ts"] or last, last) if last else out["last_ts"]
    except (zipfile.BadZipFile, OSError, csv.Error, UnicodeError) as e:      # csv.Error : NUL dans un membre (Python 3.8)
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:70])
    return out


def coverage(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    by: Dict[str, Dict[str, int]] = {}
    for f in files:
        d = by.setdefault(f["dataset"], {"expected": 0, "ok": 0, "404": 0, "error": 0, "skipped": 0, "not_yet_published": 0, "checksum_verified": 0, "checksum_unverified": 0})
        d["expected"] += 1
        st = f.get("status")
        d[st if st in ("ok", "404", "error", "not_yet_published") else "skipped"] += 1
        if st == "ok":
            d["checksum_verified" if f.get("checksum_verified") else "checksum_unverified"] += 1
    complete = {ds: (v["ok"] == v["expected"] and v["expected"] > 0) for ds, v in by.items()}
    index_ref = any(complete.get(ds, False) for ds in INDEX_REFERENCE)
    core = complete.get(CORE_MARK, False) and complete.get(CORE_TRADES, False) and index_ref
    n_ok = sum(v["ok"] for v in by.values())
    return {"by_dataset": by, "complete": complete, "index_reference_complete": index_ref, "core_complete": core,
            "core_definition": "markPriceKlines + aggTrades + (indexPriceKlines or premiumIndexKlines), every day of the window",
            "checksum_verified": sum(v["checksum_verified"] for v in by.values()), "checksum_unverified": sum(v["checksum_unverified"] for v in by.values()),
            "score": round(n_ok / max(1, sum(v["expected"] for v in by.values())), 4)}


def build(event: Dict[str, Any], files: List[Dict[str, Any]], run_id: str) -> Dict[str, Any]:
    w = window(event["t0"])
    on_disk = [f for f in files if f.get("status") == "ok"]
    man = {"event_id": event["event_id"], "symbol": event["symbol"], "t0": w["t0"], "window_start": w["start"], "window_end": w["end"], "days": days_for_window(event["t0"]),
           "run_id": run_id, "written_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files, "coverage": coverage(files),
           "bytes_on_disk": sum(f.get("bytes") or 0 for f in on_disk), "bytes_probed_total": sum(f.get("bytes") or 0 for f in files),
           "no_alpha_test": True, "no_return_computed": True}
    volatile = {"run_id", "written_at_local"}          # le contenu, pas la passe : deux passes identiques ne re-archivent pas
    man["manifest_sha256"] = hashlib.sha256(json.dumps({k: v for k, v in man.items() if k not in volatile}, sort_keys=True, default=str).encode()).hexdigest()
    return man


def _free_archive_path(root: Path, event_id: str, run_id: str) -> Path:
    """Nom d'archive jamais reutilise : <event_id>.<run_id>.json, puis .2, .3... si deja pris."""
    base = root / f"{event_id}.{run_id or 'prev'}.json"
    if not base.exists():
        return base
    i = 2
    while (root / f"{event_id}.{run_id or 'prev'}.{i}.json").exists():
        i += 1
    return root / f"{event_id}.{run_id or 'prev'}.{i}.json"


def write(man: Dict[str, Any], root: Optional[Path] = None) -> Path:
    """Jamais d'ecrasement silencieux : un manifeste dont le contenu change est archive a cote (nom libre),
    la re-ecriture est journalisee, et l'ecriture est atomique. Un manifeste illisible est archive tel quel."""
    root = Path(root) if root else MANIFEST_ROOT
    root.mkdir(parents=True, exist_ok=True); p = root / f"{man['event_id']}.json"
    if p.exists():
        raw = p.read_text(encoding="utf-8", errors="replace")
        try:
            old = json.loads(raw)
        except ValueError:
            old = None
        if isinstance(old, dict) and old.get("manifest_sha256") == man["manifest_sha256"]:
            return p                                        # contenu identique : rien a archiver
        old_run = old.get("run_id") if isinstance(old, dict) else "unreadable"
        arch = _free_archive_path(root, man["event_id"], old_run)
        _write_atomic(arch, raw if not isinstance(old, dict) else json.dumps(old, indent=1, default=str))   # bytes d'origine si illisible
        log({"kind": "manifest_rewritten", "event_id": man["event_id"], "old_run_id": old_run, "new_run_id": man["run_id"],
             "old_sha256": old.get("manifest_sha256") if isinstance(old, dict) else None, "new_sha256": man["manifest_sha256"], "archived_as": arch.name})
    _write_atomic(p, json.dumps(man, indent=1, ensure_ascii=False, default=str))
    return p


def log(rec: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = Path(path) if path else LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}, ensure_ascii=False, default=str) + "\n")


def verify(man: Dict[str, Any]) -> Dict[str, Any]:
    """Re-hash des fichiers ok sur disque contre le manifeste."""
    bad, missing, checked = [], [], 0
    for f in man["files"]:
        if f.get("status") != "ok":
            continue
        p = Path(f["local"])
        if not p.exists():
            missing.append(f["local"]); continue
        checked += 1
        if sha256_file(p) != f.get("sha256"):
            bad.append(f["local"])
    return {"checked": checked, "sha_mismatch": bad, "missing": missing, "ok": not bad and not missing}


def load_all(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    root = Path(root) if root else MANIFEST_ROOT; out = {}
    if not root.exists():
        return out
    for p in sorted(root.glob("*.json")):
        if p.name.count(".") != 1:      # versions archivees <event_id>.<run>.json ignorees
            continue
        try:
            m = json.loads(p.read_text()); out[m["event_id"]] = m
        except (ValueError, KeyError):
            continue
    return out
