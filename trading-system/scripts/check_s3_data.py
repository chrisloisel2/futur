#!/usr/bin/env python3
"""
Check S3 Data Structure for Trading System
"""
import sys
import pandas as pd
import pyarrow.parquet as pq
import s3fs

def check_schema(s3_path, name):
    print(f"\n{'='*60}")
    print(f"Checking {name}: {s3_path}")
    print('='*60)
    
    try:
        fs = s3fs.S3FileSystem()
        
        # Read first file to check schema
        files = fs.glob(s3_path)
        if not files:
            print(f"❌ No files found at {s3_path}")
            return None
            
        first_file = f"s3://{files[0]}"
        print(f"\n📄 Sample file: {first_file}")
        
        # Read schema
        table = pq.read_table(first_file, filesystem=fs)
        print(f"\n📋 Schema:")
        print(table.schema)
        
        # Read sample data
        df = table.to_pandas()
        print(f"\n📊 Shape: {df.shape}")
        print(f"\n🔍 First 3 rows:")
        print(df.head(3))
        
        print(f"\n📈 Columns: {list(df.columns)}")
        
        return df
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

# Check both locations
processed = check_schema(
    "qbia/bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT/year=2024/*.parquet",
    "PROCESSED DATA"
)

raw = check_schema(
    "qbia/bourse/raw/market/interval=1m/quote=USDT/symbol=BTCUSDT/year=2024/*.parquet",
    "RAW DATA"
)

print("\n" + "="*60)
print("RECOMMENDATION")
print("="*60)

if processed is not None and raw is not None:
    print("\n✅ Both datasets available")
    print(f"\nProcessed columns: {len(processed.columns)}")
    print(f"Raw columns: {len(raw.columns)}")
    
    # Check for required columns
    required = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
    
    processed_has = all(col in processed.columns for col in required)
    raw_has = all(col in raw.columns for col in required)
    
    if processed_has and raw_has:
        print("\n✅ Both have required OHLCV columns")
        print("\n🎯 RECOMMENDATION: Use PROCESSED")
        print("   - Likely cleaned and validated")
        print("   - Better compression (.zstd)")
        print("   - Partitioned by year")
    elif processed_has:
        print("\n🎯 RECOMMENDATION: Use PROCESSED")
    elif raw_has:
        print("\n🎯 RECOMMENDATION: Use RAW")
    else:
        print("\n⚠️  Need to check column names")
        
elif processed is not None:
    print("\n🎯 RECOMMENDATION: Use PROCESSED (only option)")
elif raw is not None:
    print("\n🎯 RECOMMENDATION: Use RAW (only option)")
else:
    print("\n❌ No data accessible")
