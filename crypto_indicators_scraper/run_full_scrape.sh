#!/bin/bash
# Full scraping script for all symbols and years

echo "Starting full scrape of all symbols from S3..."
echo "This will scrape indicators for all years (2017-2025)"
echo ""

# Optional: Add your API keys here
# CRYPTOCOMPARE_API_KEY="your_key_here"
# TAAPI_API_KEY="your_key_here"

python run_scraper.py \
    --start-year 2017 \
    --end-year 2025 \
    --proxy-enabled \
    --concurrent-requests 32 \
    --batch-size 1000

echo ""
echo "Full scrape completed!"
echo "Check S3 bucket: s3://qbia/bourse/indicators/"
