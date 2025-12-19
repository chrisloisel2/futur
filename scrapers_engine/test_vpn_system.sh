#!/bin/bash
# Test script for VPN MongoDB system

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║                   VPN MONGODB SYSTEM TEST                                    ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Checking MongoDB"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if MongoDB is running
if python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000).admin.command('ping')" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} MongoDB is running"
else
    echo -e "${RED}✗${NC} MongoDB is NOT running"
    echo ""
    echo "Please start MongoDB:"
    echo "  macOS: brew services start mongodb-community@7.0"
    echo "  Linux: sudo systemctl start mongodb"
    ((ERRORS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. Checking Python Dependencies"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 -c "import pymongo" 2>/dev/null && echo -e "${GREEN}✓${NC} pymongo installed" || { echo -e "${RED}✗${NC} pymongo not installed (pip install pymongo)"; ((ERRORS++)); }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Checking VPN Files"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

[ -f "spiders/free_vpn_scraper.py" ] && echo -e "${GREEN}✓${NC} Spider file exists" || { echo -e "${RED}✗${NC} Spider file missing"; ((ERRORS++)); }
[ -f "pipelines/vpn_mongodb_pipeline.py" ] && echo -e "${GREEN}✓${NC} Pipeline file exists" || { echo -e "${RED}✗${NC} Pipeline file missing"; ((ERRORS++)); }
[ -f "utils/vpn_manager.py" ] && echo -e "${GREEN}✓${NC} VPN Manager exists" || { echo -e "${RED}✗${NC} VPN Manager missing"; ((ERRORS++)); }
[ -f "middlewares/proxy_rotator_mongodb.py" ] && echo -e "${GREEN}✓${NC} MongoDB Middleware exists" || { echo -e "${RED}✗${NC} MongoDB Middleware missing"; ((ERRORS++)); }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Checking MongoDB VPN Count"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    python3 << 'EOF'
from pymongo import MongoClient

try:
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000)
    db = client['scrapers_db']
    collection = db['vpn_proxies']

    total = collection.count_documents({})
    active = collection.count_documents({'is_active': True})

    print(f"Total VPNs in database: {total}")
    print(f"Active VPNs: {active}")

    if total == 0:
        print("")
        print("⚠️  No VPNs found in database!")
        print("   Run this to collect VPNs:")
        print("   scrapy crawl free_vpn_scraper")
        exit(1)
    elif active == 0:
        print("")
        print("⚠️  No active VPNs!")
        print("   All VPNs are marked inactive. Refresh:")
        print("   scrapy crawl free_vpn_scraper")
        exit(1)
    else:
        print("")
        print(f"✅ System ready with {active} active VPNs")

        # Show sample
        print("")
        print("Sample VPNs:")
        for vpn in collection.find({'is_active': True}).limit(5):
            print(f"  - {vpn['proxy_url']} (source: {vpn['source']})")

except Exception as e:
    print(f"❌ Error checking MongoDB: {e}")
    exit(1)
EOF

    if [ $? -ne 0 ]; then
        ((ERRORS++))
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. Testing VPNManager"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    python3 << 'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from utils.vpn_manager import VPNManager

try:
    vpn_manager = VPNManager()
    vpn_manager.connect()

    # Get stats
    stats = vpn_manager.get_stats()

    print(f"VPNManager Stats:")
    print(f"  Total proxies: {stats.get('total_proxies', 0)}")
    print(f"  Active proxies: {stats.get('active_proxies', 0)}")
    print(f"  Reliable proxies: {stats.get('reliable_proxies', 0)}")
    print(f"  Avg success rate: {stats.get('avg_success_rate', 0):.1%}")

    # Get random proxy
    proxy = vpn_manager.get_random_proxy()
    if proxy:
        print(f"  Random proxy: {proxy}")
        print("")
        print("✅ VPNManager working correctly")
    else:
        print("")
        print("❌ Failed to get random proxy")
        exit(1)

    vpn_manager.disconnect()

except Exception as e:
    print(f"❌ Error testing VPNManager: {e}")
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
echo "6. Final Report"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ ALL CHECKS PASSED${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "🎉 VPN MongoDB system is ready!"
    echo ""
    echo "Next steps:"
    echo "  1. Update settings.py to use MongoDB middleware (see VPN_MONGODB_GUIDE.md)"
    echo "  2. Run your spiders: scrapy crawl crypto_news"
    echo "  3. Refresh VPNs periodically: scrapy crawl free_vpn_scraper"
    echo ""
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ VERIFICATION FAILED ($ERRORS errors)${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Please fix the errors above."
    echo ""
    echo "Common issues:"
    echo "  - MongoDB not running: brew services start mongodb-community@7.0"
    echo "  - PyMongo not installed: pip install pymongo"
    echo "  - No VPNs in database: scrapy crawl free_vpn_scraper"
    echo ""
    exit 1
fi
