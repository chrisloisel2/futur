"""
Example usage of the scrapers engine
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from runner import ScrapersRunner


def example_single_spider():
    """Example: Run a single spider"""
    print("\n" + "="*60)
    print("Example 1: Running a single spider (Crypto News)")
    print("="*60)

    runner = ScrapersRunner()
    runner.run_spider('crypto_news')

    print("✓ Crypto news scraping completed")


def example_category():
    """Example: Run a category of spiders"""
    print("\n" + "="*60)
    print("Example 2: Running transaction tracking category")
    print("="*60)

    runner = ScrapersRunner()
    runner.run_category('transaction_tracking')

    print("✓ Transaction tracking completed")


def example_custom_list():
    """Example: Run custom list of spiders"""
    print("\n" + "="*60)
    print("Example 3: Running custom list of spiders")
    print("="*60)

    runner = ScrapersRunner()
    runner.run_custom(['crypto_news', 'whale_alert'])

    print("✓ Custom scraping completed")


def example_read_results():
    """Example: Read scraped results"""
    print("\n" + "="*60)
    print("Example 4: Reading scraped results")
    print("="*60)

    data_dir = Path(__file__).parent / 'data' / 'raw_articles'

    if not data_dir.exists():
        print("No data directory found. Run some scrapers first.")
        return

    jsonl_files = list(data_dir.glob('*.jsonl'))

    if not jsonl_files:
        print("No JSONL files found. Run some scrapers first.")
        return

    # Read latest file
    latest_file = max(jsonl_files, key=lambda p: p.stat().st_mtime)
    print(f"Reading: {latest_file.name}")

    articles = []
    with open(latest_file, 'r', encoding='utf-8') as f:
        for line in f:
            articles.append(json.loads(line))

    print(f"\nFound {len(articles)} articles")

    # Show first article
    if articles:
        print("\nFirst article sample:")
        first = articles[0]
        print(f"  Title: {first.get('title', 'N/A')[:80]}...")
        print(f"  Source: {first.get('source', 'N/A')}")
        print(f"  URL: {first.get('url', 'N/A')}")
        print(f"  Published: {first.get('published_at', 'N/A')}")

        if first.get('crypto_entities'):
            print(f"  Crypto entities: {', '.join(first['crypto_entities'])}")

        if first.get('event_types'):
            print(f"  Event types: {', '.join(first['event_types'])}")


def example_integration_with_news_engine():
    """Example: Integration with news_signal_engine"""
    print("\n" + "="*60)
    print("Example 5: Integration with news_signal_engine")
    print("="*60)

    export_file = Path(__file__).parent / 'data' / 'raw_articles' / 'news_engine_export.json'

    if not export_file.exists():
        print("No export file found. Run scrapers first with news articles.")
        return

    with open(export_file, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    print(f"Loaded {len(articles)} articles for news_signal_engine")

    # These can now be processed by news_signal_engine
    print("\nSample article structure:")
    if articles:
        first = articles[0]
        print(json.dumps(first, indent=2, ensure_ascii=False, default=str)[:500] + "...")


def main():
    """Run examples"""
    print("\n" + "="*60)
    print("SCRAPERS ENGINE - USAGE EXAMPLES")
    print("="*60)

    import argparse
    parser = argparse.ArgumentParser(description='Run scraper examples')
    parser.add_argument(
        '--example',
        type=int,
        choices=[1, 2, 3, 4, 5],
        help='Example number to run (1-5)'
    )

    args = parser.parse_args()

    examples = {
        1: example_single_spider,
        2: example_category,
        3: example_custom_list,
        4: example_read_results,
        5: example_integration_with_news_engine,
    }

    if args.example:
        examples[args.example]()
    else:
        print("\nAvailable examples:")
        print("  1. Run a single spider")
        print("  2. Run a category of spiders")
        print("  3. Run custom list of spiders")
        print("  4. Read scraped results")
        print("  5. Integration with news_signal_engine")
        print("\nUsage: python example_usage.py --example <number>")


if __name__ == '__main__':
    main()
