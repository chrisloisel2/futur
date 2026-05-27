from marketintel.items import SignalItem
from marketintel.spiders.base import WaybackRssSpider


class DecryptSpider(WaybackRssSpider):
    name = "decrypt"
    allowed_domains = ["decrypt.co"]
    rss_urls = ["https://decrypt.co/feed"]

    def parse_rss(self, response):
        for node in response.xpath("//item"):
            title = node.xpath("title/text()").get()
            link = node.xpath("link/text()").get()
            pub_date = node.xpath("pubDate/text()").get()
            desc = node.xpath("description/text()").get()

            yield SignalItem(
                source="decrypt",
                source_type="news",
                asset="TOTAL",
                title=title,
                text=desc,
                url=link,
                published_at=pub_date,
                language="en",
                event_type="crypto_news",
                importance=0.60,
                confidence=0.60,
                metadata={
                    "feed": response.meta.get("original_rss", response.url),
                    "wayback_ts": response.meta.get("wayback_ts"),
                },
                raw={},
            )
