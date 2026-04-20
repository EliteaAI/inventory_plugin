#!/bin/bash
# Build script for Inventory Plugin UI

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/template"

echo "Building Inventory Plugin UI..."
echo "Template directory: $TEMPLATE_DIR"

cd "$TEMPLATE_DIR"

# Install dependencies
echo "Installing dependencies..."
npm install

# Build
echo "Building..."
npm run build

echo "Build complete!"
echo "Output: $SCRIPT_DIR/dist/"
ls -la "$SCRIPT_DIR/dist/"
