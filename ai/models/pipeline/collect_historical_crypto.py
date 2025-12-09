"""
Collecteur de données historiques crypto - 1 an minimum
Collecte OHLCV pour les 30 principales cryptos depuis Binance (gratuit, sans API key)
"""
import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
from typing import List, Optional
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HistoricalCryptoCollector:
    """Collecteur de données historiques crypto depuis Binance Public API."""

    def __init__(self, output_dir: str = "datasets/historical_crypto"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session: Optional[aiohttp.ClientSession] = None

        # Top 30 cryptos par market cap
        self.top_30_cryptos = [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
            "ADA/USDT", "AVAX/USDT", "DOT/USDT", "DOGE/USDT", "MATIC/USDT",
            "LTC/USDT", "LINK/USDT", "ATOM/USDT", "UNI/USDT", "XLM/USDT",
            "NEAR/USDT", "ALGO/USDT", "VET/USDT", "ICP/USDT", "FIL/USDT",
            "HBAR/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "IMX/USDT",
            "SAND/USDT", "MANA/USDT", "AXS/USDT", "GALA/USDT", "ENJ/USDT"
        ]

    async def get_session(self) -> aiohttp.ClientSession:
        """Obtenir ou créer une session HTTP."""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=10)
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector
            )
        return self.session

    async def fetch_binance_klines(
        self,
        symbol: str,
        interval: str = '1h',
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> Optional[pd.DataFrame]:
        """
        Télécharger les données OHLCV depuis Binance Public API.

        Args:
            symbol: Symbole crypto (ex: BTC/USDT)
            interval: Intervalle (1m, 5m, 15m, 1h, 4h, 1d)
            start_time: Date de début
            end_time: Date de fin
            limit: Nombre max de lignes par requête (max 1000)
        """
        # Convertir le symbole au format Binance (BTC/USDT -> BTCUSDT)
        binance_symbol = symbol.replace('/', '')

        url = "https://api.binance.com/api/v3/klines"

        params = {
            'symbol': binance_symbol,
            'interval': interval,
            'limit': limit
        }

        if start_time:
            params['startTime'] = int(start_time.timestamp() * 1000)

        if end_time:
            params['endTime'] = int(end_time.timestamp() * 1000)

        try:
            session = await self.get_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()

                    if not data:
                        return None

                    # Convertir en DataFrame
                    df = pd.DataFrame(data, columns=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore'
                    ])

                    # Convertir les types
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')

                    for col in ['open', 'high', 'low', 'close', 'volume',
                               'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
                        df[col] = df[col].astype(float)

                    df['trades'] = df['trades'].astype(int)
                    df['symbol'] = symbol

                    # Supprimer la colonne ignore
                    df = df.drop('ignore', axis=1)

                    return df

                elif response.status == 429:
                    logger.warning(f"Rate limit hit for {symbol}, waiting...")
                    await asyncio.sleep(60)
                    return None
                else:
                    logger.error(f"HTTP {response.status} for {symbol}")
                    return None

        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return None

    async def collect_historical_data(
        self,
        symbol: str,
        days: int = 365,
        interval: str = '1h'
    ) -> Optional[pd.DataFrame]:
        """
        Collecter toutes les données historiques pour un symbole.

        Args:
            symbol: Symbole crypto
            days: Nombre de jours d'historique
            interval: Intervalle de temps
        """
        logger.info(f"📊 Collecting {days} days of {interval} data for {symbol}")

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)

        all_data = []
        current_start = start_time

        # Binance limite à 1000 lignes par requête
        # Pour 1h interval: 1000h = ~41 jours par requête
        max_chunk_duration = {
            '1m': timedelta(minutes=1000),
            '5m': timedelta(minutes=5000),
            '15m': timedelta(minutes=15000),
            '1h': timedelta(hours=1000),
            '4h': timedelta(hours=4000),
            '1d': timedelta(days=1000)
        }

        chunk_duration = max_chunk_duration.get(interval, timedelta(hours=1000))

        iteration = 0
        max_iterations = 20  # Sécurité anti-boucle infinie

        while current_start < end_time and iteration < max_iterations:
            iteration += 1
            chunk_end = min(current_start + chunk_duration, end_time)

            df = await self.fetch_binance_klines(
                symbol=symbol,
                interval=interval,
                start_time=current_start,
                end_time=chunk_end,
                limit=1000
            )

            if df is not None and len(df) > 0:
                all_data.append(df)
                logger.info(f"  ✓ Chunk {iteration}: {len(df)} rows "
                          f"({df['timestamp'].min()} to {df['timestamp'].max()})")

                # Prochaine itération commence après la dernière donnée reçue
                current_start = df['close_time'].max() + timedelta(milliseconds=1)
            else:
                # Pas de données, avancer quand même
                current_start = chunk_end

            # Rate limiting courtois
            await asyncio.sleep(0.2)

        if all_data:
            final_df = pd.concat(all_data, ignore_index=True)
            final_df = final_df.drop_duplicates(subset=['timestamp'])
            final_df = final_df.sort_values('timestamp')
            final_df = final_df.reset_index(drop=True)

            logger.info(f"✅ {symbol}: {len(final_df)} rows collected "
                       f"({final_df['timestamp'].min()} to {final_df['timestamp'].max()})")

            return final_df

        return None

    async def collect_all_cryptos(
        self,
        symbols: Optional[List[str]] = None,
        days: int = 365,
        interval: str = '1h'
    ) -> dict:
        """
        Collecter toutes les cryptos.

        Args:
            symbols: Liste des symboles (None = top 30)
            days: Nombre de jours
            interval: Intervalle de temps
        """
        if symbols is None:
            symbols = self.top_30_cryptos

        logger.info(f"🚀 Starting collection for {len(symbols)} cryptos")
        logger.info(f"   Period: {days} days")
        logger.info(f"   Interval: {interval}")

        results = {}
        successful = 0
        failed = 0

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"\n[{i}/{len(symbols)}] Processing {symbol}")

            try:
                df = await self.collect_historical_data(
                    symbol=symbol,
                    days=days,
                    interval=interval
                )

                if df is not None and len(df) > 0:
                    results[symbol] = df
                    successful += 1

                    # Sauvegarder immédiatement
                    self.save_to_parquet(symbol, df, interval)
                else:
                    logger.warning(f"⚠️  No data collected for {symbol}")
                    failed += 1

            except Exception as e:
                logger.error(f"❌ Failed to collect {symbol}: {e}")
                failed += 1

            # Rate limiting entre les symboles
            if i < len(symbols):
                await asyncio.sleep(1)

        logger.info(f"\n" + "="*80)
        logger.info(f"📊 COLLECTION SUMMARY")
        logger.info(f"="*80)
        logger.info(f"Successful: {successful}/{len(symbols)}")
        logger.info(f"Failed: {failed}/{len(symbols)}")

        return results

    def save_to_parquet(self, symbol: str, df: pd.DataFrame, interval: str):
        """Sauvegarder en format Parquet."""
        safe_symbol = symbol.replace('/', '_')
        timestamp = datetime.utcnow().strftime('%Y%m%d')

        filename = self.output_dir / f"{safe_symbol}_{interval}_{timestamp}.parquet"
        df.to_parquet(filename, compression='gzip', index=False)

        logger.info(f"💾 Saved {symbol} to {filename}")

    def create_summary_report(self, results: dict, interval: str):
        """Créer un rapport de synthèse."""
        report_path = self.output_dir / f"summary_{interval}_{datetime.utcnow().strftime('%Y%m%d')}.txt"

        lines = []
        lines.append("="*80)
        lines.append("HISTORICAL CRYPTO DATA COLLECTION REPORT")
        lines.append("="*80)
        lines.append(f"\nCollection Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"Interval: {interval}")
        lines.append(f"Total Cryptos: {len(results)}")
        lines.append(f"\n{'Symbol':<15} {'Rows':>10} {'Start Date':<20} {'End Date':<20} {'Days':<10}")
        lines.append("-"*80)

        total_rows = 0

        for symbol, df in sorted(results.items()):
            rows = len(df)
            total_rows += rows
            start_date = df['timestamp'].min().strftime('%Y-%m-%d %H:%M')
            end_date = df['timestamp'].max().strftime('%Y-%m-%d %H:%M')
            days = (df['timestamp'].max() - df['timestamp'].min()).days

            lines.append(f"{symbol:<15} {rows:>10,} {start_date:<20} {end_date:<20} {days:<10}")

        lines.append("-"*80)
        lines.append(f"{'TOTAL':<15} {total_rows:>10,}")
        lines.append("="*80)

        report_text = '\n'.join(lines)

        with open(report_path, 'w') as f:
            f.write(report_text)

        logger.info(f"\n{report_text}")
        logger.info(f"\n📄 Report saved to {report_path}")

    async def close(self):
        """Fermer la session."""
        if self.session and not self.session.closed:
            await self.session.close()


async def main():
    """Point d'entrée principal."""

    print("\n" + "="*80)
    print("📊 COLLECTEUR DE DONNÉES HISTORIQUES CRYPTO")
    print("="*80)
    print("\nObjectif: Collecter 1 an de données OHLCV pour 30 cryptos")
    print("Source: Binance Public API (gratuit, sans clé requise)")
    print("Format: Parquet compressé")
    print("\n" + "="*80 + "\n")

    collector = HistoricalCryptoCollector()

    try:
        # Collecter les données
        # Vous pouvez ajuster:
        # - days: nombre de jours (365 = 1 an)
        # - interval: '1h', '4h', '1d' etc.
        results = await collector.collect_all_cryptos(
            days=365,      # 1 an
            interval='1h'  # Données horaires
        )

        # Créer le rapport
        if results:
            collector.create_summary_report(results, '1h')

            print("\n" + "="*80)
            print("✅ COLLECTION TERMINÉE AVEC SUCCÈS!")
            print("="*80)
            print(f"\nDonnées sauvegardées dans: {collector.output_dir}")
            print(f"Nombre de cryptos: {len(results)}")
            print(f"Total de lignes: {sum(len(df) for df in results.values()):,}")

            # Statistiques détaillées
            total_size_mb = sum(
                (collector.output_dir / f"{s.replace('/', '_')}_1h_{datetime.utcnow().strftime('%Y%m%d')}.parquet").stat().st_size / 1024 / 1024
                for s in results.keys()
                if (collector.output_dir / f"{s.replace('/', '_')}_1h_{datetime.utcnow().strftime('%Y%m%d')}.parquet").exists()
            )

            print(f"Taille totale: {total_size_mb:.2f} MB")
            print("\nVous pouvez maintenant utiliser ces données pour:")
            print("  • Entraîner des modèles de machine learning")
            print("  • Backtester des stratégies de trading")
            print("  • Analyser des patterns de marché")
            print("  • Générer des features techniques")

        else:
            print("\n❌ Aucune donnée collectée")

    except KeyboardInterrupt:
        print("\n\n⏹️  Arrêt par l'utilisateur")

    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)

    finally:
        await collector.close()


if __name__ == "__main__":
    asyncio.run(main())
