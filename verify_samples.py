#!/usr/bin/env python3
"""
Verify that processed crypto data exists in S3 and show samples
"""
import awswrangler as wr
import pandas as pd

# List first 30 crypto symbols processed
print("=== Checking processed cryptos in S3 ===\n")

processed_prefix = "s3://qbia/bourse/processed/market/"
files = wr.s3.list_objects(processed_prefix)

if not files:
    print("NO processed files found yet!")
    print(f"Pipeline is still running. Check {processed_prefix}")
    exit(1)

# Group by symbol
symbols = {}
for f in files:
    parts = f.split("symbol=")
    if len(parts) > 1:
        sym = parts[1].split("/")[0]
        if sym not in symbols:
            symbols[sym] = []
        symbols[sym].append(f)

print(f"Found {len(symbols)} processed symbols\n")
print("=" * 80)

# Show first 30 lines of each symbol
for i, (symbol, paths) in enumerate(sorted(symbols.items())[:30], 1):
    print(f"\n{i}. {symbol} ({len(paths)} file(s))")
    print("-" * 80)

    # Read first file for this symbol
    try:
        df = wr.s3.read_parquet(path=paths[0])
        print(df.head(30).to_string())
        print(f"\nTotal rows: {len(df)}, Columns: {len(df.columns)}")
    except Exception as e:
        print(f"Error reading: {e}")

    print("=" * 80)

print(f"\n✓ Successfully processed {len(symbols)} cryptocurrencies")
print(f"✓ Data stored in: {processed_prefix}")
