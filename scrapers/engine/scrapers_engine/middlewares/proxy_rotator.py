"""
Middleware de rotation de proxies gratuits avec changement automatique d'IP
"""
import random
import logging
import requests
from typing import List, Dict, Optional
import time
from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import Request

logger = logging.getLogger(__name__)


class FreeProxyRotatorMiddleware:
    """
    Middleware qui récupère et rotate des proxies gratuits automatiquement.
    Change l'IP aussi souvent que possible pour éviter les bans.
    """

    # Sources de proxies gratuits
    PROXY_SOURCES = [
        'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
        'https://www.proxy-list.download/api/v1/get?type=http',
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
        'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
        'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
        'https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt',
    ]

    def __init__(self, proxy_enabled=True, max_proxies=200, refresh_interval=300):
        self.proxy_enabled = proxy_enabled
        self.max_proxies = max_proxies
        self.refresh_interval = refresh_interval  # 5 minutes
        self.proxies: List[str] = []
        self.working_proxies: List[str] = []
        self.failed_proxies: set = set()
        self.proxy_stats: Dict[str, dict] = {}
        self.last_refresh = 0
        self.request_count = 0
        self.proxy_rotation_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        # Récupérer les settings
        proxy_enabled = crawler.settings.getbool('PROXY_ENABLED', True)
        max_proxies = crawler.settings.getint('MAX_PROXIES', 200)
        refresh_interval = crawler.settings.getint('PROXY_REFRESH_INTERVAL', 300)

        if not proxy_enabled:
            raise NotConfigured('Proxy rotation is disabled')

        middleware = cls(
            proxy_enabled=proxy_enabled,
            max_proxies=max_proxies,
            refresh_interval=refresh_interval
        )

        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)

        return middleware

    def spider_opened(self, spider):
        """Appelé quand le spider démarre"""
        logger.info('🔄 Initialisation du système de rotation de proxies gratuits')
        self.fetch_proxies()
        logger.info(f'✅ {len(self.proxies)} proxies chargés')

    def spider_closed(self, spider):
        """Appelé quand le spider se ferme"""
        success_rate = (len(self.working_proxies) / len(self.proxies) * 100) if self.proxies else 0
        logger.info(f'📊 Statistiques finales:')
        logger.info(f'   - Total requêtes: {self.request_count}')
        logger.info(f'   - Rotations proxy: {self.proxy_rotation_count}')
        logger.info(f'   - Proxies fonctionnels: {len(self.working_proxies)}/{len(self.proxies)}')
        logger.info(f'   - Taux de succès: {success_rate:.1f}%')

    def fetch_proxies(self) -> None:
        """Récupère les proxies depuis plusieurs sources gratuites"""
        logger.info('🔍 Récupération des proxies gratuits...')
        all_proxies = set()

        for source in self.PROXY_SOURCES:
            try:
                logger.info(f'   Fetching from: {source}')
                response = requests.get(source, timeout=10)
                if response.status_code == 200:
                    # Parser les proxies (format: IP:PORT)
                    proxies = [
                        line.strip()
                        for line in response.text.split('\n')
                        if line.strip() and ':' in line and not line.startswith('#')
                    ]
                    all_proxies.update(proxies)
                    logger.info(f'   ✅ {len(proxies)} proxies trouvés')
            except Exception as e:
                logger.warning(f'   ⚠️ Erreur source {source}: {e}')
                continue

        # Limiter le nombre de proxies
        self.proxies = list(all_proxies)[:self.max_proxies]
        random.shuffle(self.proxies)  # Mélanger pour diversifier

        logger.info(f'📦 Total proxies disponibles: {len(self.proxies)}')
        self.last_refresh = time.time()

    def get_random_proxy(self) -> Optional[str]:
        """Récupère un proxy aléatoire parmi les fonctionnels ou tous"""
        # Rafraîchir les proxies si nécessaire
        if time.time() - self.last_refresh > self.refresh_interval:
            logger.info('🔄 Rafraîchissement des proxies (intervalle dépassé)')
            self.fetch_proxies()

        # Priorité aux proxies fonctionnels
        if self.working_proxies:
            available = [p for p in self.working_proxies if p not in self.failed_proxies]
            if available:
                return random.choice(available)

        # Sinon, essayer un proxy non testé
        available = [p for p in self.proxies if p not in self.failed_proxies]
        if available:
            return random.choice(available)

        # Si tous ont échoué, réinitialiser et rafraîchir
        logger.warning('⚠️ Tous les proxies ont échoué, rafraîchissement...')
        self.failed_proxies.clear()
        self.fetch_proxies()

        return random.choice(self.proxies) if self.proxies else None

    def process_request(self, request: Request, spider):
        """Traite chaque requête en assignant un proxy"""
        self.request_count += 1

        # Ne pas ajouter de proxy si déjà présent (retry)
        if 'proxy' in request.meta:
            return None

        proxy = self.get_random_proxy()
        if proxy:
            # Format: http://IP:PORT
            if not proxy.startswith('http'):
                proxy = f'http://{proxy}'

            request.meta['proxy'] = proxy
            request.meta['proxy_rotation'] = True
            self.proxy_rotation_count += 1

            # Initialiser les stats pour ce proxy
            if proxy not in self.proxy_stats:
                self.proxy_stats[proxy] = {
                    'requests': 0,
                    'successes': 0,
                    'failures': 0
                }

            self.proxy_stats[proxy]['requests'] += 1

            # Log toutes les 10 requêtes
            if self.request_count % 10 == 0:
                logger.info(f'🔄 Proxy #{self.request_count}: {proxy}')

        else:
            logger.warning('⚠️ Aucun proxy disponible, requête sans proxy')

        return None

    def process_response(self, request: Request, response, spider):
        """Traite la réponse pour suivre les succès"""
        proxy = request.meta.get('proxy')

        if proxy and response.status == 200:
            # Marquer comme fonctionnel
            if proxy not in self.working_proxies:
                self.working_proxies.append(proxy)
                logger.info(f'✅ Nouveau proxy fonctionnel: {proxy}')

            # Mettre à jour les stats
            if proxy in self.proxy_stats:
                self.proxy_stats[proxy]['successes'] += 1

        return response

    def process_exception(self, request: Request, exception, spider):
        """Traite les exceptions pour marquer les proxies défaillants"""
        proxy = request.meta.get('proxy')

        if proxy:
            # Marquer comme défaillant
            self.failed_proxies.add(proxy)

            # Retirer des proxies fonctionnels
            if proxy in self.working_proxies:
                self.working_proxies.remove(proxy)

            # Mettre à jour les stats
            if proxy in self.proxy_stats:
                self.proxy_stats[proxy]['failures'] += 1

            logger.warning(f'❌ Proxy défaillant: {proxy} - {exception}')

            # Réessayer avec un nouveau proxy
            new_proxy = self.get_random_proxy()
            if new_proxy and new_proxy != proxy:
                request.meta['proxy'] = new_proxy
                logger.info(f'🔄 Retry avec nouveau proxy: {new_proxy}')
                return request

        return None


