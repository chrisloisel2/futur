#!/usr/bin/env python3
"""
announcement_time_extractor.py -- fonctions PURES : du texte d'une annonce aux horodatages qu'il contient.

Ce que l'on cherche, et rien d'autre :
  trading_start_ts   "will open trading at 2025-05-02 08:30 (UTC)"
  delisting_ts       "will be delisted at 2024-03-26 03:00 (UTC)"
  suspension_ts      "trading will be suspended at ...", "deposits/withdrawals suspended at ..."
  symbols            les paires citees (ABCUSDT, ABC/USDT)

Aucune inference de marche, aucun prix, aucun signal. Une phrase sans date reste sans date.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

#: "2025-05-02 08:30", "2025/05/02 08:30:00", "2025-05-02T08:30"
_ISOish = re.compile(r"(?P<y>20\d{2})[-/](?P<mo>\d{1,2})[-/](?P<d>\d{1,2})(?:[ T]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?)?")
#: "May 2, 2025 08:30", "2 May 2025 08:30"
_TEXTUAL = re.compile(r"(?:(?P<mon>[A-Za-z]{3,9})\s+(?P<d>\d{1,2}),?\s+(?P<y>20\d{2})|(?P<d2>\d{1,2})\s+(?P<mon2>[A-Za-z]{3,9})\s+(?P<y2>20\d{2}))"
                      r"(?:[ ,]+(?:at\s+)?(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?)?", re.I)
_UTC_HINT = re.compile(r"\(?\s*UTC\s*(?P<off>[+-]\s*\d{1,2})?\s*\)?", re.I)
_SYMBOL = re.compile(r"\b(?P<base>[A-Z0-9]{2,15})\s*/?\s*(?P<quote>USDT|USDC|USD|BTC|BNB|FDUSD|TRY|EUR)\b")

TRADING_START = (r"open(?:s)? trading", r"will (?:open|launch|list|begin trading)", r"trading (?:will )?(?:open|start|begin|go live)",
                 r"launch(?:es|ed)? .{0,40}perpetual", r"listing time", r"trading opens", r"available for trading", r"start(?:s)? trading")
DELISTING = (r"will (?:be )?delist", r"delisting of", r"remove(?:s|d)? .{0,30}(?:trading pair|from)", r"cease trading", r"will be removed",
             r"termination of", r"final settlement", r"close(?:s|d)? .{0,20}(?:contract|position)")
SUSPENSION = (r"suspend", r"suspension", r"halt(?:ed|s)?", r"pause(?:d|s)? .{0,20}trading", r"cease deposits")


def _mk(y: int, mo: int, d: int, h: int, mi: int, s: int, off_hours: int = 0) -> Optional[str]:
    try:
        t = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None
    if off_hours:
        t = t.replace(tzinfo=timezone.utc) - __import__("datetime").timedelta(hours=off_hours)
    return t.isoformat(timespec="seconds")


def find_datetimes(text: str) -> List[Tuple[int, int, str, bool]]:
    """-> [(debut, fin, iso_utc, heure_presente)] pour chaque date trouvee. UTC suppose (convention Binance/OKX/Bybit),
    un decalage explicite '(UTC+8)' est applique."""
    out = []
    for m in _ISOish.finditer(text):
        g = m.groupdict(); has_time = g["h"] is not None
        off = _offset_near(text, m.end())
        iso = _mk(int(g["y"]), int(g["mo"]), int(g["d"]), int(g["h"] or 0), int(g["mi"] or 0), int(g["s"] or 0), off)
        if iso:
            out.append((m.start(), m.end(), iso, has_time))
    for m in _TEXTUAL.finditer(text):
        g = m.groupdict()
        mon = (g["mon"] or g["mon2"] or "").lower(); d = g["d"] or g["d2"]; y = g["y"] or g["y2"]
        if mon not in MONTHS or not d or not y:
            continue
        has_time = g["h"] is not None
        off = _offset_near(text, m.end())
        iso = _mk(int(y), MONTHS[mon], int(d), int(g["h"] or 0), int(g["mi"] or 0), int(g["s"] or 0), off)
        if iso and not any(a <= m.start() < b for a, b, _, _ in out):
            out.append((m.start(), m.end(), iso, has_time))
    return sorted(out)


def _offset_near(text: str, pos: int) -> int:
    m = _UTC_HINT.match(text[pos:pos + 14].lstrip()) or _UTC_HINT.search(text[pos:pos + 20])
    if m and m.group("off"):
        try:
            return int(m.group("off").replace(" ", ""))
        except ValueError:
            return 0
    return 0


def _kind_distances(text, start, end, window=240):
    """Distance en caracteres entre la date et le mot-cle le plus proche de chaque famille.
    Une date appartient a la clause qui la nomme, pas a toutes celles du paragraphe."""
    lo, hi = max(0, start - window), min(len(text), end + window)
    ctx = text[lo:hi].lower(); rel_s, rel_e = start - lo, end - lo
    out = {}
    for key, pats in (("trading_start", TRADING_START), ("delisting", DELISTING), ("suspension", SUSPENSION)):
        best = None
        for pat in pats:
            for m in re.finditer(pat, ctx):
                d = 0 if (m.start() < rel_e and m.end() > rel_s) else (rel_s - m.end() if m.end() <= rel_s else m.start() - rel_e)
                if best is None or d < best:
                    best = d
        out[key] = best
    return out


def _same_line(text, start, end):
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return text[a:(b if b != -1 else len(text))]


PATS = {"trading_start": TRADING_START, "delisting": DELISTING, "suspension": SUSPENSION}


def extract(text: str, title: str = "") -> Dict[str, object]:
    """Horodatages et symboles d'une annonce. Renvoie None pour ce qui n'est pas dit explicitement."""
    text = (title + "\n" + (text or "")) if title else (text or "")
    dts = find_datetimes(text)
    picks = {"trading_start_ts": None, "delisting_ts": None, "suspension_ts": None}
    evidence = {k: None for k in picks}
    dist = {k: None for k in picks}
    for start, end, iso, has_time in dts:
        d = _kind_distances(text, start, end)
        named = {k: v for k, v in d.items() if v is not None}
        if not named:
            continue
        closest = min(named.values())
        line = _same_line(text, start, end).lower()
        for flag, key in (("trading_start", "trading_start_ts"), ("delisting", "delisting_ts"), ("suspension", "suspension_ts")):
            if named.get(flag) is None:
                continue
            on_line = any(re.search(pat, line) for pat in PATS[flag])
            if named[flag] > closest and not on_line:
                continue                                  # une autre clause nomme cette date de plus pres
            cur_time = _has_time(evidence[key])
            better = (picks[key] is None) or (has_time and not cur_time) or (has_time == cur_time and dist[key] is not None and named[flag] < dist[key])
            if better:
                picks[key] = iso; dist[key] = named[flag]
                evidence[key] = text[max(0, start - 90):min(len(text), end + 60)].replace("\n", " ").strip()
    syms = []
    for m in _SYMBOL.finditer(text):
        s2 = m.group("base") + m.group("quote")
        if s2 not in syms and not m.group("base").isdigit() and m.group("base") != m.group("quote"):
            syms.append(s2)
    return {**picks, "evidence": evidence, "keyword_distance": dist, "symbols": syms, "n_datetimes_found": len(dts),
            "has_explicit_time": {k: _has_time(v) for k, v in evidence.items()}}


def _has_time(ev) -> bool:
    return bool(ev) and bool(re.search(r"\d{1,2}:\d{2}", ev))
