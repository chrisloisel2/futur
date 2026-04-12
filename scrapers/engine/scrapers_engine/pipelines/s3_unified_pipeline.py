"""
Pipeline S3 unifié pour tous les scrapers
Respecte l'architecture partitionnée Hive pour Athena
"""

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
import boto3
from botocore.exceptions import ClientError
import hashlib
import json

logger = logging.getLogger(__name__)


class S3UnifiedPipeline:
    """
    Pipeline unifié pour sauvegarder toutes les données scrappées sur S3
    avec structure partitionnée Hive compatible Athena.

    Structure:
    s3://qbia/bourse/raw/<data_type>/
        source=<source>/
            date=<YYYY-MM-DD>/
                part-<timestamp>.parquet
    """

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

        # Buffers pour chaque type de données
        self.buffers: Dict[str, List[dict]] = {}

        # Client S3
        self.s3_client = None

        # Stats
        self.stats = {
            'items_processed': 0,
            'items_saved': 0,
            'batches_uploaded': 0,
            'errors': 0,
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize pipeline from crawler settings."""
        return cls(
            bucket=crawler.settings.get('S3_BUCKET', 'qbia'),
            prefix=crawler.settings.get('S3_PREFIX', 'bourse/raw'),
            region_name=crawler.settings.get('AWS_REGION', 'eu-west-3'),
            batch_size=crawler.settings.getint('S3_BATCH_SIZE', 100),
        )

    def open_spider(self, spider):
        """Called when spider is opened."""
        self.s3_client = boto3.client('s3', region_name=self.region_name)
        logger.info(f"S3UnifiedPipeline opened: s3://{self.bucket}/{self.prefix}")

    def close_spider(self, spider):
        """Called when spider is closed - flush remaining items."""
        logger.info("Flushing remaining items to S3...")

        # Save all remaining batches
        for buffer_key in list(self.buffers.keys()):
            if self.buffers[buffer_key]:
                self._save_batch(buffer_key, self.buffers[buffer_key])

        logger.info(f"Pipeline stats: {self.stats}")

    def process_item(self, item, spider):
        """Process each scraped item."""
        try:
            # Déterminer le type de données
            data_type = self._get_data_type(item)
            if not data_type:
                logger.warning(f"Unknown data type for item: {item}")
                return item

            # Déterminer la source
            source = item.get('source', spider.name)

            # Déterminer la date (pour partitionnement)
            date = self._get_item_date(item)

            # Créer la clé du buffer
            buffer_key = f"{data_type}|{source}|{date}"

            # Initialiser le buffer si nécessaire
            if buffer_key not in self.buffers:
                self.buffers[buffer_key] = []

            # Préparer l'item pour le stockage
            item_dict = self._prepare_item(item, data_type, source, date)

            # Ajouter au buffer
            self.buffers[buffer_key].append(item_dict)
            self.stats['items_processed'] += 1

            # Sauvegarder si le batch est prêt
            if len(self.buffers[buffer_key]) >= self.batch_size:
                self._save_batch(buffer_key, self.buffers[buffer_key])
                self.buffers[buffer_key] = []

            return item

        except Exception as e:
            logger.error(f"Error processing item: {e}", exc_info=True)
            self.stats['errors'] += 1
            return item

    def _get_data_type(self, item) -> Optional[str]:
        """Déterminer le type de données basé sur l'item."""

        # News articles
        if 'title' in item and 'body' in item and 'published_at' in item:
            return 'news'

        # Social media posts
        if 'text' in item and ('platform' in item or 'social_type' in item):
            return 'social'

        # Forum posts
        if 'forum_name' in item or 'thread_title' in item:
            return 'forums'

        # Transaction alerts (whale alerts, etc.)
        if 'transaction_hash' in item or 'amount' in item and 'blockchain' in item:
            return 'transactions'

        # Web content (generic)
        if 'url' in item and 'content' in item:
            return 'web'

        # Alternative data (sentiment, etc.)
        if 'sentiment' in item or 'signal_type' in item:
            return 'signals'

        return None

    def _get_item_date(self, item) -> str:
        """Extraire la date de l'item pour le partitionnement."""
        # Essayer différents champs de date
        date_fields = ['published_at', 'scraped_at', 'timestamp', 'created_at']

        for field in date_fields:
            if field in item and item[field]:
                try:
                    if isinstance(item[field], datetime):
                        return item[field].strftime('%Y-%m-%d')
                    elif isinstance(item[field], str):
                        dt = datetime.fromisoformat(item[field].replace('Z', '+00:00'))
                        return dt.strftime('%Y-%m-%d')
                except:
                    continue

        # Fallback: date actuelle
        return datetime.utcnow().strftime('%Y-%m-%d')

    def _prepare_item(self, item, data_type: str, source: str, date: str) -> dict:
        """Préparer l'item pour le stockage en ajoutant les métadonnées."""
        item_dict = dict(item)

        # Ajouter métadonnées de partitionnement
        item_dict['_data_type'] = data_type
        item_dict['_source'] = source
        item_dict['_partition_date'] = date
        item_dict['_scraped_at'] = datetime.utcnow().isoformat()

        # Générer un ID unique si absent
        if 'content_hash' not in item_dict and 'article_id' not in item_dict:
            item_dict['_id'] = self._generate_id(item_dict)

        # Normaliser les timestamps
        item_dict = self._normalize_timestamps(item_dict)

        return item_dict

    def _generate_id(self, item_dict: dict) -> str:
        """Générer un ID unique pour l'item."""
        # Utiliser titre + source + date pour l'ID
        content = json.dumps({
            'title': item_dict.get('title', item_dict.get('text', '')),
            'source': item_dict.get('source', ''),
            'url': item_dict.get('url', ''),
        }, sort_keys=True)

        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _normalize_timestamps(self, item_dict: dict) -> dict:
        """Normaliser tous les timestamps en ISO format."""
        timestamp_fields = [
            'published_at', 'scraped_at', 'timestamp',
            'created_at', 'updated_at', 'collected_at'
        ]

        for field in timestamp_fields:
            if field in item_dict and item_dict[field]:
                try:
                    if isinstance(item_dict[field], datetime):
                        item_dict[field] = item_dict[field].isoformat()
                    elif isinstance(item_dict[field], str):
                        # Vérifier que c'est bien ISO format
                        dt = datetime.fromisoformat(item_dict[field].replace('Z', '+00:00'))
                        item_dict[field] = dt.isoformat()
                except:
                    pass

        return item_dict

    def _save_batch(self, buffer_key: str, items: List[dict]):
        """Sauvegarder un batch d'items sur S3 en format Parquet."""
        try:
            if not items:
                return

            # Parse la clé du buffer
            data_type, source, date = buffer_key.split('|')

            logger.info(f"Saving batch: {data_type}/{source}/{date} with {len(items)} items")

            # Convertir en DataFrame
            df = pd.DataFrame(items)

            # Nettoyer les colonnes pour Parquet
            df = self._clean_dataframe(df)

            # Créer le chemin S3 avec structure Hive
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            s3_key = f"{self.prefix}/{data_type}/source={source}/date={date}/part-{timestamp}.parquet"

            # Sauvegarder temporairement en local
            with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp_file:
                temp_path = Path(tmp_file.name)

            try:
                # Écrire le parquet
                df.to_parquet(temp_path, index=False, compression='snappy')

                # Upload vers S3
                self.s3_client.upload_file(str(temp_path), self.bucket, s3_key)

                logger.info(f"✅ Uploaded to s3://{self.bucket}/{s3_key}")

                self.stats['items_saved'] += len(items)
                self.stats['batches_uploaded'] += 1

            finally:
                # Nettoyer le fichier temporaire
                if temp_path.exists():
                    temp_path.unlink()

        except Exception as e:
            logger.error(f"Error saving batch {buffer_key}: {e}", exc_info=True)
            self.stats['errors'] += 1

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Nettoyer le DataFrame pour la compatibilité Parquet."""

        # Convertir les listes en JSON strings (Parquet ne supporte pas les nested lists)
        for col in df.columns:
            if df[col].dtype == 'object':
                # Vérifier si c'est une colonne de listes
                sample = df[col].dropna().head(1)
                if len(sample) > 0 and isinstance(sample.iloc[0], (list, dict)):
                    df[col] = df[col].apply(lambda x: json.dumps(x) if x is not None else None)

        # S'assurer que les timestamps sont au bon format
        timestamp_cols = [col for col in df.columns if 'timestamp' in col.lower() or col.endswith('_at')]
        for col in timestamp_cols:
            if col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
                except:
                    pass

        # Remplacer les valeurs None/NaN dans les colonnes string
        string_cols = df.select_dtypes(include=['object']).columns
        for col in string_cols:
            df[col] = df[col].fillna('')

        return df


class AthenaTableManager:
    """
    Gestionnaire pour créer automatiquement les tables Athena
    pour les données scrappées.
    """

    def __init__(self, database: str = 'crypto_data', region_name: str = 'eu-west-3'):
        self.database = database
        self.region_name = region_name
        self.athena = boto3.client('athena', region_name=region_name)

    def create_news_table(self):
        """Créer la table pour les news articles."""
        query = f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.news_articles (
          _id STRING,
          title STRING,
          body STRING,
          url STRING,
          author STRING,
          published_at TIMESTAMP,
          scraped_at TIMESTAMP,
          language STRING,
          summary STRING,
          categories ARRAY<STRING>,
          tags ARRAY<STRING>,
          images ARRAY<STRING>,
          links ARRAY<STRING>,
          source_tier STRING,
          event_types ARRAY<STRING>,
          crypto_entities ARRAY<STRING>,
          institutional_entities ARRAY<STRING>,
          geographic_scope STRING,
          credibility_score DOUBLE,
          _scraped_at TIMESTAMP,
          _data_type STRING
        )
        PARTITIONED BY (
          source STRING,
          date STRING
        )
        STORED AS PARQUET
        LOCATION 's3://qbia/bourse/raw/news/'
        TBLPROPERTIES ('parquet.compress'='SNAPPY')
        """
        return query

    def create_social_table(self):
        """Créer la table pour les social media posts."""
        query = f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.social_posts (
          _id STRING,
          text STRING,
          url STRING,
          platform STRING,
          author STRING,
          author_followers INT,
          timestamp TIMESTAMP,
          likes INT,
          retweets INT,
          replies INT,
          sentiment_score DOUBLE,
          language STRING,
          hashtags ARRAY<STRING>,
          mentions ARRAY<STRING>,
          _scraped_at TIMESTAMP,
          _data_type STRING
        )
        PARTITIONED BY (
          source STRING,
          date STRING
        )
        STORED AS PARQUET
        LOCATION 's3://qbia/bourse/raw/social/'
        TBLPROPERTIES ('parquet.compress'='SNAPPY')
        """
        return query

    def create_forums_table(self):
        """Créer la table pour les forum posts."""
        query = f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.forum_posts (
          _id STRING,
          forum_name STRING,
          thread_title STRING,
          post_content STRING,
          url STRING,
          author STRING,
          author_rank STRING,
          timestamp TIMESTAMP,
          views INT,
          replies INT,
          _scraped_at TIMESTAMP,
          _data_type STRING
        )
        PARTITIONED BY (
          source STRING,
          date STRING
        )
        STORED AS PARQUET
        LOCATION 's3://qbia/bourse/raw/forums/'
        TBLPROPERTIES ('parquet.compress'='SNAPPY')
        """
        return query

    def create_web_table(self):
        """Créer la table pour le web content générique."""
        query = f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.web_content (
          _id STRING,
          url STRING,
          title STRING,
          content STRING,
          timestamp TIMESTAMP,
          _scraped_at TIMESTAMP,
          _data_type STRING
        )
        PARTITIONED BY (
          source STRING,
          date STRING
        )
        STORED AS PARQUET
        LOCATION 's3://qbia/bourse/raw/web/'
        TBLPROPERTIES ('parquet.compress'='SNAPPY')
        """
        return query
