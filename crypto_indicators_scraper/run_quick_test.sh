#!/bin/bash
# Quick test script for scraping a few symbols

echo "Running quick test with BTCUSDT and ETHUSDT for 2024..."

python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --no-proxy \
    --concurrent-requests 8 \
    --debug

echo "Test completed!"
