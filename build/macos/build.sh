#!/bin/bash
#
# Build script for macOS application
# Usage: ./build.sh [--sign] [--dmg] [--notarize]
#
# Options:
#   --sign       Sign the app with Developer ID (requires CODESIGN_IDENTITY env var)
#   --dmg        Create DMG installer
#   --notarize   Submit for Apple notarization (requires --sign)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="$PROJECT_ROOT/src"
BUILD_DIR="$SCRIPT_DIR"
DIST_DIR="$BUILD_DIR/dist"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
SIGN_APP=false
CREATE_DMG=false
NOTARIZE=false

for arg in "$@"; do
    case $arg in
        --sign)
            SIGN_APP=true
            shift
            ;;
        --dmg)
            CREATE_DMG=true
            shift
            ;;
        --notarize)
            NOTARIZE=true
            shift
            ;;
    esac
done

echo -e "${GREEN}Building Meshtastic BLE Bridge for macOS...${NC}"
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1)
if [[ ! $PYTHON_VERSION =~ "Python 3".(9|10|11|12|13) ]]; then
    echo -e "${RED}Error: Python 3.9+ required${NC}"
    echo "Current version: $PYTHON_VERSION"
    exit 1
fi
echo -e "${GREEN}✓ $PYTHON_VERSION${NC}"

# Navigate to source directory and install dependencies
echo ""
echo -e "${YELLOW}Installing dependencies...${NC}"
cd "$SRC_DIR"
pip3 install -r requirements-macos.txt -q

# Install PyInstaller
echo -e "${YELLOW}Installing PyInstaller...${NC}"
pip3 install pyinstaller -q

# Navigate to build directory
cd "$BUILD_DIR"

# Clean previous build
echo ""
echo -e "${YELLOW}Cleaning previous build...${NC}"
rm -rf "$DIST_DIR" build __pycache__

# Build application
echo ""
echo -e "${YELLOW}Building application...${NC}"
python3 -m PyInstaller build.spec --clean --noconfirm

# Check if build succeeded
APP_PATH="$DIST_DIR/MeshtasticBLEBridge.app"
if [ -d "$APP_PATH" ]; then
    APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
    echo ""
    echo -e "${GREEN}✓ Build successful!${NC}"
    echo -e "  Output: $APP_PATH"
    echo -e "  Size: $APP_SIZE"
else
    echo ""
    echo -e "${RED}✗ Build failed - app bundle not found${NC}"
    exit 1
fi

# Code signing
if [ "$SIGN_APP" = true ]; then
    echo ""
    echo -e "${YELLOW}Signing application...${NC}"

    if [ -z "$CODESIGN_IDENTITY" ]; then
        echo -e "${RED}Error: CODESIGN_IDENTITY environment variable not set${NC}"
        echo "Set it to your Developer ID Application certificate name or SHA-1 hash"
        echo "Example: export CODESIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\""
        exit 1
    fi

    # Sign all frameworks and libraries first
    find "$APP_PATH/Contents/Frameworks" -name "*.dylib" -o -name "*.so" 2>/dev/null | while read lib; do
        codesign --force --sign "$CODESIGN_IDENTITY" \
            --options runtime \
            --entitlements "$BUILD_DIR/entitlements.plist" \
            "$lib" 2>/dev/null || true
    done

    find "$APP_PATH/Contents/Frameworks" -name "*.framework" 2>/dev/null | while read framework; do
        codesign --force --sign "$CODESIGN_IDENTITY" \
            --options runtime \
            --entitlements "$BUILD_DIR/entitlements.plist" \
            "$framework" 2>/dev/null || true
    done

    # Sign the main executable
    codesign --force --sign "$CODESIGN_IDENTITY" \
        --options runtime \
        --entitlements "$BUILD_DIR/entitlements.plist" \
        "$APP_PATH/Contents/MacOS/MeshtasticBLEBridge"

    # Sign the app bundle
    codesign --force --sign "$CODESIGN_IDENTITY" \
        --options runtime \
        --entitlements "$BUILD_DIR/entitlements.plist" \
        "$APP_PATH"

    # Verify signature
    if codesign --verify --deep --strict "$APP_PATH" 2>/dev/null; then
        echo -e "${GREEN}✓ Code signing successful${NC}"
    else
        echo -e "${RED}✗ Code signing verification failed${NC}"
        exit 1
    fi
fi

# Create DMG
if [ "$CREATE_DMG" = true ]; then
    echo ""
    echo -e "${YELLOW}Creating DMG installer...${NC}"

    DMG_NAME="MeshtasticBLEBridge-macOS"
    DMG_PATH="$DIST_DIR/$DMG_NAME.dmg"

    # Remove existing DMG
    rm -f "$DMG_PATH"

    # Create temporary directory for DMG contents
    DMG_TEMP="$DIST_DIR/dmg_temp"
    rm -rf "$DMG_TEMP"
    mkdir -p "$DMG_TEMP"

    # Copy app to temp directory
    cp -R "$APP_PATH" "$DMG_TEMP/"

    # Create symbolic link to Applications
    ln -s /Applications "$DMG_TEMP/Applications"

    # Create DMG
    hdiutil create -volname "Meshtastic BLE Bridge" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DMG_PATH"

    # Clean up
    rm -rf "$DMG_TEMP"

    # Sign DMG if code signing is enabled
    if [ "$SIGN_APP" = true ] && [ -n "$CODESIGN_IDENTITY" ]; then
        codesign --force --sign "$CODESIGN_IDENTITY" "$DMG_PATH"
        echo -e "${GREEN}✓ DMG signed${NC}"
    fi

    DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
    echo -e "${GREEN}✓ DMG created: $DMG_PATH ($DMG_SIZE)${NC}"
fi

# Notarization
if [ "$NOTARIZE" = true ]; then
    if [ "$SIGN_APP" != true ]; then
        echo -e "${RED}Error: --notarize requires --sign${NC}"
        exit 1
    fi

    echo ""
    echo -e "${YELLOW}Submitting for notarization...${NC}"

    if [ "$CREATE_DMG" = true ]; then
        NOTARIZE_PATH="$DMG_PATH"
    else
        # Create a ZIP for notarization
        NOTARIZE_PATH="$DIST_DIR/MeshtasticBLEBridge-macOS.zip"
        ditto -c -k --keepParent "$APP_PATH" "$NOTARIZE_PATH"
    fi

    # Run notarization script
    if [ -f "$BUILD_DIR/notarize.sh" ]; then
        "$BUILD_DIR/notarize.sh" "$NOTARIZE_PATH"
    else
        echo -e "${RED}Error: notarize.sh not found${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}Build complete!${NC}"
echo ""
echo "To run the app:"
echo "  open $APP_PATH"
echo ""
if [ "$CREATE_DMG" = true ]; then
    echo "To distribute:"
    echo "  $DMG_PATH"
fi
