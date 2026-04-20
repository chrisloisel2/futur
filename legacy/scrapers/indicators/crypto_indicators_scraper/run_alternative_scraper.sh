#!/bin/bash
# Script pour scraper toutes les données alternatives

echo "=========================================="
echo "  Alternative Data Scraper for Crypto"
echo "=========================================="
echo ""

# Configuration (Modifier avec vos API keys)
SYMBOLS="BTC,ETH,BNB,SOL,XRP,ADA,DOGE,DOT,MATIC,AVAX"
LUNARCRUSH_KEY=""  # Obtenir sur https://lunarcrush.com/developers
NEWSAPI_KEY=""     # Obtenir sur https://newsapi.org/
GLASSNODE_KEY=""   # Obtenir sur https://glassnode.com/
FRED_KEY=""        # Obtenir sur https://fred.stlouisfed.org/docs/api/api_key.html

# 1. Sentiment Social
echo "📊 [1/3] Scraping Social Sentiment..."
if [ -n "$LUNARCRUSH_KEY" ]; then
    scrapy crawl crypto_sentiment \
        -a symbols=$SYMBOLS \
        -a lunarcrush_api_key=$LUNARCRUSH_KEY
else
    echo "⚠️  LunarCrush API key not set, scraping public sources only..."
    scrapy crawl crypto_sentiment -a symbols=$SYMBOLS
fi

echo ""

# 2. Geopolitical Events
echo "🌍 [2/3] Scraping Geopolitical Events..."
if [ -n "$NEWSAPI_KEY" ]; then
    scrapy crawl geopolitical \
        -a newsapi_key=$NEWSAPI_KEY
else
    echo "⚠️  NewsAPI key not set, scraping public sources only..."
    scrapy crawl geopolitical
fi

echo ""

# 3. Trends & Macro
echo "📈 [3/3] Scraping Trends & Macro Economics..."
if [ -n "$GLASSNODE_KEY" ] && [ -n "$FRED_KEY" ]; then
    scrapy crawl trends_macro \
        -a symbols=$SYMBOLS \
        -a glassnode_api_key=$GLASSNODE_KEY \
        -a fred_api_key=$FRED_KEY
elif [ -n "$FRED_KEY" ]; then
    echo "⚠️  Glassnode API key not set, scraping without on-chain data..."
    scrapy crawl trends_macro \
        -a symbols=$SYMBOLS \
        -a fred_api_key=$FRED_KEY
else
    echo "⚠️  No API keys set, scraping public sources only..."
    scrapy crawl trends_macro -a symbols=$SYMBOLS
fi

echo ""
echo "=========================================="
echo "  Alternative Data Scraping Complete!"
echo "=========================================="
echo ""
echo "📁 Check results on S3:"
echo "   s3://qbia/bourse/alternative_data/"
echo ""
echo "💡 Tip: Set API keys in this script for better data coverage"
echo ""
