"""
Script de validation des données historiques crypto
Affiche des statistiques et exemples pour vérifier la qualité des données
"""
import pandas as pd
from pathlib import Path
import numpy as np

def validate_crypto_data():
    """Valider les données collectées."""

    data_dir = Path("datasets/historical_crypto")

    print("\n" + "="*80)
    print("📊 VALIDATION DES DONNÉES HISTORIQUES CRYPTO")
    print("="*80)

    # Lister tous les fichiers parquet
    parquet_files = sorted(data_dir.glob("*_1h_*.parquet"))

    print(f"\n✅ {len(parquet_files)} fichiers trouvés\n")

    # Statistiques globales
    total_rows = 0
    total_size_mb = 0

    # Validation de quelques fichiers
    samples = parquet_files[:5]  # Vérifier les 5 premiers

    for file in samples:
        df = pd.read_parquet(file)
        size_mb = file.stat().st_size / 1024 / 1024

        symbol = file.stem.split('_1h_')[0].replace('_', '/')

        print(f"{'='*80}")
        print(f"Crypto: {symbol}")
        print(f"{'='*80}")
        print(f"Lignes: {len(df):,}")
        print(f"Colonnes: {list(df.columns)}")
        print(f"Période: {df['timestamp'].min()} → {df['timestamp'].max()}")
        print(f"Taille: {size_mb:.2f} MB")

        # Statistiques de prix
        print(f"\n📈 Statistiques de prix:")
        print(f"  Prix min:     ${df['close'].min():.8f}")
        print(f"  Prix max:     ${df['close'].max():.8f}")
        print(f"  Prix moyen:   ${df['close'].mean():.8f}")
        print(f"  Prix actuel:  ${df['close'].iloc[-1]:.8f}")

        # Variation
        price_change = ((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100
        print(f"  Variation 1Y: {price_change:+.2f}%")

        # Volume
        print(f"\n📊 Volume:")
        print(f"  Volume moyen: {df['volume'].mean():,.2f}")
        print(f"  Volume total: {df['volume'].sum():,.2f}")

        # Qualité des données
        print(f"\n✅ Qualité:")
        print(f"  Valeurs manquantes: {df.isnull().sum().sum()}")
        print(f"  Doublons: {df.duplicated().sum()}")

        # Aperçu des dernières données
        print(f"\n🔍 Dernières 3 heures:")
        print(df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(3).to_string(index=False))
        print()

    # Statistiques globales
    print("\n" + "="*80)
    print("📊 STATISTIQUES GLOBALES")
    print("="*80)

    all_data = []
    for file in parquet_files:
        df = pd.read_parquet(file)
        total_rows += len(df)
        total_size_mb += file.stat().st_size / 1024 / 1024

        symbol = file.stem.split('_1h_')[0].replace('_', '/')

        all_data.append({
            'symbol': symbol,
            'rows': len(df),
            'start': df['timestamp'].min(),
            'end': df['timestamp'].max(),
            'price_min': df['close'].min(),
            'price_max': df['close'].max(),
            'avg_volume': df['volume'].mean(),
            'total_trades': df['trades'].sum() if 'trades' in df.columns else 0
        })

    summary_df = pd.DataFrame(all_data)

    print(f"\nTotal fichiers: {len(parquet_files)}")
    print(f"Total lignes: {total_rows:,}")
    print(f"Taille totale: {total_size_mb:.2f} MB")
    print(f"Moyenne par crypto: {total_rows // len(parquet_files):,} lignes")

    # Top cryptos par volume moyen
    print(f"\n📊 Top 10 cryptos par volume moyen:")
    top_volume = summary_df.nlargest(10, 'avg_volume')[['symbol', 'avg_volume']]
    for idx, row in top_volume.iterrows():
        print(f"  {row['symbol']:15s}: {row['avg_volume']:,.0f}")

    # Cryptos avec plus grande variation de prix
    summary_df['price_range'] = summary_df['price_max'] - summary_df['price_min']
    print(f"\n💰 Top 10 cryptos par variation de prix (en valeur absolue):")
    top_range = summary_df.nlargest(10, 'price_range')[['symbol', 'price_min', 'price_max', 'price_range']]
    for idx, row in top_range.iterrows():
        print(f"  {row['symbol']:15s}: ${row['price_min']:.4f} → ${row['price_max']:.4f} (Δ ${row['price_range']:.4f})")

    print("\n" + "="*80)
    print("✅ VALIDATION TERMINÉE")
    print("="*80)
    print("\n🎉 Les données sont prêtes à être utilisées pour:")
    print("  • Entraînement de modèles ML de prédiction de prix")
    print("  • Backtesting de stratégies de trading algorithmique")
    print("  • Analyse de corrélations entre cryptos")
    print("  • Détection de patterns de marché")
    print("  • Génération de features techniques (RSI, MACD, Bollinger, etc.)")
    print("\n💡 Pour charger une crypto:")
    print("  import pandas as pd")
    print("  df = pd.read_parquet('datasets/historical_crypto/BTC_USDT_1h_20251130.parquet')")
    print()


if __name__ == "__main__":
    validate_crypto_data()
