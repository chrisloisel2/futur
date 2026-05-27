"""
Scrapers runner - orchestrate multiple spiders
"""

import sys
import logging
from datetime import datetime
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import all spiders
from spiders.whale_alert import WhaleAlertSpider
from spiders.arkham import ArkhamSpider
from spiders.bitcointalk import BitcoinTalkSpider
from spiders.crypto_news import CryptoNewsSpider
from spiders.asian_crypto import AsianCryptoSpider
from spiders.specialized_forums import SpecializedForumsSpider
from spiders.social_sentiment import SocialSentimentSpider


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


class ScrapersRunner:
    """Orchestrate multiple scrapers"""

    def __init__(self):
        self.settings = get_project_settings()
        self.process = None

        # Available spiders
        self.spiders = {
            'whale_alert': WhaleAlertSpider,
            'arkham': ArkhamSpider,
            'bitcointalk': BitcoinTalkSpider,
            'crypto_news': CryptoNewsSpider,
            'asian_crypto': AsianCryptoSpider,
            'specialized_forums': SpecializedForumsSpider,
            'social_sentiment': SocialSentimentSpider,
        }

    def run_spider(self, spider_name: str):
        """Run a single spider"""
        if spider_name not in self.spiders:
            logger.error(f"Unknown spider: {spider_name}")
            return

        logger.info(f"Starting spider: {spider_name}")

        process = CrawlerProcess(self.settings)
        process.crawl(self.spiders[spider_name])
        process.start()

    def run_all(self, parallel=False):
        """Run all spiders"""
        logger.info("Starting all scrapers...")

        if parallel:
            # Run all spiders in parallel
            process = CrawlerProcess(self.settings)
            for spider_name, spider_class in self.spiders.items():
                logger.info(f"Adding spider to queue: {spider_name}")
                process.crawl(spider_class)
            process.start()
        else:
            # Run spiders sequentially
            for spider_name in self.spiders:
                logger.info(f"Running spider: {spider_name}")
                try:
                    self.run_spider(spider_name)
                except Exception as e:
                    logger.error(f"Error running {spider_name}: {e}")
                    continue

    def run_category(self, category: str):
        """Run spiders by category"""
        categories = {
            'transaction_tracking': ['whale_alert', 'arkham'],
            'forums': ['bitcointalk', 'specialized_forums'],
            'news': ['crypto_news'],
            'asian': ['asian_crypto'],
            'social': ['social_sentiment'],
        }

        if category not in categories:
            logger.error(f"Unknown category: {category}")
            return

        spiders_to_run = categories[category]
        logger.info(f"Running category '{category}': {spiders_to_run}")

        process = CrawlerProcess(self.settings)
        for spider_name in spiders_to_run:
            if spider_name in self.spiders:
                process.crawl(self.spiders[spider_name])
        process.start()

    def run_custom(self, spider_names: list):
        """Run custom list of spiders"""
        logger.info(f"Running custom spiders: {spider_names}")

        process = CrawlerProcess(self.settings)
        for spider_name in spider_names:
            if spider_name in self.spiders:
                logger.info(f"Adding spider: {spider_name}")
                process.crawl(self.spiders[spider_name])
            else:
                logger.warning(f"Unknown spider: {spider_name}")

        process.start()


def main():
    """CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(description='Crypto scrapers runner')
    parser.add_argument(
        'action',
        choices=['list', 'run', 'run-all', 'run-category'],
        help='Action to perform'
    )
    parser.add_argument(
        '--spider',
        help='Spider name (for run action)'
    )
    parser.add_argument(
        '--category',
        choices=['transaction_tracking', 'forums', 'news', 'asian'],
        help='Category to run (for run-category action)'
    )
    parser.add_argument(
        '--spiders',
        nargs='+',
        help='List of spider names (for run action)'
    )
    parser.add_argument(
        '--parallel',
        action='store_true',
        help='Run spiders in parallel'
    )

    args = parser.parse_args()
    runner = ScrapersRunner()

    if args.action == 'list':
        print("\nAvailable spiders:")
        for name in runner.spiders:
            print(f"  - {name}")
        print("\nCategories:")
        print("  - transaction_tracking: whale_alert, arkham")
        print("  - forums: bitcointalk, specialized_forums")
        print("  - news: crypto_news")
        print("  - asian: asian_crypto")

    elif args.action == 'run':
        if args.spider:
            runner.run_spider(args.spider)
        elif args.spiders:
            runner.run_custom(args.spiders)
        else:
            print("Error: --spider or --spiders required for 'run' action")

    elif args.action == 'run-all':
        runner.run_all(parallel=args.parallel)

    elif args.action == 'run-category':
        if not args.category:
            print("Error: --category required for 'run-category' action")
        else:
            runner.run_category(args.category)


if __name__ == '__main__':
    main()
