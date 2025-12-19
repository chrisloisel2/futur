#!/usr/bin/env python3
"""
Test script for S3TradingPipeline
Tests asset detection and validation without running full scrapers
"""

import sys
from pathlib import Path

# Add scrapers_engine to path
sys.path.insert(0, str(Path(__file__).parent))

from pipelines.s3_trading_pipeline import S3TradingPipeline
from datetime import datetime


def test_asset_detection():
    """Test asset detection with various items"""
    pipeline = S3TradingPipeline()

    test_cases = [
        {
            "name": "BTC News Article",
            "item": {
                "title": "Bitcoin Surges Past $50,000 Mark",
                "body": "BTC price reached a new high today...",
                "url": "https://example.com/btc-news",
                "source": "CoinDesk"
            },
            "expected_asset": "BTC",
            "should_pass": True
        },
        {
            "name": "ETH Analysis",
            "item": {
                "title": "Ethereum Network Upgrade Complete",
                "content": "The Ethereum developers successfully deployed the latest upgrade...",
                "url": "https://example.com/eth-upgrade",
                "source": "Cointelegraph"
            },
            "expected_asset": "ETH",
            "should_pass": True
        },
        {
            "name": "SOL Transaction",
            "item": {
                "title": "Large Solana Transfer Detected",
                "text": "A whale moved 1M SOL tokens today...",
                "url": "https://whale-alert.io/transaction/sol",
                "source": "Whale Alert"
            },
            "expected_asset": "SOL",
            "should_pass": True
        },
        {
            "name": "Multiple Assets (BTC prioritized)",
            "item": {
                "title": "Bitcoin and Ethereum Rally Together",
                "body": "Both BTC and ETH saw gains today, with Bitcoin leading the charge...",
                "url": "https://example.com/crypto-rally",
                "source": "The Block"
            },
            "expected_asset": "BTC",  # Should detect BTC as primary
            "should_pass": True
        },
        {
            "name": "Altcoin (should fail)",
            "item": {
                "title": "Dogecoin Price Prediction",
                "body": "DOGE is expected to reach new heights...",
                "url": "https://example.com/doge",
                "source": "Random Source"
            },
            "expected_asset": None,
            "should_pass": False
        },
        {
            "name": "No crypto mention (should fail)",
            "item": {
                "title": "Stock Market News",
                "body": "The S&P 500 gained today...",
                "url": "https://example.com/stocks",
                "source": "CNBC"
            },
            "expected_asset": None,
            "should_pass": False
        }
    ]

    print("=" * 80)
    print("TESTING S3TradingPipeline Asset Detection")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for test in test_cases:
        print(f"Test: {test['name']}")
        print(f"  Item: {test['item'].get('title', test['item'].get('text', 'N/A'))}")

        # Detect asset
        detected = pipeline._detect_asset(test['item'])

        # Check result
        if test['should_pass']:
            if detected == test['expected_asset']:
                print(f"  ✅ PASS - Detected {detected} (expected {test['expected_asset']})")
                passed += 1
            else:
                print(f"  ❌ FAIL - Detected {detected}, expected {test['expected_asset']}")
                failed += 1
        else:
            if detected is None or detected not in pipeline.ALLOWED_ASSETS:
                print(f"  ✅ PASS - Correctly rejected (detected: {detected})")
                passed += 1
            else:
                print(f"  ❌ FAIL - Should have rejected but detected {detected}")
                failed += 1

        print()

    print("=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    return failed == 0


def test_data_type_detection():
    """Test data type detection"""
    pipeline = S3TradingPipeline()

    class MockSpider:
        name = "test_spider"

    spider = MockSpider()

    test_cases = [
        {
            "name": "News Article",
            "item": {"title": "Bitcoin News", "body": "Some content"},
            "expected_type": "news"
        },
        {
            "name": "Forum Post",
            "item": {"title": "Discussion", "forum_name": "bitcointalk", "thread_title": "Test"},
            "expected_type": "forum"
        },
        {
            "name": "Transaction",
            "item": {"transaction_hash": "0x123", "amount": "1000", "blockchain": "ethereum"},
            "expected_type": "onchain"
        },
        {
            "name": "Social Post",
            "item": {"text": "Bullish on BTC", "platform": "twitter", "likes": 100},
            "expected_type": "social"
        }
    ]

    print("=" * 80)
    print("TESTING Data Type Detection")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for test in test_cases:
        detected = pipeline._get_data_type(test['item'], spider)
        if detected == test['expected_type']:
            print(f"✅ {test['name']}: {detected}")
            passed += 1
        else:
            print(f"❌ {test['name']}: got {detected}, expected {test['expected_type']}")
            failed += 1

    print()
    print("=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    return failed == 0


def test_schema_validation():
    """Test schema validation"""
    pipeline = S3TradingPipeline()

    test_cases = [
        {
            "name": "Valid Item",
            "item": {
                "asset": "BTC",
                "source": "CoinDesk",
                "type": "news",
                "scraped_at": datetime.utcnow().isoformat(),
                "url": "https://example.com",
                "title": "Bitcoin News"
            },
            "should_pass": True
        },
        {
            "name": "Missing Required Field (asset)",
            "item": {
                "source": "CoinDesk",
                "type": "news",
                "scraped_at": datetime.utcnow().isoformat()
            },
            "should_pass": False
        },
        {
            "name": "Invalid Asset",
            "item": {
                "asset": "DOGE",
                "source": "CoinDesk",
                "type": "news",
                "scraped_at": datetime.utcnow().isoformat()
            },
            "should_pass": False
        },
        {
            "name": "Invalid Type",
            "item": {
                "asset": "BTC",
                "source": "CoinDesk",
                "type": "invalid_type",
                "scraped_at": datetime.utcnow().isoformat()
            },
            "should_pass": False
        }
    ]

    print("=" * 80)
    print("TESTING Schema Validation")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for test in test_cases:
        result = pipeline._validate_schema(test['item'])
        expected = test['should_pass']

        if result == expected:
            status = "✅" if expected else "✅"
            print(f"{status} {test['name']}: {'Valid' if result else 'Invalid'} (as expected)")
            passed += 1
        else:
            print(f"❌ {test['name']}: got {result}, expected {expected}")
            failed += 1

    print()
    print("=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    return failed == 0


def main():
    """Run all tests"""
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 20 + "S3 TRADING PIPELINE TEST SUITE" + " " * 28 + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    results = []

    # Test 1: Asset Detection
    results.append(("Asset Detection", test_asset_detection()))

    print("\n")

    # Test 2: Data Type Detection
    results.append(("Data Type Detection", test_data_type_detection()))

    print("\n")

    # Test 3: Schema Validation
    results.append(("Schema Validation", test_schema_validation()))

    # Final Summary
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 30 + "FINAL SUMMARY" + " " * 35 + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {test_name}")
        if not passed:
            all_passed = False

    print()

    if all_passed:
        print("🎉 All tests passed! S3TradingPipeline is working correctly.")
        print()
        print("Next steps:")
        print("  1. Test with a real spider: scrapy crawl crypto_news")
        print("  2. Check S3 bucket: s3://qbia/bourse/raw/")
        print("  3. Verify JSON Lines format and asset filtering")
        return 0
    else:
        print("❌ Some tests failed. Please review the errors above.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
