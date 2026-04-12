#!/usr/bin/env python3
"""
MarketIntel Data Service — collecte continue, tourne en permanence.

Chaque source est collectée à son propre rythme dans un thread dédié.
Les spiders Scrapy (RSS/HTML) tournent via subprocess pour éviter les
contraintes du reactor Twisted.

Usage
-----
    python service.py          # démarrer
    python service.py --dry    # afficher le planning sans démarrer

Systemd
-------
    sudo systemctl start marketintel
    sudo journalctl -fu marketintel

Intervals par défaut
--------------------
    coingecko_markets   :  5 min   (prix temps réel)
    mempool_space       : 10 min   (état mempool BTC)
    binance_funding     : 30 min   (funding rate 8h, vérifié fréquemment)
    newsapi             : 30 min   (news API)
    fear_greed          :  1h      (index quotidien)
    fred_macro          :  6h      (macro mensuel/hebdo)
    spiders_news        :  2h      (RSS reuters/coindesk/cointelegraph/decrypt)
    spiders_data        : 15 min   (mempool, coingecko, binance via Scrapy)
"""
import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

# ── Initialisation des chemins ────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "marketintel.settings")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("service")

# ── Writer Mongo partagé ──────────────────────────────────────────────────────
_writer_lock = threading.Lock()
_writer = None


def get_writer():
    global _writer
    if _writer is None:
        from api_collectors.mongo import MongoWriter
        _writer = MongoWriter()
        log.info("MongoWriter initialisé")
    return _writer


def write_docs(docs: list, task_name: str) -> int:
    if not docs:
        return 0
    with _writer_lock:
        writer = get_writer()
        return writer.upsert_many(docs)


# ─────────────────────────────────────────────────────────────────────────────
# Classe de tâche planifiée
# ─────────────────────────────────────────────────────────────────────────────

class Task:
    """
    Tâche qui s'exécute périodiquement dans un thread daemon.
    Reprend automatiquement après une exception.
    """

    def __init__(
        self,
        name: str,
        fn: Callable[[], List[dict]],
        interval: int,
        immediate: bool = True,
    ):
        self.name = name
        self.fn = fn
        self.interval = interval
        self.immediate = immediate

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.run_count = 0
        self.error_count = 0
        self.last_run: Optional[datetime] = None
        self.last_doc_count: int = 0

    def start(self):
        self._thread = threading.Thread(
            target=self._loop,
            name=f"task:{self.name}",
            daemon=True,
        )
        self._thread.start()
        log.info("  %-20s démarré   intervalle=%ds  first=%s",
                 self.name, self.interval,
                 "immédiat" if self.immediate else f"+{self.interval}s")

    def stop(self):
        self._stop.set()

    def _loop(self):
        if self.immediate:
            self._run_once()

        while not self._stop.wait(timeout=self.interval):
            self._run_once()

    def _run_once(self):
        t0 = time.monotonic()
        try:
            docs = self.fn()
            written = write_docs(docs, self.name)
            elapsed = time.monotonic() - t0
            self.run_count += 1
            self.last_run = datetime.now(timezone.utc)
            self.last_doc_count = len(docs) if docs else 0
            log.info("[%-20s] #%-4d  %4d docs (%d écrits)  %.1fs",
                     self.name, self.run_count,
                     self.last_doc_count, written, elapsed)
        except Exception:
            self.error_count += 1
            log.exception("[%s] erreur #%d", self.name, self.error_count)


class SpiderTask(Task):
    """
    Tâche Scrapy : lance chaque spider via subprocess pour éviter
    les contraintes du reactor Twisted.
    """

    def __init__(self, name: str, spiders: List[str], interval: int, immediate: bool = False):
        self.spiders = spiders
        super().__init__(name=name, fn=self._run_spiders, interval=interval, immediate=immediate)

    def _run_spiders(self) -> List[dict]:
        for spider_name in self.spiders:
            log.info("[%-20s] scrapy crawl %s", self.name, spider_name)
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "scrapy", "crawl", spider_name],
                    cwd=ROOT,
                    env={**os.environ, "SCRAPY_SETTINGS_MODULE": "marketintel.settings"},
                    timeout=600,          # 10 min max par spider
                    capture_output=False, # laisser les logs Scrapy dans stdout
                )
                if result.returncode != 0:
                    log.warning("[%s] spider %s exited %d",
                                self.name, spider_name, result.returncode)
            except subprocess.TimeoutExpired:
                log.warning("[%s] spider %s timeout (>600s)", self.name, spider_name)
            except Exception:
                log.exception("[%s] spider %s exception", self.name, spider_name)

        return []   # les spiders écrivent directement via MongoPipeline


