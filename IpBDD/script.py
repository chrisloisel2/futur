#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import pymongo
import concurrent.futures
import time
import re
import sys
import logging
import json
import os
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from functools import partial

# ================= CONFIGURATION =================
MONGO_URI = "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "proxy_db"
COLLECTION_NAME = "proxies"

TEST_URL = "http://httpbin.org/ip"
TIMEOUT = 8
MAX_WORKERS_DOWNLOAD = 20   # Threads pour télécharger les sources
MAX_WORKERS_VALIDATION = 200 # Threads pour tester les proxies
BATCH_SIZE = 1000            # Insertion MongoDB par lots

# Fichiers de sortie
OUTPUT_FILE = "proxies_valides.txt"
LOG_FILE = f"proxy_harvester_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# ================= LOGGING AVANCÉ =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ProxyHarvester")

# ================= CONNEXION MONGODB =================
try:
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]
    collection.create_index("ip_port", unique=True)
    logger.info("✅ Connexion MongoDB réussie")
except Exception as e:
    logger.critical(f"❌ Échec connexion MongoDB : {e}")
    sys.exit(1)

# ================= SOURCES DE PROXIES (ÉTENDUES) =================
RAW_TXT_SOURCES = {
    # --- GitHub Repos existants ---
    "proxifly_http": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "proxifly_https": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
    "proxifly_socks4": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.txt",
    "proxifly_socks5": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt",
    "fresh_http": "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/http.txt",
    "fresh_https": "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/https.txt",
    "fresh_socks4": "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/socks4.txt",
    "fresh_socks5": "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/archive/storage/classic/socks5.txt",
    "skillter_all": "https://raw.githubusercontent.com/Skillter/ProxyGather/refs/heads/master/proxies/working-proxies-all.txt",
    "skillter_http": "https://raw.githubusercontent.com/Skillter/ProxyGather/refs/heads/master/proxies/working-proxies-http.txt",
    "skillter_socks4": "https://raw.githubusercontent.com/Skillter/ProxyGather/refs/heads/master/proxies/working-proxies-socks4.txt",
    "skillter_socks5": "https://raw.githubusercontent.com/Skillter/ProxyGather/refs/heads/master/proxies/working-proxies-socks5.txt",
    "iplocate_all": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/all-proxies.txt",
    "iplocate_http": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
    "iplocate_https": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/https.txt",
    "iplocate_socks4": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks4.txt",
    "iplocate_socks5": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks5.txt",
    "thespeedx_http": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "thespeedx_socks4": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "thespeedx_socks5": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "theriturajps": "https://raw.githubusercontent.com/theriturajps/proxy-list/main/proxies.txt",
    "clarketm_raw": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    # --- Nouvelles sources (2025 actives) ---
    "monosans_http": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "monosans_socks4": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "monosans_socks5": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "jetkai_http": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "jetkai_socks4": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt",
    "jetkai_socks5": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
    "roosterkid": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS.txt",
    "sunny9577": "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt",
    "hookzof": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "proxylist_to": "https://www.proxy-list.download/api/v1/get?type=http",
    "proxylist_to_https": "https://www.proxy-list.download/api/v1/get?type=https",
    "proxylist_to_socks4": "https://www.proxy-list.download/api/v1/get?type=socks4",
    "proxylist_to_socks5": "https://www.proxy-list.download/api/v1/get?type=socks5",
}

WEB_SCRAPE_SOURCES = {
    "free_proxy_list_net": {"url": "https://free-proxy-list.net/", "parser": "table"},
    "geonode": {"url": "https://geonode.com/free-proxy-list", "parser": "table"},
    "proxydb": {"url": "https://proxydb.net/", "parser": "table"},
    "hidemy_name": {"url": "https://hidemy.name/en/proxy-list/", "parser": "table"},
    "spys_one": {"url": "https://spys.one/en/free-proxy-list/", "parser": "spys"},
    "proxy_nova": {"url": "https://www.proxynova.com/proxy-server-list/", "parser": "table"},
    "premproxy": {"url": "https://premproxy.com/list/", "parser": "table"},
}

API_SOURCES = {
    "pubproxy": "http://pubproxy.com/api/proxy?limit=500&format=txt&type=http",
    "proxyscrape_api": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "proxyscrape_socks4": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all",
    "proxyscrape_socks5": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
    "geonode_api": "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc",
}

# ================= FONCTIONS DE TÉLÉCHARGEMENT =================
def fetch_txt_source(name, url):
    """Télécharge une source TXT et retourne un set de proxies."""
    proxies = set()
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        for line in lines:
            match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
            if match:
                proxies.add(match.group(1))
        logger.debug(f"{name}: {len(proxies)} proxies")
    except Exception as e:
        logger.warning(f"Erreur {name} ({url}): {e}")
    return proxies

def fetch_web_source(name, info):
    """Scrape une page HTML."""
    proxies = set()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(info["url"], headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')
        if info["parser"] == "table":
            for table in soup.find_all('table'):
                for row in table.find_all('tr'):
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        ip = cells[0].get_text(strip=True)
                        port = cells[1].get_text(strip=True)
                        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip) and port.isdigit():
                            proxies.add(f"{ip}:{port}")
        elif info["parser"] == "spys":
            for span in soup.find_all('span', class_='spy14'):
                text = span.get_text(strip=True)
                match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)', text)
                if match:
                    proxies.add(f"{match.group(1)}:{match.group(2)}")
        logger.debug(f"{name}: {len(proxies)} proxies")
    except Exception as e:
        logger.warning(f"Erreur {name} ({info['url']}): {e}")
    return proxies

