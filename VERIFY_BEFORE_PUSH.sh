#!/bin/bash
# Final verification before pushing to GitHub

echo "🔍 Verifying sanitization..."
echo ""

# Check for personal data
echo "1. Checking for personal username..."
TIRVING=$(grep -r "tirving" . --exclude="VERIFY_BEFORE_PUSH.sh" 2>/dev/null | wc -l)
if [ $TIRVING -eq 0 ]; then
    echo "   ✅ No 'tirving' found"
else
    echo "   ❌ FAIL: Found $TIRVING instances of 'tirving'"
    grep -rn "tirving" . --exclude="VERIFY_BEFORE_PUSH.sh"
    exit 1
fi

echo "2. Checking for hardcoded paths..."
HOME_PATHS=$(grep -r "/home/" . --exclude="VERIFY_BEFORE_PUSH.sh" 2>/dev/null | wc -l)
if [ $HOME_PATHS -eq 0 ]; then
    echo "   ✅ No '/home/' paths found"
else
    echo "   ❌ FAIL: Found $HOME_PATHS hardcoded paths"
    grep -rn "/home/" . --exclude="VERIFY_BEFORE_PUSH.sh"
    exit 1
fi

echo "3. Checking for personal chat IDs..."
CHAT_ID=$(grep -r "7783240549" . --exclude="VERIFY_BEFORE_PUSH.sh" 2>/dev/null | wc -l)
if [ $CHAT_ID -eq 0 ]; then
    echo "   ✅ No personal chat IDs found"
else
    echo "   ❌ FAIL: Found personal chat ID"
    grep -rn "7783240549" . --exclude="VERIFY_BEFORE_PUSH.sh"
    exit 1
fi

echo "4. Checking for .env file (should not exist)..."
if [ -f ".env" ]; then
    echo "   ❌ FAIL: .env file exists (should be .env.example only)"
    exit 1
else
    echo "   ✅ No .env file (correct)"
fi

echo "5. Checking for state files..."
JSON_FILES=$(find . -name "*.json" ! -name "*.json.example" 2>/dev/null | wc -l)
if [ $JSON_FILES -eq 0 ]; then
    echo "   ✅ No state files (correct)"
else
    echo "   ⚠️  WARNING: Found JSON files (may contain sensitive data)"
    find . -name "*.json" ! -name "*.json.example"
fi

echo "6. Checking required files exist..."
REQUIRED=".env.example .gitignore README.md LICENSE requirements.txt kalshi_unified.py weather_providers.py"
MISSING=""
for file in $REQUIRED; do
    if [ ! -f "$file" ]; then
        MISSING="$MISSING $file"
    fi
done
if [ -z "$MISSING" ]; then
    echo "   ✅ All required files present"
else
    echo "   ❌ FAIL: Missing files:$MISSING"
    exit 1
fi

echo "7. Checking .gitignore protects secrets..."
if grep -q "\.env$" .gitignore && grep -q "\.pem$" .gitignore; then
    echo "   ✅ .gitignore protects credentials"
else
    echo "   ❌ FAIL: .gitignore missing credential protection"
    exit 1
fi

echo ""
echo "✅ All checks passed! Ready for GitHub."
echo ""
echo "📋 Next steps:"
echo "   git init"
echo "   git add ."
echo "   git commit -m 'Initial release: Kalshi weather arbitrage bot'"
echo "   git remote add origin git@github.com:yourusername/kalshi-weather-arbitrage.git"
echo "   git push -u origin main"
