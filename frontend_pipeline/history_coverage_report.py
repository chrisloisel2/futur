"""
Rapport rapide sur la couverture historique par crypto.

Affiche pour chaque symbole :
- nombre total de lignes
- nombre de jours couverts
- date de début / fin
- lignes par jour (moyenne et min/max)

Utilise MongoDB (collection historical_ohlcv) comme source principale.
"""
import argparse
import logging
import os
from datetime import datetime

import pandas as pd
from pymongo import MongoClient

# Paramètres par défaut (surcharge avec --uri/--db)
DEFAULT_URI = os.getenv("MONGO_URI", "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net//")
DEFAULT_DB = os.getenv("MONGO_DB", "trader")
COLLECTION = "historical_ohlcv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("history_coverage")


def load_dataframe(uri: str, db_name: str) -> pd.DataFrame:
    """Charge toutes les lignes de historical_ohlcv dans un DataFrame."""
    client = MongoClient(uri)
    coll = client[db_name][COLLECTION]

    cursor = coll.find({}, {"_id": 0, "symbol": 1, "timestamp": 1})
    data = list(cursor)
    if not data:
        raise SystemExit("Aucune donnée trouvée dans la collection historical_ohlcv")

    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    return df


def build_report(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule les stats par symbole."""
    grouped = df.groupby(["symbol", "date"]).size().reset_index(name="rows")
    summary_rows = []

    for symbol, sub in grouped.groupby("symbol"):
        days = len(sub)
        total_rows = int(sub["rows"].sum())
        min_date = sub["date"].min()
        max_date = sub["date"].max()
        avg_rows = sub["rows"].mean()
        min_rows = sub["rows"].min()
        max_rows = sub["rows"].max()

        summary_rows.append(
            {
                "symbol": symbol,
                "days": days,
                "total_rows": total_rows,
                "start": min_date,
                "end": max_date,
                "rows_per_day_avg": round(avg_rows, 2),
                "rows_per_day_min": int(min_rows),
                "rows_per_day_max": int(max_rows),
            }
        )

    report = pd.DataFrame(summary_rows).sort_values("symbol").reset_index(drop=True)
    return report


def print_report(report: pd.DataFrame):
    """Affiche un tableau lisible en console."""
    if report.empty:
        print("Aucune donnée à afficher.")
        return

    cols = [
        "symbol",
        "days",
        "total_rows",
        "start",
        "end",
        "rows_per_day_avg",
        "rows_per_day_min",
        "rows_per_day_max",
    ]
    report[cols] = report[cols].astype(str)
    print("\nCouverture historique par crypto:")
    print(report[cols].to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="Résumé de couverture historique par crypto")
    parser.add_argument("--uri", default=DEFAULT_URI, help="URI MongoDB (defaut: %(default)s)")
    parser.add_argument("--db", default=DEFAULT_DB, help="Nom de la base (defaut: %(default)s)")
    args = parser.parse_args()

    start = datetime.utcnow()
    logger.info("Connexion MongoDB: %s (db=%s)", args.uri, args.db)
    df = load_dataframe(args.uri, args.db)
    logger.info("Lignes chargées: %s", len(df))

    report = build_report(df)
    print_report(report)
    logger.info("Terminé en %.2fs", (datetime.utcnow() - start).total_seconds())


if __name__ == "__main__":
    main()
