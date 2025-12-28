#!/usr/bin/env python3
"""
Script pour visualiser rapidement les données Whale Alert dans MongoDB
"""

import sys
from pymongo import MongoClient
from datetime import datetime, timedelta
from collections import defaultdict


MONGO_URI = "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/"
DATABASE = "whale_data"
COLLECTION = "whale_transactions"


def connect_mongodb():
    """Connexion à MongoDB"""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        return client[DATABASE][COLLECTION]
    except Exception as e:
        print(f"❌ Erreur de connexion MongoDB: {e}")
        sys.exit(1)


def display_summary(collection):
    """Affiche un résumé des données"""
    print("=" * 80)
    print("🐋 WHALE ALERT - RÉSUMÉ DES DONNÉES")
    print("=" * 80)
    print()

    # Total transactions
    total = collection.count_documents({})
    print(f"📊 Total transactions: {total:,}")

    if total == 0:
        print("\n⚠️ Aucune donnée dans la base.")
        print("💡 Lancez: python fetch_whale_data.py --api-key YOUR_KEY")
        return

    # Par symbole
    print("\n📈 Par Cryptomonnaie:")
    pipeline = [
        {'$group': {
            '_id': '$symbol',
            'count': {'$sum': 1},
            'total_usd': {'$sum': '$amount_usd'}
        }},
        {'$sort': {'total_usd': -1}}
    ]

    for stat in collection.aggregate(pipeline):
        symbol = stat['_id']
        count = stat['count']
        total_usd = stat['total_usd']
        print(f"  {symbol}: {count:,} transactions (${total_usd:,.0f})")

    # Par type de transaction
    print("\n🔄 Par Type de Transaction:")
    pipeline = [
        {'$group': {
            '_id': '$transaction_type',
            'count': {'$sum': 1},
            'total_usd': {'$sum': '$amount_usd'}
        }},
        {'$sort': {'total_usd': -1}}
    ]

    for stat in collection.aggregate(pipeline):
        tx_type = stat['_id']
        count = stat['count']
        total_usd = stat['total_usd']
        emoji = {
            'exchange_to_wallet': '📤',
            'wallet_to_exchange': '📥',
            'exchange_to_exchange': '🔄',
            'wallet_to_wallet': '💸'
        }.get(tx_type, '❓')
        print(f"  {emoji} {tx_type}: {count:,} (${total_usd:,.0f})")

    # Période
    print("\n📅 Période Couverte:")
    oldest = collection.find_one(sort=[('timestamp', 1)])
    latest = collection.find_one(sort=[('timestamp', -1)])

    if oldest and latest:
        print(f"  Du: {oldest['timestamp']}")
        print(f"  Au: {latest['timestamp']}")

    # Top transactions
    print("\n💰 Top 5 Plus Grosses Transactions:")
    for i, tx in enumerate(collection.find().sort('amount_usd', -1).limit(5), 1):
        symbol = tx.get('symbol', 'N/A')
        amount = tx.get('amount', 0)
        amount_usd = tx.get('amount_usd', 0)
        tx_type = tx.get('transaction_type', 'unknown')
        timestamp = tx.get('timestamp', 'N/A')

        print(f"\n  {i}. ${amount_usd:,.0f} ({amount:,.2f} {symbol})")
        print(f"     Type: {tx_type}")
        print(f"     Date: {timestamp}")
        print(f"     From: {tx.get('from_owner', 'unknown')}")
        print(f"     To: {tx.get('to_owner', 'unknown')}")


def display_recent(collection, hours=24):
    """Affiche les transactions récentes"""
    print("\n" + "=" * 80)
    print(f"⏰ TRANSACTIONS DES DERNIÈRES {hours}H")
    print("=" * 80)
    print()

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    recent = list(collection.find({
        'timestamp': {'$gte': cutoff}
    }).sort('timestamp', -1).limit(20))

    if not recent:
        print(f"⚠️ Aucune transaction dans les dernières {hours}h")
        return

    print(f"📊 {len(recent)} transactions récentes (max 20 affichées)")
    print()

    for i, tx in enumerate(recent, 1):
        symbol = tx.get('symbol', 'N/A')
        amount_usd = tx.get('amount_usd', 0)
        tx_type = tx.get('transaction_type', 'unknown')
        timestamp = tx.get('timestamp', 'N/A')

        emoji = {
            'exchange_to_wallet': '📤',
            'wallet_to_exchange': '📥',
            'exchange_to_exchange': '🔄',
            'wallet_to_wallet': '💸'
        }.get(tx_type, '❓')

        print(f"{i}. {emoji} ${amount_usd:,.0f} {symbol} - {tx_type} - {timestamp}")


