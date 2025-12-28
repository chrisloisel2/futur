#!/usr/bin/env python3
"""
Script pour récupérer les mouvements de whales Bitcoin depuis 2019
Utilise l'API Whale Alert et stocke dans MongoDB
"""

import subprocess
import sys
import os
from datetime import datetime, timedelta
import argparse


def main():
    parser = argparse.ArgumentParser(
        description='Récupère les transactions Bitcoin Whale Alert depuis 2019'
    )
    parser.add_argument(
        '--api-key',
        type=str,
        help='Clé API Whale Alert (ou utiliser la variable WHALE_ALERT_API_KEY)',
        default=os.getenv('WHALE_ALERT_API_KEY')
    )
    parser.add_argument(
        '--start-date',
        type=str,
        default='2019-01-01',
        help='Date de début (format: YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        type=str,
        default=datetime.utcnow().strftime('%Y-%m-%d'),
        help='Date de fin (format: YYYY-MM-DD)'
    )
    parser.add_argument(
        '--min-value',
        type=int,
        default=500000,
        help='Valeur minimale en USD (défaut: 500000)'
    )
    parser.add_argument(
        '--currency',
        type=str,
        default='btc',
        help='Devise à tracker (btc, eth, etc.)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Mode test sans exécution réelle'
    )

    args = parser.parse_args()

    # Vérification de la clé API
    if not args.api_key:
        print("❌ Erreur: Clé API Whale Alert requise!")
        print()
        print("Options:")
        print("  1. Utiliser --api-key YOUR_KEY")
        print("  2. Définir la variable d'environnement WHALE_ALERT_API_KEY")
        print()
        print("🔗 Obtenir une clé API gratuite: https://whale-alert.io/")
        print()
        print("   La clé gratuite permet:")
        print("   - 20 requêtes par minute")
        print("   - Transactions des dernières 24h uniquement")
        print()
        print("   Pour l'historique complet depuis 2019, il faut un abonnement premium:")
        print("   https://whale-alert.io/pricing")
        sys.exit(1)

    print("🐋 Whale Alert Data Fetcher")
    print("=" * 60)
    print(f"📅 Période: {args.start_date} → {args.end_date}")
    print(f"💰 Devise: {args.currency.upper()}")
    print(f"💵 Valeur min: ${args.min_value:,}")
    print(f"🔑 API Key: {args.api_key[:8]}...")
    print("=" * 60)
    print()

    # Calcul du nombre de jours
    start = datetime.strptime(args.start_date, '%Y-%m-%d')
    end = datetime.strptime(args.end_date, '%Y-%m-%d')
    days = (end - start).days

    print(f"⏱️  Estimation: ~{days} requêtes pour {days} jours")
    print(f"⏱️  Temps estimé: ~{days / 20:.1f} minutes (avec limite de 20 req/min)")
    print()

    if args.dry_run:
        print("🔍 Mode DRY-RUN activé - aucune requête ne sera effectuée")
        return

    # Confirmation
    response = input("Continuer? (y/N): ")
    if response.lower() not in ['y', 'yes', 'o', 'oui']:
        print("❌ Annulé")
        return

    print()
    print("🚀 Démarrage du scraping...")
    print()

    # Construction de la commande scrapy
    cmd = [
        'scrapy', 'crawl', 'whale_alert_api',
        '-a', f'api_key={args.api_key}',
        '-a', f'start_date={args.start_date}',
        '-a', f'end_date={args.end_date}',
        '-a', f'min_value={args.min_value}',
        '-a', f'currency={args.currency}',
    ]

    # Exécution
    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(__file__),
            check=True
        )

        if result.returncode == 0:
            print()
            print("✅ Scraping terminé avec succès!")
            print()
            print("📊 Vérifier les données dans MongoDB:")
            print(f"   Database: whale_data")
            print(f"   Collection: whale_transactions")
            print()
            print("🔍 Exemple de requête MongoDB:")
            print("   db.whale_transactions.find({symbol: 'BTC'}).sort({timestamp: -1}).limit(10)")

    except subprocess.CalledProcessError as e:
        print()
        print(f"❌ Erreur lors du scraping: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print("⚠️ Interrompu par l'utilisateur")
        sys.exit(1)


if __name__ == '__main__':
    main()
