from email.utils import parsedate_to_datetime

from marketintel.items import SignalItem
from marketintel.spiders.base import WaybackRssSpider


class ReutersRssSpider(WaybackRssSpider):
    name = "reuters_rss"
    allowed_domains = ["feeds.reuters.com", "reuters.com"]
    rss_urls = [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/technologyNews",
    ]

    def parse_rss(self, response):
        wayback_ts = response.meta.get("wayback_ts")

        for node in response.xpath("//item"):
            link = node.xpath("link/text()").get()
            title = node.xpath("title/text()").get()
            pub_date = node.xpath("pubDate/text()").get()
            description = node.xpath("description/text()").get()

            published_at = None
            if pub_date:
                try:
                    published_at = parsedate_to_datetime(pub_date).isoformat()
                except Exception:
                    published_at = pub_date

            yield SignalItem(
                source="reuters",
                source_type="news",
                asset="MACRO",
                title=title,
                text=description,
                url=link,
                published_at=published_at,
                language="en",
                event_type="news",
                sentiment=None,
                importance=0.95,
                confidence=0.95,
                metadata={
                    "feed": response.meta.get("original_rss", response.url),
                    "wayback_ts": wayback_ts,
                },
                raw={"title": title, "description": description},
            )
