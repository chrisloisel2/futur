"""
Démo de l'ingestion temps réel - Version courte pour validation
Collecte 60 secondes de données pour montrer que tout fonctionne
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from pipeline.realtime_pipeline import RealTimePipeline


class RealtimeDemo:
    """Démo avec statistiques en temps réel."""

    def __init__(self, config):
        self.config = config
        self.pipeline = RealTimePipeline(config)
        self.data_received = defaultdict(list)
        self.start_time = None

    async def custom_callback(self, data):
        """Callback personnalisé pour afficher les données reçues."""
        symbol = data.get('symbol')
        price = data.get('price')

        if symbol and price:
            self.data_received[symbol].append(price)

            # Afficher périodiquement
            total_messages = sum(len(v) for v in self.data_received.values())

            if total_messages % 10 == 0:  # Tous les 10 messages
                elapsed = (datetime.now() - self.start_time).total_seconds()
                rate = total_messages / elapsed if elapsed > 0 else 0

                print(f"\r📊 {total_messages} messages | "
                      f"{len(self.data_received)} symboles | "
                      f"{rate:.1f} msg/s", end='', flush=True)

        # Passer au processeur de la pipeline
        await self.pipeline.process_trade_data(data)

    async def run_demo(self, duration_seconds=60):
        """Lancer la démo pendant X secondes."""

        print("\n" + "="*80)
        print("🚀 DÉMO INGESTION TEMPS RÉEL - VALIDATION")
        print("="*80)
        print(f"\nDurée: {duration_seconds} secondes")
        print("Objectif: Valider que les données crypto sont bien ingérées\n")

        # Initialiser les collectors
        await self.pipeline.initialize_collectors()

        if not self.pipeline.collectors:
            print("❌ Aucun collector n'a pu être initialisé!")
            return

        print(f"\n✅ {len(self.pipeline.collectors)} collectors initialisés")
        print(f"📡 {len(self.pipeline.active_symbols)} symboles suivis\n")

        # Afficher les collecteurs actifs
        print("Collecteurs actifs:")
        for name, _ in self.pipeline.collectors:
            print(f"  ✅ {name}")

        print("\n" + "="*80)
        print("⏳ Collecte en cours...\n")

        self.start_time = datetime.now()

        # Créer les tasks pour chaque collector
        tasks = []
        for name, collector in self.pipeline.collectors:
            # Wrapper pour capturer les données
            async def collector_wrapper(n, c):
                try:
                    await c.stream(self.custom_callback)
                except Exception as e:
                    print(f"\n⚠️  {n}: {e}")

            task = asyncio.create_task(collector_wrapper(name, collector))
            tasks.append(task)

        # Attendre la durée spécifiée
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=duration_seconds)
        except asyncio.TimeoutError:
            # Normal - c'est le timeout voulu
            pass

        # Nettoyer
        await self.pipeline.cleanup()

        # Afficher les résultats
        print("\n\n" + "="*80)
        print("📊 RÉSULTATS DE LA DÉMO")
        print("="*80)

        total_messages = sum(len(v) for v in self.data_received.values())
        elapsed = (datetime.now() - self.start_time).total_seconds()

        print(f"\nDurée: {elapsed:.1f}s")
        print(f"Messages reçus: {total_messages:,}")
        print(f"Symboles uniques: {len(self.data_received)}")
        print(f"Débit moyen: {total_messages/elapsed:.1f} messages/seconde")

        if self.data_received:
            print("\n📈 Top 10 symboles par nombre de messages:")
            sorted_symbols = sorted(self.data_received.items(), key=lambda x: len(x[1]), reverse=True)

            for i, (symbol, prices) in enumerate(sorted_symbols[:10], 1):
                latest_price = prices[-1] if prices else 0
                print(f"  {i:2d}. {symbol:20s}: {len(prices):5d} messages | "
                      f"Latest: ${latest_price:.2f}")

        # Statistiques de la pipeline
        stats = self.pipeline.get_stats()
        print(f"\n🎯 Statistiques de la pipeline:")
        print(f"  Trades traités: {stats.get('trades_processed', 0):,}")
        print(f"  Prédictions générées: {stats.get('predictions_made', 0):,}")

        # Statut des collecteurs
        collector_status = self.pipeline.get_collector_status()
        print(f"\n📡 Statut des collecteurs:")
        for name, status in collector_status.items():
            messages = status.get('messages', 0)
            status_str = status.get('status', 'unknown')
            if messages > 0:
                print(f"  ✅ {name:15s}: {messages:5d} messages ({status_str})")
            else:
                print(f"  ⚠️  {name:15s}: {messages:5d} messages ({status_str})")

        print("\n" + "="*80)

        if total_messages > 0:
            print("✅ SUCCÈS: L'ingestion temps réel fonctionne parfaitement!")
            print(f"\nLes données sont collectées et peuvent être utilisées pour:")
            print("  - Entraînement de modèles de trading")
            print("  - Analyse de signaux alpha")
            print("  - Détection de patterns en temps réel")
        else:
            print("⚠️  ATTENTION: Aucune donnée reçue")
            print("Raisons possibles:")
            print("  - Marchés fermés (actions US)")
            print("  - Problèmes de connexion")
            print("  - API keys invalides")

        print("\n👋 Démo terminée!\n")


async def main():
    """Point d'entrée principal."""

    # Charger la configuration
    config_path = Path("pipeline_config.json")

    if not config_path.exists():
        print("❌ Fichier pipeline_config.json non trouvé!")
        return

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Créer et lancer la démo
    demo = RealtimeDemo(config)
    await demo.run_demo(duration_seconds=60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️  Arrêt par l'utilisateur")
