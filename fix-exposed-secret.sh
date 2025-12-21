#!/bin/bash

# Script to fix exposed secret in AIFPL repository
# This script will help identify and remove the exposed API key from git history

set -e

echo "🔍 Searching for exposed secret in git history..."

# Search for potential API keys (32-character hex strings)
echo "Searching for 32-character hex strings (potential API keys)..."
git log --all -p | grep -E "[a-f0-9]{32}" | grep -v "^commit\|^index\|^diff\|^---\|^+++\|^@@\|^index" | head -20

# Search for THE_ODDS_API_KEY assignments
echo ""
echo "Searching for THE_ODDS_API_KEY assignments..."
git log --all -p | grep -B3 -A3 "THE_ODDS_API_KEY.*=" | grep -v "^commit\|^index\|^diff\|^---\|^+++\|^@@\|^index" | head -30

# Check if .env was ever committed
echo ""
echo "Checking if .env file was ever committed..."
if git log --all --diff-filter=A --name-only | grep -q "\.env$"; then
    echo "⚠️  WARNING: .env file was committed at some point!"
    git log --all --diff-filter=A --name-only --pretty=format:"%H %ai %s" | grep "\.env"
else
    echo "✅ No .env file was ever committed (good!)"
fi

echo ""
echo "✅ Verification complete!"
echo ""
echo "If you found the exposed secret above, you can remove it using:"
echo "1. git filter-repo (recommended):"
echo "   pip install git-filter-repo"
echo "   git filter-repo --replace-text <(echo 'OLD_SECRET==>ENV_VAR_PLACEHOLDER')"
echo ""
echo "2. Or BFG Repo-Cleaner:"
echo "   bfg --replace-text secrets.txt"
echo ""
echo "3. Then force push:"
echo "   git push --force --all"

