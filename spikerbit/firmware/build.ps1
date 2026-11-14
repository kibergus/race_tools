# build.ps1
# Portable BBC micro:bit V2 (nRF52833 + Nordic S113 BLE SoftDevice) firmware build script for Spiker:bit

param(
    [ValidateSet("build", "flash")]
    [string]$Target = "build"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$buildDir = Join-Path $PSScriptRoot "build"
$toolchainRoot = Join-Path $buildDir "toolchain"
$sdkDir = Join-Path $buildDir "sdk"
New-Item -ItemType Directory -Force -Path $buildDir, $toolchainRoot, $sdkDir | Out-Null

# 1. Locate or download portable arm-none-eabi-gcc
$gccCmd = Get-Command "arm-none-eabi-gcc" -ErrorAction SilentlyContinue
if ($gccCmd) {
    $binDir = Split-Path -Parent $gccCmd.Source
}
else {
    $cachedGcc = Get-ChildItem -Path $toolchainRoot -Filter "arm-none-eabi-gcc.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cachedGcc) {
        $zipUrl = "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v15.2.1-1.1/xpack-arm-none-eabi-gcc-15.2.1-1.1-win32-x64.zip"
        $zipPath = Join-Path $toolchainRoot "arm-none-eabi-gcc.zip"
        Write-Host "Downloading portable arm-none-eabi-gcc toolchain..."
        curl.exe -fL --retry 3 -o $zipPath $zipUrl
        if ($LASTEXITCODE -ne 0) { throw "Failed to download arm-none-eabi-gcc" }
        Expand-Archive -Path $zipPath -DestinationPath $toolchainRoot -Force
        Remove-Item $zipPath -Force
        $cachedGcc = Get-ChildItem -Path $toolchainRoot -Filter "arm-none-eabi-gcc.exe" -Recurse | Select-Object -First 1
    }
    $binDir = Split-Path -Parent $cachedGcc.FullName
}

$gcc = Join-Path $binDir "arm-none-eabi-gcc.exe"
$gxx = Join-Path $binDir "arm-none-eabi-g++.exe"
$objcopy = Join-Path $binDir "arm-none-eabi-objcopy.exe"
$sizeTool = Join-Path $binDir "arm-none-eabi-size.exe"
Write-Host "Using toolchain: $binDir"

# 2. Fetch Nordic nRF52833 MDK, ARM CMSIS Core, S113 SoftDevice headers & micro:bit V2 BLE objects if not cached
$nrfHeader = Join-Path $sdkDir "mdk\nrf.h"
$cmsisHeader = Join-Path $sdkDir "cmsis\core_cm4.h"
$s113Header = Join-Path $sdkDir "s113_headers\ble.h"
$sdObj = Join-Path $sdkDir "codal_ble\softdevice.o"

