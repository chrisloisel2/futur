"""Collecteur de signaux news/social — SOURCES PUBLIQUES DOCUMENTÉES uniquement.

Doctrine (inscrite dans le projet) : APIs publiques & RSS officiels, hashable et
reproductible. AUCUN scraping anti-bot / contournement Cloudflare / ToS-violation
(non reproductible → invalide pour un backtest causal, et bannissable). Les
sources à OAuth (Reddit, CryptoPanic) sont OPTIONNELLES sur token fourni.

Produit un event lake news append-only : (ts, source, title, url, symbols[],
sentiment[-1,1]) → agrégé en séries quotidiennes par actif, croisables dans
l'EDGE LAB et injectables (plus tard, si walk-forward le valide) comme features.
"""
from src.institutional.data.news_collector.collector import (  # noqa: F401
    collect_once, load_news_lake,
)
