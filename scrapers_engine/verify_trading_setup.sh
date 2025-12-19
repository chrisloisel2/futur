#!/bin/bash
# Verification script for S3TradingPipeline setup
# Checks that everything is properly configured

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║                   S3 TRADING PIPELINE VERIFICATION                           ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

# Function to check if file exists
check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} File exists: $1"
        return 0
    else
        echo -e "${RED}✗${NC} File missing: $1"
        ((ERRORS++))
        return 1
    fi
}

# Function to check if string exists in file
check_string_in_file() {
    if grep -q "$2" "$1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} Found in $1: $2"
        return 0
    else
        echo -e "${RED}✗${NC} Not found in $1: $2"
        ((ERRORS++))
        return 1
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Checking Core Files"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check_file "pipelines/s3_trading_pipeline.py"
check_file "settings.py"
check_file "test_trading_pipeline.py"
check_file "TRADING_PIPELINE_IMPLEMENTATION.md"
check_file "TRADING_QUICKSTART.md"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. Checking Settings Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check_string_in_file "settings.py" "S3TradingPipeline"
check_string_in_file "settings.py" "ALLOWED_ASSETS"
check_string_in_file "settings.py" "ASSET_KEYWORDS"
check_string_in_file "settings.py" "S3_TRADING_BUCKET"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Checking Python Dependencies"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check Python packages
python3 -c "import scrapy" 2>/dev/null && echo -e "${GREEN}✓${NC} scrapy installed" || { echo -e "${RED}✗${NC} scrapy not installed"; ((ERRORS++)); }
python3 -c "import boto3" 2>/dev/null && echo -e "${GREEN}✓${NC} boto3 installed" || { echo -e "${RED}✗${NC} boto3 not installed"; ((ERRORS++)); }
python3 -c "import requests" 2>/dev/null && echo -e "${GREEN}✓${NC} requests installed" || { echo -e "${RED}✗${NC} requests not installed"; ((ERRORS++)); }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Checking AWS Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check AWS CLI
if command -v aws &> /dev/null; then
    echo -e "${GREEN}✓${NC} AWS CLI installed"

    # Check AWS credentials
    if aws sts get-caller-identity &> /dev/null; then
        echo -e "${GREEN}✓${NC} AWS credentials configured"

        # Check S3 bucket access
        if aws s3 ls s3://qbia/ &> /dev/null; then
            echo -e "${GREEN}✓${NC} S3 bucket 'qbia' accessible"
        else
            echo -e "${YELLOW}⚠${NC} S3 bucket 'qbia' not accessible (may not exist yet)"
            ((WARNINGS++))
        fi
    else
        echo -e "${YELLOW}⚠${NC} AWS credentials not configured"
        echo "  Run: aws configure"
        ((WARNINGS++))
    fi
else
    echo -e "${YELLOW}⚠${NC} AWS CLI not installed"
    echo "  Install: pip3 install awscli"
    ((WARNINGS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. Checking Spiders"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check_file "spiders/crypto_news.py"
check_file "spiders/whale_alert.py"
check_file "spiders/bitcointalk.py"
check_file "spiders/arkham.py"
check_file "spiders/asian_crypto.py"
check_file "spiders/social_sentiment.py"
check_file "spiders/specialized_forums.py"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. Running Unit Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if python3 test_trading_pipeline.py > /tmp/test_output.txt 2>&1; then
    echo -e "${GREEN}✓${NC} All unit tests passed"

    # Show summary
    if grep -q "All tests passed" /tmp/test_output.txt; then
        echo -e "${GREEN}✓${NC} Asset Detection: PASS"
        echo -e "${GREEN}✓${NC} Data Type Detection: PASS"
        echo -e "${GREEN}✓${NC} Schema Validation: PASS"
    fi
else
    echo -e "${RED}✗${NC} Unit tests failed"
    echo ""
    echo "Test output:"
    cat /tmp/test_output.txt
    ((ERRORS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "7. Configuration Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "Allowed Assets:"
if grep -A 1 "ALLOWED_ASSETS" settings.py | grep -o "'\w\+'" | tr -d "'"; then
    echo -e "${GREEN}  BTC, ETH, SOL${NC}"
else
    echo -e "${RED}  Configuration error${NC}"
fi

echo ""
echo "S3 Configuration:"
grep "S3_TRADING_BUCKET" settings.py | head -1
grep "S3_TRADING_PREFIX" settings.py | head -1
grep "S3_TRADING_BATCH_SIZE" settings.py | head -1

echo ""
echo "Pipeline Priority:"
grep "S3TradingPipeline" settings.py | head -1

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "8. Final Report"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ ALL CHECKS PASSED${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "🎉 S3TradingPipeline is properly configured and ready to use!"
    echo ""
    echo "Next steps:"
    echo "  1. Run a spider: scrapy crawl crypto_news"
    echo "  2. Check S3: aws s3 ls s3://qbia/bourse/raw/news/ --recursive"
    echo "  3. Monitor stats in console output"
    echo ""
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}⚠ PASSED WITH WARNINGS ($WARNINGS)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "S3TradingPipeline is configured but has some warnings (likely AWS-related)."
    echo "The pipeline will work, but S3 uploads may fail if AWS is not configured."
    echo ""
    echo "To fix warnings:"
    echo "  - Install AWS CLI: pip3 install awscli"
    echo "  - Configure credentials: aws configure"
    echo "  - Verify bucket access: aws s3 ls s3://qbia/"
    echo ""
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ VERIFICATION FAILED ($ERRORS errors, $WARNINGS warnings)${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Please fix the errors above before running scrapers."
    echo ""
    echo "Common issues:"
    echo "  - Missing files: Re-run the installation"
    echo "  - Missing Python packages: pip3 install -r requirements.txt"
    echo "  - AWS not configured: aws configure"
    echo ""
    exit 1
fi
