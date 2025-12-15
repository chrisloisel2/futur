#!/usr/bin/env python3
"""
Test script pour vérifier que la pipeline peut démarrer correctement
"""
import asyncio
import sys
from pathlib import Path

# Ajouter les paths nécessaires
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "ai" / "models" / "pipeline"))

try:
    from pipeline_api_connector import pipeline_connector
    print("✅ pipeline_api_connector importé avec succès")
except Exception as e:
    print(f"❌ Erreur d'import pipeline_api_connector: {e}")
    sys.exit(1)

async def test_pipeline():
    """Test de démarrage de la pipeline."""
    print("\n🔧 Test de la pipeline IA...\n")

    # 1. Vérifier la configuration
    print("1️⃣ Chargement de la configuration...")
    config = pipeline_connector._get_default_config()
    print(f"   ✅ Configuration chargée: {len(config.get('collectors', {}))} collecteurs configurés")

    # 2. Tenter de démarrer la pipeline
    print("\n2️⃣ Tentative de démarrage de la pipeline...")
    try:
        result = await pipeline_connector.start_pipeline()
        print(f"   Résultat: {result}")

        if result.get('status') == 'started':
            print("   ✅ Pipeline démarrée avec succès!")

            # Attendre quelques secondes pour collecter des données
            print("\n3️⃣ Attente de 5 secondes pour collecter des données...")
            await asyncio.sleep(5)

            # Vérifier le statut
            print("\n4️⃣ Vérification du statut...")
            stats = pipeline_connector.get_stats()
            print(f"   Statut: {stats.get('status')}")
            print(f"   Symboles suivis: {stats.get('symbols_tracked', 0)}")
            print(f"   Trades traités: {stats.get('trades_processed', 0)}")

            # Obtenir les prédictions
            print("\n5️⃣ Récupération des prédictions...")
            predictions = pipeline_connector.get_predictions()
            if predictions:
                print(f"   ✅ {len(predictions)} prédictions disponibles:")
                for symbol, pred in list(predictions.items())[:3]:
                    print(f"      - {symbol}: {pred.get('signal')} (confiance: {pred.get('confidence', 0):.2f})")
            else:
                print("   ⚠️ Aucune prédiction disponible encore (normal au démarrage)")

            # Arrêter la pipeline
            print("\n6️⃣ Arrêt de la pipeline...")
            await pipeline_connector.stop_pipeline()
            print("   ✅ Pipeline arrêtée")

        else:
            print(f"   ❌ Échec du démarrage: {result.get('message')}")

    except Exception as e:
        print(f"   ❌ Erreur lors du test: {e}")
        import traceback
        traceback.print_exc()

    print("\n✨ Test terminé!\n")

if __name__ == "__main__":
    asyncio.run(test_pipeline())
