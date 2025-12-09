#!/bin/bash

echo "🧪 Testing Crypto Data API"
echo "============================"
echo ""

# Démarrer l'API en arrière-plan
python api_crypto_data.py > /tmp/api_crypto.log 2>&1 &
API_PID=$!
echo "✅ API started (PID: $API_PID)"
echo "📝 Logs: /tmp/api_crypto.log"
echo ""
echo "⏳ Waiting for API to start..."
sleep 8

# Test 1: Health check
echo ""
echo "1️⃣ Testing /api/health"
curl -s http://localhost:8000/api/health | python -m json.tool || echo "❌ Failed"

# Test 2: Liste des cryptos
echo ""
echo "2️⃣ Testing /api/cryptos"
curl -s http://localhost:8000/api/cryptos | python -m json.tool | head -30 || echo "❌ Failed"

# Test 3: Metrics BTC
echo ""
echo "3️⃣ Testing /api/metrics/BTC%2FUSDT"
curl -s "http://localhost:8000/api/metrics/BTC%2FUSDT" | python -m json.tool || echo "❌ Failed"

# Test 4: Overview
echo ""
echo "4️⃣ Testing /api/overview"
curl -s http://localhost:8000/api/overview | python -m json.tool | head -40 || echo "❌ Failed"

echo ""
echo "============================"
echo "✅ Tests completed!"
echo ""
echo "💡 To stop the API: kill $API_PID"
echo "📖 API Docs: http://localhost:8000/docs"
echo "🌐 API URL: http://localhost:8000"
echo ""
