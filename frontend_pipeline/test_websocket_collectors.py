"""
Script de test rapide pour diagnostiquer les collectors websocket
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.collectors.finnhub_collector import FinnhubCollector
from pipeline.collectors.alpaca_collector import AlpacaCollector, AlpacaCryptoCollector
from pipeline.collectors.yahoo_finance_collector import YahooFinanceCollector


async def test_finnhub(config):
    """Tester Finnhub."""
    print("\n" + "="*80)
    print("TESTING FINNHUB")
    print("="*80)

    api_key = config.get('api_key')
    collector = FinnhubCollector(api_key=api_key)

    try:
        # Connexion
        if await collector.connect():
            print("✅ Connexion réussie")

            # S'abonner à quelques symboles pour tester
            stocks = config.get('stocks', [])[:2]  # Limiter à 2 pour le test
            crypto = config.get('crypto', [])[:2]

            if stocks:
                await collector.subscribe_stocks(stocks)
                print(f"✅ Abonnement stocks: {stocks}")

            if crypto:
                exchange = config.get('crypto_exchange', 'BINANCE')
                await collector.subscribe_crypto(crypto, exchange=exchange)
                print(f"✅ Abonnement crypto: {crypto}")

            # Recevoir quelques messages
            message_count = 0
            max_messages = 5

            print(f"\n⏳ En attente de {max_messages} messages...")

            async def test_callback(data):
                nonlocal message_count
                message_count += 1
                print(f"📨 Message {message_count}: {data.get('symbol')} @ ${data.get('price')} | Vol: {data.get('volume')}")

                if message_count >= max_messages:
                    # Arrêter le test après max_messages
                    raise asyncio.CancelledError("Test terminé")

            try:
                await asyncio.wait_for(collector.stream(test_callback), timeout=30)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout après 30s - reçu {message_count} messages")
            except asyncio.CancelledError:
                print(f"✅ Test terminé - reçu {message_count} messages")

            await collector.close()
            return True

        else:
            print("❌ Échec de connexion")
            return False

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_alpaca(config):
    """Tester Alpaca."""
    print("\n" + "="*80)
    print("TESTING ALPACA")
    print("="*80)

    api_key = config.get('api_key')
    api_secret = config.get('api_secret')
    feed = config.get('feed', 'iex')

    collector = AlpacaCollector(api_key=api_key, api_secret=api_secret, feed=feed)

    try:
        # Connexion
        if await collector.connect():
            print("✅ Connexion réussie")

            # S'abonner
            symbols = config.get('symbols', [])[:2]  # Limiter à 2

            if symbols:
                await collector.subscribe(symbols, ["trades"])
                print(f"✅ Abonnement: {symbols}")

            # Recevoir quelques messages
            message_count = 0
            max_messages = 5

            print(f"\n⏳ En attente de {max_messages} messages...")

            async def test_callback(data):
                nonlocal message_count
                message_count += 1
                print(f"📨 Message {message_count}: {data.get('symbol')} @ ${data.get('price')} | Size: {data.get('size')}")

                if message_count >= max_messages:
                    raise asyncio.CancelledError("Test terminé")

            try:
                await asyncio.wait_for(collector.stream(test_callback), timeout=30)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout après 30s - reçu {message_count} messages")
            except asyncio.CancelledError:
                print(f"✅ Test terminé - reçu {message_count} messages")

            await collector.close()
            return True

        else:
            print("❌ Échec de connexion")
            return False

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_alpaca_crypto(config):
    """Tester Alpaca Crypto."""
    print("\n" + "="*80)
    print("TESTING ALPACA CRYPTO")
    print("="*80)

    api_key = config.get('api_key')
    api_secret = config.get('api_secret')

    collector = AlpacaCryptoCollector(api_key=api_key, api_secret=api_secret)

    try:
        # Connexion
        if await collector.connect():
            print("✅ Connexion réussie")

            # S'abonner
            symbols = config.get('symbols', [])[:2]  # Limiter à 2

            if symbols:
                await collector.subscribe(symbols)
                print(f"✅ Abonnement: {symbols}")

            # Recevoir quelques messages
            message_count = 0
            max_messages = 5

            print(f"\n⏳ En attente de {max_messages} messages...")

            async def test_callback(data):
                nonlocal message_count
                message_count += 1
                print(f"📨 Message {message_count}: {data.get('symbol')} @ ${data.get('price')} | Size: {data.get('size')}")

                if message_count >= max_messages:
                    raise asyncio.CancelledError("Test terminé")

            try:
                await asyncio.wait_for(collector.stream(test_callback), timeout=30)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout après 30s - reçu {message_count} messages")
            except asyncio.CancelledError:
                print(f"✅ Test terminé - reçu {message_count} messages")

            await collector.close()
            return True

        else:
            print("❌ Échec de connexion")
            return False

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_yahoo(config):
    """Tester Yahoo Finance."""
    print("\n" + "="*80)
    print("TESTING YAHOO FINANCE")
    print("="*80)

    ws_url = config.get('ws_url', 'wss://streamer.finance.yahoo.com/?version=2')

    collector = YahooFinanceCollector(ws_url=ws_url)

    try:
        # Connexion
        if await collector.connect():
            print("✅ Connexion réussie")

            # S'abonner
            symbols = config.get('symbols', [])[:2]  # Limiter à 2

            if symbols:
                await collector.subscribe(symbols)
                print(f"✅ Abonnement: {symbols}")

            # Recevoir quelques messages
            message_count = 0
            max_messages = 5

            print(f"\n⏳ En attente de {max_messages} messages...")

            async def test_callback(data):
                nonlocal message_count
                message_count += 1
                print(f"📨 Message {message_count}: {data.get('symbol')} @ ${data.get('price')}")

                if message_count >= max_messages:
                    raise asyncio.CancelledError("Test terminé")

            try:
                await asyncio.wait_for(collector.stream(test_callback), timeout=30)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout après 30s - reçu {message_count} messages")
            except asyncio.CancelledError:
                print(f"✅ Test terminé - reçu {message_count} messages")

            await collector.close()
            return True

        else:
            print("❌ Échec de connexion")
            return False

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Test principal."""
    # Charger la config
    with open('pipeline_config.json', 'r') as f:
        config = json.load(f)

    collectors_config = config.get('collectors', {})

    print("\n" + "="*80)
    print("TESTS DES COLLECTORS WEBSOCKET")
    print("="*80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = {}

    # Test Finnhub
    if collectors_config.get('finnhub', {}).get('enabled'):
        results['finnhub'] = await test_finnhub(collectors_config['finnhub'])
    else:
        print("\n⏭️  Finnhub désactivé - test ignoré")

    # Test Alpaca
    if collectors_config.get('alpaca', {}).get('enabled'):
        results['alpaca'] = await test_alpaca(collectors_config['alpaca'])
    else:
        print("\n⏭️  Alpaca désactivé - test ignoré")

    # Test Alpaca Crypto
    if collectors_config.get('alpaca_crypto', {}).get('enabled'):
        results['alpaca_crypto'] = await test_alpaca_crypto(collectors_config['alpaca_crypto'])
    else:
        print("\n⏭️  Alpaca Crypto désactivé - test ignoré")

    # Test Yahoo
    if collectors_config.get('yahoo', {}).get('enabled'):
        results['yahoo'] = await test_yahoo(collectors_config['yahoo'])
    else:
        print("\n⏭️  Yahoo désactivé - test ignoré")

    # Résumé
    print("\n" + "="*80)
    print("RÉSUMÉ DES TESTS")
    print("="*80)

    for name, success in results.items():
        status = "✅ SUCCÈS" if success else "❌ ÉCHEC"
        print(f"{name.upper()}: {status}")

    total = len(results)
    successes = sum(1 for s in results.values() if s)

    print(f"\nTotal: {successes}/{total} tests réussis")

    if successes == total:
        print("\n🎉 Tous les tests ont réussi! La pipeline est prête.")
    else:
        print("\n⚠️  Certains tests ont échoué. Vérifiez les erreurs ci-dessus.")


if __name__ == "__main__":
    asyncio.run(main())
