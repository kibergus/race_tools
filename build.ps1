# build.ps1
# Master build, test, and packaging script for BrBrDbTelemetry
# Supports rFactor 2, Assetto Corsa Evo, and the Unified GUI Installer

param(
    [string]$Target = "build"
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Ensure latest git tags are fetched so version metadata matches GitHub releases
if (Get-Command git -ErrorAction SilentlyContinue) {
    try {
        git fetch --tags origin 2>$null
    } catch {}
}

Write-Host "=================================================="
Write-Host "BrBrDb Telemetry Suite - Target: $Target"
Write-Host "=================================================="

if ($Target -eq "test") {
    Write-Host ""
    Write-Host "--- 1. Testing rFactor 2 Plugin (C++ & C#) ---"
    powershell -ExecutionPolicy Bypass -File .\rfactor_plugin\build.ps1 -Target test
    if ($LASTEXITCODE -ne 0) {
        Write-Error "rFactor 2 tests failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "--- 2. Testing Telemetry Daemon (C++) ---"
    powershell -ExecutionPolicy Bypass -File .\brbrdaemon\build.ps1 -Target test
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Telemetry daemon tests failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "--- 3. Testing Unified Installer (C#) ---"
    & dotnet run --project installer/BrBrDbTelemetryInstallerTests.csproj
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Installer tests failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "SUCCESS: All test suites passed cleanly!"
    exit 0
}

if ($Target -eq "build" -or $Target -eq "installer") {
    Write-Host ""
    Write-Host "--- 1. Building rFactor 2 Plugin (BrBrDbTelemetry64.dll) ---"
    powershell -ExecutionPolicy Bypass -File .\rfactor_plugin\build.ps1 -Target build
    if ($LASTEXITCODE -ne 0) {
        Write-Error "rFactor 2 build failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "--- 2. Building Telemetry Daemon (brbrdaemon.exe) ---"
    powershell -ExecutionPolicy Bypass -File .\brbrdaemon\build.ps1 -Target build
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Telemetry daemon build failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "--- 3. Publishing Unified GUI Installer (Embedding both plugins) ---"
    $installerProj = "installer/BrBrDbTelemetryInstaller.csproj"
    $publishDir = "installer/publish"
    & dotnet publish $installerProj -c Release -o $publishDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Unified installer publish failed!"
        exit $LASTEXITCODE
    }

    $finalInstaller = "installer/BrBrDbTelemetryInstaller.exe"
    $running = Get-Process BrBrDbTelemetryInstaller -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Closing currently running instance of BrBrDbTelemetryInstaller..."
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }
    Copy-Item (Join-Path $publishDir "BrBrDbTelemetryInstaller.exe") -Destination $finalInstaller -Force
    Write-Host ""
    Write-Host "SUCCESS: Built Unified Installer at $finalInstaller"

    if ($Target -eq "installer") {
        Write-Host "Launching Installer: $finalInstaller..."
        Start-Process -FilePath $finalInstaller
    }

    exit 0
}

Write-Error "Unknown target: $Target. Valid targets: test, build, installer"
exit 1
