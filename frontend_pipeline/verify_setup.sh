#!/bin/bash

echo "🔍 Verification du Setup Crypto Dashboard"
echo "==========================================="
echo ""

ERRORS=0
WARNINGS=0

# Fonction pour afficher les résultats
check_pass() {
    echo "✅ $1"
}

check_fail() {
    echo "❌ $1"
    ((ERRORS++))
}

check_warn() {
    echo "⚠️  $1"
    ((WARNINGS++))
}

# 1. Vérifier Python
echo "1️⃣ Checking Python..."
if command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1)
    check_pass "Python found: $PYTHON_VERSION"
else
    check_fail "Python not found"
fi

# 2. Vérifier les dépendances Python
echo ""
echo "2️⃣ Checking Python dependencies..."
REQUIRED_PACKAGES=("fastapi" "uvicorn" "pandas" "aiohttp")
for package in "${REQUIRED_PACKAGES[@]}"; do
    if python -c "import $package" &> /dev/null; then
        check_pass "$package installed"
    else
        check_fail "$package NOT installed (run: pip install $package)"
    fi
done

# 3. Vérifier les données historiques
echo ""
echo "3️⃣ Checking historical data..."
DATA_DIR="datasets/historical_crypto"
if [ -d "$DATA_DIR" ]; then
    FILE_COUNT=$(ls -1 "$DATA_DIR"/*.parquet 2>/dev/null | wc -l | tr -d ' ')
    if [ "$FILE_COUNT" -gt 0 ]; then
        check_pass "Found $FILE_COUNT crypto data files"
        TOTAL_SIZE=$(du -sh "$DATA_DIR" | cut -f1)
        echo "   📦 Total size: $TOTAL_SIZE"
    else
        check_fail "No data files found in $DATA_DIR"
        echo "   💡 Run: python collect_historical_crypto.py"
    fi
else
    check_fail "Data directory not found: $DATA_DIR"
fi

# 4. Vérifier Node.js
echo ""
echo "4️⃣ Checking Node.js..."
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version)
    check_pass "Node.js found: $NODE_VERSION"
else
    check_warn "Node.js not found (required for frontend)"
fi

# 5. Vérifier le frontend
echo ""
echo "5️⃣ Checking React frontend..."
FRONTEND_DIR="frontend/alpha-dashboard"
if [ -d "$FRONTEND_DIR" ]; then
    check_pass "Frontend directory exists"

    # Vérifier package.json
    if [ -f "$FRONTEND_DIR/package.json" ]; then
        check_pass "package.json found"
    else
        check_fail "package.json not found"
    fi

    # Vérifier node_modules
    if [ -d "$FRONTEND_DIR/node_modules" ]; then
        check_pass "node_modules installed"
    else
        check_warn "node_modules not found (run: npm install)"
    fi

    # Vérifier les composants
    if [ -f "$FRONTEND_DIR/src/components/RealTimeCandlestickChart.tsx" ]; then
        check_pass "RealTimeCandlestickChart.tsx exists"
    else
        check_fail "RealTimeCandlestickChart.tsx not found"
    fi
else
    check_fail "Frontend directory not found: $FRONTEND_DIR"
fi

# 6. Vérifier les scripts
echo ""
echo "6️⃣ Checking scripts..."
SCRIPTS=("api_crypto_data.py" "collect_historical_crypto.py" "start_crypto_dashboard.sh")
for script in "${SCRIPTS[@]}"; do
    if [ -f "$script" ]; then
        check_pass "$script exists"
    else
        check_fail "$script not found"
    fi
done

# 7. Vérifier si l'API tourne déjà
echo ""
echo "7️⃣ Checking if API is running..."
if lsof -ti:8000 &> /dev/null; then
    check_warn "API already running on port 8000"
    echo "   💡 To restart: lsof -ti:8000 | xargs kill -9"
else
    check_pass "Port 8000 available"
fi

# 8. Vérifier si le frontend tourne
echo ""
echo "8️⃣ Checking if frontend is running..."
if lsof -ti:3000 &> /dev/null; then
    check_warn "Frontend already running on port 3000"
    echo "   💡 To restart: lsof -ti:3000 | xargs kill -9"
else
    check_pass "Port 3000 available"
fi

# 9. Test rapide de l'API (si elle tourne)
echo ""
echo "9️⃣ Testing API (if running)..."
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    check_pass "API is responding"

    # Tester l'endpoint cryptos
    if curl -s http://localhost:8000/api/cryptos | grep -q "success"; then
        check_pass "API returns crypto data"
    else
        check_warn "API responding but no crypto data"
    fi
else
    check_warn "API not running (start with: ./start_crypto_dashboard.sh)"
fi

# 10. Vérifier la documentation
echo ""
echo "🔟 Checking documentation..."
DOCS=("README_CRYPTO_DASHBOARD.md" "COMPLETE_SETUP.md")
for doc in "${DOCS[@]}"; do
    if [ -f "$doc" ]; then
        check_pass "$doc exists"
    else
        check_warn "$doc not found"
    fi
done

# Résumé final
echo ""
echo "==========================================="
echo "📊 VERIFICATION SUMMARY"
echo "==========================================="
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo "🎉 PERFECT! Everything is set up correctly!"
    echo ""
    echo "Next steps:"
    echo "  1. Start the dashboard: ./start_crypto_dashboard.sh"
    echo "  2. Open http://localhost:3000 in your browser"
    echo "  3. Click on any crypto to see candlestick charts"
    echo ""
elif [ $ERRORS -eq 0 ]; then
    echo "✅ GOOD! Setup is mostly complete"
    echo ""
    echo "⚠️  Warnings: $WARNINGS"
    echo "   These are minor issues that won't prevent the dashboard from working"
    echo ""
    echo "You can start the dashboard with: ./start_crypto_dashboard.sh"
    echo ""
else
    echo "❌ ISSUES FOUND"
    echo ""
    echo "Errors: $ERRORS"
    echo "Warnings: $WARNINGS"
    echo ""
    echo "Please fix the errors above before starting the dashboard"
    echo ""
fi

# Afficher les commandes utiles
echo "==========================================="
echo "📝 USEFUL COMMANDS"
echo "==========================================="
echo ""
echo "Start dashboard:       ./start_crypto_dashboard.sh"
echo "Collect data:          python collect_historical_crypto.py"
echo "Validate data:         python validate_crypto_data.py"
echo "Test API:              ./test_api.sh"
echo "Stop everything:       lsof -ti:8000,3000 | xargs kill -9"
echo ""
echo "API Health:            curl http://localhost:8000/api/health"
echo "API Docs:              http://localhost:8000/docs"
echo "Frontend:              http://localhost:3000"
echo ""

exit $ERRORS
