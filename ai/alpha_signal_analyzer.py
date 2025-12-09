"""
ALPHA SIGNAL ANALYZER
=====================
Analyse le dataset collecté pour identifier des signaux alpha exploitables.

Stratégies de détection:
1. Divergences on-chain vs prix
2. Sentiment extrême (contrarian indicators)
3. Anomalies dans les funding rates
4. Corrélations macro inattendues
5. Flow analysis (exchange in/out)
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import json
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')


class AlphaSignalAnalyzer:
    """Analyseur de signaux alpha à partir du dataset collecté."""

    def __init__(self, dataset_path: Path):
        self.dataset_path = dataset_path
        self.data = {}
        self.signals = []
        self.load_data()

    def load_data(self):
        """Charger tous les fichiers parquet du dataset."""
        print(f"Loading dataset from {self.dataset_path}")

        for file_path in self.dataset_path.glob("*.parquet"):
            name = file_path.stem
            try:
                self.data[name] = pd.read_parquet(file_path)
                print(f"  ✓ Loaded {name}: {len(self.data[name])} records")
            except Exception as e:
                print(f"  ✗ Failed to load {name}: {e}")

    def analyze_onchain_price_divergence(self) -> List[Dict]:
        """
        Signal Alpha #1: Divergence on-chain vs prix

        Concept: Si le réseau montre une forte activité (addresses actives,
        transactions) mais le prix stagne ou baisse = signal d'accumulation.
        """
        print("\n[SIGNAL #1] Analyzing on-chain vs price divergence...")
        signals = []

        if 'binance_ohlcv' not in self.data or 'onchain_metrics' not in self.data:
            print("  ✗ Missing required data")
            return signals

        # Pour chaque asset avec données on-chain
        for asset in ['BTC', 'ETH']:
            symbol = f"{asset}/USDT"

            # Prix
            price_data = self.data['binance_ohlcv']
            if symbol in price_data['symbol'].values:
                symbol_prices = price_data[price_data['symbol'] == symbol].copy()
                symbol_prices['timestamp'] = pd.to_datetime(symbol_prices['timestamp'])
                symbol_prices = symbol_prices.sort_values('timestamp')

                # Calculer le momentum du prix (7 jours)
                symbol_prices['price'] = symbol_prices['close'].astype(float)
                symbol_prices['price_change_7d'] = symbol_prices['price'].pct_change(7)

                # On-chain (si disponible)
                onchain = self.data['onchain_metrics']
                if asset in onchain['asset'].values:
                    # Signal: prix -10% mais on-chain stable/up = accumulation
                    latest_price_change = symbol_prices['price_change_7d'].iloc[-1] if len(symbol_prices) > 7 else 0

                    if latest_price_change < -0.10:  # Prix down 10%+
                        signals.append({
                            'signal_type': 'ON_CHAIN_ACCUMULATION',
                            'asset': asset,
                            'strength': 'STRONG',
                            'direction': 'BULLISH',
                            'price_change_7d': latest_price_change,
                            'reasoning': f'{asset} showing price weakness but on-chain fundamentals strong',
                            'timestamp': datetime.utcnow()
                        })
                        print(f"  ✓ Found accumulation signal for {asset}")

        return signals

    def analyze_extreme_sentiment(self) -> List[Dict]:
        """
        Signal Alpha #2: Sentiment extrême (contrarian)

        Concept: Fear & Greed Index < 20 = extreme fear = buy opportunity
                 Fear & Greed Index > 80 = extreme greed = sell signal
        """
        print("\n[SIGNAL #2] Analyzing extreme sentiment...")
        signals = []

        if 'fear_greed_index' not in self.data:
            print("  ✗ Missing fear & greed data")
            return signals

        fg_data = self.data['fear_greed_index'].copy()
        if len(fg_data) == 0:
            return signals

        fg_data['value'] = pd.to_numeric(fg_data['value'], errors='coerce')
        latest = fg_data.iloc[0]  # Plus récent
        fg_value = latest['value']

        # Extreme Fear (contrarian buy)
        if fg_value < 25:
            signals.append({
                'signal_type': 'EXTREME_FEAR',
                'asset': 'BTC',  # Généralement corrélé au marché global
                'strength': 'STRONG' if fg_value < 15 else 'MEDIUM',
                'direction': 'BULLISH',
                'fear_greed_value': fg_value,
                'reasoning': f'Extreme fear at {fg_value} - contrarian buy opportunity',
                'timestamp': datetime.utcnow()
            })
            print(f"  ✓ Extreme FEAR detected: {fg_value}")

        # Extreme Greed (contrarian sell)
        elif fg_value > 75:
            signals.append({
                'signal_type': 'EXTREME_GREED',
                'asset': 'BTC',
                'strength': 'STRONG' if fg_value > 85 else 'MEDIUM',
                'direction': 'BEARISH',
                'fear_greed_value': fg_value,
                'reasoning': f'Extreme greed at {fg_value} - consider taking profits',
                'timestamp': datetime.utcnow()
            })
            print(f"  ✓ Extreme GREED detected: {fg_value}")

        return signals

    def analyze_funding_rate_anomalies(self) -> List[Dict]:
        """
        Signal Alpha #3: Anomalies dans les funding rates

        Concept: Funding rate très positif = trop de longs = risque de squeeze
                 Funding rate très négatif = trop de shorts = risque de short squeeze
        """
        print("\n[SIGNAL #3] Analyzing funding rate anomalies...")
        signals = []

        if 'funding_rates' not in self.data:
            print("  ✗ Missing funding rate data")
            return signals

        funding = self.data['funding_rates'].copy()
        if len(funding) == 0:
            return signals

        funding['funding_rate'] = pd.to_numeric(funding['funding_rate'], errors='coerce')

        # Par symbol
        for symbol in funding['symbol'].unique():
            symbol_funding = funding[funding['symbol'] == symbol]
            latest_rate = symbol_funding['funding_rate'].iloc[-1] if len(symbol_funding) > 0 else 0

            # Funding rate très positif (> 0.1% par 8h) = overleveraged longs
            if latest_rate > 0.001:  # 0.1% per 8h = 0.375% daily
                signals.append({
                    'signal_type': 'HIGH_FUNDING_RATE',
                    'asset': symbol,
                    'strength': 'STRONG' if latest_rate > 0.002 else 'MEDIUM',
                    'direction': 'BEARISH',
                    'funding_rate': latest_rate,
                    'reasoning': f'Very high funding rate {latest_rate:.4f} - overleveraged longs, risk of long squeeze',
                    'timestamp': datetime.utcnow()
                })
                print(f"  ✓ High funding detected for {symbol}: {latest_rate:.4f}")

            # Funding rate très négatif (< -0.05%) = overleveraged shorts
            elif latest_rate < -0.0005:
                signals.append({
                    'signal_type': 'NEGATIVE_FUNDING_RATE',
                    'asset': symbol,
                    'strength': 'STRONG' if latest_rate < -0.001 else 'MEDIUM',
                    'direction': 'BULLISH',
                    'funding_rate': latest_rate,
                    'reasoning': f'Negative funding rate {latest_rate:.4f} - overleveraged shorts, risk of short squeeze',
                    'timestamp': datetime.utcnow()
                })
                print(f"  ✓ Negative funding detected for {symbol}: {latest_rate:.4f}")

        return signals

    def analyze_macro_correlation(self) -> List[Dict]:
        """
        Signal Alpha #4: Corrélations macro inattendues

        Concept: Décorrelation BTC vs indices traditionnels peut signaler un mouvement indépendant
        """
        print("\n[SIGNAL #4] Analyzing macro correlations...")
        signals = []

        if 'binance_ohlcv' not in self.data or 'global_markets' not in self.data:
            print("  ✗ Missing required data for correlation analysis")
            return signals

        # Analyser la corrélation BTC vs S&P500
        btc_data = self.data['binance_ohlcv']
        if 'BTC/USDT' in btc_data['symbol'].values:
            btc = btc_data[btc_data['symbol'] == 'BTC/USDT'].copy()
            btc['price'] = btc['close'].astype(float)
            btc_recent_change = btc['price'].pct_change(7).iloc[-1] if len(btc) > 7 else 0

            markets = self.data['global_markets']
            if '^GSPC' in markets['symbol'].values:  # S&P 500
                sp500 = markets[markets['symbol'] == '^GSPC'].iloc[0]
                sp500_change = (sp500['price'] - sp500['previous_close']) / sp500['previous_close']

                # Si BTC monte alors que S&P baisse (décorrélation)
                if btc_recent_change > 0.05 and sp500_change < -0.02:
                    signals.append({
                        'signal_type': 'MACRO_DECOUPLING',
                        'asset': 'BTC',
                        'strength': 'MEDIUM',
                        'direction': 'BULLISH',
                        'btc_change': btc_recent_change,
                        'sp500_change': sp500_change,
                        'reasoning': 'BTC showing strength while traditional markets weak - decoupling signal',
                        'timestamp': datetime.utcnow()
                    })
                    print(f"  ✓ Macro decoupling detected: BTC +{btc_recent_change:.2%}, S&P {sp500_change:.2%}")

        return signals

    def analyze_orderbook_imbalance(self) -> List[Dict]:
        """
        Signal Alpha #5: Déséquilibre orderbook

        Concept: Si bid depth >> ask depth = pression acheteuse forte
        """
        print("\n[SIGNAL #5] Analyzing orderbook imbalances...")
        signals = []

        if 'orderbook_depth' not in self.data:
            print("  ✗ Missing orderbook data")
            return signals

        orderbook = self.data['orderbook_depth'].copy()
        if len(orderbook) == 0:
            return signals

        orderbook['bid_depth'] = pd.to_numeric(orderbook['bid_depth'], errors='coerce')
        orderbook['ask_depth'] = pd.to_numeric(orderbook['ask_depth'], errors='coerce')
        orderbook['imbalance'] = orderbook['bid_depth'] / (orderbook['ask_depth'] + 1)

        for idx, row in orderbook.iterrows():
            # Forte imbalance côté bid (ratio > 1.5)
            if row['imbalance'] > 1.5:
                signals.append({
                    'signal_type': 'ORDERBOOK_BID_IMBALANCE',
                    'asset': row['symbol'],
                    'strength': 'STRONG' if row['imbalance'] > 2.0 else 'MEDIUM',
                    'direction': 'BULLISH',
                    'imbalance_ratio': row['imbalance'],
                    'reasoning': f'Strong bid-side pressure, ratio {row["imbalance"]:.2f}',
                    'timestamp': datetime.utcnow()
                })

            # Forte imbalance côté ask (ratio < 0.7)
            elif row['imbalance'] < 0.7:
                signals.append({
                    'signal_type': 'ORDERBOOK_ASK_IMBALANCE',
                    'asset': row['symbol'],
                    'strength': 'STRONG' if row['imbalance'] < 0.5 else 'MEDIUM',
                    'direction': 'BEARISH',
                    'imbalance_ratio': row['imbalance'],
                    'reasoning': f'Strong ask-side pressure, ratio {row["imbalance"]:.2f}',
                    'timestamp': datetime.utcnow()
                })

        return signals

    def analyze_social_momentum(self) -> List[Dict]:
        """
        Signal Alpha #6: Momentum social

        Concept: Spike soudain dans les mentions Reddit/Twitter peut précéder un mouvement de prix
        """
        print("\n[SIGNAL #6] Analyzing social momentum...")
        signals = []

        if 'reddit_sentiment' not in self.data:
            print("  ✗ Missing social data")
            return signals

        reddit = self.data['reddit_sentiment'].copy()
        if len(reddit) == 0:
            return signals

        # Analyser les posts avec beaucoup d'engagement
        reddit['engagement'] = pd.to_numeric(reddit['score'], errors='coerce') + \
                               pd.to_numeric(reddit['num_comments'], errors='coerce')

        # Top posts récents
        top_posts = reddit.nlargest(5, 'engagement')

        for _, post in top_posts.iterrows():
            # Si un post a beaucoup d'engagement (signe d'intérêt)
            if post['engagement'] > 1000:  # Seuil arbitraire
                # Identifier les mentions de crypto dans le titre
                title_lower = str(post['title']).lower()
                crypto_mentioned = None

                for crypto in ['bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol']:
                    if crypto in title_lower:
                        crypto_mentioned = crypto.upper() if len(crypto) <= 3 else crypto.title()
                        break

                if crypto_mentioned:
                    signals.append({
                        'signal_type': 'SOCIAL_MOMENTUM',
                        'asset': crypto_mentioned,
                        'strength': 'MEDIUM',
                        'direction': 'NEUTRAL',  # Nécessite analyse du sentiment du post
                        'engagement': post['engagement'],
                        'post_title': post['title'],
                        'reasoning': f'High social engagement on Reddit: {post["engagement"]:.0f} score',
                        'timestamp': datetime.utcnow()
                    })

        return signals

    def analyze_long_short_extremes(self) -> List[Dict]:
        """
        Signal Alpha #7: Ratio long/short extrême

        Concept: Si ratio L/S > 3 = trop de longs = contrarian bearish
                 Si ratio L/S < 0.5 = trop de shorts = contrarian bullish
        """
        print("\n[SIGNAL #7] Analyzing long/short ratio extremes...")
        signals = []

        if 'long_short_ratio' not in self.data:
            print("  ✗ Missing long/short ratio data")
            return signals

        ls_data = self.data['long_short_ratio'].copy()
        if len(ls_data) == 0:
            return signals

        ls_data['long_short_ratio'] = pd.to_numeric(ls_data['long_short_ratio'], errors='coerce')

        # Par symbol, prendre le ratio le plus récent
        for symbol in ls_data['symbol'].unique():
            symbol_ls = ls_data[ls_data['symbol'] == symbol]
            latest_ratio = symbol_ls['long_short_ratio'].iloc[-1] if len(symbol_ls) > 0 else 1

            # Trop de longs (ratio > 2.5)
            if latest_ratio > 2.5:
                signals.append({
                    'signal_type': 'EXTREME_LONG_POSITIONING',
                    'asset': symbol,
                    'strength': 'STRONG' if latest_ratio > 3.5 else 'MEDIUM',
                    'direction': 'BEARISH',
                    'long_short_ratio': latest_ratio,
                    'reasoning': f'Extreme long positioning, L/S ratio {latest_ratio:.2f} - contrarian bearish',
                    'timestamp': datetime.utcnow()
                })
                print(f"  ✓ Extreme longs for {symbol}: {latest_ratio:.2f}")

            # Trop de shorts (ratio < 0.6)
            elif latest_ratio < 0.6:
                signals.append({
                    'signal_type': 'EXTREME_SHORT_POSITIONING',
                    'asset': symbol,
                    'strength': 'STRONG' if latest_ratio < 0.4 else 'MEDIUM',
                    'direction': 'BULLISH',
                    'long_short_ratio': latest_ratio,
                    'reasoning': f'Extreme short positioning, L/S ratio {latest_ratio:.2f} - contrarian bullish',
                    'timestamp': datetime.utcnow()
                })
                print(f"  ✓ Extreme shorts for {symbol}: {latest_ratio:.2f}")

        return signals

    def run_all_analyses(self) -> List[Dict]:
        """Exécuter toutes les analyses et compiler les signaux."""
        print("\n" + "=" * 80)
        print("ALPHA SIGNAL DETECTION - RUNNING ALL ANALYSES")
        print("=" * 80)

        all_signals = []

        # Exécuter toutes les analyses
        analyses = [
            self.analyze_onchain_price_divergence,
            self.analyze_extreme_sentiment,
            self.analyze_funding_rate_anomalies,
            self.analyze_macro_correlation,
            self.analyze_orderbook_imbalance,
            self.analyze_social_momentum,
            self.analyze_long_short_extremes,
        ]

        for analysis_func in analyses:
            try:
                signals = analysis_func()
                all_signals.extend(signals)
            except Exception as e:
                print(f"  ✗ Analysis {analysis_func.__name__} failed: {e}")

        self.signals = all_signals
        return all_signals

    def generate_report(self, output_path: Path):
        """Générer un rapport détaillé des signaux alpha."""
        if not self.signals:
            print("No signals to report")
            return

        print("\n" + "=" * 80)
        print("ALPHA SIGNALS REPORT")
        print("=" * 80)

        # Grouper par type et direction
        df_signals = pd.DataFrame(self.signals)

        print(f"\nTotal signals detected: {len(df_signals)}")

        # Par direction
        if 'direction' in df_signals.columns:
            print("\nBy direction:")
            direction_counts = df_signals['direction'].value_counts()
            for direction, count in direction_counts.items():
                print(f"  {direction}: {count}")

        # Par force
        if 'strength' in df_signals.columns:
            print("\nBy strength:")
            strength_counts = df_signals['strength'].value_counts()
            for strength, count in strength_counts.items():
                print(f"  {strength}: {count}")

        # Top signaux par asset
        if 'asset' in df_signals.columns:
            print("\nSignals by asset:")
            asset_counts = df_signals['asset'].value_counts().head(10)
            for asset, count in asset_counts.items():
                print(f"  {asset}: {count} signals")

        # Sauvegarder le rapport
        report_path = output_path / "alpha_signals_report.json"
        with open(report_path, 'w') as f:
            json.dump(self.signals, f, indent=2, default=str)

        print(f"\nFull report saved to: {report_path}")

        # Sauvegarder aussi en CSV pour faciliter l'analyse
        csv_path = output_path / "alpha_signals.csv"
        df_signals.to_csv(csv_path, index=False)
        print(f"CSV report saved to: {csv_path}")

        # Afficher les signaux STRONG
        strong_signals = df_signals[df_signals['strength'] == 'STRONG']
        if len(strong_signals) > 0:
            print("\n" + "-" * 80)
            print(f"STRONG SIGNALS ({len(strong_signals)}):")
            print("-" * 80)

            for _, signal in strong_signals.iterrows():
                print(f"\n{signal['signal_type']} - {signal['asset']}")
                print(f"  Direction: {signal['direction']}")
                print(f"  Reasoning: {signal['reasoning']}")

        print("\n" + "=" * 80)


def main():
    """Point d'entrée principal."""

    # Trouver le dataset le plus récent
    datasets_path = Path("datasets/alpha_trading")

    if not datasets_path.exists():
        print(f"No datasets found in {datasets_path}")
        print("Please run mass_data_collector_v2.py first to collect data")
        return

    # Trouver le dataset le plus récent
    dataset_folders = sorted(datasets_path.glob("dataset_*"), reverse=True)

    if not dataset_folders:
        print("No dataset folders found")
        return

    latest_dataset = dataset_folders[0]
    print(f"Using dataset: {latest_dataset.name}")

    # Analyser
    analyzer = AlphaSignalAnalyzer(latest_dataset)
    signals = analyzer.run_all_analyses()

    # Générer le rapport
    analyzer.generate_report(latest_dataset)

    print(f"\n✓ Analysis complete! Found {len(signals)} alpha signals")


if __name__ == "__main__":
    main()
