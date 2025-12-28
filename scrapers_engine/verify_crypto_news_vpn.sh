#!/bin/bash
# Script de vérification pour crypto_news avec VPN MongoDB

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║           VERIFICATION: crypto_news avec VPN MongoDB                         ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ERRORS=0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Vérification MongoDB"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if MongoDB is running
if python3 -c "from pymongo import MongoClient; MongoClient('mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net//', serverSelectionTimeoutMS=2000).admin.command('ping')" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} MongoDB est en cours d'exécution"
else
    echo -e "${RED}✗${NC} MongoDB n'est PAS en cours d'exécution"
    echo ""
    echo "Pour démarrer MongoDB:"
    echo "  brew services start mongodb-community@7.0"
    ((ERRORS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. Vérification VPN dans MongoDB"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    python3 << 'EOF'
from pymongo import MongoClient

try:
    client = MongoClient('mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net//', serverSelectionTimeoutMS=2000)
    db = client['scrapers_db']
    collection = db['vpn_proxies']

    total = collection.count_documents({})
    active = collection.count_documents({'is_active': True})

    print(f"Total VPN: {total:,}")
    print(f"VPN actifs: {active:,}")
    print("")

    if active == 0:
        print("⚠️  ATTENTION: Aucun VPN actif!")
        print("   Lancez: scrapy crawl free_vpn_scraper_enhanced")
        exit(1)
    elif active < 100:
        print(f"⚠️  ATTENTION: Seulement {active} VPN actifs (recommandé: >1000)")
        print("   Lancez: scrapy crawl free_vpn_scraper_enhanced")
    else:
        print(f"✅ {active:,} VPN actifs disponibles")

        # Afficher 3 exemples
        print("")
        print("Exemples de VPN:")
        for vpn in collection.find({'is_active': True}).limit(3):
            print(f"  - {vpn['proxy_url']}")

    client.close()

except Exception as e:
    print(f"❌ Erreur: {e}")
    exit(1)
EOF

    if [ $? -ne 0 ]; then
        ((ERRORS++))
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Vérification Configuration settings.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check MongoDB middleware in settings
if grep -q "proxy_rotator_mongodb.MongoDBProxyRotatorMiddleware" settings.py; then
    echo -e "${GREEN}✓${NC} MongoDB middleware configuré"
else
    echo -e "${RED}✗${NC} MongoDB middleware NON configuré"
    echo ""
    echo "Ajoutez dans settings.py:"
    echo "  'middlewares.proxy_rotator_mongodb.MongoDBProxyRotatorMiddleware': 350,"
    ((ERRORS++))
fi

# Check PROXY_ENABLED
if grep -q "PROXY_ENABLED = True" settings.py; then
    echo -e "${GREEN}✓${NC} PROXY_ENABLED = True"
else
    echo -e "${YELLOW}⚠${NC} PROXY_ENABLED non défini ou False"
fi

# Check VPN_DELETE_ON_FAILURE
if grep -q "VPN_DELETE_ON_FAILURE = True" settings.py; then
    echo -e "${GREEN}✓${NC} VPN_DELETE_ON_FAILURE = True (auto-delete activé)"
else
    echo -e "${YELLOW}⚠${NC} VPN_DELETE_ON_FAILURE non défini"
fi

# Check MongoDB URI
if grep -q "MONGODB_URI = 'mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net//'" settings.py; then
    echo -e "${GREEN}✓${NC} MONGODB_URI configuré"
else
    echo -e "${RED}✗${NC} MONGODB_URI non configuré"
    ((ERRORS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Vérification Imports Python"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 << 'EOF'
import sys

errors = 0

# Test imports
try:
    from utils.vpn_manager import VPNManager
    print("✅ VPNManager importé")
except Exception as e:
    print(f"❌ Erreur import VPNManager: {e}")
    errors += 1

try:
    from middlewares.proxy_rotator_mongodb import MongoDBProxyRotatorMiddleware
    print("✅ MongoDBProxyRotatorMiddleware importé")
except Exception as e:
    print(f"❌ Erreur import MongoDBProxyRotatorMiddleware: {e}")
    errors += 1

try:
    from spiders.crypto_news import CryptoNewsSpider
    print("✅ CryptoNewsSpider importé")
except Exception as e:
    print(f"❌ Erreur import CryptoNewsSpider: {e}")
    errors += 1

if errors > 0:
    exit(1)
EOF

if [ $? -ne 0 ]; then
    ((ERRORS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. Test VPNManager"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    python3 << 'EOF'
from utils.vpn_manager import VPNManager

try:
    vpn_manager = VPNManager()
    vpn_manager.connect()

    # Get stats
    stats = vpn_manager.get_stats()

    print("VPNManager Stats:")
    print(f"  Total proxies: {stats.get('total_proxies', 0):,}")
    print(f"  Active proxies: {stats.get('active_proxies', 0):,}")
    print(f"  Reliable proxies: {stats.get('reliable_proxies', 0):,}")
    print("")

    # Get random proxy
    proxy = vpn_manager.get_random_proxy()
    if proxy:
        print(f"Random proxy test: {proxy}")
        print("")
        print("✅ VPNManager fonctionne correctement")
    else:
        print("❌ Impossible de récupérer un proxy")
        exit(1)

    vpn_manager.disconnect()

except Exception as e:
    print(f"❌ Erreur VPNManager: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
EOF

    if [ $? -ne 0 ]; then
        ((ERRORS++))
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. Résumé"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ TOUTES LES VÉRIFICATIONS PASSÉES${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "🎉 Le spider crypto_news est prêt à utiliser les VPN MongoDB!"
    echo ""
    echo "Pour lancer le spider:"
    echo -e "  ${BLUE}scrapy crawl crypto_news${NC}"
    echo ""
    echo "Logs attendus:"
    echo "  🔄 Initialisation du système de rotation de proxies MongoDB"
    echo "  🗑️ Mode AUTO-DELETE activé: les VPN défaillants seront supprimés immédiatement"
    echo "  ✅ 200 proxies chargés depuis MongoDB"
    echo ""
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ VÉRIFICATION ÉCHOUÉE ($ERRORS erreurs)${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Veuillez corriger les erreurs ci-dessus."
    echo ""
    echo "Documentation:"
    echo "  - CRYPTO_NEWS_VPN_SETUP.md"
    echo "  - VPN_AUTO_DELETE_GUIDE.md"
    echo ""
    exit 1
fi
