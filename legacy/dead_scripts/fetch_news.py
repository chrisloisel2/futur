#!/usr/bin/env python3
"""
scripts/fetch_news.py
=====================
Collecte les articles crypto depuis RSS + CryptoPanic (gratuit).
Calcule le sentiment NLP (VADER + keyword scoring) pour chaque article.
Stocke dans market_intel.articles avec score bullish/bearish.

Sources RSS (100% gratuites, aucune clé) :
  - CoinTelegraph, Decrypt, Bitcoin Magazine, The Block,
    CryptoNews, BeInCrypto, CoinGape, NewsBTC, Bitcoinist,
    Google News Crypto RSS, CryptoPanic RSS

Usage :
  python scripts/fetch_news.py           # Collecte complète
  python scripts/fetch_news.py --update  # Articles des 48h
"""
from __future__ import annotations

import logging, os, sys, time, hashlib, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
from pymongo import MongoClient, UpdateOne

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("fetch_news")

MONGO_URI = os.getenv("FUTUR_MONGO_URI", "mongodb://localhost:27017")
DB_INTEL  = "market_intel"

_s = requests.Session()
_s.headers["User-Agent"] = "Mozilla/5.0 (compatible; news-bot/1.0)"

# ── Sources RSS ───────────────────────────────────────────────────────────────

RSS_SOURCES = {
    "cointelegraph":    "https://cointelegraph.com/rss",
    "decrypt":          "https://decrypt.co/feed",
    "bitcoin_magazine": "https://bitcoinmagazine.com/feed",
    "theblock":         "https://www.theblock.co/rss.xml",
    "cryptonews":       "https://cryptonews.com/news/feed/",
    "beincrypto":       "https://beincrypto.com/feed/",
    "coingape":         "https://coingape.com/feed/",
    "newstbc":          "https://www.newsbtc.com/feed/",
    "bitcoinist":       "https://bitcoinist.com/feed/",
    "cryptopanic":      "https://cryptopanic.com/news/rss/",
    "ambcrypto":        "https://ambcrypto.com/feed/",
    "cryptodaily":      "https://cryptodaily.co.uk/feed",
    "coinjournal":      "https://coinjournal.net/feed/",
    "crypto_news_net":  "https://cryptonewsnet.com/feed/",
    # Google News RSS (filtrés sur crypto)
    "gnews_bitcoin":    "https://news.google.com/rss/search?q=bitcoin&hl=en-US&gl=US&ceid=US:en",
    "gnews_ethereum":   "https://news.google.com/rss/search?q=ethereum&hl=en-US&gl=US&ceid=US:en",
    "gnews_crypto":     "https://news.google.com/rss/search?q=cryptocurrency+market&hl=en-US&gl=US&ceid=US:en",
}

# ── Sentiment NLP ─────────────────────────────────────────────────────────────

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    def vader_score(text: str) -> float:
        return _vader.polarity_scores(text)["compound"]
except ImportError:
    def vader_score(text: str) -> float:
        return 0.0

# Mots-clés crypto bullish/bearish
BULLISH_KW = [
    "surge", "rally", "breakout", "bullish", "ath", "all-time high", "moon",
    "accumulate", "buy", "hodl", "adoption", "institutional", "etf approved",
    "partnership", "upgrade", "mainnet", "launch", "record", "growth",
    "recovery", "rebound", "support", "bitcoin etf", "halving", "accumulation",
]
BEARISH_KW = [
    "crash", "dump", "bearish", "sell-off", "hack", "exploit", "scam",
    "sec", "lawsuit", "ban", "regulation", "warning", "plunge", "collapse",
    "FUD", "panic", "liquidation", "fear", "crisis", "bankrupt", "fraud",
    "fine", "sanction", "crackdown", "rug", "exit scam",
]

CRYPTO_ENTITIES = ["BTC", "ETH", "SOL", "BNB", "XRP", "bitcoin", "ethereum",
                   "solana", "binance", "coinbase", "crypto"]

def compute_sentiment(title: str, summary: str) -> dict:
    text = f"{title} {summary}"
    text_lower = text.lower()

    vader  = vader_score(text)
    bull_k = sum(1 for kw in BULLISH_KW if kw in text_lower)
    bear_k = sum(1 for kw in BEARISH_KW if kw in text_lower)

    # Score final composite
    kw_score   = (bull_k - bear_k) / max(bull_k + bear_k, 1) if (bull_k + bear_k) > 0 else 0
    composite  = 0.5 * vader + 0.5 * kw_score

    # Actifs mentionnés
    mentioned = [e for e in CRYPTO_ENTITIES if e.lower() in text_lower]

    return {
        "sentiment_compound":  round(composite, 4),
        "sentiment_vader":     round(vader, 4),
        "sentiment_kw_score":  round(kw_score, 4),
        "bullish_keywords":    bull_k,
        "bearish_keywords":    bear_k,
        "crypto_mentions":     mentioned,
        "sentiment_label":     (
            "bullish"  if composite >  0.05 else
            "bearish"  if composite < -0.05 else
            "neutral"
        ),
    }


