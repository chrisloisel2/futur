"""
Scheduler for running scrapers periodically
"""

import schedule
import time
import logging
from datetime import datetime
from runner import ScrapersRunner


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


class ScrapersScheduler:
    """Schedule periodic scraping jobs"""

    def __init__(self):
        self.runner = ScrapersRunner()

    def run_news_scraping(self):
        """Run news scrapers (frequent)"""
        logger.info("=== Running scheduled news scraping ===")
        try:
            self.runner.run_custom(['crypto_news', 'asian_crypto'])
        except Exception as e:
            logger.error(f"Error in news scraping: {e}")

    def run_transaction_tracking(self):
        """Run transaction tracking (frequent)"""
        logger.info("=== Running scheduled transaction tracking ===")
        try:
            self.runner.run_category('transaction_tracking')
        except Exception as e:
            logger.error(f"Error in transaction tracking: {e}")

    def run_forum_scraping(self):
        """Run forum scrapers (less frequent)"""
        logger.info("=== Running scheduled forum scraping ===")
        try:
            self.runner.run_category('forums')
        except Exception as e:
            logger.error(f"Error in forum scraping: {e}")

    def setup_schedule(self):
        """Setup scraping schedule"""
        # High-frequency sources (every 15 minutes)
        schedule.every(15).minutes.do(self.run_news_scraping)
        schedule.every(15).minutes.do(self.run_transaction_tracking)

        # Medium-frequency sources (every hour)
        schedule.every(1).hours.do(self.run_forum_scraping)

        logger.info("Scheduler setup complete")
        logger.info("News & Transactions: every 15 minutes")
        logger.info("Forums: every 1 hour")

    def run(self):
        """Run scheduler"""
        self.setup_schedule()

        logger.info("Scheduler started. Press Ctrl+C to stop.")

        # Run initial scraping
        logger.info("Running initial scraping...")
        self.run_news_scraping()
        self.run_transaction_tracking()

        # Start scheduler loop
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")


def main():
    """CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(description='Crypto scrapers scheduler')
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once instead of continuously'
    )

    args = parser.parse_args()
    scheduler = ScrapersScheduler()

    if args.once:
        logger.info("Running scrapers once...")
        scheduler.run_news_scraping()
        scheduler.run_transaction_tracking()
        scheduler.run_forum_scraping()
    else:
        scheduler.run()


if __name__ == '__main__':
    main()
