#!/usr/bin/env python3
"""
Script pour vérifier les données disponibles sur S3.
"""
import boto3
from collections import defaultdict

def check_s3_structure():
    """Check S3 bucket structure and available data."""
    s3_client = boto3.client('s3', region_name='us-east-1')
    bucket = 'qbia'

    print("=" * 80)
    print("VÉRIFICATION DES DONNÉES S3")
    print("=" * 80)
    print()

    # Check source data (OHLCV)
    print("📊 DONNÉES SOURCES (OHLCV):")
    print("-" * 80)

    source_prefix = 'bourse/mintrad'
    years_data = defaultdict(list)

    try:
        # List all years
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=source_prefix + '/',
            Delimiter='/'
        )

        for prefix_obj in response.get('CommonPrefixes', []):
            folder = prefix_obj['Prefix'].rstrip('/').split('/')[-1]
            if 'klines_1m_TRADING_USDT_' in folder:
                year = folder.split('_')[-1]

                # Count symbols for this year
                year_response = s3_client.list_objects_v2(
                    Bucket=bucket,
                    Prefix=f"{source_prefix}/{folder}/",
                    MaxKeys=1000
                )

                symbol_count = len([obj for obj in year_response.get('Contents', [])
                                   if obj['Key'].endswith('.parquet')])

                years_data[year].append(symbol_count)
                print(f"  {year}: {symbol_count} cryptos")

        print()
        print(f"Total années disponibles: {len(years_data)}")

    except Exception as e:
        print(f"Erreur: {e}")

    print()

    # Check indicators data
    print("📈 DONNÉES INDICATEURS SCRAPÉES:")
    print("-" * 80)

    indicators_prefix = 'bourse/indicators'
    indicators_data = defaultdict(int)

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=indicators_prefix + '/',
            Delimiter='/'
        )

        for prefix_obj in response.get('CommonPrefixes', []):
            folder = prefix_obj['Prefix'].rstrip('/').split('/')[-1]

            # Count files in this folder
            folder_response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=f"{indicators_prefix}/{folder}/",
                MaxKeys=1000
            )

            file_count = len([obj for obj in folder_response.get('Contents', [])
                             if obj['Key'].endswith('.parquet')])

            indicators_data[folder] = file_count
            print(f"  {folder}: {file_count} fichiers")

        if not indicators_data:
            print("  ⚠️  Aucune donnée d'indicateurs trouvée (pas encore scrapée)")
        else:
            print()
            print(f"Total dossiers d'indicateurs: {len(indicators_data)}")
            print(f"Total fichiers d'indicateurs: {sum(indicators_data.values())}")

    except Exception as e:
        print(f"Erreur: {e}")

    print()
    print("=" * 80)
    print()

    # Show some examples
    print("📝 EXEMPLES DE FICHIERS DISPONIBLES:")
    print("-" * 80)

    try:
        # Get some examples from 2024
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix='bourse/mintrad/klines_1m_TRADING_USDT_2024/',
            MaxKeys=5
        )

        print("Sources OHLCV (2024):")
        for obj in response.get('Contents', [])[:5]:
            key = obj['Key']
            size_mb = obj['Size'] / (1024 * 1024)
            print(f"  s3://{bucket}/{key} ({size_mb:.2f} MB)")

    except Exception as e:
        print(f"Erreur: {e}")

    print()
    print("=" * 80)


if __name__ == '__main__':
    check_s3_structure()
