"""
Pipeline S3 pour scraping orienté trading algorithmique crypto
Collecte exclusivement BTC, ETH, SOL en format JSON Lines

Objectif: Métadonnées exploitables pour modèles IA de trading
Architecture: s3://qbia/bourse/raw/{type}/{source}/{asset}/{YYYY}/{MM}/{DD}/
Format: JSON Lines (.jsonl)
"""

import logging
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
import boto3
from botocore.exceptions import ClientError
import re

logger = logging.getLogger(__name__)


class S3TradingPipeline:
    """
    Pipeline dédié au trading algorithmique.

    Caractéristiques:
    - Filtrage strict: BTC, ETH, SOL uniquement
    - Format: JSON Lines (1 item = 1 ligne JSON)
    - Validation: Schéma minimal requis
    - Stockage: S3 avec structure Hive
    - Pas de nettoyage/NLP: Données brutes
    """

    # Assets autorisés UNIQUEMENT
    ALLOWED_ASSETS = {'BTC', 'ETH', 'SOL'}

    # Mots-clés pour détecter les assets
    ASSET_KEYWORDS = {
        'BTC': [
            'bitcoin', 'btc', 'btcusd', 'btc/usd', 'xbt',
            'btcusdt', 'satoshi', 'sats', 'btceur'
        ],
        'ETH': [
            'ethereum', 'eth', 'ethusd', 'eth/usd', 'ethusdt',
            'ether', 'etheur', 'vitalik'
        ],
        'SOL': [
            'solana', 'sol', 'solusd', 'sol/usd', 'solusdt',
            'soleur', 'solana network'
        ]
    }

    # Types de données autorisés
    ALLOWED_TYPES = {'news', 'forum', 'market', 'social', 'onchain'}

    # Schéma minimal requis
    REQUIRED_FIELDS = {'asset', 'source', 'type', 'scraped_at'}
    OPTIONAL_FIELDS = {'url', 'title', 'content', 'author', 'published_at', 'language', 'metadata'}

    def __init__(
        self,
        bucket: str = 'qbia',
        prefix: str = 'bourse/raw',
        region_name: str = 'eu-west-3',
        batch_size: int = 100
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip('/')
        self.region_name = region_name
        self.batch_size = batch_size

        # Buffers par type/source/asset/date
        self.buffers: Dict[str, List[dict]] = {}

        # Client S3
        self.s3_client = None

        # Stats
        self.stats = {
            'items_received': 0,
            'items_validated': 0,
            'items_rejected': 0,
            'items_uploaded': 0,
            'batches_uploaded': 0,
            'errors': 0,
            'rejections': {
                'invalid_asset': 0,
                'no_asset_detected': 0,
                'invalid_type': 0,
                'missing_fields': 0,
                'validation_error': 0,
            }
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize pipeline from crawler settings."""
        return cls(
            bucket=crawler.settings.get('S3_TRADING_BUCKET', 'qbia'),
            prefix=crawler.settings.get('S3_TRADING_PREFIX', 'bourse/raw'),
            region_name=crawler.settings.get('AWS_REGION', 'eu-west-3'),
            batch_size=crawler.settings.getint('S3_TRADING_BATCH_SIZE', 100),
        )

    def open_spider(self, spider):
        """Called when spider is opened."""
        self.s3_client = boto3.client('s3', region_name=self.region_name)
        logger.info(f"📊 S3TradingPipeline opened: s3://{self.bucket}/{self.prefix}")
        logger.info(f"🎯 Trading mode: Filtering for {', '.join(self.ALLOWED_ASSETS)} only")

    def close_spider(self, spider):
        """Called when spider is closed - flush remaining items."""
        logger.info("💾 Flushing remaining trading data to S3...")

        # Save all remaining batches
        for buffer_key in list(self.buffers.keys()):
            if self.buffers[buffer_key]:
                self._save_batch(buffer_key, self.buffers[buffer_key])

        # Log final stats
        logger.info("=" * 80)
        logger.info("📊 S3TradingPipeline FINAL STATS")
        logger.info("=" * 80)
        logger.info(f"Items received: {self.stats['items_received']}")
        logger.info(f"Items validated: {self.stats['items_validated']}")
        logger.info(f"Items rejected: {self.stats['items_rejected']}")
        logger.info(f"Items uploaded: {self.stats['items_uploaded']}")
        logger.info(f"Batches uploaded: {self.stats['batches_uploaded']}")
        logger.info(f"Errors: {self.stats['errors']}")

        if self.stats['items_rejected'] > 0:
            logger.info("\n❌ Rejection reasons:")
            for reason, count in self.stats['rejections'].items():
                if count > 0:
                    logger.info(f"   {reason}: {count}")

        logger.info("=" * 80)

    def process_item(self, item, spider):
        """Process each scraped item with strict trading validation."""
        self.stats['items_received'] += 1

        try:
            # 1. Détection de l'asset
            asset = self._detect_asset(item)
            if not asset:
                self.stats['items_rejected'] += 1
                self.stats['rejections']['no_asset_detected'] += 1
                logger.debug(f"❌ No asset detected in item from {spider.name}")
                return item

            # 2. Validation de l'asset
            if asset not in self.ALLOWED_ASSETS:
                self.stats['items_rejected'] += 1
                self.stats['rejections']['invalid_asset'] += 1
                logger.debug(f"❌ Invalid asset '{asset}' (allowed: {self.ALLOWED_ASSETS})")
                return item

            # 3. Validation du type
            data_type = self._get_data_type(item, spider)
            if data_type not in self.ALLOWED_TYPES:
                self.stats['items_rejected'] += 1
                self.stats['rejections']['invalid_type'] += 1
                logger.debug(f"❌ Invalid type '{data_type}' (allowed: {self.ALLOWED_TYPES})")
                return item

            # 4. Préparer l'item (format trading)
            trading_item = self._prepare_trading_item(item, asset, data_type, spider)

            # 5. Validation du schéma
            if not self._validate_schema(trading_item):
                self.stats['items_rejected'] += 1
                self.stats['rejections']['missing_fields'] += 1
                logger.debug(f"❌ Missing required fields in item")
                return item

            self.stats['items_validated'] += 1

            # 6. Déterminer la date pour partitionnement
            date_parts = self._get_date_parts(trading_item)

            # 7. Créer la clé du buffer: type|source|asset|YYYY|MM|DD
            buffer_key = f"{data_type}|{trading_item['source']}|{asset}|{date_parts['year']}|{date_parts['month']}|{date_parts['day']}"

            # 8. Initialiser le buffer si nécessaire
            if buffer_key not in self.buffers:
                self.buffers[buffer_key] = []

            # 9. Ajouter au buffer
            self.buffers[buffer_key].append(trading_item)

            # 10. Sauvegarder si le batch est prêt
            if len(self.buffers[buffer_key]) >= self.batch_size:
                self._save_batch(buffer_key, self.buffers[buffer_key])
                self.buffers[buffer_key] = []

            return item

        except Exception as e:
            logger.error(f"❌ Error processing trading item: {e}", exc_info=True)
            self.stats['errors'] += 1
            self.stats['rejections']['validation_error'] += 1
            return item

    def _detect_asset(self, item) -> Optional[str]:
        """
        Détecte l'asset (BTC/ETH/SOL) dans l'item.

        Recherche dans: title, content, text, body, url
        Retourne: 'BTC', 'ETH', 'SOL' ou None
        """
        # Si l'asset est déjà spécifié et valide
        if 'asset' in item and item['asset'] in self.ALLOWED_ASSETS:
            return item['asset']

        # Construire le texte à analyser
        search_fields = ['title', 'content', 'text', 'body', 'url', 'description']
        search_text = ''

        for field in search_fields:
            if field in item and item[field]:
                search_text += ' ' + str(item[field]).lower()

        if not search_text:
            return None

        # Compter les occurrences de chaque asset
        asset_scores = {}

        for asset, keywords in self.ASSET_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                # Compter les occurrences (avec boundary pour éviter les faux positifs)
                pattern = r'\b' + re.escape(keyword) + r'\b'
                matches = re.findall(pattern, search_text, re.IGNORECASE)
                score += len(matches)

            if score > 0:
                asset_scores[asset] = score

        # Retourner l'asset avec le score le plus élevé
        if asset_scores:
            detected_asset = max(asset_scores, key=asset_scores.get)
            logger.debug(f"🎯 Detected asset: {detected_asset} (scores: {asset_scores})")
            return detected_asset

        return None

    def _get_data_type(self, item, spider) -> str:
        """
        Détermine le type de données (news/forum/market/social/onchain).
        """
        # Si le type est déjà spécifié
        if 'type' in item:
            return item['type']

        # Si l'item a un type explicite
        if 'data_type' in item:
            return item['data_type']

        # Basé sur les champs présents
        if 'transaction_hash' in item or 'blockchain' in item or 'amount' in item:
            return 'onchain'

        if 'forum_name' in item or 'thread_title' in item:
            return 'forum'

        if 'platform' in item or 'social_type' in item or 'likes' in item or 'retweets' in item:
            return 'social'

        if 'price' in item or 'volume' in item or 'market_cap' in item:
            return 'market'

        # Basé sur le nom du spider
        spider_name = spider.name.lower()

        if 'whale' in spider_name or 'arkham' in spider_name:
            return 'market'

        if 'bitcointalk' in spider_name or 'forum' in spider_name:
            return 'forum'

        if 'twitter' in spider_name or 'telegram' in spider_name or 'reddit' in spider_name:
            return 'social'

        # Par défaut: news
        return 'news'

    def _prepare_trading_item(self, item, asset: str, data_type: str, spider) -> dict:
        """
        Prépare l'item au format trading minimal.

        Schéma minimal requis:
        {
            "asset": "BTC|ETH|SOL",
            "source": "string",
            "type": "news|forum|market|social|onchain",
            "url": "string",
            "title": "string|null",
            "content": "string|null",
            "author": "string|null",
            "published_at": "ISO8601|null",
            "scraped_at": "ISO8601",
            "language": "string|null",
            "metadata": {}
        }
        """
        now = datetime.now(timezone.utc).isoformat()

        trading_item = {
            # Champs requis
            'asset': asset,
            'source': item.get('source', spider.name),
            'type': data_type,
            'scraped_at': now,

            # Champs optionnels
            'url': item.get('url') or item.get('link') or None,
            'title': item.get('title') or None,
            'content': self._extract_content(item),
            'author': item.get('author') or item.get('author_name') or None,
            'published_at': self._normalize_timestamp(item.get('published_at') or item.get('timestamp') or item.get('created_at')),
            'language': item.get('language') or item.get('lang') or None,

            # Métadonnées additionnelles (tout le reste)
            'metadata': {}
        }

        # Ajouter métadonnées supplémentaires
        excluded_fields = {'asset', 'source', 'type', 'url', 'title', 'content', 'author', 'published_at', 'scraped_at', 'language'}

        for key, value in item.items():
            if key not in excluded_fields and value is not None:
                # Ne garder que les valeurs simples (str, int, float, bool) ou listes/dicts
                if isinstance(value, (str, int, float, bool, list, dict)):
                    trading_item['metadata'][key] = value

        return trading_item

    def _extract_content(self, item) -> Optional[str]:
        """Extrait le contenu de l'item (plusieurs champs possibles)."""
        content_fields = ['content', 'body', 'text', 'description', 'post_content']

        for field in content_fields:
            if field in item and item[field]:
                content = str(item[field])
                # Limiter à 10000 caractères pour éviter les items trop gros
                return content[:10000] if len(content) > 10000 else content

        return None

    def _normalize_timestamp(self, timestamp) -> Optional[str]:
        """Normalise un timestamp en ISO8601."""
        if not timestamp:
            return None

        try:
            if isinstance(timestamp, datetime):
                return timestamp.isoformat()
            elif isinstance(timestamp, str):
                # Essayer de parser et re-formater
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                return dt.isoformat()
            elif isinstance(timestamp, (int, float)):
                # Unix timestamp
                dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                return dt.isoformat()
        except:
            pass

        return None

    def _validate_schema(self, item: dict) -> bool:
        """Valide que l'item respecte le schéma minimal."""
        # Vérifier les champs requis
        for field in self.REQUIRED_FIELDS:
            if field not in item or item[field] is None or item[field] == '':
                logger.debug(f"❌ Missing required field: {field}")
                return False

        # Vérifier les valeurs
        if item['asset'] not in self.ALLOWED_ASSETS:
            return False

        if item['type'] not in self.ALLOWED_TYPES:
            return False

        return True

    def _get_date_parts(self, item: dict) -> dict:
        """Extrait les parties de date pour le partitionnement."""
        # Utiliser published_at si disponible, sinon scraped_at
        timestamp = item.get('published_at') or item.get('scraped_at')

        try:
            if timestamp:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            else:
                dt = datetime.now(timezone.utc)

            return {
                'year': dt.strftime('%Y'),
                'month': dt.strftime('%m'),
                'day': dt.strftime('%d')
            }
        except:
            now = datetime.now(timezone.utc)
            return {
                'year': now.strftime('%Y'),
                'month': now.strftime('%m'),
                'day': now.strftime('%d')
            }

    def _save_batch(self, buffer_key: str, items: List[dict]):
        """
        Sauvegarde un batch d'items sur S3 en format JSON Lines.

        Structure S3:
        s3://qbia/bourse/raw/{type}/{source}/{asset}/{YYYY}/{MM}/{DD}/{source}_{asset}_{timestamp}.jsonl
        """
        try:
            if not items:
                return

            # Parse la clé du buffer: type|source|asset|YYYY|MM|DD
            parts = buffer_key.split('|')
            data_type, source, asset, year, month, day = parts

            logger.info(f"💾 Saving trading batch: {data_type}/{source}/{asset}/{year}/{month}/{day} ({len(items)} items)")

            # Créer le nom de fichier avec timestamp
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f"{source}_{asset}_{timestamp}.jsonl"

            # Structure S3: {prefix}/{type}/{source}/{asset}/{YYYY}/{MM}/{DD}/{filename}
            s3_key = f"{self.prefix}/{data_type}/{source}/{asset}/{year}/{month}/{day}/{filename}"

            # Créer le fichier JSON Lines en local temporaire
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as tmp_file:
                temp_path = Path(tmp_file.name)

                # Écrire chaque item sur une ligne (JSON Lines)
                for item in items:
                    json_line = json.dumps(item, ensure_ascii=False)
                    tmp_file.write(json_line + '\n')

            try:
                # Upload vers S3
                self.s3_client.upload_file(
                    str(temp_path),
                    self.bucket,
                    s3_key,
                    ExtraArgs={'ContentType': 'application/jsonl'}
                )

                logger.info(f"✅ Uploaded {len(items)} items to s3://{self.bucket}/{s3_key}")

                self.stats['items_uploaded'] += len(items)
                self.stats['batches_uploaded'] += 1

            except ClientError as e:
                logger.error(f"❌ S3 upload error: {e}")
                self.stats['errors'] += 1

            finally:
                # Nettoyer le fichier temporaire
                if temp_path.exists():
                    temp_path.unlink()

        except Exception as e:
            logger.error(f"❌ Error saving trading batch {buffer_key}: {e}", exc_info=True)
            self.stats['errors'] += 1


# Fonction utilitaire pour tester la détection d'asset
def test_asset_detection():
    """Test de la détection d'asset."""
    pipeline = S3TradingPipeline()

    test_cases = [
        {"title": "Bitcoin price reaches new high", "expected": "BTC"},
        {"content": "Ethereum developers announce new upgrade", "expected": "ETH"},
        {"text": "Solana network sees massive growth", "expected": "SOL"},
        {"title": "BTC and ETH both rally today", "expected": "BTC"},  # BTC devrait gagner
        {"content": "Random crypto news about some altcoin", "expected": None},
        {"url": "https://example.com/btcusd-analysis", "expected": "BTC"},
    ]

    print("Testing asset detection:")
    print("=" * 80)

    for i, test in enumerate(test_cases, 1):
        detected = pipeline._detect_asset(test)
        expected = test.pop('expected')
        status = "✅" if detected == expected else "❌"
        print(f"{status} Test {i}: Expected {expected}, Got {detected}")
        print(f"   Item: {test}")
        print()


if __name__ == '__main__':
    test_asset_detection()
