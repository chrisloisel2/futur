"""tests/test_news_collector.py — lexique sentiment, tagging, parse RSS, dédup."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.news_collector.lexicon import score_sentiment, tag_symbols
import src.institutional.data.news_collector.collector as C


def test_sentiment_sign():
    assert score_sentiment("Bitcoin surges to all-time high on ETF approval") > 0.3
    assert score_sentiment("Exchange hacked, funds stolen, market crashes") < -0.3
    assert abs(score_sentiment("Bitcoin trades sideways in quiet session")) < 0.3


def test_sentiment_negation():
    pos = score_sentiment("SEC approves the ETF")
    neg = score_sentiment("SEC denies the ETF approval, no green light")
    assert neg < pos


def test_tag_whole_word_only():
    assert tag_symbols("Solana network sees inflows") == ["SOLUSDT"]
    assert "SOLUSDT" not in tag_symbols("solar panels power the mine")  # pas 'sol'
    tags = tag_symbols("Ethereum and Bitcoin both rally")
    assert set(tags) == {"ETHUSDT", "BTCUSDT"}


def test_parse_rss_minimal():
    raw = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Bitcoin rallies hard</title><link>http://x/1</link>
        <description>BTC surges</description>
        <pubDate>Mon, 06 Jul 2026 12:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items = C._parse_rss("test", raw)
    assert len(items) == 1
    it = items[0]
    assert it["symbols"] == ["BTCUSDT"] and it["sentiment"] > 0
    assert it["source"] == "test" and it["url"] == "http://x/1"


def test_write_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STORE", tmp_path)
    recs = [{"ts": pd.Timestamp("2026-07-06T10:00:00Z"), "source": "s",
             "title": "t", "url": "http://x/1", "symbols": ["BTCUSDT"],
             "sentiment": 0.5}]
    assert C._write(recs) == 1
    assert C._write(recs) == 0            # même URL → dédup
    recs2 = [{**recs[0], "url": "http://x/2"}]
    assert C._write(recs2) == 1
    lake = C.load_news_lake()
    assert len(lake) == 2 and set(lake["url_hash"].map(len)) == {16}
