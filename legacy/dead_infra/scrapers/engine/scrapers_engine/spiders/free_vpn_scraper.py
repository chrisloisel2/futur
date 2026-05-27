"""
Free VPN/Proxy scraper
Scrape free proxies from multiple sources and store in MongoDB
"""

import scrapy
from datetime import datetime
from items import VPNProxyItem
import re
import requests


class FreeVPNSpider(scrapy.Spider):
    name = 'free_vpn_scraper'
    allowed_domains = []  # Allow all domains for proxy sources

    # Multiple free proxy sources
    start_urls = []

    custom_settings = {
        'DOWNLOAD_DELAY': 1,
        'CONCURRENT_REQUESTS': 8,
        'ROBOTSTXT_OBEY': False,  # Many proxy sites block robots

        # Use MongoDB pipeline WITH TESTING for VPN items
        'ITEM_PIPELINES': {
            'pipelines.vpn_mongodb_pipeline_with_test.VPNMongoDBPipelineWithTesting': 100,
        },

        # VPN Testing Configuration
        'VPN_TEST_BEFORE_STORE': True,  # Enable testing
        'VPN_TEST_TIMEOUT': 10,  # 10 seconds per test
        'VPN_TEST_WORKERS': 50,  # 50 concurrent test workers
        'VPN_TEST_BATCH_SIZE': 100,  # Test 100 VPNs at a time

        # Disable proxy rotation for this spider (avoid recursion)
        'PROXY_ENABLED': False,
    }

    # API sources (direct proxy lists)
    API_SOURCES = [
        'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
        'https://www.proxy-list.download/api/v1/get?type=http',
        'https://www.proxy-list.download/api/v1/get?type=https',
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
        'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
        'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt',
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/http.txt',
        'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
        'https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt',
        'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt',
    ]

    def start_requests(self):
        """Start by fetching API-based proxy lists"""
        self.logger.info(f"🔍 Starting VPN scraper - fetching from {len(self.API_SOURCES)} sources")

        for url in self.API_SOURCES:
            yield scrapy.Request(
                url=url,
                callback=self.parse_api_list,
                meta={'source': url},
                dont_filter=True,
                errback=self.handle_error
            )

    def parse_api_list(self, response):
        """Parse text-based proxy lists (IP:PORT format)"""
        source = response.meta.get('source', response.url)
        self.logger.info(f"📥 Parsing proxy list from: {source}")

        # Split by lines and parse each proxy
        lines = response.text.strip().split('\n')
        proxy_count = 0

        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Parse IP:PORT format
            proxy_item = self._parse_proxy_line(line, source)
            if proxy_item:
                proxy_count += 1
                yield proxy_item

        self.logger.info(f"✅ Found {proxy_count} proxies from {source}")

    def _parse_proxy_line(self, line, source):
        """Parse a single proxy line (IP:PORT or IP:PORT:PROTOCOL format)"""
        try:
            # Remove extra whitespace and split
            parts = line.strip().split(':')

            if len(parts) < 2:
                return None

            ip = parts[0].strip()
            port = parts[1].strip()

            # Validate IP format
            if not self._is_valid_ip(ip):
                return None

            # Validate port
            try:
                port_int = int(port)
                if port_int < 1 or port_int > 65535:
                    return None
            except ValueError:
                return None

            # Determine protocol
            protocol = 'http'
            if len(parts) >= 3:
                proto_hint = parts[2].lower()
                if 'https' in proto_hint or 'ssl' in proto_hint:
                    protocol = 'https'
                elif 'socks5' in proto_hint:
                    protocol = 'socks5'
                elif 'socks4' in proto_hint:
                    protocol = 'socks4'
            else:
                # Guess from source URL
                if 'https' in source.lower():
                    protocol = 'https'
                elif 'socks5' in source.lower():
                    protocol = 'socks5'

            # Create VPN item
            item = VPNProxyItem()
            item['ip'] = ip
            item['port'] = port
            item['protocol'] = protocol
            item['proxy_url'] = f"{protocol}://{ip}:{port}"
            item['source'] = self._clean_source_name(source)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Initialize tracking fields
            item['last_checked'] = None
            item['last_success'] = None
            item['success_count'] = 0
            item['fail_count'] = 0
            item['response_time'] = None

            # Default status
            item['is_active'] = True  # Assume active until tested
            item['is_anonymous'] = None  # Unknown until tested

            # Optional fields
            item['country'] = None
            item['country_code'] = None
            item['anonymity_level'] = None
            item['speed'] = None
            item['uptime'] = None

            return item

        except Exception as e:
            self.logger.debug(f"Error parsing proxy line '{line}': {e}")
            return None

    def _is_valid_ip(self, ip):
        """Validate IP address format"""
        pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if not re.match(pattern, ip):
            return False

        # Check each octet is 0-255
        parts = ip.split('.')
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255:
                    return False
            except ValueError:
                return False

        return True

    def _clean_source_name(self, url):
        """Extract clean source name from URL"""
        if 'proxyscrape' in url:
            return 'ProxyScrape API'
        elif 'proxy-list.download' in url:
            return 'Proxy-List Download'
        elif 'TheSpeedX' in url:
            return 'TheSpeedX GitHub'
        elif 'ShiftyTR' in url:
            return 'ShiftyTR GitHub'
        elif 'monosans' in url:
            return 'Monosans GitHub'
        elif 'clarketm' in url:
            return 'Clarketm GitHub'
        elif 'sunny9577' in url:
            return 'Sunny9577 GitHub'
        elif 'hookzof' in url:
            return 'Hookzof GitHub'
        else:
            # Extract domain
            match = re.search(r'https?://(?:www\.)?([^/]+)', url)
            return match.group(1) if match else 'Unknown'

    def handle_error(self, failure):
        """Handle request errors"""
        self.logger.error(f"❌ Request failed: {failure.request.url}")
        self.logger.error(f"   Error: {failure.value}")


