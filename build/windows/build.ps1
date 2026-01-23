# PowerShell build script for Windows executable
# Usage: .\build.ps1

Write-Host "Building Meshtastic BLE Bridge for Windows..." -ForegroundColor Green
Write-Host ""

# Check Python version
$pythonVersion = python --version 2>&1
if ($pythonVersion -notmatch "Python 3\.(9|10|11|12)") {
    Write-Host "Error: Python 3.9+ required" -ForegroundColor Red
    Write-Host "Current version: $pythonVersion" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Python version: $pythonVersion" -ForegroundColor Green

# Navigate to src directory
Set-Location -Path "..\..\src"

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements-gui.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install dependencies" -ForegroundColor Red
    exit 1
}

# Install PyInstaller
Write-Host ""
Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
pip install pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install PyInstaller" -ForegroundColor Red
    exit 1
}

# Navigate to build directory
Set-Location -Path "..\build\windows"

# Clean previous build
Write-Host ""
Write-Host "Cleaning previous build..." -ForegroundColor Yellow
if (Test-Path "dist") {
    Remove-Item -Path "dist" -Recurse -Force
}
if (Test-Path "build") {
    Remove-Item -Path "build" -Recurse -Force
}

# Build executable
Write-Host ""
Write-Host "Building executable..." -ForegroundColor Yellow
pyinstaller build.spec --clean --noconfirm

# Check if build succeeded
if (Test-Path "dist\MeshtasticBLEBridge.exe") {
    $size = (Get-Item "dist\MeshtasticBLEBridge.exe").Length / 1MB
    Write-Host ""
    Write-Host "✓ Build successful!" -ForegroundColor Green
    Write-Host "  Output: dist\MeshtasticBLEBridge.exe" -ForegroundColor Cyan
    Write-Host "  Size: $([math]::Round($size, 2)) MB" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "You can now run: .\dist\MeshtasticBLEBridge.exe" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "✗ Build failed" -ForegroundColor Red
    exit 1
}
