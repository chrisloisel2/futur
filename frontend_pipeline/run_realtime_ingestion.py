"""
Script principal pour lancer l'ingestion de données temps réel
Collecte le maximum d'informations depuis toutes les sources disponibles
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.realtime_pipeline import RealTimePipeline


async def main():
    """Lancer la pipeline temps réel."""

    # Charger la configuration
    config_path = Path("pipeline_config.json")

    if not config_path.exists():
        print("❌ Fichier pipeline_config.json non trouvé!")
        return

    with open(config_path, 'r') as f:
        config = json.load(f)

    print("\n" + "="*80)
    print("🚀 LANCEMENT DE L'INGESTION TEMPS RÉEL")
    print("="*80)
    print("\nObjectif: Collecter le maximum d'informations sur les cryptos")
    print("Sources activées:")

    # Afficher les sources activées
    for name, collector_config in config.get('collectors', {}).items():
        if collector_config.get('enabled'):
            symbols = collector_config.get('symbols',
                     collector_config.get('stocks',
                     collector_config.get('crypto', [])))
            print(f"  ✅ {name.upper()}: {len(symbols) if isinstance(symbols, list) else 0} symboles")

    print("\n" + "="*80)
    print("\n⏳ Initialisation de la pipeline...")

    # Créer et lancer la pipeline
    pipeline = RealTimePipeline(config)

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        print("\n\n⏹️  Arrêt demandé par l'utilisateur...")
        await pipeline.save_data()
        await pipeline.cleanup()

        # Afficher les statistiques finales
        stats = pipeline.get_stats()
        print("\n" + "="*80)
        print("📊 STATISTIQUES FINALES")
        print("="*80)
        print(f"Trades traités: {stats.get('trades_processed', 0):,}")
        print(f"Prédictions générées: {stats.get('predictions_made', 0):,}")
        print(f"Symboles suivis: {stats.get('symbols_tracked', 0)}")

        # Statut des collecteurs
        collector_status = pipeline.get_collector_status()
        print("\n📡 Statut des collecteurs:")
        for name, status in collector_status.items():
            status_str = status.get('status', 'unknown')
            messages = status.get('messages', 0)
            print(f"  {name}: {status_str} ({messages} messages)")

        print("\n✅ Données sauvegardées dans:", config.get('storage_path', 'datasets/realtime'))
        print("\n👋 Au revoir!\n")


if __name__ == "__main__":
    asyncio.run(main())
