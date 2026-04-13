"""
WaybackRssSpider — classe de base pour les spiders RSS avec support historique.

En mode live   (from_date=None) : fetch direct des URLs RSS.
En mode history (from_date set)  : utilise le CDX API Wayback Machine pour
récupérer les snapshots RSS archivés jour par jour, puis les parse normalement.

Sous-classes doivent définir :
  rss_urls  : list[str]       — URLs RSS cibles
  parse_rss(response)          — parse un feed RSS (live ou Wayback), yield items

Utilisation spider args :
  scrapy crawl my_spider -a from_date=2021-01-01 -a to_date=2024-12-31
"""
import json
import scrapy
from datetime import date


class WaybackRssSpider(scrapy.Spider):
    # Sous-classe : définir ces attributs
    rss_urls: list = []

    # Nb max de snapshots CDX par feed URL (collapse=1/jour → 200 ≈ 1 snapshot tous les 5j sur 3 ans)
    _cdx_limit: int = 200

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date               # "YYYY-MM-DD" ou None
        self.to_date = to_date or str(date.today())

        # Ajouter web.archive.org aux domaines autorisés en mode historique
        if self.from_date and self.allowed_domains:
            self.allowed_domains = list(self.allowed_domains) + ["web.archive.org"]

    @property
    def _historical(self) -> bool:
        return bool(self.from_date)

    # ── Routing ───────────────────────────────────────────────────────────────

    async def start(self):
        for request in self.start_requests():
            yield request

    def start_requests(self):
        if self._historical:
            yield from self._cdx_requests()
        else:
            for url in self.rss_urls:
                yield scrapy.Request(url, callback=self.parse_rss)

    # ── Wayback CDX ───────────────────────────────────────────────────────────

    def _cdx_requests(self):
        from_ts = self.from_date.replace("-", "")
        to_ts = self.to_date.replace("-", "")

        for rss_url in self.rss_urls:
            cdx_url = (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={rss_url}"
                "&output=json&fl=timestamp"
                f"&from={from_ts}&to={to_ts}"
                f"&limit={self._cdx_limit}"
                "&collapse=timestamp:8"   # 1 snapshot par jour max
                "&matchType=exact"
            )
            yield scrapy.Request(
                cdx_url,
                callback=self._parse_cdx,
                meta={"rss_url": rss_url},
                priority=10,
            )

    def _parse_cdx(self, response):
        rss_url = response.meta["rss_url"]
        try:
            rows = json.loads(response.text)
        except Exception:
            self.logger.warning("CDX parse error pour %s", rss_url)
            return

        if len(rows) <= 1:
            self.logger.info("Aucun snapshot Wayback pour %s", rss_url)
            return

        self.logger.info("Wayback : %d snapshots pour %s", len(rows) - 1, rss_url)

        for row in rows[1:]:   # rows[0] = en-tête ["timestamp"]
            ts = row[0]
            # id_/ = contenu brut sans toolbar Wayback injectée
            wayback_url = f"https://web.archive.org/web/{ts}id_/{rss_url}"
            yield scrapy.Request(
                wayback_url,
                callback=self.parse_rss,
                meta={"wayback_ts": ts, "original_rss": rss_url},
                dont_filter=True,
            )

    # ── À implémenter ─────────────────────────────────────────────────────────

    def parse_rss(self, response):
        raise NotImplementedError("Implémenter parse_rss() dans la sous-classe")
