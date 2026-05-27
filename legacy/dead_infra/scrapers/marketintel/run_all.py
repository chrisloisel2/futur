#!/usr/bin/env python3
"""
Orchestrateur unique MarketIntel.

Lance les spiders Scrapy ET les collecteurs API, avec support date range
pour reconstituer une base historique depuis plusieurs années.

En mode live   : tous les spiders (courant) + tous les API collectors (courant)
En mode history: tous les spiders avec from_date/to_date + tous les API collectors
                 avec date range. mempool_space est ignoré (données point-in-time).

Exemples
--------
  # Live complet : spiders + API courant
  python run_all.py

  # Historique 3 ans : spiders (Wayback pour news) + API collectors avec date range
  python run_all.py --history --years-back 3

  # Historique plage précise
  python run_all.py --history --from 2020-01-01 --to 2024-12-31

  # Spiders seulement (live)
  python run_all.py --skip-apis

  # API collectors seulement (live)
  python run_all.py --skip-spiders

  # Spiders seulement, historique
  python run_all.py --history --years-back 2 --skip-apis

  # Lister les spiders disponibles
  python run_all.py --list-spiders
"""
import argparse
import logging
import os
import sys
from datetime import date

# ── Positionnement ────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "marketintel.settings")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("run_all")

# ── Spiders déclarés par famille ─────────────────────────────────────────────
# news : Wayback Machine en mode historique
NEWS_SPIDERS = ["reuters_rss", "coindesk", "cointelegraph", "decrypt"]

# market/macro/sentiment/onchain : pagination API directe en mode historique
DATA_SPIDERS = [
    "binance_funding",
    "coingecko_markets",
    "alternative_me_fng",
    "fred_calendar",
    "mempool_space",    # live only — s'auto-désactive si from_date fourni
]

ALL_SPIDERS = DATA_SPIDERS + NEWS_SPIDERS


# ─────────────────────────────────────────────────────────────────────────────
# Spiders
# ─────────────────────────────────────────────────────────────────────────────

def run_spiders(names: list, from_date: date = None, to_date: date = None) -> None:
    """
    Lance les spiders via CrawlerProcess.
    Si from_date est fourni, les spiders reçoivent from_date/to_date comme
    spider arguments — chaque spider décide comment les utiliser.
    """
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    log.info("=== SPIDERS [%d] mode=%s ===",
             len(names), "history" if from_date else "live")

    kwargs = {}
    if from_date:
        kwargs["from_date"] = str(from_date)
        kwargs["to_date"] = str(to_date)

    settings = get_project_settings()
    process = CrawlerProcess(settings)
    for name in names:
        process.crawl(name, **kwargs)

    process.start()   # bloquant — attend la fin de toutes les spiders
    log.info("=== SPIDERS terminés ===")


# ─────────────────────────────────────────────────────────────────────────────
# API collectors
# ─────────────────────────────────────────────────────────────────────────────

