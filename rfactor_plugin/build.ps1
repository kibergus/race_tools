# build.ps1
# Portable build and deploy script for BrBrDbTelemetry

param(
    [string]$Target = "deploy"
)

$ErrorActionPreference = "Stop"

# Ensure the script always runs from its own directory so relative paths work correctly,
# regardless of the working directory of the caller (e.g. VS Code, CI, command line).
Set-Location $PSScriptRoot

# Disable MSBuild node reuse to prevent background processes from holding output pipes open,
# which can cause VS Code tasks and debug sessions to hang indefinitely.
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

# Verify CMake is executable
try {
    & $cmakePath --version | Out-Null
    Write-Host "Using CMake: $cmakePath"
} catch {
    Write-Error "CMake could not be found or executed. Please ensure CMake or Visual Studio is installed."
}

Write-Host ""
Write-Host "Configuring CMake project..."
& $cmakePath -S . -B build

# Copy test configuration file to the build output directory for mock_runner and tests
$binReleaseDir = Join-Path (Get-Item .).FullName "build\bin\Release"
if (!(Test-Path $binReleaseDir)) {
    New-Item -ItemType Directory -Path $binReleaseDir -Force | Out-Null
}
Copy-Item "BrBrTelemetry.test.json" -Destination (Join-Path $binReleaseDir "BrBrDbTelemetry.json") -Force


if ($Target -eq "test") {
    Write-Host ""
    Write-Host "Compiling C# installer unit tests..."
    $csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) {
        Write-Error "C# compiler csc.exe not found at $csc! Cannot compile C# tests."
        exit 1
    }
    
    $testExe = "install\BrBrDbTelemetryInstallerTests.exe"
    if (Test-Path $testExe) {
        Remove-Item $testExe -Force
    }

    & $csc "/target:exe" "/out:$testExe" "/r:System.dll" "install\InstallManager.cs" "install\ConfigHelper.cs" "install\InstallManagerTests.cs"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "C# installer unit tests compilation failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Running C# installer unit tests..."
    & $testExe
    $csharpExit = $LASTEXITCODE

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

    if (Test-Path $testExe) {
        Remove-Item $testExe -Force
    }

    if ($csharpExit -ne 0 -or $cppExit -ne 0) {
        Write-Error "Unit tests failed!"
        exit 1
    }
    exit 0
}

if ($Target -eq "mock") {
    Write-Host ""
    Write-Host "Running mock telemetry runner via CMake..."
    & $cmakePath --build build --config Release --target run_mock
    exit $LASTEXITCODE
}

if ($Target -eq "build") {
    Write-Host ""
    Write-Host "Building all targets (Release)..."
    & $cmakePath --build build --config Release
    exit $LASTEXITCODE
}

if ($Target -eq "installer") {
    Write-Host ""
    Write-Host "Building BrBrDbTelemetry plugin (Release)..."
    & $cmakePath --build build --config Release --target BrBrDbTelemetry
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Plugin compilation failed! Aborting."
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Compiling GUI Installer and Configurator..."
    $csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) {
        Write-Error "C# compiler csc.exe not found at $csc! Cannot compile installer."
        exit 1
    }

    $sourceDll = "build/bin/Release/BrBrDbTelemetry64.dll"
    if (-not (Test-Path $sourceDll)) {
        Write-Error "Could not find built DLL at $sourceDll. Aborting installer compilation."
        exit 1
    }

    $installerExe = "install/BrBrDbTelemetryInstaller.exe"
    Write-Host "Running C# compiler..."
    & $csc "/target:winexe" "/out:$installerExe" "/resource:$sourceDll" "/r:System.Windows.Forms.dll" "/r:System.Drawing.dll" "/r:System.dll" "install\Program.cs" "install\InstallManager.cs" "install\ConfigHelper.cs" "install\InstallerForm.cs"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "C# Installer compilation failed!"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Launching Installer: $installerExe..."
    Start-Process -FilePath $installerExe
    exit 0
}

if ($Target -eq "deploy") {
    Write-Host ""
    Write-Host "Running all unit tests..."
    powershell -File .\build.ps1 -Target test
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Unit tests failed! Aborting deployment."
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Building BrBrDbTelemetry plugin (Release)..."
    & $cmakePath --build build --config Release --target BrBrDbTelemetry
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Plugin compilation failed! Aborting deployment."
        exit $LASTEXITCODE
    }

    # 3. Compile C# GUI Installer & Configurator
    Write-Host ""
    Write-Host "Compiling GUI Installer and Configurator..."
    $csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) {
        Write-Error "C# compiler csc.exe not found at $csc! Cannot compile installer."
        exit 1
    }

    $sourceDll = "build/bin/Release/BrBrDbTelemetry64.dll"
    if (-not (Test-Path $sourceDll)) {
        Write-Error "Could not find built DLL at $sourceDll. Aborting installer compilation."
        exit 1
    }

    $installerExe = "install/BrBrDbTelemetryInstaller.exe"

    Write-Host "Running C# compiler..."
    & $csc "/target:winexe" "/out:$installerExe" "/resource:$sourceDll" "/r:System.Windows.Forms.dll" "/r:System.Drawing.dll" "/r:System.dll" "install\Program.cs" "install\InstallManager.cs" "install\ConfigHelper.cs" "install\InstallerForm.cs"

    if ($LASTEXITCODE -eq 0 -and (Test-Path $installerExe)) {
        Write-Host "SUCCESS: Built GUI Installer at $installerExe"
    } else {
        Write-Error "C# Installer compilation failed!"
        exit $LASTEXITCODE
    }

    # 4. Locate rFactor 2 installation folder
    Write-Host ""
    Write-Host "Locating rFactor 2 installation..."
    $rf2Paths = @(
        "C:\Program Files (x86)\Steam\steamapps\common\rFactor 2",
        "D:\SteamLibrary\steamapps\common\rFactor 2",
        "E:\SteamLibrary\steamapps\common\rFactor 2"
    )

    # Also try to check the Steam registry key
    # TODO: Parse secondary Steam library paths dynamically via steamapps\libraryfolders.vdf when building an installer.
    $steamReg = Get-ItemProperty -Path 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue
    if ($steamReg -and $steamReg.SteamPath) {
        $steamCommon = Join-Path $steamReg.SteamPath "steamapps\common\rFactor 2"
        if (Test-Path $steamCommon) {
            $rf2Paths = @($steamCommon) + $rf2Paths
        }
    }

    $rf2Root = $null
    foreach ($path in $rf2Paths) {
        if (Test-Path $path) {
            $rf2Root = $path
            break
        }
    }

    if ($rf2Root) {
        $pluginsDir = Join-Path $rf2Root "Bin64\Plugins"
        if (Test-Path $pluginsDir) {
            Write-Host "Found rFactor 2 Plugins folder: $pluginsDir"
            $sourceDll = "build/bin/Release/BrBrDbTelemetry64.dll"
            if (Test-Path $sourceDll) {
                Write-Host "Deploying plugin to rFactor 2..."
                Copy-Item $sourceDll -Destination $pluginsDir -Force
                Write-Host "SUCCESS: Deployed BrBrDbTelemetry64.dll to $pluginsDir"
            } else {
                Write-Warning "Could not find built DLL at $sourceDll. Skipping deployment."
            }
        } else {
            Write-Warning "Could not find Bin64\Plugins inside rFactor 2 directory at $rf2Root."
        }
    } else {
        Write-Warning "Could not automatically locate rFactor 2 directory. Built DLL is ready in build\bin\Release\BrBrDbTelemetry64.dll"
    }

    Write-Host ""
    Write-Host "Build and deploy process finished!"
}
