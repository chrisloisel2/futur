#!/usr/bin/env python3
"""
Script de test pour le système Whale Alert
Teste la connexion MongoDB et le pipeline
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from pymongo import MongoClient
from datetime import datetime
import os


def test_mongodb_connection():
    """Test la connexion à MongoDB"""
    print("🔍 Test 1: Connexion MongoDB")
    print("-" * 60)

    mongo_uri = "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/"

    try:
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000
        )

        # Test ping
        client.admin.command('ping')
        print("✅ Connexion MongoDB réussie")

        # Liste des databases
        dbs = client.list_database_names()
        print(f"📊 Databases disponibles: {len(dbs)}")

        # Vérifier si whale_data existe
        if 'whale_data' in dbs:
            print("✅ Database 'whale_data' existe")

            db = client['whale_data']
            collections = db.list_collection_names()
            print(f"📦 Collections: {collections}")

            # Compter les documents
            if 'whale_transactions' in collections:
                count = db['whale_transactions'].count_documents({})
                print(f"💾 Transactions stockées: {count}")

                if count > 0:
                    # Afficher quelques stats
                    latest = db['whale_transactions'].find_one(sort=[('timestamp', -1)])
                    oldest = db['whale_transactions'].find_one(sort=[('timestamp', 1)])

                    print(f"\n📈 Statistiques:")
                    print(f"  Plus ancienne: {latest.get('timestamp') if latest else 'N/A'}")
                    print(f"  Plus récente: {oldest.get('timestamp') if oldest else 'N/A'}")

                    # Stats par symbole
                    pipeline = [
                        {'$group': {
                            '_id': '$symbol',
                            'count': {'$sum': 1},
                            'total_usd': {'$sum': '$amount_usd'}
                        }},
                        {'$sort': {'count': -1}}
                    ]
                    stats = list(db['whale_transactions'].aggregate(pipeline))

                    print(f"\n📊 Par cryptomonnaie:")
                    for stat in stats[:5]:
                        print(f"  {stat['_id']}: {stat['count']} transactions (${stat['total_usd']:,.0f})")
        else:
            print("⚠️ Database 'whale_data' n'existe pas encore (normal si aucune donnée)")

        client.close()
        return True

    except Exception as e:
        print(f"❌ Erreur de connexion: {e}")
        return False


def test_api_key():
    """Vérifie si une clé API est disponible"""
    print("\n🔍 Test 2: Clé API Whale Alert")
    print("-" * 60)

    api_key = os.getenv('WHALE_ALERT_API_KEY')

    if api_key:
        print(f"✅ Clé API trouvée: {api_key[:8]}...")
        return True
    else:
        print("⚠️ Aucune clé API trouvée dans les variables d'environnement")
        print("\n💡 Pour définir la clé API:")
        print("   export WHALE_ALERT_API_KEY='votre_clé'")
        print("\n🔗 Obtenir une clé API: https://whale-alert.io/")
        return False


def test_imports():
    """Teste les imports nécessaires"""
    print("\n🔍 Test 3: Dépendances Python")
    print("-" * 60)

    dependencies = {
        'scrapy': None,
        'pymongo': None,
    }

    all_ok = True

    for dep in dependencies.keys():
        try:
            module = __import__(dep)
            version = getattr(module, '__version__', 'unknown')
            print(f"✅ {dep}: {version}")
        except ImportError:
            print(f"❌ {dep}: non installé")
            all_ok = False

    if not all_ok:
        print("\n💡 Pour installer les dépendances:")
        print("   pip install -r requirements.txt")

    return all_ok


def test_spider_files():
    """Vérifie que les fichiers nécessaires existent"""
    print("\n🔍 Test 4: Fichiers du système")
    print("-" * 60)

    files = {
        'Spider': 'spiders/whale_alert_api.py',
        'Pipeline': 'pipelines/whale_mongodb_pipeline.py',
        'Items': 'items.py',
        'Script': 'fetch_whale_data.py',
        'Guide': 'WHALE_ALERT_GUIDE.md',
    }

    all_ok = True
    base_path = Path(__file__).parent

    for name, file_path in files.items():
        full_path = base_path / file_path
        if full_path.exists():
            size = full_path.stat().st_size
            print(f"✅ {name}: {file_path} ({size:,} bytes)")
        else:
            print(f"❌ {name}: {file_path} (manquant)")
            all_ok = False

    return all_ok


def show_usage_examples():
    """Affiche des exemples d'utilisation"""
    print("\n" + "=" * 60)
    print("📖 EXEMPLES D'UTILISATION")
    print("=" * 60)

    print("\n1️⃣ Avec le script Python:")
    print("   python fetch_whale_data.py --api-key YOUR_KEY")

    print("\n2️⃣ Avec Scrapy directement:")
    print("   scrapy crawl whale_alert_api -a api_key=YOUR_KEY")

    print("\n3️⃣ Période personnalisée:")
    print("   python fetch_whale_data.py --api-key YOUR_KEY \\")
    print("     --start-date 2024-01-01 --end-date 2024-12-31")

    print("\n4️⃣ Autre cryptomonnaie (Ethereum):")
    print("   python fetch_whale_data.py --api-key YOUR_KEY --currency eth")

    print("\n5️⃣ Test sans exécution:")
    print("   python fetch_whale_data.py --api-key YOUR_KEY --dry-run")


def main():
    print("=" * 60)
    print("🐋 WHALE ALERT - TEST DU SYSTÈME")
    print("=" * 60)
    print()

    results = {
        'MongoDB': test_mongodb_connection(),
        'API Key': test_api_key(),
        'Dépendances': test_imports(),
        'Fichiers': test_spider_files(),
    }

    # Résumé
    print("\n" + "=" * 60)
    print("📊 RÉSUMÉ DES TESTS")
    print("=" * 60)

    for name, success in results.items():
        status = "✅ OK" if success else "❌ ÉCHEC"
        print(f"{status} - {name}")

    all_passed = all(results.values())

    print()
    if all_passed:
        print("🎉 Tous les tests sont passés!")
        print("✅ Le système est prêt à être utilisé")
    else:
        print("⚠️ Certains tests ont échoué")
        print("📝 Vérifiez les erreurs ci-dessus")

    # Afficher les exemples d'utilisation
    show_usage_examples()

    print("\n" + "=" * 60)
    print("📚 Documentation complète: WHALE_ALERT_GUIDE.md")
    print("=" * 60)

    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
