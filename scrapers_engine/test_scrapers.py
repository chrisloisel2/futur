"""
Test script for scrapers - verify they work correctly
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

# Import spiders
from spiders.whale_alert import WhaleAlertSpider
from spiders.arkham import ArkhamSpider
from spiders.crypto_news import CryptoNewsSpider


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

logger = logging.getLogger(__name__)


def test_spider(spider_class, name):
    """Test a single spider"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing spider: {name}")
    logger.info(f"{'='*60}")

    try:
        settings = get_project_settings()
        settings.set('LOG_LEVEL', 'INFO')
        settings.set('CLOSESPIDER_ITEMCOUNT', 5)  # Stop after 5 items

        process = CrawlerProcess(settings)
        process.crawl(spider_class)
        process.start()

        logger.info(f"✓ {name} test completed successfully")
        return True

    except Exception as e:
        logger.error(f"✗ {name} test failed: {e}")
        return False


def main():
    """Run tests"""
    logger.info("Starting scrapers test suite...")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")

    results = {}

    # Test transaction tracking
    logger.info("\n\n### TESTING TRANSACTION TRACKING ###")
    results['whale_alert'] = test_spider(WhaleAlertSpider, 'Whale Alert')

    # Test news scrapers
    logger.info("\n\n### TESTING NEWS SCRAPERS ###")
    results['crypto_news'] = test_spider(CryptoNewsSpider, 'Crypto News')

    # Summary
    logger.info("\n\n" + "="*60)
    logger.info("TEST SUMMARY")
    logger.info("="*60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for spider, result in results.items():
        status = "✓ PASSED" if result else "✗ FAILED"
        logger.info(f"{spider:20} {status}")

    logger.info(f"\nTotal: {passed}/{total} passed")

    if passed == total:
        logger.info("\n🎉 All tests passed!")
    else:
        logger.warning(f"\n⚠️  {total - passed} test(s) failed")


if __name__ == '__main__':
    main()
