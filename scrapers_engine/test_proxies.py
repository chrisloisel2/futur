#!/usr/bin/env python3
"""
Script de test pour vérifier le système de rotation de proxies
"""
import sys
import logging
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from scrapy import Spider, Request

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

logger = logging.getLogger(__name__)


class ProxyTestSpider(Spider):
    """Spider de test pour vérifier la rotation des proxies"""
    name = 'proxy_test'
    custom_settings = {
        'CONCURRENT_REQUESTS': 10,
        'DOWNLOAD_DELAY': 0.5,
        'LOG_LEVEL': 'INFO',
    }

    # Sites de test (retournent l'IP du client)
    start_urls = [
        'https://api.ipify.org?format=json',  # Retourne l'IP en JSON
        'https://httpbin.org/ip',  # Retourne l'IP
        'https://ifconfig.me/ip',  # Retourne l'IP
    ] * 10  # Faire 30 requêtes au total

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ips_seen = set()
        self.request_count = 0

    def parse(self, response):
        """Parse la réponse pour extraire l'IP"""
        self.request_count += 1

        # Essayer de parser en JSON
        try:
            data = response.json()
            ip = data.get('origin') or data.get('ip')
        except:
            ip = response.text.strip()

        if ip:
            self.ips_seen.add(ip)
            logger.info(f'🌐 Requête #{self.request_count}: IP détectée = {ip}')
            logger.info(f'📊 Total IPs uniques: {len(self.ips_seen)}')

        # Log du proxy utilisé
        proxy = response.request.meta.get('proxy')
        if proxy:
            logger.info(f'🔄 Proxy utilisé: {proxy}')

    def closed(self, reason):
        """Appelé quand le spider se ferme"""
        logger.info('=' * 80)
        logger.info('📊 RÉSULTATS DU TEST DE PROXIES')
        logger.info('=' * 80)
        logger.info(f'Total requêtes: {self.request_count}')
        logger.info(f'IPs uniques détectées: {len(self.ips_seen)}')
        logger.info(f'Taux de rotation: {len(self.ips_seen) / self.request_count * 100:.1f}%')
        logger.info('=' * 80)

        if len(self.ips_seen) > 1:
            logger.info('✅ SUCCÈS: Les proxies changent l\'IP!')
        else:
            logger.warning('⚠️ ATTENTION: Une seule IP détectée')

        logger.info(f'IPs vues: {sorted(self.ips_seen)}')


def main():
    """Lance le test"""
    logger.info('🚀 Démarrage du test de rotation de proxies...')
    logger.info('📋 Ce test va:')
    logger.info('   1. Charger des proxies gratuits depuis plusieurs sources')
    logger.info('   2. Faire 30 requêtes avec rotation automatique')
    logger.info('   3. Afficher les statistiques de rotation d\'IP')
    logger.info('')

    settings = get_project_settings()

    # Forcer l'activation des proxies
    settings.set('PROXY_ENABLED', True)
    settings.set('MAX_PROXIES', 200)
    settings.set('PROXY_REFRESH_INTERVAL', 300)

    process = CrawlerProcess(settings)
    process.crawl(ProxyTestSpider)
    process.start()


if __name__ == '__main__':
    main()