def fetch_api_source(name, url):
    """Appelle une API retournant du texte."""
    proxies = set()
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            for line in lines:
                line = line.strip()
                if ':' in line and re.match(r'^\d', line):
                    proxies.add(line)
        logger.debug(f"{name}: {len(proxies)} proxies")
    except Exception as e:
        logger.warning(f"Erreur {name} ({url}): {e}")
    return proxies

def fetch_geonode_api(name, url):
    """Spécifique pour l'API Geonode qui renvoie du JSON."""
    proxies = set()
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        for item in data.get('data', []):
            ip = item.get('ip')
            port = item.get('port')
            if ip and port:
                proxies.add(f"{ip}:{port}")
        logger.debug(f"{name}: {len(proxies)} proxies")
    except Exception as e:
        logger.warning(f"Erreur {name}: {e}")
    return proxies

# ================= VALIDATION DES PROXIES =================
def test_proxy(proxy_str):
    """Teste un proxy et retourne un document MongoDB si valide."""
    proxies = {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
    try:
        start = time.perf_counter()
        r = requests.get(TEST_URL, proxies=proxies, timeout=TIMEOUT)
        latency = round((time.perf_counter() - start) * 1000, 2)
        if r.status_code == 200:
            return {
                "ip_port": proxy_str,
                "ip": proxy_str.split(":")[0],
                "port": int(proxy_str.split(":")[1]),
                "protocol": "http",
                "latency_ms": latency,
                "last_checked": datetime.utcnow(),
                "source": "verified"
            }
    except Exception:
        pass
    return None

def validate_proxies_parallel(proxy_set):
    """Valide un ensemble de proxies en parallèle."""
    valid = []
    total = len(proxy_set)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_VALIDATION) as executor:
        futures = {executor.submit(test_proxy, p): p for p in proxy_set}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                valid.append(result)
            if i % 500 == 0 or i == total:
                logger.info(f"Validation: {i}/{total} traités, {len(valid)} valides")
    return valid

# ================= INSERTION MONGODB PAR LOTS =================
def bulk_insert(proxy_docs):
    """Insère ou met à jour en masse."""
    if not proxy_docs:
        return 0, 0
    bulk_ops = []
    for doc in proxy_docs:
        bulk_ops.append(
            pymongo.UpdateOne(
                {"ip_port": doc["ip_port"]},
                {"$set": doc},
                upsert=True
            )
        )
    try:
        result = collection.bulk_write(bulk_ops, ordered=False)
        logger.info(f"Bulk write: {result.upserted_count} inserted, {result.modified_count} updated")
        return result.upserted_count, result.modified_count
    except Exception as e:
        logger.error(f"Erreur bulk write: {e}")
        return 0, 0

# ================= SAUVEGARDE FICHIER =================
def save_valid_to_file(proxy_docs):
    with open(OUTPUT_FILE, 'w') as f:
        for doc in proxy_docs:
            f.write(doc["ip_port"] + '\n')
    logger.info(f"Proxies valides sauvegardés dans {OUTPUT_FILE}")

# ================= MAIN =================
def main():
    logger.info("=== DÉMARRAGE DU HARVESTER DE PROXIES ===")
    start_time = time.time()

    # 1. Téléchargement parallèle de toutes les sources
    all_raw = set()
    download_tasks = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_DOWNLOAD) as executor:
        # Sources TXT
        for name, url in RAW_TXT_SOURCES.items():
            download_tasks.append(executor.submit(fetch_txt_source, name, url))
        # Sources Web
        for name, info in WEB_SCRAPE_SOURCES.items():
            download_tasks.append(executor.submit(fetch_web_source, name, info))
        # Sources API standards
        for name, url in API_SOURCES.items():
            if "geonode" in name:
                download_tasks.append(executor.submit(fetch_geonode_api, name, url))
            else:
                download_tasks.append(executor.submit(fetch_api_source, name, url))

        # Récupération des résultats
        for future in as_completed(download_tasks):
            try:
                proxies = future.result()
                all_raw.update(proxies)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération d'une source: {e}")

    logger.info(f"📦 Total brut collecté : {len(all_raw)} proxies")

    if not all_raw:
        logger.error("Aucun proxy brut récupéré. Arrêt.")
        return

    # 2. Validation des proxies
    logger.info("🔍 Début de la validation des proxies...")
    valid_proxies = validate_proxies_parallel(all_raw)
    logger.info(f"✅ Validation terminée : {len(valid_proxies)} proxies fonctionnels")

    # 3. Sauvegarde locale
    if valid_proxies:
        save_valid_to_file(valid_proxies)

    # 4. Insertion MongoDB par lots
    total_added = 0
    total_updated = 0
    for i in range(0, len(valid_proxies), BATCH_SIZE):
        batch = valid_proxies[i:i+BATCH_SIZE]
        added, updated = bulk_insert(batch)
        total_added += added
        total_updated += updated
        logger.info(f"Insertion lot {i//BATCH_SIZE + 1}: {added} ajoutés, {updated} mis à jour")

    # 5. Statistiques finales
    elapsed = time.time() - start_time
    total_in_db = collection.count_documents({})
    logger.info("=== RÉCAPITULATIF ===")
    logger.info(f"Proxies bruts récupérés : {len(all_raw)}")
    logger.info(f"Proxies valides : {len(valid_proxies)}")
    logger.info(f"Nouveaux en base : {total_added}")
    logger.info(f"Mis à jour : {total_updated}")
    logger.info(f"Total en base : {total_in_db}")
    logger.info(f"Temps d'exécution : {elapsed:.2f} secondes")
    logger.info(f"Log enregistré dans : {LOG_FILE}")

if __name__ == "__main__":
    main()
