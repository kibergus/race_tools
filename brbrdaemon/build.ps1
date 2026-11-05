# build.ps1
# Portable build and test script for brbrdaemon

param(
    [ValidateSet("build", "test", "mock", "deploy")]
    [string]$Target = "build"
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$env:MSBUILDDISABLENODEREUSE = "1"

# 1. Locate Visual Studio and CMake dynamically
Write-Host "Locating Visual Studio and CMake..."
$cmakePath = "cmake"
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"

if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -property installationPath
    if ($vsPath) {
        $vsCmake = Join-Path $vsPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
        if (Test-Path $vsCmake) {
            $cmakePath = $vsCmake
            Write-Host "Found Visual Studio CMake: $cmakePath"
        }
    }
}

try {
    & $cmakePath --version | Out-Null
    Write-Host "Using CMake: $cmakePath"
}
catch {
    Write-Error "CMake could not be found or executed. Please ensure CMake or Visual Studio is installed."
    exit 1
}

Write-Host ""
Write-Host "Configuring CMake project..."
& $cmakePath -S . -B build
if ($LASTEXITCODE -ne 0) {
    Write-Error "CMake configuration failed!"
    exit $LASTEXITCODE
}

$binReleaseDir = Join-Path (Get-Item .).FullName "build\bin\Release"
if (!(Test-Path $binReleaseDir)) {
    New-Item -ItemType Directory -Path $binReleaseDir -Force | Out-Null
}

if ($Target -eq "test") {
    Copy-Item "brbrdaemon.test.json" -Destination (Join-Path $binReleaseDir "BrBrDbTelemetry.json") -Force

    Write-Host ""
    Write-Host "Building C++ unit tests..."
    & $cmakePath --build build --config Release --target unit_tests
    if ($LASTEXITCODE -ne 0) {
        Write-Error "C++ unit tests compilation failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Running unit tests via CMake..."
    & $cmakePath --build build --config Release --target run_tests
    $cppExit = $LASTEXITCODE

    if ($cppExit -ne 0) {
        Write-Error "Unit tests failed!"
        exit 1
    }
    Write-Host "SUCCESS: All unit tests passed!"
    exit 0
}

if ($Target -eq "mock") {
    Copy-Item "brbrdaemon.test.json" -Destination (Join-Path $binReleaseDir "BrBrDbTelemetry.json") -Force

    Write-Host ""
    Write-Host "Running mock telemetry runner via CMake..."
    & $cmakePath --build build --config Release --target run_mock
    exit $LASTEXITCODE
}

if ($Target -eq "build") {
    Write-Host ""
    Write-Host "Building brbrdaemon (Release)..."
    & $cmakePath --build build --config Release --target brbrdaemon
    exit $LASTEXITCODE
}

if ($Target -eq "deploy") {
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -Target test
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Unit tests failed! Aborting deployment."
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Building brbrdaemon (Release)..."
    & $cmakePath --build build --config Release --target brbrdaemon
    if ($LASTEXITCODE -ne 0) {
        Write-Error "brbrdaemon compilation failed! Aborting deployment."
        exit $LASTEXITCODE
    }

    $sourceExe = "build/bin/Release/brbrdaemon.exe"
    $targetDir = Join-Path $env:LOCALAPPDATA "BrBrDbTelemetry"
    if (Test-Path $targetDir) {
        Write-Host "Deploying brbrdaemon to $targetDir..."
        Stop-Process -Name brbrdaemon -Force -ErrorAction SilentlyContinue
        Stop-Process -Name BrBrDbTelemetryAce -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500

        Copy-Item $sourceExe -Destination (Join-Path $targetDir "brbrdaemon.exe") -Force
        Write-Host "SUCCESS: Deployed brbrdaemon.exe to $targetDir"
    }
    else {
        Write-Host "Target directory $targetDir does not exist yet. Built binary is in $sourceExe"
    }
    exit 0
}
