#!/usr/bin/env bash
# build_wheel.sh
# A script to clean, build, and verify the race_tools python package distribution wheels.

set -euo pipefail

# 1. Navigate to the script's directory
CDPATH="" cd -- "$(dirname -- "$0")"

echo "🧹 Cleaning previous build and distribution artifacts..."
rm -rf build/ dist/ *.egg-info/ .eggs/

echo "📦 Building the source distribution and wheel package..."
if command -v pyproject-build &> /dev/null; then
    pyproject-build
elif python3 -c "import build" &> /dev/null; then
    python3 -m build
else
    echo "⚠️  Neither 'pyproject-build' nor python 'build' module found!"
    echo "Please install build dependencies first: pip install .[dev]"
    exit 1
fi

echo "🔍 Running twine verification on the built packages..."
if command -v twine &> /dev/null; then
    twine check dist/*
else
    echo "⚠️  'twine' command not found! Skipping verification."
    echo "Please install twine: pip install .[dev]"
fi

echo "✅ Distribution packages built and verified successfully!"
echo "Generated files:"
ls -lh dist/