# ── Fetch RSS ─────────────────────────────────────────────────────────────────

def parse_rss_date(entry) -> datetime:
    """Parse la date d'un article RSS."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                import calendar
                ts = calendar.timegm(val)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def fetch_rss(source: str, url: str, since: Optional[datetime] = None) -> list[dict]:
    """Télécharge et parse un flux RSS."""
    try:
        resp = _s.get(url, timeout=15)
        if not resp.ok:
            log.warning(f"  {source}: HTTP {resp.status_code}")
            return []
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning(f"  {source}: {e}")
        return []

    results = []
    for entry in feed.entries:
        title   = getattr(entry, "title",   "") or ""
        summary = getattr(entry, "summary", "") or ""
        link    = getattr(entry, "link",    "") or ""
        pub_dt  = parse_rss_date(entry)

        # Filtre temporel
        if since and pub_dt < since:
            continue

        # Filtre pertinence crypto
        text_lower = (title + summary).lower()
        if not any(kw in text_lower for kw in ["bitcoin","crypto","btc","eth","blockchain","defi","nft","web3"]):
            continue

        # Hash unique
        art_hash = hashlib.md5((title + link).encode()).hexdigest()

        # Nettoyage HTML
        clean_summary = re.sub(r"<[^>]+>", " ", summary)[:500].strip()

        sentiment = compute_sentiment(title, clean_summary)

        results.append({
            "article_id":  art_hash,
            "source":      source,
            "title":       title[:200],
            "url":         link[:500],
            "summary":     clean_summary,
            "published_at": pub_dt,
            "scraped_at":  datetime.now(timezone.utc),
            **sentiment,
        })

    return results


def upsert_articles(articles: list[dict]) -> int:
    if not articles:
        return 0
    coll = MongoClient(MONGO_URI)[DB_INTEL]["articles"]
    try:
        coll.create_index("article_id", unique=True, background=True)
        coll.create_index("published_at", background=True)
        coll.create_index("sentiment_compound", background=True)
        coll.create_index("source", background=True)
    except Exception:
        pass

    ops = [
        UpdateOne({"article_id": a["article_id"]}, {"$set": a}, upsert=True)
        for a in articles
    ]
    res = MongoClient(MONGO_URI)[DB_INTEL]["articles"].bulk_write(ops, ordered=False)
    return res.upserted_count + res.modified_count


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true",
                        help="Seulement les 48 dernières heures")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=48) if args.update else None

    log.info("=" * 60)
    log.info("NEWS COLLECTOR — RSS + Sentiment NLP")
    log.info(f"Sources: {len(RSS_SOURCES)} | Since: {since or 'ALL'}")
    log.info("=" * 60)

    total_new = 0
    total_found = 0

    for source, url in RSS_SOURCES.items():
        articles = fetch_rss(source, url, since)
        n = upsert_articles(articles)
        total_found += len(articles)
        total_new   += n
        log.info(f"  {source:25s}: {len(articles):4d} articles | {n:4d} nouveaux")
        time.sleep(0.3)

    # Stats sentiment
    coll = MongoClient(MONGO_URI)[DB_INTEL]["articles"]
    total_in_db = coll.count_documents({})

    from pymongo import DESCENDING
    recent = list(coll.find({}, {"title": 1, "sentiment_label": 1, "sentiment_compound": 1,
                                  "source": 1, "published_at": 1})
                      .sort("published_at", DESCENDING).limit(10))

    log.info("=" * 60)
    log.info(f"TOTAL articles en DB: {total_in_db:,}")
    log.info(f"Collectés cette session: {total_found} | Nouveaux: {total_new}")
    log.info("=" * 60)
    log.info("=== 10 DERNIERS ARTICLES ===")
    for a in recent:
        label = a.get("sentiment_label", "?")
        icon  = "📈" if label == "bullish" else "📉" if label == "bearish" else "➖"
        score = a.get("sentiment_compound", 0)
        ts    = a.get("published_at", "?")
        ts_str = str(ts)[:16] if ts else "?"
        print(f"  {icon} [{score:+.2f}] {a.get('title','')[:60]:<60} [{ts_str}]")


if __name__ == "__main__":
    main()
