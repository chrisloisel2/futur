"""
Bitcoin Whale Spider - Mempool.space API (100% gratuit, illimité)
Récupère les grandes transactions Bitcoin depuis 2019
"""

import scrapy
import json
from datetime import datetime
import time
from items import TransactionAlertItem


class BitcoinMempoolSpider(scrapy.Spider):
    name = 'bitcoin_mempool'
    allowed_domains = ['mempool.space']

    custom_settings = {
        'DOWNLOAD_DELAY': 0.5,  # Respectueux même si API illimitée
        'CONCURRENT_REQUESTS_PER_DOMAIN': 2,
        'ROBOTSTXT_OBEY': False,  # API doesn't need robots.txt
        'ITEM_PIPELINES': {
            'pipelines.blockchain_whale_mongodb_pipeline.BlockchainWhaleMongoDBPipeline': 400,
        }
    }

    def __init__(self, start_block=None, end_block=None, limit=None, *args, **kwargs):
        super(BitcoinMempoolSpider, self).__init__(*args, **kwargs)

        self.base_url = 'https://mempool.space/api'

        # Configuration
        self.start_block = int(start_block) if start_block else None
        self.end_block = int(end_block) if end_block else None
        self.limit = int(limit) if limit else None  # Limite de blocs à traiter (pour tests)

        # Stats
        self.blocks_processed = 0
        self.transactions_found = 0

        self.logger.info(f"🐋 Bitcoin Mempool Spider initialized")
        if self.start_block:
            self.logger.info(f"📅 Start block: {self.start_block}")
        if self.end_block:
            self.logger.info(f"📅 End block: {self.end_block}")
        if self.limit:
            self.logger.info(f"🔢 Limit: {self.limit} blocks")

    def start_requests(self):
        """Génère les requêtes pour récupérer les blocs Bitcoin"""

        # Obtenir la hauteur actuelle de la blockchain
        url = f"{self.base_url}/blocks/tip/height"

        yield scrapy.Request(
            url=url,
            callback=self.parse_tip_height,
            dont_filter=True
        )

    def parse_tip_height(self, response):
        """Parse la hauteur actuelle et génère les requêtes pour les blocs"""
        try:
            current_height = int(response.text.strip())
            self.logger.info(f"📊 Current Bitcoin block height: {current_height:,}")

            # Déterminer les blocs à scanner
            start = self.start_block if self.start_block else current_height - 100  # Par défaut: 100 derniers blocs
            end = self.end_block if self.end_block else current_height

            # Vérifier la limite
            if self.limit:
                end = min(start + self.limit, end)

            self.logger.info(f"🔍 Scanning blocks {start:,} to {end:,} ({end - start + 1} blocks)")

            # Générer requêtes pour chaque bloc
            for height in range(start, end + 1):
                yield scrapy.Request(
                    url=f"{self.base_url}/block-height/{height}",
                    callback=self.parse_block_hash,
                    meta={'height': height},
                    dont_filter=True
                )

        except Exception as e:
            self.logger.error(f"❌ Error parsing tip height: {e}")

    def parse_block_hash(self, response):
        """Parse le hash du bloc et requête les détails"""
        height = response.meta['height']

        try:
            block_hash = response.text.strip()

            # Requête pour les détails du bloc
            yield scrapy.Request(
                url=f"{self.base_url}/block/{block_hash}",
                callback=self.parse_block,
                meta={'height': height, 'block_hash': block_hash},
                dont_filter=True
            )

        except Exception as e:
            self.logger.error(f"❌ Error parsing block hash for height {height}: {e}")

    def parse_block(self, response):
        """Parse un bloc Bitcoin et extrait les transactions whale"""
        height = response.meta['height']
        block_hash = response.meta['block_hash']

        try:
            block_data = json.loads(response.text)

            self.blocks_processed += 1

            # Informations du bloc
            block_timestamp = block_data.get('timestamp')
            tx_count = block_data.get('tx_count', 0)

            self.logger.debug(f"📦 Block {height}: {tx_count} transactions")

            # Obtenir les transactions du bloc
            # Mempool.space renvoie un array de txids
            if 'tx' in block_data:
                txids = block_data['tx'][:25]  # Limiter aux 25 premières (souvent les plus grosses)

                for txid in txids:
                    yield scrapy.Request(
                        url=f"{self.base_url}/tx/{txid}",
                        callback=self.parse_transaction,
                        meta={
                            'block_height': height,
                            'block_hash': block_hash,
                            'block_timestamp': block_timestamp
                        },
                        dont_filter=True
                    )

            # Log progression
            if self.blocks_processed % 100 == 0:
                self.logger.info(f"📊 Progress: {self.blocks_processed} blocks processed, {self.transactions_found} whale transactions found")

        except Exception as e:
            self.logger.error(f"❌ Error parsing block {height}: {e}")

    def parse_transaction(self, response):
        """Parse une transaction Bitcoin et détermine si c'est une whale transaction"""
        block_height = response.meta['block_height']
        block_hash = response.meta['block_hash']
        block_timestamp = response.meta['block_timestamp']

        try:
            tx_data = json.loads(response.text)

            # Calculer le montant total en BTC
            total_output_btc = sum(vout.get('value', 0) for vout in tx_data.get('vout', [])) / 100000000  # Satoshis to BTC

            # Extraction des données
            txid = tx_data.get('txid')
            fee = tx_data.get('fee', 0) / 100000000  # Satoshis to BTC

            # Créer l'item
            item = TransactionAlertItem()

            # Données de base
            item['tx_hash'] = txid
            item['blockchain'] = 'bitcoin'
            item['symbol'] = 'BTC'
            item['amount'] = total_output_btc
            item['block_number'] = block_height

            # Timestamp
            if block_timestamp:
                item['timestamp'] = datetime.fromtimestamp(block_timestamp).isoformat()
            else:
                item['timestamp'] = datetime.utcnow().isoformat()

            # Adresses (première input et output)
            inputs = tx_data.get('vin', [])
            outputs = tx_data.get('vout', [])

            if inputs and 'prevout' in inputs[0]:
                item['from_address'] = inputs[0]['prevout'].get('scriptpubkey_address', 'unknown')
            else:
                item['from_address'] = 'coinbase'  # Transaction coinbase (mining reward)

            if outputs:
                item['to_address'] = outputs[0].get('scriptpubkey_address', 'unknown')
            else:
                item['to_address'] = 'unknown'

            # Fees
            item['fees'] = fee
            item['tx_type'] = 'transfer'

            # Métadonnées
            item['scraped_at'] = datetime.utcnow().isoformat()
            item['source'] = 'Mempool.space API'
            item['url'] = f"https://mempool.space/tx/{txid}"

            self.transactions_found += 1

            # Note: amount_usd sera calculé par le pipeline
            yield item

        except Exception as e:
            self.logger.error(f"❌ Error parsing transaction: {e}")

    def closed(self, reason):
        """Called when spider closes"""
        self.logger.info(f"🏁 Spider closed: {reason}")
        self.logger.info(f"📊 Blocks processed: {self.blocks_processed}")
        self.logger.info(f"🐋 Whale transactions found: {self.transactions_found}")
