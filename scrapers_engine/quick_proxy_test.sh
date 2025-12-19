#!/bin/bash
# Script de test rapide du système de proxies

echo "======================================================================"
echo "🔄 Test Rapide du Système de Rotation de Proxies"
echo "======================================================================"
echo ""

cd "$(dirname "$0")"

echo "📍 Répertoire: $(pwd)"
echo ""

echo "1️⃣ Test du système de proxies (30 requêtes)..."
echo "   Ce test va vérifier que les proxies changent l'IP"
echo ""

python3 test_proxies.py

echo ""
echo "======================================================================"
echo "✅ Test terminé!"
echo "======================================================================"
echo ""
echo "📋 Prochaines étapes:"
echo "   1. Si le test a réussi, lancez vos scrapers normalement:"
echo "      scrapy crawl crypto_news"
echo "      scrapy crawl whale_alert"
echo "      scrapy crawl bitcointalk"
echo ""
echo "   2. Les proxies sont automatiquement activés pour tous les spiders"
echo ""
echo "   3. Pour désactiver temporairement:"
echo "      PROXY_ENABLED=False scrapy crawl my_spider"
echo ""
echo "   4. Voir la documentation complète:"
echo "      cat PROXY_SYSTEM_GUIDE.md"
echo ""
