"""
Whale Alert API spider - récupère les grandes transactions Bitcoin depuis 2019
API: https://docs.whale-alert.io/
"""

import scrapy
import json
from datetime import datetime, timedelta
from items import TransactionAlertItem
import time
import os


class WhaleAlertAPISpider(scrapy.Spider):
    name = 'whale_alert_api'
    allowed_domains = ['api.whale-alert.io']

    # Configuration
    custom_settings = {
        'DOWNLOAD_DELAY': 1,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'ROBOTSTXT_OBEY': False,  # API doesn't need robots.txt
        'ITEM_PIPELINES': {
            'pipelines.validation.ValidationPipeline': 100,
            'pipelines.whale_mongodb_pipeline.WhaleMongoDBPipeline': 400,
        }
    }

    def __init__(self, api_key=None, start_date='2019-01-01', end_date=None,
                 min_value=500000, currency='btc', *args, **kwargs):
        super(WhaleAlertAPISpider, self).__init__(*args, **kwargs)

        # API Key (à passer en paramètre ou via variable d'environnement)
        self.api_key = api_key or os.getenv('WHALE_ALERT_API_KEY')
        if not self.api_key:
            self.logger.warning("⚠️ No API key provided! Get one at https://whale-alert.io/")
            self.logger.warning("Usage: scrapy crawl whale_alert_api -a api_key=YOUR_KEY")

        # Paramètres de filtrage
        self.currency = currency.lower()  # btc par défaut
        self.min_value = int(min_value)  # Valeur minimale en USD

        # Dates
        self.start_date = datetime.strptime(start_date, '%Y-%m-%d')
        self.end_date = datetime.strptime(end_date, '%Y-%m-%d') if end_date else datetime.utcnow()

        # Stats
        self.total_transactions = 0
        self.total_periods = 0

        self.logger.info(f"🐋 Whale Alert API Spider initialized")
        self.logger.info(f"📅 Period: {self.start_date.date()} to {self.end_date.date()}")
        self.logger.info(f"💰 Currency: {self.currency.upper()}")
        self.logger.info(f"💵 Min value: ${self.min_value:,}")

    def start_requests(self):
        """Génère les requêtes pour chaque période de temps"""
        if not self.api_key:
            self.logger.error("❌ Cannot start without API key!")
            return

        # L'API Whale Alert permet de récupérer max 100 transactions par requête
        # On divise en périodes de 24h pour éviter les limites
        current_date = self.start_date

        while current_date < self.end_date:
            next_date = min(current_date + timedelta(days=1), self.end_date)

            start_timestamp = int(current_date.timestamp())
            end_timestamp = int(next_date.timestamp())

            # Construction de l'URL API
            url = self._build_api_url(start_timestamp, end_timestamp)

            self.total_periods += 1

            yield scrapy.Request(
                url=url,
                callback=self.parse_transactions,
                meta={
                    'start_date': current_date,
                    'end_date': next_date,
                    'start_timestamp': start_timestamp,
                    'end_timestamp': end_timestamp
                },
                dont_filter=True
            )

            current_date = next_date

        self.logger.info(f"📊 Generated {self.total_periods} time periods to fetch")

    def _build_api_url(self, start_timestamp, end_timestamp):
        """Construit l'URL de l'API avec les paramètres"""
        base_url = "https://api.whale-alert.io/v1/transactions"

        params = [
            f"api_key={self.api_key}",
            f"start={start_timestamp}",
            f"end={end_timestamp}",
            f"min_value={self.min_value}",
            f"currency={self.currency}",
            "limit=100"  # Max par requête
        ]

        return f"{base_url}?{'&'.join(params)}"

    def parse_transactions(self, response):
        """Parse la réponse de l'API"""
        start_date = response.meta['start_date']
        end_date = response.meta['end_date']

        try:
            data = json.loads(response.text)

            if 'result' not in data:
                self.logger.warning(f"⚠️ No 'result' in response for {start_date.date()}")
                if 'message' in data:
                    self.logger.warning(f"API Message: {data['message']}")
                return

            transactions = data.get('transactions', [])
            count = data.get('count', 0)

            self.logger.info(f"📦 Period {start_date.date()}: {count} transactions")

            for tx in transactions:
                item = self._parse_transaction(tx)
                if item:
                    self.total_transactions += 1
                    yield item

            # Si on a 100 transactions (limite), il peut y en avoir plus
            # On doit faire une nouvelle requête avec un offset
            if count >= 100:
                self.logger.info(f"🔄 More transactions available for {start_date.date()}, fetching next batch...")
                # Note: L'API Whale Alert ne supporte pas l'offset dans la version gratuite
                # Pour une version complète, il faudrait un abonnement premium

        except json.JSONDecodeError as e:
            self.logger.error(f"❌ JSON decode error: {e}")
            self.logger.error(f"Response text: {response.text[:500]}")
        except Exception as e:
            self.logger.error(f"❌ Error parsing transactions: {e}")

    def _parse_transaction(self, tx):
        """Parse une transaction individuelle"""
        try:
            item = TransactionAlertItem()

            # Données de base
            item['tx_hash'] = tx.get('hash', '')
            item['blockchain'] = tx.get('blockchain', self.currency.upper())
            item['symbol'] = tx.get('symbol', self.currency.upper())

            # Montants
            item['amount'] = float(tx.get('amount', 0))
            item['amount_usd'] = float(tx.get('amount_usd', 0))

            # Adresses
            from_data = tx.get('from', {})
            to_data = tx.get('to', {})

            item['from_address'] = from_data.get('address', 'unknown')
            item['to_address'] = to_data.get('address', 'unknown')
            item['from_owner'] = from_data.get('owner', None)
            item['to_owner'] = to_data.get('owner', None)

            # Type de transaction
            item['transaction_type'] = self._determine_transaction_type(from_data, to_data)

            # Métadonnées
            item['timestamp'] = datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat()
            item['scraped_at'] = datetime.utcnow().isoformat()
            item['source'] = 'Whale Alert API'
            item['url'] = f"https://whale-alert.io/transaction/{tx.get('blockchain', 'bitcoin')}/{tx.get('hash', '')}"

            return item

        except Exception as e:
            self.logger.error(f"Error parsing transaction: {e}")
            self.logger.error(f"Transaction data: {tx}")
            return None

    def _determine_transaction_type(self, from_data, to_data):
        """Détermine le type de transaction en fonction des adresses"""
        from_type = from_data.get('owner_type', 'unknown')
        to_type = to_data.get('owner_type', 'unknown')

        # Mapping des types
        type_map = {
            ('exchange', 'wallet'): 'exchange_to_wallet',
            ('wallet', 'exchange'): 'wallet_to_exchange',
            ('exchange', 'exchange'): 'exchange_to_exchange',
            ('wallet', 'wallet'): 'wallet_to_wallet',
            ('exchange', 'unknown'): 'exchange_outflow',
            ('unknown', 'exchange'): 'exchange_inflow',
        }

        return type_map.get((from_type, to_type), 'unknown')

    def closed(self, reason):
        """Called when spider closes"""
        self.logger.info(f"🏁 Spider closed: {reason}")
        self.logger.info(f"📊 Total transactions collected: {self.total_transactions}")
        self.logger.info(f"📅 Total periods processed: {self.total_periods}")
