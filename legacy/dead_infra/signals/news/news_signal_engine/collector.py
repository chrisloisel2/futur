"""
Collecteur de données depuis NEWS APIs.
Support multiple sources: NewsAPI, Newsdata.io, etc.
"""

import requests
from datetime import datetime, timedelta
from typing import List, Optional
import time
import logging

from .models import RawNewsArticle
from config import (
    ALL_ENTITIES,
    NEWS_API_RATE_LIMIT,
    NEWS_SEARCH_LANGUAGES
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NewsAPICollector:
    """Collecteur via NewsAPI.org"""

    def __init__(self, api_key: str):
        """
        Initialiser avec API key de newsapi.org
        """
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2"
        self.rate_limit_remaining = NEWS_API_RATE_LIMIT
        self.rate_limit_reset = time.time()

    def search(
        self,
        query: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        language: str = "en",
        page_size: int = 100
    ) -> List[RawNewsArticle]:
        """
        Rechercher articles

        Args:
            query: requête de recherche
            from_date: date de début
            to_date: date de fin
            language: langue
            page_size: nombre de résultats max
        """

        # Dates par défaut (dernières 24h)
        if to_date is None:
            to_date = datetime.utcnow()
        if from_date is None:
            from_date = to_date - timedelta(hours=24)

        params = {
            "q": query,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "language": language,
            "pageSize": min(page_size, 100),
            "sortBy": "publishedAt",
            "apiKey": self.api_key
        }

        try:
            response = requests.get(
                f"{self.base_url}/everything",
                params=params,
                timeout=30
            )

            self._update_rate_limit()

            if response.status_code != 200:
                logger.error(f"API error: {response.status_code}")
                return []

            data = response.json()

            if data.get("status") != "ok":
                logger.error(f"API error: {data.get('message')}")
                return []

            articles = self._parse_response(data.get("articles", []))
            logger.info(f"Collected {len(articles)} articles for query: {query}")

            return articles

        except Exception as e:
            logger.error(f"Error collecting articles: {e}")
            return []

    def search_entities(
        self,
        entities: Optional[List[str]] = None,
        hours_back: int = 24,
        max_results: int = 100
    ) -> List[RawNewsArticle]:
        """
        Rechercher articles mentionnant des entités crypto/macro

        Args:
            entities: liste d'entités (None = toutes)
            hours_back: heures en arrière
            max_results: max articles
        """

        if entities is None:
            entities = ALL_ENTITIES[:10]  # top 10 pour éviter requêtes trop longues

        # Construire requête
        # OR entre entités
        query_parts = []
        for entity in entities[:5]:  # limiter pour longueur query
            if " " in entity:
                query_parts.append(f'"{entity}"')
            else:
                query_parts.append(entity)

        query = " OR ".join(query_parts)

        # Ajouter contexte crypto
        query += " AND (crypto OR cryptocurrency OR bitcoin OR blockchain)"

        to_date = datetime.utcnow()
        from_date = to_date - timedelta(hours=hours_back)

        return self.search(query, from_date, to_date, page_size=max_results)

    def _parse_response(self, articles_data: List[dict]) -> List[RawNewsArticle]:
        """Parser réponse NewsAPI → RawNewsArticle"""

        articles = []
        timestamp_collecte = datetime.utcnow()

        for item in articles_data:
            try:
                # Parse publication date
                pub_date_str = item.get("publishedAt")
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))

                # Extract data
                title = item.get("title", "")
                description = item.get("description", "")
                content = item.get("content", "")

                # Combiner description + content
                body = f"{description}\n\n{content}".strip()

                source_name = item.get("source", {}).get("name", "Unknown")
                url = item.get("url", "")

                # Générer article ID depuis URL
                import hashlib
                article_id = hashlib.md5(url.encode()).hexdigest()

                # Détecter langue (basique)
                lang = "en"  # NewsAPI filtre déjà

                article = RawNewsArticle(
                    article_id=article_id,
                    title=title,
                    body=body,
                    lang=lang,
                    source=source_name,
                    source_url=url,
                    timestamp_publication=pub_date,
                    timestamp_collecte=timestamp_collecte,
                    categories=[],  # NewsAPI ne fournit pas
                    links=[url]
                )

                articles.append(article)

            except Exception as e:
                logger.warning(f"Error parsing article: {e}")
                continue

        return articles

    def _update_rate_limit(self):
        """MAJ compteur rate limit"""
        self.rate_limit_remaining -= 1

        if self.rate_limit_remaining <= 0:
            # Attendre reset (1h)
            wait_time = self.rate_limit_reset - time.time()
            if wait_time > 0:
                logger.warning(f"Rate limit hit, waiting {wait_time:.0f}s")
                time.sleep(wait_time)

            self.rate_limit_remaining = NEWS_API_RATE_LIMIT
            self.rate_limit_reset = time.time() + 3600  # +1h