class FreeVPNScraperEnhanced(FreeVPNSpider):
    """
    Enhanced version that also scrapes from web pages (not just APIs)
    """
    name = 'free_vpn_scraper_enhanced'

    # Add web-based proxy listing sites
    WEB_SOURCES = [
        'https://free-proxy-list.net/',
        'https://www.sslproxies.org/',
        'https://www.us-proxy.org/',
        'https://www.socks-proxy.net/',
    ]

    def start_requests(self):
        """Start with both API and web sources"""
        # First, get API sources (from parent class)
        for request in super().start_requests():
            yield request

        # Then, get web sources
        for url in self.WEB_SOURCES:
            yield scrapy.Request(
                url=url,
                callback=self.parse_web_page,
                meta={'source': url},
                dont_filter=True,
                errback=self.handle_error
            )

    def parse_web_page(self, response):
        """Parse HTML-based proxy listing pages"""
        source = response.meta.get('source', response.url)
        self.logger.info(f"🌐 Parsing web page: {source}")

        proxy_count = 0

        # Try to find proxy table (common pattern)
        # Most free proxy sites use tables with IP, Port, Country, etc.

        # Method 1: Look for <table> with tbody
        table = response.css('table.table tbody, table#proxylisttable tbody')

        if table:
            rows = table.css('tr')
            for row in rows:
                cells = row.css('td::text').getall()

                if len(cells) >= 2:
                    ip = cells[0].strip()
                    port = cells[1].strip()

                    # Optional: country, anonymity, https support
                    country = cells[2].strip() if len(cells) > 2 else None
                    anonymity = cells[4].strip() if len(cells) > 4 else None
                    https = cells[6].strip() if len(cells) > 6 else 'no'

                    # Validate
                    if self._is_valid_ip(ip):
                        protocol = 'https' if https.lower() == 'yes' else 'http'

                        item = VPNProxyItem()
                        item['ip'] = ip
                        item['port'] = port
                        item['protocol'] = protocol
                        item['proxy_url'] = f"{protocol}://{ip}:{port}"
                        item['source'] = self._clean_source_name(source)
                        item['scraped_at'] = datetime.utcnow().isoformat()
                        item['country'] = country
                        item['anonymity_level'] = anonymity.lower() if anonymity else None

                        # Initialize tracking
                        item['last_checked'] = None
                        item['last_success'] = None
                        item['success_count'] = 0
                        item['fail_count'] = 0
                        item['response_time'] = None
                        item['is_active'] = True
                        item['is_anonymous'] = 'anonymous' in anonymity.lower() if anonymity else None

                        # Optional
                        item['country_code'] = None
                        item['speed'] = None
                        item['uptime'] = None

                        proxy_count += 1
                        yield item

            self.logger.info(f"✅ Found {proxy_count} proxies from {source}")

        else:
            # Method 2: Look for IP:PORT patterns in text
            text = response.text
            pattern = r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{2,5})'
            matches = re.findall(pattern, text)

            for ip, port in matches:
                if self._is_valid_ip(ip):
                    item = VPNProxyItem()
                    item['ip'] = ip
                    item['port'] = port
                    item['protocol'] = 'http'
                    item['proxy_url'] = f"http://{ip}:{port}"
                    item['source'] = self._clean_source_name(source)
                    item['scraped_at'] = datetime.utcnow().isoformat()

                    # Initialize defaults
                    item['last_checked'] = None
                    item['last_success'] = None
                    item['success_count'] = 0
                    item['fail_count'] = 0
                    item['response_time'] = None
                    item['is_active'] = True
                    item['is_anonymous'] = None
                    item['country'] = None
                    item['country_code'] = None
                    item['anonymity_level'] = None
                    item['speed'] = None
                    item['uptime'] = None

                    proxy_count += 1
                    yield item

            self.logger.info(f"✅ Found {proxy_count} proxies from {source} (regex)")