def run_api_collectors(from_date: date, to_date: date, history_mode: bool) -> None:
    from api_collectors.mongo import MongoWriter
    from api_collectors.collectors.binance_api import fetch_binance_funding_rates
    from api_collectors.collectors.coingecko_api import (
        fetch_coingecko_markets,
        fetch_coingecko_history,
    )
    from api_collectors.collectors.alternative_me_api import fetch_fear_greed
    from api_collectors.collectors.fred_api import fetch_fred_macro
    from api_collectors.collectors.newsapi_api import fetch_newsapi_everything

    if history_mode:
        log.info("=== API COLLECTORS [historique %s → %s] ===", from_date, to_date)
        collectors = [
            ("binance_funding",
             lambda: fetch_binance_funding_rates(from_date=from_date, to_date=to_date)),
            ("coingecko_history",
             lambda: fetch_coingecko_history(from_date=from_date, to_date=to_date)),
            ("fear_greed",
             lambda: fetch_fear_greed(from_date=from_date)),
            ("fred_macro",
             lambda: fetch_fred_macro(from_date=from_date)),
            # newsapi ignoré en mode history (limité ~1 mois côté API)
        ]
    else:
        log.info("=== API COLLECTORS [live] ===")
        collectors = [
            ("binance_funding",   fetch_binance_funding_rates),
            ("coingecko_markets", fetch_coingecko_markets),
            ("fear_greed",        fetch_fear_greed),
            ("fred_macro",        fetch_fred_macro),
            ("newsapi",           fetch_newsapi_everything),
        ]

    writer = MongoWriter()
    try:
        all_docs = []
        for name, fn in collectors:
            try:
                docs = fn()
                log.info("  %-25s → %d documents", name, len(docs))
                all_docs.extend(docs)
            except Exception:
                log.exception("  collecteur échoué : %s", name)

        changed = writer.upsert_many(all_docs)
        log.info("=== API COLLECTORS terminés — %d docs écrits/mis à jour ===", changed)
    finally:
        writer.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Orchestrateur MarketIntel — spiders + API collectors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--history", action="store_true",
        help="Mode historique : spiders avec Wayback + API avec date range",
    )
    p.add_argument(
        "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Date de début",
    )
    p.add_argument(
        "--to", dest="to_date", metavar="YYYY-MM-DD",
        default=str(date.today()),
        help="Date de fin (défaut : aujourd'hui)",
    )
    p.add_argument(
        "--years-back", type=int, metavar="N",
        help="Raccourci : --from = aujourd'hui − N ans",
    )
    p.add_argument(
        "--skip-spiders", action="store_true",
        help="Ne pas lancer les spiders Scrapy",
    )
    p.add_argument(
        "--skip-apis", action="store_true",
        help="Ne pas lancer les collecteurs API",
    )
    p.add_argument(
        "--spiders", nargs="+", metavar="NAME",
        help=f"Sous-ensemble de spiders. Disponibles : {', '.join(ALL_SPIDERS)}",
    )
    p.add_argument(
        "--list-spiders", action="store_true",
        help="Afficher les spiders disponibles et quitter",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list_spiders:
        print("News spiders (Wayback Machine en mode history) :")
        for s in NEWS_SPIDERS:
            print(f"  {s}")
        print("\nData spiders (pagination API directe en mode history) :")
        for s in DATA_SPIDERS:
            print(f"  {s}")
        sys.exit(0)

    # ── Calcul de la plage de dates ──────────────────────────────────────────
    to_date = date.fromisoformat(args.to_date)

    if args.years_back:
        from_date = date(to_date.year - args.years_back, to_date.month, to_date.day)
    elif args.from_date:
        from_date = date.fromisoformat(args.from_date)
    else:
        from_date = None   # live = pas de plage

    if args.history and from_date is None:
        log.error("--history requiert --from YYYY-MM-DD ou --years-back N")
        sys.exit(1)

    log.info(
        "MarketIntel — mode=%-8s  plage=%s → %s",
        "history" if args.history else "live",
        from_date or "today",
        to_date,
    )

    # ── Spiders ──────────────────────────────────────────────────────────────
    if not args.skip_spiders:
        spider_names = args.spiders if args.spiders else ALL_SPIDERS
        unknown = [s for s in spider_names if s not in ALL_SPIDERS]
        if unknown:
            log.warning("Spiders inconnus ignorés : %s", unknown)
            spider_names = [s for s in spider_names if s in ALL_SPIDERS]

        run_spiders(
            spider_names,
            from_date=from_date if args.history else None,
            to_date=to_date if args.history else None,
        )

    # ── API collectors ───────────────────────────────────────────────────────
    if not args.skip_apis:
        run_api_collectors(
            from_date=from_date or to_date,
            to_date=to_date,
            history_mode=args.history,
        )


if __name__ == "__main__":
    main()
