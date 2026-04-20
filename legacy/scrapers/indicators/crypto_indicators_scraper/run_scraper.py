#!/usr/bin/env python3
"""
Launch script for crypto indicators scraper.

Usage:
    # Scrape all symbols from S3 for all years
    python run_scraper.py

    # Scrape specific symbols
    python run_scraper.py --symbols BTCUSDT,ETHUSDT,BNBUSDT

    # Scrape specific year range
    python run_scraper.py --start-year 2023 --end-year 2024

    # Scrape with custom settings
    python run_scraper.py --symbols BTCUSDT --start-year 2024 --proxy-enabled
"""
import argparse
import logging
import sys
from pathlib import Path
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

# Add project to path
project_path = Path(__file__).parent
sys.path.insert(0, str(project_path))

from crypto_indicators_scraper.spiders.crypto_indicators_spider import CryptoIndicatorsSpider

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Crypto Indicators Scraper')

    parser.add_argument(
        '--symbols',
        type=str,
        help='Comma-separated list of symbols to scrape (e.g., BTCUSDT,ETHUSDT). If not provided, will scrape all symbols from S3.'
    )

    parser.add_argument(
        '--start-year',
        type=int,
        help='Start year for scraping (default: 2017)'
    )

    parser.add_argument(
        '--end-year',
        type=int,
        help='End year for scraping (default: current year)'
    )

    parser.add_argument(
        '--bucket',
        type=str,
        default='qbia',
        help='S3 bucket name (default: qbia)'
    )

    parser.add_argument(
        '--prefix',
        type=str,
        default='bourse/mintrad',
        help='S3 prefix for source data (default: bourse/mintrad)'
    )

    parser.add_argument(
        '--proxy-enabled',
        action='store_true',
        help='Enable proxy rotation'
    )

    parser.add_argument(
        '--no-proxy',
        action='store_true',
        help='Disable proxy rotation'
    )

    parser.add_argument(
        '--concurrent-requests',
        type=int,
        default=32,
        help='Number of concurrent requests (default: 32)'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Batch size for S3 uploads (default: 1000)'
    )

    parser.add_argument(
        '--cryptocompare-api-key',
        type=str,
        help='CryptoCompare API key'
    )

    parser.add_argument(
        '--taapi-api-key',
        type=str,
        help='TaaPI API key'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    # Configure logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load Scrapy settings
    settings = get_project_settings()

    # Override settings from command line
    if args.concurrent_requests:
        settings.set('CONCURRENT_REQUESTS', args.concurrent_requests)

    if args.batch_size:
        settings.set('S3_BATCH_SIZE', args.batch_size)

    if args.no_proxy:
        settings.set('PROXY_ROTATION_ENABLED', False)
    elif args.proxy_enabled:
        settings.set('PROXY_ROTATION_ENABLED', True)

    if args.cryptocompare_api_key:
        settings.set('CRYPTOCOMPARE_API_KEY', args.cryptocompare_api_key)

    if args.taapi_api_key:
        settings.set('TAAPI_API_KEY', args.taapi_api_key)

    # Prepare spider arguments
    spider_kwargs = {
        'bucket': args.bucket,
        'prefix': args.prefix,
    }

    if args.symbols:
        spider_kwargs['symbols'] = args.symbols

    if args.start_year:
        spider_kwargs['start_year'] = args.start_year

    if args.end_year:
        spider_kwargs['end_year'] = args.end_year

    if args.cryptocompare_api_key:
        spider_kwargs['cryptocompare_api_key'] = args.cryptocompare_api_key

    if args.taapi_api_key:
        spider_kwargs['taapi_api_key'] = args.taapi_api_key

    logger.info("=" * 80)
    logger.info("Starting Crypto Indicators Scraper")
    logger.info("=" * 80)
    logger.info(f"Bucket: {args.bucket}")
    logger.info(f"Prefix: {args.prefix}")
    logger.info(f"Symbols: {args.symbols if args.symbols else 'ALL FROM S3'}")
    logger.info(f"Year range: {args.start_year or 2017} - {args.end_year or 'current'}")
    logger.info(f"Proxy enabled: {not args.no_proxy}")
    logger.info(f"Concurrent requests: {args.concurrent_requests}")
    logger.info("=" * 80)

    # Create and configure the crawler
    process = CrawlerProcess(settings)

    # Start the spider
    process.crawl(CryptoIndicatorsSpider, **spider_kwargs)
    process.start()

    logger.info("=" * 80)
    logger.info("Scraping completed!")
    logger.info("=" * 80)


if __name__ == '__main__':
    main()
