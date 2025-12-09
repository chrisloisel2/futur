#!/bin/bash

echo "🚀 Starting Crypto Data Dashboard"
echo "=================================="
echo ""

# Tuer les processus existants sur les ports
echo "🧹 Cleaning up existing processes..."
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true
sleep 2

# Démarrer l'API backend
echo ""
echo "📡 Starting API Backend (port 8000)..."
cd /Users/christopher/Desktop/futur
python api_crypto_data.py > /tmp/crypto_api.log 2>&1 &
API_PID=$!
echo "   PID: $API_PID"
echo "   Logs: /tmp/crypto_api.log"

# Attendre que l'API démarre
echo "   Waiting for API to start..."
sleep 5

# Vérifier que l'API fonctionne
echo "   Testing API health..."
HEALTH=$(curl -s http://localhost:8000/api/health | python -m json.tool 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "   ✅ API is healthy!"
else
    echo "   ❌ API failed to start. Check logs: tail -f /tmp/crypto_api.log"
    exit 1
fi

# Démarrer le frontend React
echo ""
echo "🎨 Starting React Frontend (port 3000)..."
cd /Users/christopher/Desktop/futur/frontend/alpha-dashboard

# Vérifier si node_modules existe
if [ ! -d "node_modules" ]; then
    echo "   Installing dependencies..."
    npm install
fi

echo "   Starting development server..."
npm start > /tmp/crypto_frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   PID: $FRONTEND_PID"
echo "   Logs: /tmp/crypto_frontend.log"

echo ""
echo "=================================="
echo "✅ Dashboard is starting!"
echo "=================================="
echo ""
echo "📡 API Backend:  http://localhost:8000"
echo "   Docs:         http://localhost:8000/docs"
echo "   Health:       http://localhost:8000/api/health"
echo ""
echo "🎨 React Frontend: http://localhost:3000"
echo "   (will open automatically in ~15 seconds)"
echo ""
echo "📊 Available Data:"
echo "   - 29 cryptos with 1 year of historical data"
echo "   - Real-time updates every 30 seconds"
echo "   - Interactive candlestick charts"
echo "   - MA5, MA10, MA20, MA30 indicators"
echo ""
echo "🛑 To stop:"
echo "   kill $API_PID $FRONTEND_PID"
echo "   or run: lsof -ti:8000,3000 | xargs kill -9"
echo ""
echo "📝 View logs:"
echo "   API:      tail -f /tmp/crypto_api.log"
echo "   Frontend: tail -f /tmp/crypto_frontend.log"
echo ""
echo "Press Ctrl+C to stop monitoring (processes will continue running)"
echo "=================================="

# Surveiller les logs
tail -f /tmp/crypto_api.log /tmp/crypto_frontend.log
