#!/usr/bin/env python3
"""
Visualisation des transactions whale dans MongoDB
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from pymongo import MongoClient
from datetime import datetime, timedelta


MONGO_URI = "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/"
DATABASE = "whale_data"
COLLECTION = "whale_transactions"


def connect():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    return client[DATABASE][COLLECTION]


def display_summary(coll):
    print("="*80)
    print("🐋 WHALE TRANSACTIONS - RÉSUMÉ")
    print("="*80 + "\n")

    total = coll.count_documents({})
    print(f"📊 Total transactions: {total:,}\n")

    if total == 0:
        print("⚠️  Aucune donnée. Lancez: python run_whale_scanner.py --blockchain btc --limit 100")
        return

    # Par blockchain
    print("📈 Par Blockchain:")
    for blockchain in ['bitcoin', 'ethereum', 'solana']:
        count = coll.count_documents({'blockchain': blockchain})
        if count > 0:
            total_usd = sum(doc.get('amount_usd', 0) for doc in coll.find({'blockchain': blockchain}))
            print(f"  {blockchain.upper()}: {count:,} transactions (${total_usd:,.0f})")

    # Par type
    print("\n🔄 Par Type:")
    for tx_type in ['exchange_to_wallet', 'wallet_to_exchange', 'exchange_to_exchange', 'wallet_to_wallet']:
        count = coll.count_documents({'transaction_type': tx_type})
        if count > 0:
            emoji = {'exchange_to_wallet': '📤', 'wallet_to_exchange': '📥',
                    'exchange_to_exchange': '🔄', 'wallet_to_wallet': '💸'}.get(tx_type, '❓')
            print(f"  {emoji} {tx_type}: {count:,}")

    # Top 5 transactions
    print("\n💰 Top 5 Plus Grosses Transactions:")
    for i, tx in enumerate(coll.find().sort('amount_usd', -1).limit(5), 1):
        print(f"\n  {i}. ${tx.get('amount_usd', 0):,.0f} ({tx.get('amount', 0):.2f} {tx.get('symbol', 'N/A')})")
        print(f"     Blockchain: {tx.get('blockchain', 'N/A').upper()}")
        print(f"     Type: {tx.get('transaction_type', 'unknown')}")
        print(f"     Date: {tx.get('timestamp', 'N/A')}")
        print(f"     From: {tx.get('from_owner', 'unknown')}")
        print(f"     To: {tx.get('to_owner', 'unknown')}")


def display_recent(coll, hours=24):
    print("\n" + "="*80)
    print(f"⏰ TRANSACTIONS DES DERNIÈRES {hours}H")
    print("="*80 + "\n")

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    recent = list(coll.find({'timestamp': {'$gte': cutoff}}).sort('timestamp', -1).limit(20))

    if not recent:
        print(f"⚠️  Aucune transaction dans les dernières {hours}h")
        return

    print(f"📊 {len(recent)} transactions récentes:\n")

    for i, tx in enumerate(recent, 1):
        emoji = {'exchange_to_wallet': '📤', 'wallet_to_exchange': '📥'}.get(tx.get('transaction_type'), '💸')
        print(f"{i}. {emoji} ${tx.get('amount_usd', 0):,.0f} {tx.get('symbol', 'N/A')} - "
              f"{tx.get('transaction_type', 'unknown')} - {tx.get('timestamp', 'N/A')}")


def main():
    print("🔌 Connexion à MongoDB...")
    coll = connect()
    print("✅ Connecté!\n")

    display_summary(coll)
    display_recent(coll, hours=24)

    print("\n" + "="*80)
    print("📚 MongoDB Compass: https://cloud.mongodb.com/")
    print("="*80)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompu")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