class UserAgentRotatorMiddleware:
    """
    Middleware qui change le User-Agent à chaque requête
    pour éviter la détection de bot
    """

    USER_AGENTS = [
        # Chrome Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',

        # Chrome Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',

        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',

        # Firefox Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',

        # Safari Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',

        # Edge Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',

        # Chrome Linux
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',

        # Mobile
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    ]

    def process_request(self, request: Request, spider):
        """Assigne un User-Agent aléatoire à chaque requête"""
        request.headers['User-Agent'] = random.choice(self.USER_AGENTS)
        return None


class HeadersRotatorMiddleware:
    """
    Middleware qui change les headers à chaque requête
    pour simuler différents navigateurs
    """

    ACCEPT_LANGUAGES = [
        'en-US,en;q=0.9',
        'en-GB,en;q=0.9',
        'fr-FR,fr;q=0.9,en;q=0.8',
        'de-DE,de;q=0.9,en;q=0.8',
        'es-ES,es;q=0.9,en;q=0.8',
        'it-IT,it;q=0.9,en;q=0.8',
        'pt-BR,pt;q=0.9,en;q=0.8',
        'ja-JP,ja;q=0.9,en;q=0.8',
        'zh-CN,zh;q=0.9,en;q=0.8',
    ]

    ACCEPT_ENCODINGS = [
        'gzip, deflate, br',
        'gzip, deflate',
    ]

    def process_request(self, request: Request, spider):
        """Assigne des headers aléatoires"""
        request.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        request.headers['Accept-Language'] = random.choice(self.ACCEPT_LANGUAGES)
        request.headers['Accept-Encoding'] = random.choice(self.ACCEPT_ENCODINGS)
        request.headers['DNT'] = '1'
        request.headers['Connection'] = 'keep-alive'
        request.headers['Upgrade-Insecure-Requests'] = '1'

        # Ajouter Referer aléatoire (simuler une navigation normale)
        referers = [
            'https://www.google.com/',
            'https://www.bing.com/',
            'https://www.duckduckgo.com/',
            'https://www.reddit.com/',
            'https://www.twitter.com/',
        ]
        if random.random() > 0.5:  # 50% de chances d'avoir un referer
            request.headers['Referer'] = random.choice(referers)

        return None
