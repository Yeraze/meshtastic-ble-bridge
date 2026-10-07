# Building Meshtastic BLE Bridge for macOS

This directory contains the build configuration for creating a macOS application bundle.

## Prerequisites

- macOS 10.13 (High Sierra) or later
- Python 3.9 or later
- Xcode Command Line Tools (`xcode-select --install`)

## Quick Build

```bash
# From this directory
./build.sh
```

The app bundle will be created at `dist/MeshtasticBLEBridge.app`.

## Build Options

```bash
# Basic build (no signing)
./build.sh

# Build with code signing
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./build.sh --sign

# Build with code signing and DMG
./build.sh --sign --dmg

# Build with signing, DMG, and notarization
export APPLE_ID="your@email.com"
export APPLE_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="XXXXXXXXXX"
./build.sh --sign --dmg --notarize
```

## Files

| File | Description |
|------|-------------|
| `build.spec` | PyInstaller configuration for app bundle |
| `build.sh` | Main build script |
| `notarize.sh` | Apple notarization script |
| `entitlements.plist` | App sandbox entitlements (Bluetooth, Network) |
| `Info.plist` | App bundle metadata template |

## Code Signing

### Requirements

To code sign and notarize the app for distribution, you need:

1. **Apple Developer Account** ($99/year)
2. **Developer ID Application certificate**
3. **App-specific password** for notarization

### Setup Code Signing

1. **Create Developer ID Certificate:**
   - Open Keychain Access
   - Keychain Access → Certificate Assistant → Request a Certificate
   - Go to [developer.apple.com](https://developer.apple.com) → Certificates
   - Create "Developer ID Application" certificate
   - Download and install in Keychain

2. **Find Your Certificate Identity:**
   ```bash
   security find-identity -v -p codesigning
   ```
   Look for: `Developer ID Application: Your Name (TEAMID)`

3. **Set Environment Variable:**
   ```bash
   export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
   ```

### Setup Notarization

1. **Create App-Specific Password:**
   - Go to [appleid.apple.com](https://appleid.apple.com)
   - Sign In → App-Specific Passwords → Generate

2. **Find Your Team ID:**
   - Go to [developer.apple.com](https://developer.apple.com) → Membership
   - Note your Team ID

3. **Set Environment Variables:**
   ```bash
   export APPLE_ID="your@email.com"
   export APPLE_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
   export APPLE_TEAM_ID="XXXXXXXXXX"
   ```

## Entitlements

The app requires these entitlements (defined in `entitlements.plist`):

| Entitlement | Purpose |
|-------------|---------|
| `com.apple.security.device.bluetooth` | Access Bluetooth hardware |
| `com.apple.security.network.server` | Listen on TCP port |
| `com.apple.security.network.client` | Connect to network |
| `com.apple.security.cs.allow-unsigned-executable-memory` | Python runtime |
| `com.apple.security.cs.disable-library-validation` | Bundled libraries |

## Info.plist Settings

Key settings in `Info.plist`:

| Key | Value | Purpose |
|-----|-------|---------|
| `LSUIElement` | true | Menu bar app (no dock icon) |
| `NSBluetoothAlwaysUsageDescription` | String | Bluetooth permission prompt |
| `NSHighResolutionCapable` | true | Retina display support |
| `LSMinimumSystemVersion` | 10.13.0 | Minimum macOS version |

## GitHub Actions

The build is automated via `.github/workflows/release-macos.yml`.

### CI Workflow (PRs)

The `test.yml` workflow runs on every PR and includes:
- macOS GUI import tests
- Full app bundle build (unsigned)
- Build artifact upload for verification

### Release Workflow

The `release-macos.yml` workflow runs on releases and builds:
- ARM64 (Apple Silicon) DMG
- x64 (Intel) DMG
- Both signed and notarized (if secrets configured)

### Required Secrets

For automated builds with signing and notarization, add these secrets to your repository:

| Secret | Description |
|--------|-------------|
| `APPLE_CERTIFICATE` | Base64-encoded .p12 certificate (Developer ID Application) |
| `APPLE_CERTIFICATE_PASSWORD` | Password for the .p12 certificate |
| `APPLE_SIGNING_IDENTITY` | Full signing identity string (e.g., "Developer ID Application: Name (TEAMID)") |
| `APPLE_API_KEY` | App Store Connect API Key ID (e.g., "XXXXXXXXXX") |
| `APPLE_API_KEY_CONTENT` | Contents of the .p8 API key file |
| `APPLE_API_ISSUER` | App Store Connect API Issuer ID (UUID format) |

### Setting Up Signing Secrets

#### 1. Export Your Certificate

```bash
# Find your Developer ID Application certificate
security find-identity -v -p codesigning

# Export from Keychain (will prompt for password)
security export -k ~/Library/Keychains/login.keychain-db \
  -t identities -f pkcs12 -P "your-password" \
  -o certificate.p12

# Convert to base64
base64 -i certificate.p12 | tr -d '\n' > certificate.txt
```

Add the contents of `certificate.txt` as `APPLE_CERTIFICATE`.

#### 2. Get Your Signing Identity

```bash
security find-identity -v -p codesigning | grep "Developer ID Application"
# Output: "Developer ID Application: Your Name (TEAMID)"
```

Set `APPLE_SIGNING_IDENTITY` to the quoted string.

#### 3. Create App Store Connect API Key

1. Go to [App Store Connect](https://appstoreconnect.apple.com) → Users and Access → Keys
2. Click **+** to create a new key
3. Name: "GitHub Actions" or similar
4. Access: "Developer"
5. Download the .p8 file (only available once!)
6. Note the Key ID and Issuer ID

Set secrets:
- `APPLE_API_KEY`: The Key ID (e.g., "ABC123XYZ")
- `APPLE_API_KEY_CONTENT`: Contents of the .p8 file
- `APPLE_API_ISSUER`: The Issuer ID (UUID from the Keys page)

## Troubleshooting

### Build Fails

**Missing dependencies:**
```bash
pip3 install -r ../../src/requirements-macos.txt
pip3 install pyinstaller
```

**Xcode tools not installed:**
```bash
xcode-select --install
```

### Code Signing Fails

**Certificate not found:**
```bash
# List available identities
security find-identity -v -p codesigning
```

**Keychain locked:**
```bash
security unlock-keychain login.keychain
```

### Notarization Fails

**Invalid credentials:**
- Verify Apple ID and app-specific password
- Check Team ID is correct

**App rejected:**
- Check notarization log for issues
- Common issues: unsigned binaries, hardened runtime missing

### App Won't Launch

**Gatekeeper:**
- Right-click → Open → Open
- Or: `xattr -cr MeshtasticBLEBridge.app`

**Missing entitlements:**
- Verify entitlements.plist is correct
- Re-sign with `--options runtime`

## Manual PyInstaller Build

If the build script doesn't work:

```bash
cd ../../src
pip3 install -r requirements-macos.txt
pip3 install pyinstaller

cd ../build/macos
pyinstaller build.spec --clean --noconfirm
```

## Output

After a successful build:

```
dist/
├── MeshtasticBLEBridge.app/    # App bundle
└── MeshtasticBLEBridge-macOS.dmg  # (if --dmg used)
```