# ─────────────────────────────────────────────────────────────────────────────
# Définition du planning
# ─────────────────────────────────────────────────────────────────────────────

def build_schedule() -> List[Task]:
    from api_collectors.collectors.coingecko_api    import fetch_coingecko_markets
    from api_collectors.collectors.binance_api      import fetch_binance_funding_rates
    from api_collectors.collectors.alternative_me_api import fetch_fear_greed
    from api_collectors.collectors.fred_api         import fetch_fred_macro
    from api_collectors.collectors.newsapi_api      import fetch_newsapi_everything

    return [
        # ── Marché temps réel ─────────────────────────────────────────────
        Task("coingecko_markets",    fetch_coingecko_markets,         interval=5  * 60,  immediate=True),
        Task("binance_funding",      fetch_binance_funding_rates,     interval=30 * 60,  immediate=True),

        # ── Sentiment ─────────────────────────────────────────────────────
        Task("fear_greed",           fetch_fear_greed,                interval=60 * 60,  immediate=True),

        # ── News API ──────────────────────────────────────────────────────
        Task("newsapi",              fetch_newsapi_everything,        interval=30 * 60,  immediate=True),

        # ── Macro (données lentes) ────────────────────────────────────────
        Task("fred_macro",           fetch_fred_macro,                interval=6  * 3600, immediate=True),

        # ── Scrapy : spiders news RSS ─────────────────────────────────────
        # (pas de données historiques en live → pas de Wayback, RSS direct)
        SpiderTask(
            name="spiders_news",
            spiders=["reuters_rss", "coindesk", "cointelegraph", "decrypt"],
            interval=2 * 3600,
            immediate=False,   # laisser les API collectors démarrer d'abord
        ),

        # ── Scrapy : spiders données onchain ─────────────────────────────
        SpiderTask(
            name="spiders_data",
            spiders=["mempool_space", "coingecko_markets", "binance_funding",
                     "alternative_me_fng", "fred_calendar"],
            interval=15 * 60,
            immediate=True,
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Status loop
# ─────────────────────────────────────────────────────────────────────────────

def status_loop(tasks: List[Task], stop_event: threading.Event):
    """Affiche un résumé toutes les heures."""
    while not stop_event.wait(timeout=3600):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log.info("═══ STATUS [%s] ═══", now)
        for t in tasks:
            last = t.last_run.strftime("%H:%M:%S") if t.last_run else "—"
            log.info("  %-20s  runs=%-4d  errors=%-3d  last=%-8s  docs=%d",
                     t.name, t.run_count, t.error_count, last, t.last_doc_count)
        log.info("═══════════════════════════")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MarketIntel Data Service")
    parser.add_argument("--dry", action="store_true",
                        help="Afficher le planning sans démarrer")
    args = parser.parse_args()

    log.info("═══ MarketIntel Service ═══")
    log.info("ROOT    : %s", ROOT)
    log.info("PID     : %d", os.getpid())

    tasks = build_schedule()

    if args.dry:
        print(f"\n{'Tâche':<22} {'Intervalle':>12}  {'First run':<12}")
        print("-" * 50)
        for t in tasks:
            mins = t.interval // 60
            unit = f"{mins}min" if mins < 60 else f"{mins // 60}h"
            first = "immédiat" if t.immediate else f"+{unit}"
            print(f"{t.name:<22} {unit:>12}  {first:<12}")
        return

    # ── Gestion des signaux ───────────────────────────────────────────────────
    stop_event = threading.Event()

    def _shutdown(sig, frame):
        log.info("Signal %s reçu — arrêt propre en cours…", signal.Signals(sig).name)
        stop_event.set()
        for t in tasks:
            t.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    # ── Démarrage ─────────────────────────────────────────────────────────────
    log.info("Démarrage de %d tâches :", len(tasks))
    for task in tasks:
        task.start()

    # Status thread
    status_thread = threading.Thread(
        target=status_loop, args=(tasks, stop_event),
        name="status", daemon=True,
    )
    status_thread.start()

    log.info("Service opérationnel. Ctrl+C pour arrêter.")

    # Attendre le signal d'arrêt
    stop_event.wait()

    log.info("Arrêt — fermeture MongoWriter…")
    with _writer_lock:
        if _writer is not None:
            _writer.close()

    log.info("Service arrêté proprement.")


if __name__ == "__main__":
    main()
