"""
src/institutional/data/news_collector/collector.py
─────────────────────────────────────────────────────────────────────────────
Collecte news/social depuis SOURCES PUBLIQUES documentées, parse en stdlib
(pas de feedparser — disque contraint), dédup par hash d'URL, tag symboles +
sentiment, écriture append-only immutable (partitions par date).

Sources par défaut (200 OK, sans auth) :
  RSS : Cointelegraph, Decrypt, Bitcoin Magazine, NewsBTC, CoinDesk (suivi 308)
  JSON: CoinGecko trending (attention/social)
Optionnelles (token env) : CRYPTOPANIC_TOKEN, REDDIT_TOKEN — non activées sans clé.

Store : data/news_raw/date=YYYY-MM-DD/part-*.parquet  (gitignored)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

import pandas as pd

from src.institutional.data.news_collector.lexicon import score_sentiment, tag_symbols

ROOT = Path(__file__).resolve().parents[4]
STORE = ROOT / "data" / "news_raw"
UA = "Mozilla/5.0 (compatible; futur-research/1.0; public-feeds)"

RSS_SOURCES = {
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "newsbtc": "https://www.newsbtc.com/feed/",
    # coindesk retiré : redirection 308 relative en boucle (anti-bot cassé
    # pour l'accès programmatique) → source morte, non reproductible.
}

_TAG = re.compile(r"<[^>]+>")


def _get(url: str, timeout: float = 20.0) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _clean(s: str) -> str:
    return _TAG.sub("", s or "").replace("&amp;", "&").replace("&#39;", "'").strip()


def _parse_rss(source: str, raw: bytes) -> List[Dict]:
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        pub = item.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub) if pub else datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)
        text = _clean(title) + ". " + _clean(desc)
        out.append({
            "ts": ts.astimezone(timezone.utc), "source": source,
            "title": _clean(title)[:400], "url": link.strip(),
            "symbols": tag_symbols(text), "sentiment": round(score_sentiment(text), 4),
        })
    return out


def _fetch_coingecko_trending() -> List[Dict]:
    raw = _get("https://api.coingecko.com/api/v3/search/trending")
    if not raw:
        return []
    try:
        coins = json.loads(raw).get("coins", [])
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for c in coins:
        it = c.get("item", {})
        name = it.get("name", "")
        out.append({
            "ts": now, "source": "coingecko_trending",
            "title": f"TRENDING: {name} (rank {it.get('market_cap_rank')})",
            "url": f"coingecko://trending/{it.get('id')}",
            "symbols": tag_symbols(name), "sentiment": 0.3,  # attention = léger biais +
        })
    return out


def _fetch_optional_token_sources() -> List[Dict]:
    """CryptoPanic / Reddit : UNIQUEMENT si token fourni (ToS-compliant)."""
    out = []
    cp = os.environ.get("CRYPTOPANIC_TOKEN")
    if cp:
        raw = _get(f"https://cryptopanic.com/api/v1/posts/?auth_token={cp}&public=true")
        if raw:
            try:
                for p in json.loads(raw).get("results", []):
                    title = p.get("title", "")
                    ts = pd.Timestamp(p.get("published_at")).to_pydatetime()
                    out.append({"ts": ts, "source": "cryptopanic",
                                "title": title[:400], "url": p.get("url", ""),
                                "symbols": tag_symbols(title),
                                "sentiment": round(score_sentiment(title), 4)})
            except Exception:
                pass
    return out


def _write(records: List[Dict]) -> int:
    if not records:
        return 0
    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["url_hash"] = df["url"].map(lambda u: hashlib.sha256(u.encode()).hexdigest()[:16])
    df = df.sort_values("ts")
    written = 0
    for date, g in df.groupby(df["ts"].dt.strftime("%Y-%m-%d")):
        part_dir = STORE / f"date={date}"
        part_dir.mkdir(parents=True, exist_ok=True)
        seen = set()
        for pq in part_dir.glob("*.parquet"):
            try:
                seen |= set(pd.read_parquet(pq, columns=["url_hash"])["url_hash"])
            except Exception:
                pass
        fresh = g[~g["url_hash"].isin(seen)]
        if fresh.empty:
            continue
        fresh = fresh.copy()
        fresh["symbols"] = fresh["symbols"].map(lambda s: ",".join(s))
        tmp = part_dir / f".part-{uuid.uuid4().hex[:8]}.tmp"
        final = part_dir / f"part-{datetime.now(timezone.utc):%H%M%S}-{uuid.uuid4().hex[:8]}.parquet"
        fresh.to_parquet(tmp, index=False)
        tmp.replace(final)
        written += len(fresh)
    return written


def collect_once() -> Dict:
    records: List[Dict] = []
    per_source = {}
    for name, url in RSS_SOURCES.items():
        raw = _get(url)
        items = _parse_rss(name, raw) if raw else []
        per_source[name] = len(items)
        records += items
    cg = _fetch_coingecko_trending()
    per_source["coingecko_trending"] = len(cg)
    records += cg
    opt = _fetch_optional_token_sources()
    if opt:
        per_source["token_sources"] = len(opt)
    records += opt
    n_new = _write(records)
    tagged = sum(1 for r in records if r["symbols"])
    return {"fetched": len(records), "new_written": n_new,
            "tagged": tagged, "per_source": per_source}


def load_news_lake() -> pd.DataFrame:
    parts = sorted(STORE.glob("date=*/part-*.parquet"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.drop_duplicates(subset=["url_hash"]).sort_values("ts")
