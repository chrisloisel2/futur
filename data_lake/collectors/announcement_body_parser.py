#!/usr/bin/env python3
"""
announcement_body_parser.py -- fonctions PURES : du corps brut d'une annonce au texte lisible.

Binance CMS renvoie le corps comme un arbre de noeuds JSON ({"node":"element","tag":...,"child":[...]}).
OKX et Bybit renvoient du HTML. Aucune requete ici, aucune heuristique de trading : uniquement du texte.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional

BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "table", "thead", "tbody"}
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t  ]+")
_NL = re.compile(r"\n{3,}")


def flatten_cms_body(body: Any) -> str:
    """Arbre de noeuds Binance CMS -> texte. Accepte l'arbre deja decode ou la chaine JSON."""
    if isinstance(body, str):
        b = body.strip()
        if not b:
            return ""
        if b[0] in "{[":
            try:
                body = json.loads(b)
            except ValueError:
                return clean_html(b)
        else:
            return clean_html(b)
    out: List[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for x in n:
                walk(x)
            return
        if not isinstance(n, dict):
            return
        if n.get("node") == "text":
            out.append(html.unescape(str(n.get("text", ""))))
            return
        tag = (n.get("tag") or "").lower()
        block = tag in BLOCK_TAGS
        if block and out and not out[-1].endswith("\n"):
            out.append("\n")
        walk(n.get("child"))
        if tag == "td" or tag == "th":
            out.append("\t")
        if block:
            out.append("\n")

    walk(body)
    return normalise("".join(out))


def clean_html(raw: str) -> str:
    """HTML -> texte : scripts retires, balises de bloc converties en sauts de ligne."""
    s = _SCRIPT.sub(" ", raw or "")
    s = re.sub(r"</(p|div|li|tr|h[1-6]|section|table)>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]>", "\t", s, flags=re.I)
    s = _TAG.sub(" ", s)
    return normalise(html.unescape(s))


def normalise(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _NL.sub("\n\n", s).strip()


def parse_payload(source: str, payload: Any) -> Dict[str, Optional[str]]:
    """-> {title, body_text, publication_ts, update_ts} depuis la charge brute d'une source."""
    out: Dict[str, Optional[str]] = {"title": None, "body_text": "", "publication_ts": None, "update_ts": None}
    if source == "binance":
        d = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(d, dict):
            return out
        out["title"] = d.get("title")
        out["body_text"] = flatten_cms_body(d.get("body"))
        for k, dst in (("releaseDate", "publication_ts"), ("publishDate", "publication_ts"), ("updateDate", "update_ts"), ("lastUpdateDate", "update_ts")):
            v = d.get(k)
            if v and not out[dst]:
                out[dst] = ms_to_iso(v)
        return out
    if isinstance(payload, (str, bytes)):
        raw = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        out["body_text"] = extract_main_html(raw)
        m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        if m:
            out["title"] = normalise(html.unescape(m.group(1)))
        for pat in (r'"datePublished"\s*:\s*"([^"]+)"', r'property="article:published_time"\s+content="([^"]+)"'):
            m = re.search(pat, raw)
            if m:
                out["publication_ts"] = m.group(1); break
        m = re.search(r'"dateModified"\s*:\s*"([^"]+)"', raw)
        if m:
            out["update_ts"] = m.group(1)
    return out


def extract_main_html(raw: str) -> str:
    """Corps d'article : on privilegie un bloc JSON-LD articleBody, sinon la page nettoyee."""
    m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if m:
        try:
            return normalise(json.loads('"' + m.group(1) + '"'))
        except ValueError:
            pass
    return clean_html(raw)


def ms_to_iso(v: Any) -> Optional[str]:
    from datetime import datetime, timezone
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v) if v else None
    n = n // 1000 if n > 10**14 else n
    if n > 10**12 * 10:
        return None
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat(timespec="seconds")
