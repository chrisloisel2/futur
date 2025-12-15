#!/bin/bash

echo "🧪 Test des endpoints de training API"
echo "========================================"
echo ""

# Couleurs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

API_URL="http://localhost:8000"

# Test 1: Health check
echo "1. Test Health Check..."
if curl -s "${API_URL}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ API is running${NC}"
else
    echo -e "${RED}❌ API is not running. Start it with: python api_server.py${NC}"
    exit 1
fi

# Test 2: Training configs
echo ""
echo "2. Test GET /training/configs..."
RESPONSE=$(curl -s "${API_URL}/training/configs")
if echo "$RESPONSE" | grep -q "success"; then
    echo -e "${GREEN}✅ Training configs endpoint works${NC}"
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
else
    echo -e "${RED}❌ Training configs endpoint failed${NC}"
    echo "Response: $RESPONSE"
fi

# Test 3: Training jobs
echo ""
echo "3. Test GET /training/jobs..."
RESPONSE=$(curl -s "${API_URL}/training/jobs")
if echo "$RESPONSE" | grep -q "success"; then
    echo -e "${GREEN}✅ Training jobs endpoint works${NC}"
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
else
    echo -e "${RED}❌ Training jobs endpoint failed${NC}"
    echo "Response: $RESPONSE"
fi

# Test 4: Model versions
echo ""
echo "4. Test GET /training/models..."
RESPONSE=$(curl -s "${API_URL}/training/models")
if echo "$RESPONSE" | grep -q "success"; then
    echo -e "${GREEN}✅ Model versions endpoint works${NC}"
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
else
    echo -e "${RED}❌ Model versions endpoint failed${NC}"
    echo "Response: $RESPONSE"
fi

echo ""
echo "========================================"
echo "🧪 Tests terminés"
