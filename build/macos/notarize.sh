#!/bin/bash
#
# Apple Notarization Script for Meshtastic BLE Bridge
#
# This script submits the app to Apple for notarization and waits for completion.
#
# Required environment variables:
#   APPLE_ID            - Your Apple ID email
#   APPLE_APP_PASSWORD  - App-specific password (generate at appleid.apple.com)
#   APPLE_TEAM_ID       - Your Apple Developer Team ID
#
# Usage: ./notarize.sh <path-to-app-or-dmg>
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check arguments
if [ -z "$1" ]; then
    echo -e "${RED}Error: No file specified${NC}"
    echo "Usage: $0 <path-to-app-or-dmg>"
    exit 1
fi

NOTARIZE_PATH="$1"

if [ ! -e "$NOTARIZE_PATH" ]; then
    echo -e "${RED}Error: File not found: $NOTARIZE_PATH${NC}"
    exit 1
fi

# Check required environment variables
if [ -z "$APPLE_ID" ]; then
    echo -e "${RED}Error: APPLE_ID environment variable not set${NC}"
    exit 1
fi

if [ -z "$APPLE_APP_PASSWORD" ]; then
    echo -e "${RED}Error: APPLE_APP_PASSWORD environment variable not set${NC}"
    echo "Generate an app-specific password at https://appleid.apple.com"
    exit 1
fi

if [ -z "$APPLE_TEAM_ID" ]; then
    echo -e "${RED}Error: APPLE_TEAM_ID environment variable not set${NC}"
    exit 1
fi

echo -e "${YELLOW}Submitting for notarization...${NC}"
echo "File: $NOTARIZE_PATH"

# Submit for notarization using notarytool (macOS 13+)
# Falls back to altool for older macOS versions
if xcrun notarytool --version &>/dev/null; then
    # Modern notarytool (macOS 13+)
    echo "Using notarytool..."

    # Submit and wait for result
    xcrun notarytool submit "$NOTARIZE_PATH" \
        --apple-id "$APPLE_ID" \
        --password "$APPLE_APP_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait \
        --timeout 30m

    RESULT=$?

    if [ $RESULT -eq 0 ]; then
        echo -e "${GREEN}✓ Notarization successful${NC}"
    else
        echo -e "${RED}✗ Notarization failed${NC}"

        # Get the log for debugging
        echo "Fetching notarization log..."
        xcrun notarytool log "$NOTARIZE_PATH" \
            --apple-id "$APPLE_ID" \
            --password "$APPLE_APP_PASSWORD" \
            --team-id "$APPLE_TEAM_ID" \
            notarization-log.json 2>/dev/null || true

        if [ -f notarization-log.json ]; then
            cat notarization-log.json
        fi

        exit 1
    fi
else
    # Legacy altool (macOS 12 and earlier)
    echo "Using altool (legacy)..."

    # Store credentials in keychain (one-time setup)
    xcrun altool --store-password-in-keychain-item "AC_PASSWORD" \
        -u "$APPLE_ID" \
        -p "$APPLE_APP_PASSWORD" 2>/dev/null || true

    # Submit for notarization
    SUBMIT_OUTPUT=$(xcrun altool --notarize-app \
        --primary-bundle-id "com.meshtastic.ble-bridge" \
        --username "$APPLE_ID" \
        --password "@keychain:AC_PASSWORD" \
        --asc-provider "$APPLE_TEAM_ID" \
        --file "$NOTARIZE_PATH" 2>&1)

    echo "$SUBMIT_OUTPUT"

    # Extract RequestUUID
    REQUEST_UUID=$(echo "$SUBMIT_OUTPUT" | grep "RequestUUID" | awk '{print $3}')

    if [ -z "$REQUEST_UUID" ]; then
        echo -e "${RED}✗ Failed to get RequestUUID${NC}"
        exit 1
    fi

    echo "Request UUID: $REQUEST_UUID"
    echo ""
    echo -e "${YELLOW}Waiting for notarization to complete...${NC}"

    # Poll for completion
    while true; do
        sleep 30

        STATUS_OUTPUT=$(xcrun altool --notarization-info "$REQUEST_UUID" \
            --username "$APPLE_ID" \
            --password "@keychain:AC_PASSWORD" 2>&1)

        STATUS=$(echo "$STATUS_OUTPUT" | grep "Status:" | awk '{print $2}')

        if [ "$STATUS" = "success" ]; then
            echo -e "${GREEN}✓ Notarization successful${NC}"
            break
        elif [ "$STATUS" = "invalid" ]; then
            echo -e "${RED}✗ Notarization failed${NC}"
            echo "$STATUS_OUTPUT"
            exit 1
        else
            echo "Status: $STATUS - still processing..."
        fi
    done
fi

# Staple the notarization ticket
echo ""
echo -e "${YELLOW}Stapling notarization ticket...${NC}"

if [[ "$NOTARIZE_PATH" == *.dmg ]]; then
    xcrun stapler staple "$NOTARIZE_PATH"
elif [[ "$NOTARIZE_PATH" == *.app ]]; then
    xcrun stapler staple "$NOTARIZE_PATH"
elif [[ "$NOTARIZE_PATH" == *.zip ]]; then
    # For ZIP files, we need to extract, staple the app, and re-zip
    echo "Extracting ZIP to staple app..."

    TEMP_DIR=$(mktemp -d)
    unzip -q "$NOTARIZE_PATH" -d "$TEMP_DIR"

    APP_PATH=$(find "$TEMP_DIR" -name "*.app" -type d | head -1)

    if [ -n "$APP_PATH" ]; then
        xcrun stapler staple "$APP_PATH"
        echo -e "${GREEN}✓ Stapled: $APP_PATH${NC}"

        # Re-create ZIP
        rm "$NOTARIZE_PATH"
        ditto -c -k --keepParent "$APP_PATH" "$NOTARIZE_PATH"
    fi

    rm -rf "$TEMP_DIR"
fi

echo -e "${GREEN}✓ Notarization complete${NC}"

# Verify notarization
echo ""
echo -e "${YELLOW}Verifying notarization...${NC}"
spctl --assess --verbose=4 "$NOTARIZE_PATH" 2>&1 || true

echo ""
echo -e "${GREEN}Done!${NC}"
