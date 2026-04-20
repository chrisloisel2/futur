import json
import re
from urllib.parse import urljoin, urlparse

import scrapy


class CoindeskBitcoinSpider(scrapy.Spider):
    name = "coindesk_bitcoin_all"
    allowed_domains = ["coindesk.com", "www.coindesk.com"]

    MAX_PAGES = 2000  # borne haute de sécurité
    start_urls = ["https://www.coindesk.com/tag/bitcoin"]

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS": 8,
        "AUTOTHROTTLE_ENABLED": True,
        "FEED_EXPORT_ENCODING": "utf-8",
        "LOG_LEVEL": "INFO",
        "USER_AGENT": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
    }

    seen_articles = set()

    def start_requests(self):
        # Page 1 sans suffixe
        yield scrapy.Request(
            "https://www.coindesk.com/tag/bitcoin",
            callback=self.parse_listing,
            meta={"page": 1},
        )

        # Pages numérotées
        for page in range(2, self.MAX_PAGES + 1):
            yield scrapy.Request(
                f"https://www.coindesk.com/tag/bitcoin/{page}",
                callback=self.parse_listing,
                meta={"page": page},
            )

    def parse_listing(self, response):
        page = response.meta.get("page")

        # Si la page est vide / 404 → arrêt logique
        if response.status != 200 or not response.css("a::attr(href)").get():
            self.logger.info(f"STOP pagination at page {page}")
            return

        links = response.css("a::attr(href)").getall()

        for href in links:
            url = urljoin(response.url, href)

            if not self._is_article_url(url):
                continue

            if url in self.seen_articles:
                continue

            self.seen_articles.add(url)
            yield scrapy.Request(url, callback=self.parse_article)

    def parse_article(self, response):
        if response.status != 200:
            return

        ld_items = self._extract_jsonld(response)
        article_ld = self._find_article_ld(ld_items)

        title = None
        body = None
        published = None
        author = None
        tags = []

        if article_ld:
            title = article_ld.get("headline")
            body = article_ld.get("articleBody")
            published = article_ld.get("datePublished")

            a = article_ld.get("author")
            if isinstance(a, dict):
                author = a.get("name")
            elif isinstance(a, list) and a and isinstance(a[0], dict):
                author = a[0].get("name")

            kw = article_ld.get("keywords")
            if isinstance(kw, str):
                tags = [x.strip() for x in kw.split(",")]

        if not title:
            title = response.css("h1::text").get()

        if not body:
            body = "\n".join(
                t.strip()
                for t in response.css("article p::text").getall()
                if t.strip()
            )

        # Filtre sécurité Bitcoin
        text = f"{response.url} {title or ''} {body or ''}".lower()
        if "bitcoin" not in text and "btc" not in text:
            return

        yield {
            "url": response.url,
            "title": title,
            "published": published,
            "author": author,
            "tags": tags,
            "body": body,
        }

    # ---------------- HELPERS ----------------

    def _is_article_url(self, url: str) -> bool:
        u = urlparse(url)
        if not u.netloc.endswith("coindesk.com"):
            return False

        path = (u.path or "").lower()

        if any(x in path for x in [
            "/video/",
            "/tv/",
            "/podcast",
            "/events",
            "/learn",
            "/about",
            "/privacy",
            "/terms",
            "/careers",
            "/tag/bitcoin",
        ]):
            return False

        # Pattern classique Coindesk
        if re.search(r"/\d{4}/\d{2}/\d{2}/", path):
            return True

        # Slug long (fallback)
        return len(path.strip("/").split("/")) >= 2

    def _extract_jsonld(self, response):
        items = []
        for raw in response.xpath("//script[@type='application/ld+json']/text()").getall():
            try:
                data = json.loads(raw)
            except Exception:
                continue

            if isinstance(data, list):
                items.extend(x for x in data if isinstance(x, dict))
            elif isinstance(data, dict):
                if "@graph" in data:
                    items.extend(
                        x for x in data["@graph"] if isinstance(x, dict)
                    )
                else:
                    items.append(data)
        return items

    def _find_article_ld(self, items):
        for obj in items:
            t = obj.get("@type")
            if isinstance(t, list) and "NewsArticle" in t:
                return obj
            if t in ("NewsArticle", "Article"):
                return obj
        return None
