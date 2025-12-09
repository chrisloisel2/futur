"""
QUICK START - Test rapide du système de collecte
================================================
Collecte un échantillon de données pour vérifier que tout fonctionne.
"""
import asyncio
import os
from pathlib import Path
from datetime import datetime, timedelta

# Import depuis mass_data_collector_v2
import sys
sys.path.append(str(Path(__file__).parent))

from mass_data_collector_v2 import MassDataCollector, APIConfig


async def quick_test():
    """Test rapide avec données limitées."""
    
    print("\n" + "=" * 80)
    print("QUICK START - Testing Alpha Data Collection System")
    print("=" * 80 + "\n")
    
    # Configuration minimale (fonctionnera avec APIs publiques)
    config = APIConfig(
        FRED_API_KEY=os.getenv("FRED_API_KEY", ""),
        ALPHA_VANTAGE_API_KEY=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
    )
    
    collector = MassDataCollector(config)
    
    # Test avec seulement 5 cryptos et 7 jours
    test_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=7)
    
    print(f"Testing with {len(test_symbols)} symbols")
    print(f"Date range: {start_date.date()} to {end_date.date()}\n")
    
    try:
        # Test 1: Market Data
        print("\n[TEST 1/5] Market Data Collection")
        print("-" * 80)
        market_data = await collector.market_collector.collect_binance_data(
            test_symbols, start_date, end_date
        )
        print(f"✓ Collected {len(market_data)} OHLCV records")
        
        # Test 2: On-Chain Data
        print("\n[TEST 2/5] On-Chain Data Collection")
        print("-" * 80)
        onchain_data = await collector.onchain_collector.collect_public_onchain_data(['BTC', 'ETH'])
        print(f"✓ Collected {len(onchain_data)} on-chain records")
        
        # Test 3: Sentiment Data
        print("\n[TEST 3/5] Sentiment Data Collection")
        print("-" * 80)
        fear_greed = await collector.sentiment_collector.collect_fear_greed_index()
        print(f"✓ Collected {len(fear_greed)} Fear & Greed records")
        
        # Test 4: Macro Data
        print("\n[TEST 4/5] Macro Data Collection")
        print("-" * 80)
        global_markets = await collector.macro_collector.collect_global_market_indices()
        print(f"✓ Collected {len(global_markets)} market index records")
        
        # Test 5: Derivatives Data
        print("\n[TEST 5/5] Derivatives Data Collection")
        print("-" * 80)
        funding = await collector.derivatives_collector.collect_funding_rates(test_symbols)
        print(f"✓ Collected {len(funding)} funding rate records")
        
        print("\n" + "=" * 80)
        print("ALL TESTS PASSED! ✓")
        print("=" * 80)
        print("\nYour system is ready to collect alpha signals!")
        print("\nNext steps:")
        print("  1. Run: python mass_data_collector_v2.py  (full data collection)")
        print("  2. Run: python alpha_signal_analyzer.py    (analyze signals)")
        print("\n" + "=" * 80 + "\n")
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        await collector.close()


if __name__ == "__main__":
    asyncio.run(quick_test())
