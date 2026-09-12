#!/usr/bin/env python3
"""
book_depth_parser.py -- fonctions PURES : lire une archive Vision `bookDepth` et en faire des instantanes de carnet.

Format Vision (futures UM) : une ligne par (horodatage, niveau), niveaux en POURCENTAGE du mid :
    -5, -4, -3, -2, -1, -0.2 (cote bid, cumul du mid vers le bas)  |  0.2, 1, 2, 3, 4, 5 (cote ask)
    depth = quantite cumulee, notional = montant cumule en quote. Environ un instantane toutes les 30 s.

Ce que ce format NE contient PAS : le meilleur bid, le meilleur ask, le mid. Deux generations d'archives :
    - avant 2026 : niveaux +-1 % .. +-5 % seulement (resolution 100 bps) ;
    - 2026 : niveau +-0,2 % en plus (resolution 20 bps).
Toute grandeur demandee sous la resolution de l'instantane est rendue None, jamais interpolee vers le bas.
Aucun prix de retour, aucun signal.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

NATIVE_BANDS_BPS = (20, 100, 200, 300, 400, 500)      # |percentage| * 100 ; le 20 n'existe que depuis 2026
COARSE_BANDS_BPS = (100, 200, 300, 400, 500)
LEVELS = {-5.0: 500, -4.0: 400, -3.0: 300, -2.0: 200, -1.0: 100, -0.2: 20, 0.2: 20, 1.0: 100, 2.0: 200, 3.0: 300, 4.0: 400, 5.0: 500}


class BookError(ValueError):
    pass


def parse_ts(v: str) -> Optional[int]:
    """Horodatage naif Vision (UTC) ou epoch -> ms UTC."""
    v = (v or "").strip()
    if not v:
        return None
    if v.isdigit():
        n = int(v); return n // 1000 if n > 10 ** 14 else n
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    return int(d.timestamp() * 1000)


def iter_rows(zip_path: Path) -> Iterable[Tuple[int, float, float, float]]:
    """(ts_ms, percentage, depth, notional) ; l'en-tete et les lignes illisibles sont sautes."""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                    if len(row) < 4:
                        continue
                    ts = parse_ts(row[0])
                    if ts is None:
                        continue
                    try:
                        yield ts, float(row[1]), float(row[2]), float(row[3])
                    except ValueError:
                        continue


def snapshots(rows: Iterable[Tuple[int, float, float, float]]) -> List[Dict[str, Any]]:
    """Regroupe les lignes par horodatage en instantanes {ts_ms, bid: {bps: notional}, ask: {bps: notional}, ok, error}."""
    by: Dict[int, Dict[str, Dict[int, float]]] = {}
    for ts, pct, depth, notional in rows:
        band = LEVELS.get(round(pct, 2))
        if band is None:
            continue
        side = "bid" if pct < 0 else "ask"
        by.setdefault(ts, {"bid": {}, "ask": {}})[side][band] = notional
    out = []
    for ts in sorted(by):
        s = by[ts]; err = validate(s)
        out.append({"ts_ms": ts, "bid": s["bid"], "ask": s["ask"], "ok": err is None, "error": err, "resolution_bps": resolution_bps(s)})
    return out


def resolution_bps(s: Dict[str, Dict[int, float]]) -> Optional[int]:
    """La bande la plus fine presente des deux cotes : 20 (archives 2026) ou 100 (avant)."""
    bands = set(s.get("bid") or {}) & set(s.get("ask") or {})
    return min(bands) if bands else None


def validate(s: Dict[str, Dict[int, float]]) -> Optional[str]:
    """Un carnet est bon si chaque cote a l'un des deux jeux de niveaux connus, tous >= 0 et cumulatifs."""
    for side in ("bid", "ask"):
        d = s.get(side) or {}
        if set(d) not in (set(NATIVE_BANDS_BPS), set(COARSE_BANDS_BPS)):
            return "%s side has bands %s" % (side, sorted(d))
        vals = [d[b] for b in NATIVE_BANDS_BPS if b in d]
        if any(v < 0 for v in vals):
            return "%s side has a negative notional" % side
        if any(vals[i] > vals[i + 1] + 1e-9 for i in range(len(vals) - 1)):
            return "%s side is not cumulative" % side
    return None


def load(zip_path: Path) -> List[Dict[str, Any]]:
    try:
        return snapshots(iter_rows(zip_path))
    except (zipfile.BadZipFile, OSError, csv.Error) as e:
        raise BookError("%s: %s" % (type(e).__name__, str(e)[:80]))


def nearest_at_or_after(snaps: List[Dict[str, Any]], ts_ms: int, tolerance_ms: int = 120_000) -> Optional[Dict[str, Any]]:
    """Le premier instantane a ou apres ts (l'etat du carnet tel qu'un lecteur l'aurait vu), dans la tolerance."""
    for s in snaps:
        if s["ts_ms"] >= ts_ms:
            return s if s["ts_ms"] - ts_ms <= tolerance_ms else None
    return None


def count_between(snaps: List[Dict[str, Any]], a_ms: int, b_ms: int) -> int:
    return sum(1 for s in snaps if a_ms <= s["ts_ms"] < b_ms)


def depth_within(side: Dict[int, float], bps: int) -> Tuple[Optional[float], Optional[int], bool]:
    """Notional cumule dans +-bps du mid. -> (valeur, bande native utilisee, est_une_borne_inferieure).
    Sous 20 bps : None (au-dela de la resolution). Entre deux bandes : la bande native INFERIEURE, donc une
    borne inferieure de la profondeur reelle dans la bande demandee, jamais une interpolation."""
    fit = [b for b in NATIVE_BANDS_BPS if b <= bps and b in side]
    if not fit:
        return None, None, False                      # sous la resolution de CET instantane
    b = fit[-1]
    return side.get(b), b, (b != bps)


def slippage_for_notional(side: Dict[int, float], notional_usd: float) -> Dict[str, Optional[float]]:
    """Cout de passage d'un ordre au marche de `notional_usd` contre ce cote du carnet.
    ub_bps : la bande native qui contient l'ordre (borne superieure du pire fill) ;
    bps    : estimation lineaire en supposant une distribution uniforme DANS la bande (dite, pas cachee) ;
    None   : l'ordre depasse le carnet observe (5 %)."""
    prev_band, prev_cum = 0, 0.0
    for b in NATIVE_BANDS_BPS:
        cum = side.get(b)
        if cum is None:
            continue                                  # bande absente de cette generation d'archive
        if cum >= notional_usd:
            frac = (notional_usd - prev_cum) / (cum - prev_cum) if cum > prev_cum else 1.0
            est = prev_band + frac * (b - prev_band)
            return {"ub_bps": float(b), "bps": round(est, 3), "fills": True}
        prev_band, prev_cum = b, cum
    return {"ub_bps": None, "bps": None, "fills": False}


def imbalance(snap: Dict[str, Any], bps: int) -> Optional[float]:
    b, _, _ = depth_within(snap["bid"], bps); a, _, _ = depth_within(snap["ask"], bps)
    if b is None or a is None or (b + a) <= 0:
        return None
    return round((b - a) / (b + a), 4)
