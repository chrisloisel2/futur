import logging

from api_collectors.mongo import MongoWriter
from api_collectors.collectors.binance_api import fetch_binance_funding_rates
from api_collectors.collectors.coingecko_api import fetch_coingecko_markets
from api_collectors.collectors.alternative_me_api import fetch_fear_greed
from api_collectors.collectors.fred_api import fetch_fred_macro
from api_collectors.collectors.newsapi_api import fetch_newsapi_everything

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

_COLLECTORS = [
    ("binance_funding",  fetch_binance_funding_rates),
    ("coingecko",        fetch_coingecko_markets),
    ("fear_greed",       fetch_fear_greed),
    ("fred_macro",       fetch_fred_macro),
    ("newsapi",          fetch_newsapi_everything),
]


def main() -> None:
    writer = MongoWriter()
    try:
        all_docs = []
        for name, fn in _COLLECTORS:
            try:
                docs = fn()
                logging.info("%s -> %d documents", name, len(docs))
                all_docs.extend(docs)
            except Exception:
                logging.exception("collector failed: %s", name)

        changed = writer.upsert_many(all_docs)
        logging.info("mongo upsert complete -> %d docs written/updated", changed)
    finally:
        writer.close()


if __name__ == "__main__":
    main()
