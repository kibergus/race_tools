/* Linker script for BBC micro:bit V2 (nRF52833_xxAA) with Nordic S113 SoftDevice */

SEARCH_DIR(.)
GROUP(-lgcc -lc)

MEMORY
{
  MBR (rx)        : ORIGIN = 0x00000000, LENGTH = 0x01000
  SD (rx)         : ORIGIN = 0x00001000, LENGTH = 0x1B000
  FLASH (rx)      : ORIGIN = 0x0001C000, LENGTH = 0x5B000
  BOOTLOADER (rx) : ORIGIN = 0x00077000, LENGTH = 0x07000
  SETTINGS (rx)   : ORIGIN = 0x0007E000, LENGTH = 0x02000
  UICR (rx)       : ORIGIN = 0x10001014, LENGTH = 0x00008
  RAM (rwx)       : ORIGIN = 0x20004000, LENGTH = 0x1C000
  CODE_RAM (rwx)  : ORIGIN = 0x00804000, LENGTH = 0x1C000
}

SECTIONS
{
  .mbr :
  {
    KEEP(*(.mbr))
  } > MBR

  .softdevice :
  {
    KEEP(*(.softdevice))
  } > SD
}

INCLUDE "nrf_common.ld"

SECTIONS
{
  .bootloader :
  {
    KEEP(*(.bootloader))
  } > BOOTLOADER

  .settings :
  {
    KEEP(*(.settings))
  } > SETTINGS

  .uicr :
  {
    KEEP(*(.uicr))
  } > UICR
}