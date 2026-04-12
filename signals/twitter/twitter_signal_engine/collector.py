"""
Collecteur de données depuis Twitter API v2.
Streaming et recherche.
"""

import tweepy
from datetime import datetime
from typing import List, Optional, Callable
import time
import logging

from models import RawTweet
from config import (
    ALL_ENTITIES,
    RATE_LIMIT_REQUESTS_PER_15MIN,
    RATE_LIMIT_TWEETS_PER_REQUEST
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterCollector:
    """Collecteur via Twitter API v2"""

    def __init__(self, bearer_token: str):
        """
        Initialiser avec bearer token (Academic Research access recommandé)
        """
        self.client = tweepy.Client(
            bearer_token=bearer_token,
            wait_on_rate_limit=True
        )

        self.rate_limit_remaining = RATE_LIMIT_REQUESTS_PER_15MIN
        self.rate_limit_reset = time.time()

    def search_recent(
        self,
        query: str,
        max_results: int = 100,
        since_id: Optional[str] = None
    ) -> List[RawTweet]:
        """
        Recherche de tweets récents (7 derniers jours)

        Args:
            query: requête Twitter (ex: "BTC OR ETH")
            max_results: max tweets par requête (10-100)
            since_id: ID du dernier tweet collecté (pour pagination)
        """

        try:
            response = self.client.search_recent_tweets(
                query=query,
                max_results=min(max_results, RATE_LIMIT_TWEETS_PER_REQUEST),
                since_id=since_id,
                tweet_fields=[
                    'created_at', 'lang', 'public_metrics',
                    'entities', 'referenced_tweets', 'author_id'
                ],
                expansions=['author_id'],
                user_fields=[
                    'created_at', 'public_metrics', 'verified'
                ]
            )

            self._update_rate_limit()

            if not response.data:
                return []

            # Parser réponse
            tweets = self._parse_response(response)
            logger.info(f"Collected {len(tweets)} tweets for query: {query}")

            return tweets

        except tweepy.TooManyRequests:
            logger.warning("Rate limit exceeded, waiting...")
            time.sleep(60)
            return []

        except Exception as e:
            logger.error(f"Error collecting tweets: {e}")
            return []

    def search_entities(
        self,
        entities: Optional[List[str]] = None,
        max_results: int = 100
    ) -> List[RawTweet]:
        """
        Rechercher tweets mentionnant des entités crypto/macro

        Args:
            entities: liste d'entités (None = toutes)
            max_results: max tweets
        """

        if entities is None:
            entities = ALL_ENTITIES

        # Construire requête (Twitter query syntax)
        # OR entre entités, limiter à crypto context
        query_parts = [f"({entity})" for entity in entities[:25]]  # max 25 termes
        query = " OR ".join(query_parts)

        # Filtres additionnels
        query += " -is:retweet lang:en"  # pas de RT, anglais seulement

        return self.search_recent(query, max_results)

    def _parse_response(self, response) -> List[RawTweet]:
        """Parser réponse Twitter API → RawTweet"""

        tweets = []
        timestamp_collecte = datetime.utcnow()

        # Créer dict des users
        users = {}
        if response.includes and 'users' in response.includes:
            for user in response.includes['users']:
                users[user.id] = user

        for tweet_data in response.data:
            # Récupérer user info
            author = users.get(tweet_data.author_id)

            if not author:
                continue  # skip si pas d'info auteur

            # Extraire métriques
            metrics = tweet_data.public_metrics
            author_metrics = author.public_metrics

            # Extraire entités (hashtags, cashtags)
            hashtags = []
            cashtags = []
            has_links = False
            has_media = False

            if hasattr(tweet_data, 'entities') and tweet_data.entities:
                if 'hashtags' in tweet_data.entities:
                    hashtags = [h['tag'] for h in tweet_data.entities['hashtags']]
                if 'cashtags' in tweet_data.entities:
                    cashtags = [c['tag'] for c in tweet_data.entities['cashtags']]
                if 'urls' in tweet_data.entities:
                    has_links = len(tweet_data.entities['urls']) > 0

            if hasattr(tweet_data, 'attachments'):
                has_media = True

            # Déterminer type (quote, reply)
            is_quote = False
            is_reply = False
            quoted_id = None
            reply_id = None

            if hasattr(tweet_data, 'referenced_tweets') and tweet_data.referenced_tweets:
                for ref in tweet_data.referenced_tweets:
                    if ref.type == 'quoted':
                        is_quote = True
                        quoted_id = ref.id
                    elif ref.type == 'replied_to':
                        is_reply = True
                        reply_id = ref.id

            # Construire RawTweet
            raw_tweet = RawTweet(
                tweet_id=tweet_data.id,
                text=tweet_data.text,
                lang=tweet_data.lang,
                timestamp_publication=tweet_data.created_at,
                timestamp_collecte=timestamp_collecte,
                author_id=author.id,
                followers_count=author_metrics['followers_count'],
                following_count=author_metrics['following_count'],
                account_created_at=author.created_at,
                verified=author.verified if hasattr(author, 'verified') else False,
                impressions=metrics.get('impression_count'),
                likes=metrics['like_count'],
                retweets=metrics['retweet_count'],
                replies=metrics['reply_count'],
                has_media=has_media,
                has_links=has_links,
                hashtags=hashtags,
                cashtags=cashtags,
                is_quote=is_quote,
                is_reply=is_reply,
                quoted_tweet_id=quoted_id,
                reply_to_tweet_id=reply_id
            )

            tweets.append(raw_tweet)

        return tweets

    def _update_rate_limit(self):
        """MAJ compteur rate limit"""
        self.rate_limit_remaining -= 1

        if self.rate_limit_remaining <= 0:
            # Attendre reset (15 min)
            wait_time = self.rate_limit_reset - time.time()
            if wait_time > 0:
                logger.warning(f"Rate limit hit, waiting {wait_time:.0f}s")
                time.sleep(wait_time)

            self.rate_limit_remaining = RATE_LIMIT_REQUESTS_PER_15MIN
            self.rate_limit_reset = time.time() + 900  # +15 min


class StreamCollector:
    """Collecteur en streaming temps réel"""

    def __init__(self, bearer_token: str):
        self.bearer_token = bearer_token
        self.stream = None

    def start_stream(
        self,
        callback: Callable[[RawTweet], None],
        entities: Optional[List[str]] = None
    ):
        """
        Démarrer stream temps réel

        Args:
            callback: fonction appelée pour chaque tweet
            entities: entités à tracker
        """

        if entities is None:
            entities = ALL_ENTITIES

        # Créer custom stream handler
        class CustomStreamListener(tweepy.StreamingClient):
            def __init__(self, bearer_token, callback_fn):
                super().__init__(bearer_token, wait_on_rate_limit=True)
                self.callback = callback_fn

            def on_tweet(self, tweet):
                # Parser et appeler callback
                try:
                    raw_tweet = self._parse_stream_tweet(tweet)
                    self.callback(raw_tweet)
                except Exception as e:
                    logger.error(f"Error processing stream tweet: {e}")

            def _parse_stream_tweet(self, tweet):
                # Similar parsing que search_recent
                # TODO: implement full parsing
                pass

            def on_errors(self, errors):
                logger.error(f"Stream errors: {errors}")

        self.stream = CustomStreamListener(self.bearer_token, callback)

        # Ajouter règles de filtrage
        for entity in entities[:25]:  # max 25 règles
            self.stream.add_rules(
                tweepy.StreamRule(f"{entity} lang:en -is:retweet")
            )

        # Démarrer stream
        logger.info(f"Starting stream for {len(entities)} entities")
        self.stream.filter(
            tweet_fields=['created_at', 'lang', 'public_metrics', 'entities'],
            expansions=['author_id'],
            user_fields=['created_at', 'public_metrics', 'verified']
        )

    def stop_stream(self):
        """Arrêter stream"""
        if self.stream:
            self.stream.disconnect()
            logger.info("Stream stopped")