if (-not ((Test-Path $nrfHeader) -and (Test-Path $cmsisHeader) -and (Test-Path $s113Header) -and (Test-Path $sdObj))) {
    Write-Host "Fetching Nordic nRF52833 MDK, CMSIS Core, and S113 BLE SoftDevice SDK..."
    python -c @"
import os, urllib.request

def fetch(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with open(dst, 'wb') as f:
        f.write(urllib.request.urlopen(req, timeout=20).read())

sdk = r'$sdkDir'
mdk_base = 'https://raw.githubusercontent.com/NordicSemiconductor/nrfx/master/bsp/stable/mdk/'
mdk_files = [
    'nrf.h', 'nrf_erratas.h', 'nrf_mem.h', 'nrf_peripherals.h', 'nrf_vectors.h',
    'common/compiler_abstraction.h', 'common/nrf_common.ld', 'common/startup_nrf_common.c', 'common/system_nrf.h',
    'nrf52/nrf51_to_nrf52.h', 'nrf52/nrf52.h', 'nrf52/nrf52_bitfields.h', 'nrf52/nrf52_common.ld',
    'nrf52/nrf52_erratas.h', 'nrf52/nrf52_to_nrf52833.h', 'nrf52/system_nrf52.c', 'nrf52/system_nrf52.h',
    'nrf52/system_nrf52_approtect.h',
    'nrf52/nrf52833/gcc_startup_nrf52833.S', 'nrf52/nrf52833/nrf52833.h',
    'nrf52/nrf52833/nrf52833_bitfields.h', 'nrf52/nrf52833/nrf52833_peripherals.h',
    'nrf52/nrf52833/nrf52833_vectors.h', 'nrf52/nrf52833/nrf52833_xxaa.ld',
    'nrf52/nrf52833/nrf52833_xxaa_memory.h', 'nrf52/nrf52833/system_nrf52833.c',
    'nrf52/nrf52833/system_nrf52833.h',
]
for rel in mdk_files:
    dst = os.path.join(sdk, 'mdk', rel.replace('/', os.sep))
    if not os.path.isfile(dst):
        fetch(mdk_base + rel, dst)

cmsis_base = 'https://raw.githubusercontent.com/ARM-Software/CMSIS_5/develop/CMSIS/Core/Include/'
for cf in ['core_cm4.h', 'cmsis_version.h', 'cmsis_compiler.h', 'cmsis_gcc.h', 'mpu_armv7.h']:
    dst = os.path.join(sdk, 'cmsis', cf)
    if not os.path.isfile(dst):
        fetch(cmsis_base + cf, dst)

s113_base = 'https://raw.githubusercontent.com/microbit-foundation/codal-microbit-nrf5sdk/master/nRF5SDK/components/softdevice/s113/headers/'
s113_files = [
    'ble.h', 'ble_err.h', 'ble_gap.h', 'ble_gatt.h', 'ble_gattc.h', 'ble_gatts.h',
    'ble_hci.h', 'ble_l2cap.h', 'ble_ranges.h', 'ble_types.h', 'nrf_error.h',
    'nrf_error_sdm.h', 'nrf_error_soc.h', 'nrf_nvic.h', 'nrf_sd_def.h', 'nrf_sdm.h',
    'nrf_soc.h', 'nrf_svc.h', 'nrf52/nrf_mbr.h',
]
for sf in s113_files:
    dst = os.path.join(sdk, 's113_headers', sf.replace('/', os.sep))
    if not os.path.isfile(dst):
        fetch(s113_base + sf, dst)

codal_lib_base = 'https://raw.githubusercontent.com/lancaster-university/codal-microbit-v2/master/lib/'
for objf in ['mbr.o', 'softdevice.o', 'bootloader.o', 'settings.o', 'uicr.o']:
    dst = os.path.join(sdk, 'codal_ble', objf)
    if not os.path.isfile(dst):
        fetch(codal_lib_base + objf, dst)
"@
    if ($LASTEXITCODE -ne 0) { throw "Failed to download nRF52833 MDK / S113 SoftDevice SDK" }
}

# 3. Compile startup, system_nrf52833, and main.cpp
$commonFlags = @(
    "-mcpu=cortex-m4",
    "-mthumb",
    "-mfloat-abi=hard",
    "-mfpu=fpv4-sp-d16",
    "-DNRF52833_XXAA",
    "-DSOFTDEVICE_PRESENT",
    "-DS113",
    "-DNRF_SD_BLE_API_VERSION=7",
    "-DCONFIG_NFCT_PINS_AS_GPIOS",
    "-DCONFIG_GPIO_AS_PINRESET",
    "-D__STACK_SIZE=4096",
    "-D__HEAP_SIZE=0",
    "-Os",
    "-g3",
    "-Wall",
    "-Wextra",
    "-ffunction-sections",
    "-fdata-sections",
    "-isystem", "$sdkDir\cmsis",
    "-isystem", "$sdkDir\mdk",
    "-isystem", "$sdkDir\mdk\common",
    "-isystem", "$sdkDir\mdk\nrf52",
    "-isystem", "$sdkDir\mdk\nrf52\nrf52833",
    "-isystem", "$sdkDir\s113_headers",
    "-isystem", "$sdkDir\s113_headers\nrf52"
)

$objDir = Join-Path $buildDir "obj"
New-Item -ItemType Directory -Force -Path $objDir | Out-Null

Write-Host "Compiling startup & system files..."
& $gcc @commonFlags -c "$sdkDir\mdk\nrf52\nrf52833\gcc_startup_nrf52833.S" -o "$objDir\startup.o"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $gcc @commonFlags -c "$sdkDir\mdk\nrf52\nrf52833\system_nrf52833.c" -o "$objDir\system_nrf52833.o"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Compiling main.cpp..."
& $gxx @commonFlags -std=c++17 -fno-exceptions -fno-rtti -fno-use-cxa-atexit -fno-threadsafe-statics `
    -c "$PSScriptRoot\main.cpp" -o "$objDir\main.o"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. Link unified ELF (MBR + S113 SoftDevice + App + Bootloader) & generate Intel HEX
$elfOut = Join-Path $buildDir "spikerbit_firmware.elf"
$hexOut = Join-Path $PSScriptRoot "spikerbit_firmware.hex"
$bleObjDir = Join-Path $sdkDir "codal_ble"

Write-Host "Linking unified S113 BLE + USB spikerbit_firmware.elf..."
& $gxx @commonFlags `
    "--specs=nano.specs", `
    "--specs=nosys.specs", `
    "-Wl,--gc-sections", `
    "-Wl,--no-warn-rwx-segments", `
    "-Wl,-Map=$buildDir\spikerbit_firmware.map", `
    "-L$sdkDir\mdk\common", `
    "-L$sdkDir\mdk\nrf52", `
    "-L$sdkDir\mdk\nrf52\nrf52833", `
    "-T$PSScriptRoot\nrf52833_s113.ld", `
    "$bleObjDir\mbr.o", `
    "$bleObjDir\softdevice.o", `
    "$bleObjDir\bootloader.o", `
    "$bleObjDir\settings.o", `
    "$bleObjDir\uicr.o", `
    "$objDir\startup.o", `
    "$objDir\system_nrf52833.o", `
    "$objDir\main.o", `
    "-o", $elfOut
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $sizeTool $elfOut
& $objcopy -O ihex $elfOut $hexOut
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "SUCCESS: Generated $hexOut ($((Get-Item $hexOut).Length) bytes)"

if ($Target -eq "flash") {
    python -c "import sys; sys.path.insert(0, r'$(Split-Path -Parent (Split-Path -Parent $PSScriptRoot))'); from spikerbit.flasher import flash_firmware; print('Flashed to:', flash_firmware(verify_remount=True))"
    exit $LASTEXITCODE
}
