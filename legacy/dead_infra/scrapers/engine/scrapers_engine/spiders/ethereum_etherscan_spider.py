"""
Ethereum Whale Spider - Etherscan API (gratuit: 5 req/sec, 100k req/jour)
Récupère les grandes transactions Ethereum depuis 2019
"""

import scrapy
import json
from datetime import datetime
import os
from items import TransactionAlertItem


class EthereumEtherscanSpider(scrapy.Spider):
    name = 'ethereum_etherscan'
    allowed_domains = ['api.etherscan.io']

    custom_settings = {
        'DOWNLOAD_DELAY': 0.2,  # 5 req/sec max
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'ROBOTSTXT_OBEY': False,
        'ITEM_PIPELINES': {
            'pipelines.blockchain_whale_mongodb_pipeline.BlockchainWhaleMongoDBPipeline': 400,
        }
    }

    def __init__(self, api_key=None, start_block=None, end_block=None, limit=None, *args, **kwargs):
        super(EthereumEtherscanSpider, self).__init__(*args, **kwargs)

        self.api_key = api_key or os.getenv('ETHERSCAN_API_KEY')
        if not self.api_key:
            self.logger.warning("⚠️ No Etherscan API key! Get one free at https://etherscan.io/apis")

        self.base_url = 'https://api.etherscan.io/api'
        self.start_block = int(start_block) if start_block else None
        self.end_block = int(end_block) if end_block else None
        self.limit = int(limit) if limit else None

        self.blocks_processed = 0
        self.transactions_found = 0

        self.logger.info(f"🐋 Ethereum Etherscan Spider initialized")

    def start_requests(self):
        """Récupère le dernier bloc puis scan les blocs"""
        if not self.api_key:
            self.logger.error("❌ API key required! Use: -a api_key=YOUR_KEY")
            return

        # Obtenir le dernier bloc
        url = f"{self.base_url}?module=proxy&action=eth_blockNumber&apikey={self.api_key}"
        yield scrapy.Request(url, callback=self.parse_latest_block, dont_filter=True)

    def parse_latest_block(self, response):
        """Parse le dernier bloc et génère les requêtes"""
        try:
            data = json.loads(response.text)
            current_height = int(data['result'], 16)
            self.logger.info(f"📊 Current Ethereum block: {current_height:,}")

            start = self.start_block if self.start_block else current_height - 100
            end = self.end_block if self.end_block else current_height

            if self.limit:
                end = min(start + self.limit, end)

            self.logger.info(f"🔍 Scanning blocks {start:,} to {end:,}")

            # Scan par blocs
            for height in range(start, end + 1):
                block_hex = hex(height)
                url = f"{self.base_url}?module=proxy&action=eth_getBlockByNumber&tag={block_hex}&boolean=true&apikey={self.api_key}"
                yield scrapy.Request(url, callback=self.parse_block, meta={'height': height}, dont_filter=True)

        except Exception as e:
            self.logger.error(f"❌ Error: {e}")

    def parse_block(self, response):
        """Parse un bloc Ethereum"""
        height = response.meta['height']

        try:
            data = json.loads(response.text)
            block = data.get('result', {})

            if not block or block is None:
                return

            self.blocks_processed += 1
            transactions = block.get('transactions', [])

            for tx in transactions[:50]:  # Limiter aux 50 premières
                value_wei = int(tx.get('value', '0x0'), 16)
                value_eth = value_wei / 1e18

                if value_eth > 0:  # Filtrage sera fait par le pipeline (>$100k)
                    yield self._create_item(tx, height, block)

            if self.blocks_processed % 100 == 0:
                self.logger.info(f"📊 Progress: {self.blocks_processed} blocks, {self.transactions_found} transactions")

        except Exception as e:
            self.logger.error(f"❌ Error block {height}: {e}")

    def _create_item(self, tx, block_height, block):
        """Crée un TransactionAlertItem depuis une transaction Ethereum"""
        self.transactions_found += 1

        item = TransactionAlertItem()

        # Données de base
        item['tx_hash'] = tx.get('hash')
        item['blockchain'] = 'ethereum'
        item['symbol'] = 'ETH'
        item['amount'] = int(tx.get('value', '0x0'), 16) / 1e18
        item['block_number'] = block_height

        # Adresses
        item['from_address'] = tx.get('from', '').lower()
        item['to_address'] = tx.get('to', '').lower() if tx.get('to') else 'contract_creation'

        # Gas et fees
        gas_price = int(tx.get('gasPrice', '0x0'), 16)
        gas_used = int(tx.get('gas', '0x0'), 16)
        item['gas_price'] = gas_price / 1e9  # Wei to Gwei
        item['gas_used'] = gas_used
        item['fees'] = (gas_price * gas_used) / 1e18  # Fees en ETH

        # Type de transaction
        if tx.get('input', '0x') != '0x':
            item['tx_type'] = 'contract_call'
        else:
            item['tx_type'] = 'transfer'

        # Timestamp
        block_timestamp = int(block.get('timestamp', '0x0'), 16)
        item['timestamp'] = datetime.fromtimestamp(block_timestamp).isoformat()

        # Métadonnées
        item['scraped_at'] = datetime.utcnow().isoformat()
        item['source'] = 'Etherscan API'
        item['url'] = f"https://etherscan.io/tx/{tx.get('hash')}"

        return item

    def closed(self, reason):
        self.logger.info(f"🏁 Closed: {reason}")
        self.logger.info(f"📊 Blocks: {self.blocks_processed}, Transactions: {self.transactions_found}")
