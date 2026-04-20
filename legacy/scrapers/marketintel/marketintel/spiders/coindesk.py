from marketintel.items import SignalItem
from marketintel.spiders.base import WaybackRssSpider


class CoindeskSpider(WaybackRssSpider):
    name = "coindesk"
    allowed_domains = ["www.coindesk.com"]
    rss_urls = ["https://www.coindesk.com/arc/outboundfeeds/rss/"]

    def parse_rss(self, response):
        for node in response.xpath("//item"):
            title = node.xpath("title/text()").get()
            link = node.xpath("link/text()").get()
            pub_date = node.xpath("pubDate/text()").get()
            desc = node.xpath("description/text()").get()

            if self._historical:
                # Mode historique : pas de suivi de lien, on utilise la description
                yield SignalItem(
                    source="coindesk",
                    source_type="news",
                    asset=self._detect_asset(f"{title} {desc}"),
                    title=title,
                    text=desc,
                    url=link,
                    published_at=pub_date,
                    language="en",
                    event_type="crypto_news",
                    importance=0.80,
                    confidence=0.80,
                    metadata={
                        "wayback_ts": response.meta.get("wayback_ts"),
                        "feed": response.meta.get("original_rss", response.url),
                    },
                    raw={},
                )
            else:
                yield response.follow(
                    link,
                    callback=self._parse_article,
                    meta={
                        "title": title,
                        "published_at": pub_date,
                        "description": desc,
                        "url": link,
                    },
                )

    def _parse_article(self, response):
        paragraphs = response.xpath("//article//p//text()").getall()
        text = " ".join(t.strip() for t in paragraphs if t.strip())

        yield SignalItem(
            source="coindesk",
            source_type="news",
            asset=self._detect_asset(text),
            title=response.meta["title"],
            text=text,
            url=response.meta["url"],
            published_at=response.meta["published_at"],
            language="en",
            event_type="crypto_news",
            importance=0.80,
            confidence=0.80,
            metadata={"description": response.meta.get("description")},
            raw={"html_url": response.url},
        )

    @staticmethod
    def _detect_asset(text: str) -> str:
        text = (text or "").upper()
        if "BITCOIN" in text or "BTC" in text:
            return "BTC"
        if "ETHEREUM" in text or "ETH" in text:
            return "ETH"
        if "SOLANA" in text or "SOL" in text:
            return "SOL"
        return "TOTAL"
