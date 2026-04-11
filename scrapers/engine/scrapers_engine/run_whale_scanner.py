#!/usr/bin/env python3
"""
🐋 FREE WHALE SCANNER - Script de lancement principal
Système 100% gratuit pour tracker les mouvements de whales Bitcoin/Ethereum/Solana
Économie: $1,788/an (vs Whale Alert Professional à $149/mois)
"""

import subprocess
import sys
import os
import argparse
from datetime import datetime


BANNER = """
╔════════════════════════════════════════════════════════════════╗
║                  🐋 FREE WHALE SCANNER                        ║
║              Bitcoin • Ethereum • Solana                       ║
║                   100% Gratuit                                 ║
╚════════════════════════════════════════════════════════════════╝
"""

def main():
    print(BANNER)

    parser = argparse.ArgumentParser(
        description='Scanner gratuit de transactions whale (>$100k)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:

  # Bitcoin - 100 derniers blocs (test rapide)
  python run_whale_scanner.py --blockchain btc --limit 100

  # Bitcoin - depuis un bloc spécifique
  python run_whale_scanner.py --blockchain btc --start-block 800000

  # Ethereum - 50 derniers blocs avec clé API
  python run_whale_scanner.py --blockchain eth --limit 50 --api-key YOUR_ETHERSCAN_KEY

  # Tous les blockchains (mode production)
  python run_whale_scanner.py --blockchain all

APIs Gratuites Requises:
  • Bitcoin: Aucune (Mempool.space est gratuit et illimité)
  • Ethereum: Etherscan API key (gratuite) → https://etherscan.io/apis
  • Solana: Optionnel (RPC public disponible)

Base MongoDB:
  • URI: mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/
  • Database: whale_data
  • Collection: whale_transactions
        """
    )

    parser.add_argument(
        '--blockchain', '-b',
        choices=['btc', 'eth', 'sol', 'all'],
        default='btc',
        help='Blockchain à scanner (btc, eth, sol, ou all pour toutes)'
    )

    parser.add_argument(
        '--start-block',
        type=int,
        help='Bloc de départ (par défaut: 100 derniers blocs)'
    )

    parser.add_argument(
        '--end-block',
        type=int,
        help='Bloc de fin (par défaut: bloc actuel)'
    )

    parser.add_argument(
        '--limit',
        type=int,
        help='Limite de blocs à scanner (utile pour tests)'
    )

    parser.add_argument(
        '--api-key',
        type=str,
        help='Clé API (Etherscan pour Ethereum)'
    )

    parser.add_argument(
        '--test',
        action='store_true',
        help='Mode test: scanne seulement 10 blocs'
    )

    args = parser.parse_args()

    # Mode test
    if args.test:
        args.limit = 10
        print("🧪 MODE TEST: Limit de 10 blocs\n")

    # Déterminer quels spiders lancer
    spiders_to_run = []

    if args.blockchain in ['btc', 'all']:
        spider_args = []
        if args.start_block:
            spider_args.append(f"-a start_block={args.start_block}")
        if args.end_block:
            spider_args.append(f"-a end_block={args.end_block}")
        if args.limit:
            spider_args.append(f"-a limit={args.limit}")

        spiders_to_run.append(('bitcoin_mempool', spider_args))

    if args.blockchain in ['eth', 'all']:
        if not args.api_key and not os.getenv('ETHERSCAN_API_KEY'):
            print("⚠️  WARNING: Ethereum scanner needs an Etherscan API key")
            print("   Get one free at: https://etherscan.io/apis")
            print("   Usage: --api-key YOUR_KEY or export ETHERSCAN_API_KEY=YOUR_KEY\n")
            if args.blockchain == 'eth':
                sys.exit(1)
        else:
            spider_args = []
            if args.api_key:
                spider_args.append(f"-a api_key={args.api_key}")
            if args.start_block:
                spider_args.append(f"-a start_block={args.start_block}")
            if args.end_block:
                spider_args.append(f"-a end_block={args.end_block}")
            if args.limit:
                spider_args.append(f"-a limit={args.limit}")

            spiders_to_run.append(('ethereum_etherscan', spider_args))

    if args.blockchain in ['sol', 'all']:
        spider_args = []
        if args.limit:
            spider_args.append(f"-a limit={args.limit}")
        spiders_to_run.append(('solana_solscan', spider_args))

    # Exécuter les spiders
    print(f"🚀 Lancement du scan...\n")
    print(f"📊 Blockchains: {args.blockchain.upper()}")
    print(f"💾 Stockage: MongoDB (whale_data.whale_transactions)")
    print(f"💰 Seuil whale: $100,000 USD\n")

    for spider_name, spider_args in spiders_to_run:
        print(f"{'='*60}")
        print(f"🔍 Scanner: {spider_name}")
        print(f"{'='*60}\n")

        cmd = ['scrapy', 'crawl', spider_name] + spider_args

        try:
            result = subprocess.run(
                cmd,
                cwd=os.path.dirname(__file__),
                check=True
            )

            if result.returncode == 0:
                print(f"\n✅ {spider_name} terminé avec succès!\n")
            else:
                print(f"\n⚠️  {spider_name} terminé avec des erreurs\n")

        except subprocess.CalledProcessError as e:
            print(f"\n❌ Erreur lors de l'exécution de {spider_name}: {e}\n")
        except KeyboardInterrupt:
            print(f"\n⚠️  Scan interrompu par l'utilisateur\n")
            sys.exit(1)

    print(f"\n{'='*60}")
    print(f"🎉 SCAN TERMINÉ")
    print(f"{'='*60}\n")
    print(f"📊 Visualiser les données:")
    print(f"   python view_blockchain_whales.py\n")
    print(f"🔗 MongoDB Atlas:")
    print(f"   https://cloud.mongodb.com/\n")


if __name__ == '__main__':
    main()