class NewsdataIOCollector:
    """Collecteur via Newsdata.io (alternative à NewsAPI)"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://newsdata.io/api/1/news"

    def search(
        self,
        query: str,
        language: str = "en",
        max_results: int = 100
    ) -> List[RawNewsArticle]:
        """Rechercher articles via Newsdata.io"""

        params = {
            "apikey": self.api_key,
            "q": query,
            "language": language,
            "size": min(max_results, 50)  # max 50 par requête
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=30)

            if response.status_code != 200:
                logger.error(f"API error: {response.status_code}")
                return []

            data = response.json()

            if data.get("status") != "success":
                logger.error(f"API error: {data.get('message')}")
                return []

            articles = self._parse_response(data.get("results", []))
            return articles

        except Exception as e:
            logger.error(f"Error: {e}")
            return []

    def _parse_response(self, results: List[dict]) -> List[RawNewsArticle]:
        """Parser Newsdata.io → RawNewsArticle"""

        articles = []
        timestamp_collecte = datetime.utcnow()

        for item in results:
            try:
                # Parse date
                pub_date_str = item.get("pubDate")
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))

                title = item.get("title", "")
                description = item.get("description", "")
                content = item.get("content", "")

                body = f"{description}\n\n{content}".strip()

                source_name = item.get("source_id", "Unknown")
                url = item.get("link", "")

                import hashlib
                article_id = hashlib.md5(url.encode()).hexdigest()

                article = RawNewsArticle(
                    article_id=article_id,
                    title=title,
                    body=body,
                    lang=item.get("language", "en"),
                    source=source_name,
                    source_url=url,
                    timestamp_publication=pub_date,
                    timestamp_collecte=timestamp_collecte,
                    country=item.get("country", [None])[0] if item.get("country") else None,
                    categories=item.get("category", [])
                )

                articles.append(article)

            except Exception as e:
                logger.warning(f"Error parsing: {e}")
                continue

        return articles


class MultiSourceCollector:
    """Collecteur agrégeant plusieurs sources NEWS"""

    def __init__(
        self,
        newsapi_key: Optional[str] = None,
        newsdataio_key: Optional[str] = None
    ):
        self.collectors = []

        if newsapi_key:
            self.collectors.append(NewsAPICollector(newsapi_key))

        if newsdataio_key:
            self.collectors.append(NewsdataIOCollector(newsdataio_key))

    def search_all(
        self,
        query: str,
        max_results_per_source: int = 100
    ) -> List[RawNewsArticle]:
        """Rechercher sur toutes les sources"""

        all_articles = []

        for collector in self.collectors:
            try:
                articles = collector.search(query, max_results=max_results_per_source)
                all_articles.extend(articles)
            except Exception as e:
                logger.error(f"Collector error: {e}")
                continue

        # Déduplication par article_id
        seen_ids = set()
        unique_articles = []

        for article in all_articles:
            if article.article_id not in seen_ids:
                seen_ids.add(article.article_id)
                unique_articles.append(article)

        logger.info(f"Collected {len(unique_articles)} unique articles from {len(self.collectors)} sources")

        return unique_articles
