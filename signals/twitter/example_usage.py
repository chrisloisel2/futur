"""
Exemples d'utilisation du Twitter Signal Engine
"""

import os
from datetime import datetime

from collector import TwitterCollector
from pipeline import TwitterSignalPipeline, StreamingPipeline
from signals import SignalAggregator


def example_batch_simple():
    """Exemple basique: collecte + traitement batch"""

    # 1. Init collecteur
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    collector = TwitterCollector(bearer_token)

    # 2. Collecter tweets récents sur BTC
    print("Collecting tweets about BTC...")
    raw_tweets = collector.search_entities(entities=["BTC"], max_results=100)
    print(f"Collected {len(raw_tweets)} tweets")

    # 3. Traiter avec pipeline
    pipeline = TwitterSignalPipeline()
    signal_batch = pipeline.process_batch(raw_tweets)

    # 4. Afficher top signaux
    print(f"\n{len(signal_batch.signals)} signals generated")

    top_signals = signal_batch.get_top_signals(n=5)
    for i, signal in enumerate(top_signals, 1):
        print(f"\n{i}. {signal.entity} ({signal.window})")
        print(f"   Direction: {signal.sentiment_direction.value}")
        print(f"   Score: {signal.credibility_weighted_score:.3f}")
        print(f"   Confidence: {signal.data_confidence:.3f}")

    # 5. Export JSON
    with open("example_signals.json", "w") as f:
        import json
        json.dump(signal_batch.to_json(), f, indent=2)

    print("\nExported to example_signals.json")


def example_multi_entity():
    """Exemple: multi-entités avec consensus"""

    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    collector = TwitterCollector(bearer_token)

    # Entités principales crypto
    entities = ["BTC", "ETH", "SOL"]

    print(f"Collecting tweets for {entities}...")
    raw_tweets = collector.search_entities(entities=entities, max_results=300)

    # Pipeline
    pipeline = TwitterSignalPipeline()
    signal_batch = pipeline.process_batch(raw_tweets)

    # Filtrer par confiance minimale
    high_confidence = signal_batch.filter_by_confidence(min_confidence=0.7)
    print(f"\nHigh confidence signals: {len(high_confidence.signals)}")

    # Consensus par entité
    aggregator = SignalAggregator()
    for entity in entities:
        consensus = aggregator.find_consensus(signal_batch.signals, entity)
        if consensus:
            print(f"\nConsensus for {entity}:")
            print(f"  Direction: {consensus['consensus_direction']}")
            print(f"  Strength: {consensus['consensus_strength']:.3f}")
            print(f"  Windows aligned: {consensus['windows_aligned']}")
            if consensus['warnings']:
                print(f"  Warnings: {', '.join(consensus['warnings'])}")


def example_streaming():
    """Exemple: streaming temps réel"""

    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")

    pipeline = StreamingPipeline(buffer_size=50)

    def on_tweet(tweet):
        """Callback pour chaque tweet"""
        signals = pipeline.add_tweet(tweet)

        if signals.signals:
            print(f"\n[{datetime.now()}] New signals batch:")
            for signal in signals.signals[:3]:  # afficher 3 premiers
                print(f"  {signal.entity}: {signal.sentiment_direction.value} "
                      f"(score: {signal.credibility_weighted_score:.2f})")

    # Démarrer stream
    print("Starting stream for BTC, ETH...")
    print("Press Ctrl+C to stop")

    collector = StreamCollector(bearer_token)

    try:
        collector.start_stream(on_tweet, entities=["BTC", "ETH"])
    except KeyboardInterrupt:
        print("\nStopping stream...")
        collector.stop_stream()

        # Flush final
        final = pipeline.flush()
        print(f"Final flush: {len(final.signals)} signals")


def example_filtering():
    """Exemple: filtrage et analyse de rejet"""

    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    collector = TwitterCollector(bearer_token)

    # Collecter large échantillon
    raw_tweets = collector.search_entities(max_results=500)

    pipeline = TwitterSignalPipeline()
    signal_batch = pipeline.process_batch(raw_tweets)

    # Stats de filtrage
    stats = pipeline.get_stats()

    print(f"\nFiltrage Stats:")
    print(f"  Collected: {stats.tweets_collected}")
    print(f"  Filtered: {stats.tweets_filtered} "
          f"({stats.tweets_filtered/stats.tweets_collected*100:.1f}%)")
    print(f"  Processed: {stats.tweets_processed}")

    print(f"\nRejection Breakdown:")
    for reason, count in sorted(
        stats.rejection_breakdown.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        print(f"  {reason}: {count}")


def example_custom_entities():
    """Exemple: entités personnalisées"""

    # Événements macro récents
    custom_entities = [
        "FOMC", "CPI", "NFP",  # macro
        "ETF", "SEC", "regulation",  # régulation
        "halving", "merge"  # crypto events
    ]

    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    collector = TwitterCollector(bearer_token)

    # Recherche sur ces entités
    raw_tweets = collector.search_entities(entities=custom_entities, max_results=200)

    pipeline = TwitterSignalPipeline()
    signal_batch = pipeline.process_batch(raw_tweets)

    # Grouper par type d'entité
    macro_signals = [s for s in signal_batch.signals if s.entity in ["FOMC", "CPI", "NFP"]]
    reg_signals = [s for s in signal_batch.signals if s.entity in ["ETF", "SEC", "regulation"]]

    print(f"\nMacro signals: {len(macro_signals)}")
    print(f"Regulation signals: {len(reg_signals)}")


if __name__ == "__main__":
    import sys

    examples = {
        "1": ("Simple batch", example_batch_simple),
        "2": ("Multi-entity consensus", example_multi_entity),
        "3": ("Streaming", example_streaming),
        "4": ("Filtering analysis", example_filtering),
        "5": ("Custom entities", example_custom_entities)
    }

    print("Twitter Signal Engine - Examples")
    print("="*50)
    for key, (name, _) in examples.items():
        print(f"{key}. {name}")

    choice = input("\nChoose example (1-5): ").strip()

    if choice in examples:
        name, func = examples[choice]
        print(f"\nRunning: {name}\n")
        func()
    else:
        print("Invalid choice")
