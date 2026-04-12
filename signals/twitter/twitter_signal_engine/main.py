"""
Point d'entrée principal du système de signaux Twitter.
Modes: batch, streaming, backtest
"""

import argparse
import json
import os
from datetime import datetime, timedelta
import logging

from collector import TwitterCollector, StreamCollector
from pipeline import TwitterSignalPipeline, StreamingPipeline
from signals import SignalAggregator
from config import ALL_ENTITIES


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def batch_mode(bearer_token: str, entities: list, max_tweets: int, output_file: str):
    """
    Mode batch: collecte → traitement → export signaux

    Args:
        bearer_token: Twitter API bearer token
        entities: liste d'entités à tracker
        max_tweets: nombre max de tweets à collecter
        output_file: fichier JSON de sortie
    """

    logger.info(f"Starting batch mode for entities: {entities}")

    # 1. Collecter tweets
    collector = TwitterCollector(bearer_token)
    raw_tweets = collector.search_entities(entities, max_results=max_tweets)

    logger.info(f"Collected {len(raw_tweets)} raw tweets")

    if not raw_tweets:
        logger.warning("No tweets collected, exiting")
        return

    # 2. Traiter avec pipeline
    pipeline = TwitterSignalPipeline()
    signal_batch = pipeline.process_batch(raw_tweets)

    # 3. Statistiques
    stats = pipeline.get_stats()
    logger.info(f"Pipeline stats: {stats.tweets_processed} processed, "
                f"{stats.signals_generated} signals generated")

    # 4. Export
    output = {
        "metadata": {
            "mode": "batch",
            "collection_time": datetime.utcnow().isoformat(),
            "entities": entities,
            "tweets_collected": len(raw_tweets),
            "tweets_processed": stats.tweets_processed,
            "signals_generated": stats.signals_generated
        },
        "signals": signal_batch.to_json(),
        "stats": {
            "tweets_collected": stats.tweets_collected,
            "tweets_filtered": stats.tweets_filtered,
            "tweets_processed": stats.tweets_processed,
            "rejection_breakdown": stats.rejection_breakdown
        }
    }

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"Signals exported to {output_file}")

    # 5. Top signals
    top_signals = signal_batch.get_top_signals(n=10)
    print("\n" + "="*80)
    print("TOP 10 SIGNALS")
    print("="*80)
    for i, signal in enumerate(top_signals, 1):
        print(f"\n{i}. {signal.entity} ({signal.window})")
        print(f"   Direction: {signal.sentiment_direction.value}")
        print(f"   Strength: {signal.sentiment_strength:.3f}")
        print(f"   Burst: {signal.attention_burst:.3f}")
        print(f"   Score: {signal.credibility_weighted_score:.3f}")
        print(f"   Confidence: {signal.data_confidence:.3f}")
        if signal.warning_flags:
            print(f"   Warnings: {', '.join(signal.warning_flags)}")


def streaming_mode(bearer_token: str, entities: list, output_dir: str, buffer_size: int):
    """
    Mode streaming: collecte temps réel → signaux continus

    Args:
        bearer_token: Twitter API bearer token
        entities: liste d'entités à tracker
        output_dir: dossier pour signaux (1 fichier/batch)
        buffer_size: taille du buffer avant traitement
    """

    logger.info(f"Starting streaming mode for entities: {entities}")

    os.makedirs(output_dir, exist_ok=True)

    # Pipeline streaming
    pipeline = StreamingPipeline(buffer_size=buffer_size)

    # Callback pour nouveaux tweets
    batch_counter = 0

    def on_tweet(tweet):
        nonlocal batch_counter

        signals = pipeline.add_tweet(tweet)

        if signals.signals:
            batch_counter += 1
            output_file = os.path.join(
                output_dir,
                f"signals_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{batch_counter}.json"
            )

            with open(output_file, 'w') as f:
                json.dump(signals.to_json(), f, indent=2)

            logger.info(f"Batch {batch_counter}: {len(signals.signals)} signals → {output_file}")

    # Démarrer stream
    collector = StreamCollector(bearer_token)

    try:
        collector.start_stream(on_tweet, entities)
    except KeyboardInterrupt:
        logger.info("Stopping stream...")
        collector.stop_stream()

        # Flush buffer final
        final_signals = pipeline.flush()
        if final_signals.signals:
            output_file = os.path.join(
                output_dir,
                f"signals_final_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            )
            with open(output_file, 'w') as f:
                json.dump(final_signals.to_json(), f, indent=2)

        logger.info("Stream stopped")


def consensus_mode(signal_files: list, output_file: str):
    """
    Mode consensus: agrège signaux multi-fenêtres

    Args:
        signal_files: liste de fichiers JSON avec signaux
        output_file: fichier de sortie consensus
    """

    logger.info(f"Finding consensus across {len(signal_files)} signal files")

    # Charger tous les signaux
    all_signals = []

    for file in signal_files:
        with open(file, 'r') as f:
            data = json.load(f)
            # Parser signaux (TODO: implement deserialize)
            pass

    # Trouver consensus par entité
    aggregator = SignalAggregator()
    entities = set(s.entity for s in all_signals)

    consensus_results = []
    for entity in entities:
        consensus = aggregator.find_consensus(all_signals, entity)
        if consensus:
            consensus_results.append(consensus)

    # Export
    output = {
        "consensus_time": datetime.utcnow().isoformat(),
        "signal_files_count": len(signal_files),
        "consensus_count": len(consensus_results),
        "consensus_signals": consensus_results
    }

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"Consensus exported to {output_file}")


def main():
    """CLI principal"""

    parser = argparse.ArgumentParser(
        description="Twitter/X Signal Engine for Crypto Trading"
    )

    parser.add_argument(
        "mode",
        choices=["batch", "stream", "consensus"],
        help="Mode de fonctionnement"
    )

    parser.add_argument(
        "--bearer-token",
        help="Twitter API Bearer Token (ou env var TWITTER_BEARER_TOKEN)"
    )

    parser.add_argument(
        "--entities",
        nargs="+",
        help="Entités à tracker (default: toutes)"
    )

    parser.add_argument(
        "--max-tweets",
        type=int,
        default=100,
        help="Max tweets en mode batch"
    )

    parser.add_argument(
        "--output",
        default="signals_output.json",
        help="Fichier de sortie"
    )

    parser.add_argument(
        "--output-dir",
        default="signals_stream",
        help="Dossier de sortie (mode stream)"
    )

    parser.add_argument(
        "--buffer-size",
        type=int,
        default=100,
        help="Taille buffer (mode stream)"
    )

    parser.add_argument(
        "--signal-files",
        nargs="+",
        help="Fichiers de signaux (mode consensus)"
    )

    args = parser.parse_args()

    # Récupérer bearer token
    bearer_token = args.bearer_token or os.getenv("TWITTER_BEARER_TOKEN")

    if args.mode in ["batch", "stream"] and not bearer_token:
        parser.error("--bearer-token ou TWITTER_BEARER_TOKEN requis")

    # Entités
    entities = args.entities or ALL_ENTITIES

    # Router vers mode
    if args.mode == "batch":
        batch_mode(bearer_token, entities, args.max_tweets, args.output)

    elif args.mode == "stream":
        streaming_mode(bearer_token, entities, args.output_dir, args.buffer_size)

    elif args.mode == "consensus":
        if not args.signal_files:
            parser.error("--signal-files requis pour mode consensus")
        consensus_mode(args.signal_files, args.output)


if __name__ == "__main__":
    main()