def display_daily_stats(collection, days=7):
    """Affiche les stats quotidiennes"""
    print("\n" + "=" * 80)
    print(f"📊 STATISTIQUES DES {days} DERNIERS JOURS")
    print("=" * 80)
    print()

    pipeline = [
        {
            '$group': {
                '_id': {
                    '$dateToString': {
                        'format': '%Y-%m-%d',
                        'date': '$timestamp'
                    }
                },
                'count': {'$sum': 1},
                'total_usd': {'$sum': '$amount_usd'},
                'exchange_outflow': {
                    '$sum': {
                        '$cond': [
                            {'$eq': ['$transaction_type', 'exchange_to_wallet']},
                            '$amount_usd',
                            0
                        ]
                    }
                },
                'exchange_inflow': {
                    '$sum': {
                        '$cond': [
                            {'$eq': ['$transaction_type', 'wallet_to_exchange']},
                            '$amount_usd',
                            0
                        ]
                    }
                }
            }
        },
        {'$sort': {'_id': -1}},
        {'$limit': days}
    ]

    stats = list(collection.aggregate(pipeline))

    if not stats:
        print("⚠️ Aucune donnée disponible")
        return

    print(f"{'Date':<12} {'Txs':>8} {'Total USD':>18} {'Outflow':>18} {'Inflow':>18} {'Net':>18}")
    print("-" * 100)

    for stat in stats:
        date = stat['_id']
        count = stat['count']
        total = stat['total_usd']
        outflow = stat['exchange_outflow']
        inflow = stat['exchange_inflow']
        net = outflow - inflow

        net_emoji = '📤' if net > 0 else '📥' if net < 0 else '➖'

        print(
            f"{date:<12} {count:>8,} ${total:>16,.0f} "
            f"${outflow:>16,.0f} ${inflow:>16,.0f} "
            f"{net_emoji} ${net:>15,.0f}"
        )


def display_exchanges_stats(collection):
    """Affiche les stats par exchange"""
    print("\n" + "=" * 80)
    print("🏦 STATISTIQUES PAR EXCHANGE")
    print("=" * 80)
    print()

    # Top exchanges - Outflow
    print("📤 Top Exchanges - Outflow (Bullish)")
    print("-" * 40)

    pipeline = [
        {'$match': {'transaction_type': 'exchange_to_wallet'}},
        {'$group': {
            '_id': '$from_owner',
            'count': {'$sum': 1},
            'total_usd': {'$sum': '$amount_usd'}
        }},
        {'$sort': {'total_usd': -1}},
        {'$limit': 10}
    ]

    for stat in collection.aggregate(pipeline):
        exchange = stat['_id'] or 'unknown'
        count = stat['count']
        total = stat['total_usd']
        print(f"  {exchange}: {count:,} txs (${total:,.0f})")

    # Top exchanges - Inflow
    print("\n📥 Top Exchanges - Inflow (Bearish)")
    print("-" * 40)

    pipeline = [
        {'$match': {'transaction_type': 'wallet_to_exchange'}},
        {'$group': {
            '_id': '$to_owner',
            'count': {'$sum': 1},
            'total_usd': {'$sum': '$amount_usd'}
        }},
        {'$sort': {'total_usd': -1}},
        {'$limit': 10}
    ]

    for stat in collection.aggregate(pipeline):
        exchange = stat['_id'] or 'unknown'
        count = stat['count']
        total = stat['total_usd']
        print(f"  {exchange}: {count:,} txs (${total:,.0f})")


def main():
    print("🔌 Connexion à MongoDB...")
    collection = connect_mongodb()
    print("✅ Connecté!\n")

    # Affichage des différentes vues
    display_summary(collection)
    display_recent(collection, hours=24)
    display_daily_stats(collection, days=7)
    display_exchanges_stats(collection)

    print("\n" + "=" * 80)
    print("📚 Pour plus d'analyses, utilisez MongoDB Compass ou mongosh")
    print("🔗 Connexion: " + MONGO_URI)
    print("=" * 80)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrompu par l'utilisateur")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        sys.exit(1)
