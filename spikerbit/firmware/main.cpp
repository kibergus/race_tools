// Copyright 2026 Alexey Guseynov. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// ==============================================================================
//
// BBC micro:bit V2 (nRF52833) 500 Hz Hardware Timer + PPI + SAADC EasyDMA
// Dual-Transport (BLE Wireless + USB Serial) BRT1 Streamer for Spiker:bit
//
// Hardware & BLE pipeline:
//   1. Nordic S113 SoftDevice enabled (LFCLK RC calibrated + continuous 16 MHz
//      HFXO crystal locked via sd_clock_hfclk_request() for <30 ppm timing).
//   2. Full galvanic isolation mode: powered by Spiker:bit 2x AAA batteries
//      with USB unplugged, streaming over BLE GATT Notifications (MTU up to 247,
//      TX power 0 dBm, LDO mode for zero DC/DC switching ripple). Also supports
//      115200-baud USB CDC UART simultaneously when plugged in via USB.
//   3. LED matrix, speaker (P0.00), and mic bias (P0.20) powered down completely.
//   4. P8 (P0.10) / P9 (P0.09) digital output mux selects Spiker:bit filter bank:
//      - EEG: P8 = 0, P9 = 1
//      - ECG: P8 = 1, P9 = 0
//      - EMG: P8 = 0, P9 = 0
//   5. NRF_TIMER2 (16 MHz, Prescaler 0) fires EVENTS_COMPARE[0] at 4000 Hz
//      (nominal CC[0] = 4000 ticks = 250 us, trimmable via host T: command).
//      NRF_TIMER1 runs at 1 MHz (1 us ticks) to interpolate exact sub-block
//      timestamps for P:<t0_us> -> 0x06 PONG min-RTT BLE clock synchronization.
//   6. PPI channel 6 routes TIMER2->EVENTS_COMPARE[0] -> SAADC->TASKS_SAMPLE
//      and PPI channel 7 routes SAADC->EVENTS_END -> SAADC->TASKS_START purely
//      in silicon (zero radio/ISR jitter).
//   7. NRF_SAADC samples P1 (P0.03 / AIN1, ratiometric 0..VDD) at 10-bit
//      resolution with 8x hardware oversampling (4000 Hz / 8 = 500.0 Hz).

#include "nrf.h"
#include "ble.h"
#include "ble_err.h"
#include "ble_gap.h"
#include "ble_gatt.h"
#include "ble_gatts.h"
#include "ble_hci.h"
#include "nrf_error.h"
#include "nrf_sdm.h"
#include "nrf_soc.h"

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace {

constexpr int kSamplesPerChunk = 10;           // 10 samples @ 500 Hz = 20 ms per chunk
constexpr int kBaselineIntervalChunks = 250;   // 250 chunks * 20 ms = 5.0 seconds
constexpr uint32_t kUartWatchdogChunks = 500;  // Pause UART TX after 10s without host cmd
constexpr int64_t kDefaultDt100us = 20;        // 20 * 100 us = 2.000 ms (500 Hz)
constexpr uint32_t kDefaultTimerCc = 4000;     // 4000 ticks @ 16 MHz = 250 us
constexpr uint32_t kMinTimerCc = 3960;         // -1% clamp
constexpr uint32_t kMaxTimerCc = 4040;         // +1% clamp

// BLE configuration constants
constexpr uint8_t kBleConnCfgTag = 1;
constexpr uint16_t kMaxAttMtu = 247;
constexpr uint16_t kDefaultBlePayload = 20;
constexpr uint16_t kMaxBlePayload = kMaxAttMtu - 3;  // 244 bytes
constexpr uint32_t kBleCoalesceThreshold = 120;      // Batch ~3 chunks (60 ms) per notify
constexpr uint32_t kAppRamBase = 0x20004000UL;

// Spiker:bit 128-bit Vendor-Specific UUIDs:
//   Service: 5b170001-b5a3-f393-e0a9-e50e24dcca9e
//   RX Char: 5b170002-b5a3-f393-e0a9-e50e24dcca9e (Write / Write Without Response)
//   TX Char: 5b170003-b5a3-f393-e0a9-e50e24dcca9e (Notify)
constexpr ble_uuid128_t kSpikerBaseUuid128 = {{
    0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
    0x93, 0xF3, 0xA3, 0xB5, 0x00, 0x00, 0x17, 0x5B,
}};
constexpr uint16_t kUuidService = 0x0001;
constexpr uint16_t kUuidRxChar = 0x0002;
constexpr uint16_t kUuidTxChar = 0x0003;

// BBC micro:bit V2 (nRF52833) pin assignments
constexpr uint32_t kPinSpeaker = 0;           // P0.00
constexpr uint32_t kPinP1Analog = 3;          // P0.03 (AIN1)
constexpr uint32_t kPinUartTx = 6;            // P0.06 (Target -> DAPLink USB UART TX)
constexpr uint32_t kPinUartRxPort1 = 8;       // P1.08 (DAPLink -> Target USB UART RX)
constexpr uint32_t kUartRxPsel = (1UL << 5) | kPinUartRxPort1;
constexpr uint32_t kPinP9 = 9;                // P0.09 (Spiker:bit filter select)
constexpr uint32_t kPinP8 = 10;               // P0.10 (Spiker:bit filter select)
constexpr uint32_t kPinRunMic = 20;           // P0.20 (MEMS mic bias rail)

constexpr uint32_t kLedRowMaskP0 =
    (1UL << 21) | (1UL << 22) | (1UL << 15) | (1UL << 24) | (1UL << 19);

enum class SpikerMode : uint8_t {
  EEG = 0,
  ECG = 1,
  EMG = 2,
};

enum class TransportSource : uint8_t {
  kUart = 0,
  kBle = 1,
};

// Critical section helper masking ONLY app priorities (>= 2), never SoftDevice (0, 1)
inline void EnterAppCritical() {
  __set_BASEPRI(2U << (8U - __NVIC_PRIO_BITS));
}

inline void ExitAppCritical() {
  __set_BASEPRI(0U);
}

// Double-buffered SAADC EasyDMA buffers (10 x int16_t each)
alignas(4) volatile int16_t g_dma_buf[2][kSamplesPerChunk];
volatile uint8_t g_active_dma_bank = 0;
volatile bool g_first_dma_started = false;
volatile int16_t g_ready_samples[kSamplesPerChunk];
volatile bool g_block_ready = false;
volatile uint32_t g_last_dma_end_us = 0;

// Lock-free 256-byte UART RX ring buffer populated by UARTE0_UART0_IRQHandler
constexpr uint32_t kUartRxRingSize = 256;
volatile uint8_t g_uart_rx_ring[kUartRxRingSize];
volatile uint32_t g_uart_rx_head = 0;
volatile uint32_t g_uart_rx_tail = 0;

// Outgoing BLE GATT Notification ring buffer (2048 bytes, power of 2)
constexpr uint32_t kBleTxRingSize = 2048;
uint8_t g_ble_tx_ring[kBleTxRingSize];
uint32_t g_ble_tx_head = 0;
uint32_t g_ble_tx_tail = 0;

// BLE GATT state
uint8_t g_vs_uuid_type = BLE_UUID_TYPE_VENDOR_BEGIN;
uint16_t g_service_handle = BLE_GATT_HANDLE_INVALID;
ble_gatts_char_handles_t g_rx_handles{};
ble_gatts_char_handles_t g_tx_handles{};
uint8_t g_rx_value_buf[64];
uint8_t g_tx_value_buf[kMaxBlePayload];

uint8_t g_adv_handle = BLE_GAP_ADV_SET_HANDLE_NOT_SET;
uint8_t g_adv_payload[31];
uint8_t g_scan_rsp_payload[31];
ble_gap_adv_data_t g_adv_data{};

volatile uint16_t g_ble_conn_handle = BLE_CONN_HANDLE_INVALID;
volatile bool g_ble_notify_enabled = false;
volatile uint16_t g_ble_max_payload = kDefaultBlePayload;

// Fixed-point clock & streaming state in 100 us units (scale = 0.0001 s)
volatile int64_t g_time_100us = 0;
volatile bool g_uart_streaming_enabled = false;
volatile bool g_ble_streaming_enabled = false;
volatile bool g_force_baseline = true;
volatile bool g_send_magic = true;
uint32_t g_chunks_since_baseline = 0;
uint32_t g_uart_watchdog_chunks = 0;

int64_t g_tracked_time_100us = 0;
int16_t g_tracked_adc = 512;
volatile SpikerMode g_current_mode = SpikerMode::EEG;

extern "C" void SoftDeviceFaultHandler(uint32_t /*id*/, uint32_t /*pc*/, uint32_t /*info*/) {
  NVIC_SystemReset();
}

extern "C" void SWI2_EGU2_IRQHandler(void) {
  // Wakes sd_app_evt_wait() immediately on any pending BLE or SoC SoftDevice event
}

bool StartsWith(const char* str, const char* prefix) {
  while (*prefix != '\0') {
    if (*str != *prefix) return false;
    ++str;
    ++prefix;
  }
  return true;
}

int64_t ParseInt64(const char* str, const char** out_end) {
  const char* p = str;
  bool neg = false;
  if (*p == '-') {
    neg = true;
    ++p;
  } else if (*p == '+') {
    ++p;
  }
  int64_t val = 0;
  while (*p >= '0' && *p <= '9') {
    val = val * 10 + static_cast<int64_t>(*p - '0');
    ++p;
  }
  if (out_end != nullptr) {
    *out_end = p;
  }
  return neg ? -val : val;
}

char HexDigit(uint8_t nibble) {
  nibble &= 0x0FU;
  return (nibble < 10U) ? static_cast<char>('0' + nibble)
                        : static_cast<char>('A' + (nibble - 10U));
}

void InitQuietBoardGpios() {
  const uint32_t quiet_low_mask =
      kLedRowMaskP0 | (1UL << kPinSpeaker) | (1UL << kPinRunMic);
  NRF_P0->OUTCLR = quiet_low_mask;
  NRF_P0->DIRSET = quiet_low_mask;

  NRF_P0->PIN_CNF[kPinP1Analog] =
      (GPIO_PIN_CNF_DIR_Input << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Disconnect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Disabled << GPIO_PIN_CNF_PULL_Pos);

  NRF_P0->PIN_CNF[kPinP8] =
      (GPIO_PIN_CNF_DIR_Output << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Disconnect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Disabled << GPIO_PIN_CNF_PULL_Pos) |
      (GPIO_PIN_CNF_DRIVE_S0S1 << GPIO_PIN_CNF_DRIVE_Pos);
  NRF_P0->PIN_CNF[kPinP9] =
      (GPIO_PIN_CNF_DIR_Output << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Disconnect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Disabled << GPIO_PIN_CNF_PULL_Pos) |
      (GPIO_PIN_CNF_DRIVE_S0S1 << GPIO_PIN_CNF_DRIVE_Pos);
}

void SetSpikerModePins(SpikerMode mode) {
  g_current_mode = mode;
  switch (mode) {
    case SpikerMode::EEG:
      NRF_P0->OUTCLR = (1UL << kPinP8);
      NRF_P0->OUTSET = (1UL << kPinP9);
      break;
    case SpikerMode::ECG:
      NRF_P0->OUTSET = (1UL << kPinP8);
      NRF_P0->OUTCLR = (1UL << kPinP9);
      break;
    case SpikerMode::EMG:
      NRF_P0->OUTCLR = (1UL << kPinP8) | (1UL << kPinP9);
      break;
  }
  g_force_baseline = true;
}

void InitUsbSerial115200() {
  NRF_UART0->ENABLE = UART_ENABLE_ENABLE_Disabled;

  NRF_P0->OUTSET = (1UL << kPinUartTx);
  NRF_P0->DIRSET = (1UL << kPinUartTx);
  NRF_P1->PIN_CNF[kPinUartRxPort1] =
      (GPIO_PIN_CNF_DIR_Input << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Connect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Pullup << GPIO_PIN_CNF_PULL_Pos);

  NRF_UART0->PSEL.RTS = 0xFFFFFFFFUL;
  NRF_UART0->PSEL.CTS = 0xFFFFFFFFUL;
  NRF_UART0->PSEL.TXD = kPinUartTx;
  NRF_UART0->PSEL.RXD = kUartRxPsel;
  NRF_UART0->CONFIG = 0;
  NRF_UART0->BAUDRATE = UART_BAUDRATE_BAUDRATE_Baud115200;

  NRF_UART0->EVENTS_RXDRDY = 0;
  NRF_UART0->EVENTS_TXDRDY = 0;
  NRF_UART0->EVENTS_ERROR = 0;
  NRF_UART0->ERRORSRC = NRF_UART0->ERRORSRC;

  NRF_UART0->INTENSET = UART_INTENSET_RXDRDY_Msk | UART_INTENSET_ERROR_Msk;
  NVIC_SetPriority(UARTE0_UART0_IRQn, 3);
  NVIC_EnableIRQ(UARTE0_UART0_IRQn);

  NRF_UART0->ENABLE = UART_ENABLE_ENABLE_Enabled;
  NRF_UART0->TASKS_STARTRX = 1;
  NRF_UART0->TASKS_STARTTX = 1;
}

extern "C" void UARTE0_UART0_IRQHandler(void) {
  if (NRF_UART0->EVENTS_ERROR) {
    NRF_UART0->EVENTS_ERROR = 0;
    NRF_UART0->ERRORSRC = NRF_UART0->ERRORSRC;
    NRF_UART0->TASKS_STARTRX = 1;
  }
  while (NRF_UART0->EVENTS_RXDRDY) {
    NRF_UART0->EVENTS_RXDRDY = 0;
    uint8_t byte_in = static_cast<uint8_t>(NRF_UART0->RXD);
    uint32_t next_head = (g_uart_rx_head + 1U) & (kUartRxRingSize - 1U);
    if (next_head != g_uart_rx_tail) {
      g_uart_rx_ring[g_uart_rx_head] = byte_in;
      g_uart_rx_head = next_head;
    }
  }
}

extern "C" void SAADC_IRQHandler(void) {
  if (NRF_SAADC->EVENTS_END) {
    NRF_SAADC->EVENTS_END = 0;
    NRF_TIMER1->TASKS_CAPTURE[0] = 1;
    g_last_dma_end_us = NRF_TIMER1->CC[0];

    uint8_t completed_bank = g_active_dma_bank;
    g_active_dma_bank ^= 1U;
    for (int i = 0; i < kSamplesPerChunk; ++i) {
      g_ready_samples[i] = g_dma_buf[completed_bank][i];
    }
    g_block_ready = true;
  }

  if (NRF_SAADC->EVENTS_STARTED) {
    NRF_SAADC->EVENTS_STARTED = 0;
    uint8_t next_bank = g_active_dma_bank ^ 1U;
    NRF_SAADC->RESULT.PTR =
        reinterpret_cast<uint32_t>(const_cast<int16_t*>(g_dma_buf[next_bank]));
    NRF_SAADC->RESULT.MAXCNT = kSamplesPerChunk;
    g_first_dma_started = true;
  }
}

int64_t GetExactMcuTime100us() {
  EnterAppCritical();
  int64_t base_100us = g_time_100us;
  bool pending_block = g_block_ready;
  uint32_t last_end_us = g_last_dma_end_us;
  NRF_TIMER1->TASKS_CAPTURE[1] = 1;
  uint32_t now_us = NRF_TIMER1->CC[1];
  ExitAppCritical();

  if (pending_block) {
    base_100us += kSamplesPerChunk * kDefaultDt100us;
  }
  uint32_t elapsed_us = now_us - last_end_us;
  if (elapsed_us > 25000U) {
    elapsed_us = 25000U;
  }
  return base_100us + static_cast<int64_t>(elapsed_us / 100U);
}

void StartBleAdvertising() {
  ble_gap_adv_params_t adv_params;
  std::memset(&adv_params, 0, sizeof(adv_params));
  adv_params.properties.type =
      BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED;
  adv_params.p_peer_addr = nullptr;
  adv_params.filter_policy = BLE_GAP_ADV_FP_ANY;
  adv_params.interval = 64;  // 64 * 0.625 ms = 40 ms fast discovery
  adv_params.duration = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
  adv_params.primary_phy = BLE_GAP_PHY_1MBPS;

  (void)sd_ble_gap_adv_set_configure(&g_adv_handle, &g_adv_data, &adv_params);
  (void)sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_ADV, g_adv_handle, 0);
  (void)sd_ble_gap_adv_start(g_adv_handle, kBleConnCfgTag);
}

void InitSoftDeviceAndBle() {
  (void)sd_softdevice_vector_table_base_set(0x0001C000UL);

  // 1. Enable S113 SoftDevice with calibrated internal LFCLK RC (micro:bit V2 has no 32kHz XTAL)
  nrf_clock_lf_cfg_t clock_lf_cfg{};
  clock_lf_cfg.source = NRF_CLOCK_LF_SRC_RC;
  clock_lf_cfg.rc_ctiv = 16;
  clock_lf_cfg.rc_temp_ctiv = 2;
  clock_lf_cfg.accuracy = NRF_CLOCK_LF_ACCURACY_500_PPM;
  (void)sd_softdevice_enable(&clock_lf_cfg, SoftDeviceFaultHandler);

  // Enable SoftDevice event interrupt (SWI2) at app low priority 6
  NVIC_SetPriority(SWI2_EGU2_IRQn, 6);
  NVIC_EnableIRQ(SWI2_EGU2_IRQn);

  // 2. Lock 16 MHz HFXO external crystal continuously for <30 ppm ADC timing
  (void)sd_clock_hfclk_request();
  uint32_t hfclk_running = 0;
  while (hfclk_running == 0) {
    (void)sd_clock_hfclk_is_running(&hfclk_running);
  }

  // 3. Configure BLE stack parameters (MTU 247, DLE 251, HVN queue 12)
  ble_cfg_t ble_cfg;

  std::memset(&ble_cfg, 0, sizeof(ble_cfg));
  ble_cfg.common_cfg.vs_uuid_cfg.vs_uuid_count = 2;
  (void)sd_ble_cfg_set(BLE_COMMON_CFG_VS_UUID, &ble_cfg, kAppRamBase);

  std::memset(&ble_cfg, 0, sizeof(ble_cfg));
  ble_cfg.conn_cfg.conn_cfg_tag = kBleConnCfgTag;
  ble_cfg.conn_cfg.params.gatt_conn_cfg.att_mtu = kMaxAttMtu;
  (void)sd_ble_cfg_set(BLE_CONN_CFG_GATT, &ble_cfg, kAppRamBase);

  std::memset(&ble_cfg, 0, sizeof(ble_cfg));
  ble_cfg.conn_cfg.conn_cfg_tag = kBleConnCfgTag;
  ble_cfg.conn_cfg.params.gap_conn_cfg.conn_count = 1;
  ble_cfg.conn_cfg.params.gap_conn_cfg.event_length = 6;
  (void)sd_ble_cfg_set(BLE_CONN_CFG_GAP, &ble_cfg, kAppRamBase);

  std::memset(&ble_cfg, 0, sizeof(ble_cfg));
  ble_cfg.conn_cfg.conn_cfg_tag = kBleConnCfgTag;
  ble_cfg.conn_cfg.params.gatts_conn_cfg.hvn_tx_queue_size = 12;
  (void)sd_ble_cfg_set(BLE_CONN_CFG_GATTS, &ble_cfg, kAppRamBase);

  uint32_t ram_base = kAppRamBase;
  (void)sd_ble_enable(&ram_base);

  // 4. Build unique device name "Spiker:bit XXXX" from FICR device address
  uint32_t dev_id = NRF_FICR->DEVICEADDR[0];
  char dev_name[16] = {
      'S', 'p', 'i', 'k', 'e', 'r', ':', 'b', 'i', 't', ' ',
      HexDigit(static_cast<uint8_t>((dev_id >> 12) & 0xFU)),
      HexDigit(static_cast<uint8_t>((dev_id >> 8) & 0xFU)),
      HexDigit(static_cast<uint8_t>((dev_id >> 4) & 0xFU)),
      HexDigit(static_cast<uint8_t>(dev_id & 0xFU)),
      '\0',
  };

  ble_gap_conn_sec_mode_t open_perm;
  BLE_GAP_CONN_SEC_MODE_SET_OPEN(&open_perm);
  (void)sd_ble_gap_device_name_set(
      &open_perm, reinterpret_cast<const uint8_t*>(dev_name), 15);

  ble_gap_conn_params_t ppcp{};
  ppcp.min_conn_interval = 6;    // 7.5 ms
  ppcp.max_conn_interval = 12;   // 15.0 ms
  ppcp.slave_latency = 0;
  ppcp.conn_sup_timeout = 400;   // 4.0 s
  (void)sd_ble_gap_ppcp_set(&ppcp);

  // 5. Register 128-bit Spiker:bit GATT Service + RX (Write) + TX (Notify) characteristics
  (void)sd_ble_uuid_vs_add(&kSpikerBaseUuid128, &g_vs_uuid_type);

  ble_uuid_t service_uuid{};
  service_uuid.type = g_vs_uuid_type;
  service_uuid.uuid = kUuidService;
  (void)sd_ble_gatts_service_add(
      BLE_GATTS_SRVC_TYPE_PRIMARY, &service_uuid, &g_service_handle);

  // RX Characteristic (Host -> MCU commands)
  {
    ble_gatts_char_md_t char_md{};
    char_md.char_props.write = 1;
    char_md.char_props.write_wo_resp = 1;

    ble_uuid_t rx_uuid{};
    rx_uuid.type = g_vs_uuid_type;
    rx_uuid.uuid = kUuidRxChar;

    ble_gatts_attr_md_t attr_md{};
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.write_perm);
    attr_md.vloc = BLE_GATTS_VLOC_USER;
    attr_md.vlen = 1;

    ble_gatts_attr_t attr_char_value{};
    attr_char_value.p_uuid = &rx_uuid;
    attr_char_value.p_attr_md = &attr_md;
    attr_char_value.init_len = 0;
    attr_char_value.init_offs = 0;
    attr_char_value.max_len = sizeof(g_rx_value_buf);
    attr_char_value.p_value = g_rx_value_buf;

    (void)sd_ble_gatts_characteristic_add(
        g_service_handle, &char_md, &attr_char_value, &g_rx_handles);
  }

  // TX Characteristic (MCU -> Host BRT1 stream & PONG notifications)
  {
    ble_gatts_attr_md_t cccd_md{};
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.write_perm);
    cccd_md.vloc = BLE_GATTS_VLOC_STACK;

    ble_gatts_char_md_t char_md{};
    char_md.char_props.notify = 1;
    char_md.p_cccd_md = &cccd_md;

    ble_uuid_t tx_uuid{};
    tx_uuid.type = g_vs_uuid_type;
    tx_uuid.uuid = kUuidTxChar;

    ble_gatts_attr_md_t attr_md{};
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_NO_ACCESS(&attr_md.write_perm);
    attr_md.vloc = BLE_GATTS_VLOC_USER;
    attr_md.vlen = 1;

    ble_gatts_attr_t attr_char_value{};
    attr_char_value.p_uuid = &tx_uuid;
    attr_char_value.p_attr_md = &attr_md;
    attr_char_value.init_len = 0;
    attr_char_value.init_offs = 0;
    attr_char_value.max_len = sizeof(g_tx_value_buf);
    attr_char_value.p_value = g_tx_value_buf;

    (void)sd_ble_gatts_characteristic_add(
        g_service_handle, &char_md, &attr_char_value, &g_tx_handles);
  }

  // 6. Encode Primary Advertisement (Flags + Complete Name) & Scan Response (128-bit UUID)
  size_t adv_len = 0;
  g_adv_payload[adv_len++] = 2;
  g_adv_payload[adv_len++] = BLE_GAP_AD_TYPE_FLAGS;
  g_adv_payload[adv_len++] = BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE;

  g_adv_payload[adv_len++] = 16;  // 1 type byte + 15 name chars
  g_adv_payload[adv_len++] = BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME;
  std::memcpy(&g_adv_payload[adv_len], dev_name, 15);
  adv_len += 15;

  size_t sr_len = 0;
  g_scan_rsp_payload[sr_len++] = 17;  // 1 type byte + 16 UUID bytes
  g_scan_rsp_payload[sr_len++] = BLE_GAP_AD_TYPE_128BIT_SERVICE_UUID_COMPLETE;
  std::memcpy(&g_scan_rsp_payload[sr_len], kSpikerBaseUuid128.uuid128, 16);
  g_scan_rsp_payload[sr_len + 12] = static_cast<uint8_t>(kUuidService & 0xFFU);
  g_scan_rsp_payload[sr_len + 13] = static_cast<uint8_t>((kUuidService >> 8) & 0xFFU);
  sr_len += 16;

  g_adv_data.adv_data.p_data = g_adv_payload;
  g_adv_data.adv_data.len = static_cast<uint16_t>(adv_len);
  g_adv_data.scan_rsp_data.p_data = g_scan_rsp_payload;
  g_adv_data.scan_rsp_data.len = static_cast<uint16_t>(sr_len);

  StartBleAdvertising();
}

void InitHardwareTimersAndDmaAdc() {
  // 1. Free-running 1 MHz (1 us) hardware clock on TIMER1 for sub-100us PONG interpolation
  NRF_TIMER1->TASKS_STOP = 1;
  NRF_TIMER1->MODE = TIMER_MODE_MODE_Timer;
  NRF_TIMER1->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
  NRF_TIMER1->PRESCALER = 4;  // 16 MHz / 2^4 = 1.0 MHz (1 us per tick)
  NRF_TIMER1->TASKS_CLEAR = 1;
  NRF_TIMER1->TASKS_START = 1;

  // 2. Configure SAADC on P1 (AIN1 = P0.03 on micro:bit V2)
  NRF_SAADC->ENABLE = 0;
  NRF_SAADC->RESOLUTION = SAADC_RESOLUTION_VAL_10bit;
  NRF_SAADC->OVERSAMPLE = SAADC_OVERSAMPLE_OVERSAMPLE_Bypass;
  NRF_SAADC->SAMPLERATE = (SAADC_SAMPLERATE_MODE_Task << SAADC_SAMPLERATE_MODE_Pos);

  NRF_SAADC->CH[0].PSELP = SAADC_CH_PSELP_PSELP_AnalogInput1;
  NRF_SAADC->CH[0].PSELN = SAADC_CH_PSELN_PSELN_NC;
  NRF_SAADC->CH[0].CONFIG =
      (SAADC_CH_CONFIG_RESP_Bypass << SAADC_CH_CONFIG_RESP_Pos) |
      (SAADC_CH_CONFIG_RESN_Bypass << SAADC_CH_CONFIG_RESN_Pos) |
      (SAADC_CH_CONFIG_GAIN_Gain1_4 << SAADC_CH_CONFIG_GAIN_Pos) |
      (SAADC_CH_CONFIG_REFSEL_VDD1_4 << SAADC_CH_CONFIG_REFSEL_Pos) |
      (SAADC_CH_CONFIG_TACQ_10us << SAADC_CH_CONFIG_TACQ_Pos) |
      (SAADC_CH_CONFIG_MODE_SE << SAADC_CH_CONFIG_MODE_Pos) |
      (SAADC_CH_CONFIG_BURST_Disabled << SAADC_CH_CONFIG_BURST_Pos);

  NRF_SAADC->ENABLE = SAADC_ENABLE_ENABLE_Enabled;
  NRF_SAADC->EVENTS_CALIBRATEDONE = 0;
  NRF_SAADC->TASKS_CALIBRATEOFFSET = 1;
  while (NRF_SAADC->EVENTS_CALIBRATEDONE == 0) {
  }
  NRF_SAADC->EVENTS_CALIBRATEDONE = 0;
  NRF_SAADC->EVENTS_DONE = 0;
  NRF_SAADC->EVENTS_RESULTDONE = 0;

  NRF_SAADC->OVERSAMPLE = SAADC_OVERSAMPLE_OVERSAMPLE_Over8x;

  g_active_dma_bank = 0;
  g_first_dma_started = false;
  NRF_SAADC->RESULT.PTR =
      reinterpret_cast<uint32_t>(const_cast<int16_t*>(g_dma_buf[0]));
  NRF_SAADC->RESULT.MAXCNT = kSamplesPerChunk;

  NRF_SAADC->EVENTS_STARTED = 0;
  NRF_SAADC->EVENTS_END = 0;
  NRF_SAADC->INTENSET = SAADC_INTENSET_STARTED_Msk | SAADC_INTENSET_END_Msk;
  // Priority 2 is the highest application interrupt priority permitted by S113 SoftDevice
  NVIC_SetPriority(SAADC_IRQn, 2);
  NVIC_EnableIRQ(SAADC_IRQn);

  NRF_SAADC->TASKS_START = 1;
  while (!g_first_dma_started) {
  }

  // 3. Configure NRF_TIMER2 at 16 MHz, CC[0] = 4000 ticks (4000 Hz / 8x = 500 Hz)
  NRF_TIMER2->TASKS_STOP = 1;
  NRF_TIMER2->MODE = TIMER_MODE_MODE_Timer;
  NRF_TIMER2->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
  NRF_TIMER2->PRESCALER = 0;  // 16 MHz
  NRF_TIMER2->CC[0] = kDefaultTimerCc;
  NRF_TIMER2->SHORTS = TIMER_SHORTS_COMPARE0_CLEAR_Msk;
  NRF_TIMER2->TASKS_CLEAR = 1;

  NRF_TIMER1->TASKS_CAPTURE[0] = 1;
  g_last_dma_end_us = NRF_TIMER1->CC[0];

  // 4. Route TIMER2->EVENTS_COMPARE[0] -> SAADC->TASKS_SAMPLE (PPI ch 6) and
  //    SAADC->EVENTS_END -> SAADC->TASKS_START (PPI ch 7) via SoftDevice SoC API
  (void)sd_ppi_channel_assign(
      6, &NRF_TIMER2->EVENTS_COMPARE[0], &NRF_SAADC->TASKS_SAMPLE);
  (void)sd_ppi_channel_assign(
      7, &NRF_SAADC->EVENTS_END, &NRF_SAADC->TASKS_START);
  (void)sd_ppi_channel_enable_set((1UL << 6) | (1UL << 7));

  NRF_TIMER2->TASKS_START = 1;
}

uint32_t BleTxRingCount() {
  return (g_ble_tx_head - g_ble_tx_tail) & (kBleTxRingSize - 1U);
}

uint32_t BleTxRingFree() {
  return (kBleTxRingSize - 1U) - BleTxRingCount();
}

void EnqueueBleBytes(const uint8_t* data, size_t len) {
  if (g_ble_conn_handle == BLE_CONN_HANDLE_INVALID || !g_ble_notify_enabled) {
    return;
  }
  if (len > BleTxRingFree()) {
    // Drop whole packet cleanly on RF stall and force self-healing Magic + Baseline
    g_ble_tx_head = 0;
    g_ble_tx_tail = 0;
    g_send_magic = true;
    g_force_baseline = true;
    return;
  }
  for (size_t i = 0; i < len; ++i) {
    g_ble_tx_ring[g_ble_tx_head] = data[i];
    g_ble_tx_head = (g_ble_tx_head + 1U) & (kBleTxRingSize - 1U);
  }
}

void FlushBleTxRing(bool force_immediate) {
  if (g_ble_conn_handle == BLE_CONN_HANDLE_INVALID || !g_ble_notify_enabled) {
    return;
  }

  uint16_t max_pkt = g_ble_max_payload;
  if (max_pkt < kDefaultBlePayload) max_pkt = kDefaultBlePayload;
  if (max_pkt > kMaxBlePayload) max_pkt = kMaxBlePayload;

  if (!force_immediate && max_pkt >= kBleCoalesceThreshold &&
      BleTxRingCount() < kBleCoalesceThreshold) {
    return;
  }

  uint8_t pkt_buf[kMaxBlePayload];
  while (BleTxRingCount() > 0) {
    uint32_t avail = BleTxRingCount();
    uint16_t send_len = (avail > max_pkt) ? max_pkt : static_cast<uint16_t>(avail);

    uint32_t idx = g_ble_tx_tail;
    for (uint16_t i = 0; i < send_len; ++i) {
      pkt_buf[i] = g_ble_tx_ring[idx];
      idx = (idx + 1U) & (kBleTxRingSize - 1U);
    }

    uint16_t hvx_len = send_len;
    ble_gatts_hvx_params_t hvx{};
    hvx.handle = g_tx_handles.value_handle;
    hvx.type = BLE_GATT_HVX_NOTIFICATION;
    hvx.offset = 0;
    hvx.p_len = &hvx_len;
    hvx.p_data = pkt_buf;

    uint32_t err = sd_ble_gatts_hvx(g_ble_conn_handle, &hvx);
    if (err == NRF_SUCCESS) {
      g_ble_tx_tail = (g_ble_tx_tail + hvx_len) & (kBleTxRingSize - 1U);
    } else if (err == BLE_ERROR_GATTS_SYS_ATTR_MISSING) {
      (void)sd_ble_gatts_sys_attr_set(g_ble_conn_handle, nullptr, 0, 0);
      break;
    } else {
      // NRF_ERROR_RESOURCES / BUSY: HVN TX queue full; will drain on HVN_TX_COMPLETE
      break;
    }
  }
}

void WriteUartBytes(const uint8_t* data, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    NRF_UART0->EVENTS_TXDRDY = 0;
    NRF_UART0->TXD = data[i];
    while (NRF_UART0->EVENTS_TXDRDY == 0) {
    }
  }
}

void BroadcastStreamBytes(const uint8_t* data, size_t len) {
  if (g_uart_streaming_enabled) {
    WriteUartBytes(data, len);
  }
  if (g_ble_streaming_enabled) {
    EnqueueBleBytes(data, len);
  }
}

void EmitMagicChunk() {
  static const uint8_t kMagicChunk[7] = {0x04, 0x00, 0x01, 'B', 'R', 'T', '1'};
  BroadcastStreamBytes(kMagicChunk, sizeof(kMagicChunk));
}

void EmitBaselineChunk(int64_t time_100us, int16_t adc_val) {
  uint8_t buf[19];
  buf[0] = 0x10;
  buf[1] = 0x00;
  buf[2] = 0x03;

  double time_sec = static_cast<double>(time_100us) * 0.0001;
  double spiker_val = static_cast<double>(adc_val);
  std::memcpy(&buf[3], &time_sec, sizeof(double));
  std::memcpy(&buf[11], &spiker_val, sizeof(double));

  g_tracked_time_100us = time_100us;
  g_tracked_adc = adc_val;
  BroadcastStreamBytes(buf, sizeof(buf));
}

void EmitDeltaBatchChunk(const int64_t* times_100us, const int16_t* adc_vals, int count) {
  uint16_t payload_len = static_cast<uint16_t>(count * 4);
  uint8_t buf[3 + kSamplesPerChunk * 4];
  buf[0] = static_cast<uint8_t>(payload_len & 0xFF);
  buf[1] = static_cast<uint8_t>((payload_len >> 8) & 0xFF);
  buf[2] = 0x04;

  size_t offset = 3;
  for (int i = 0; i < count; ++i) {
    int64_t dt_ticks = times_100us[i] - g_tracked_time_100us;
    if (dt_ticks < -32767) dt_ticks = -32767;
    if (dt_ticks > 32767) dt_ticks = 32767;
    int16_t q_dt = static_cast<int16_t>(dt_ticks);
    g_tracked_time_100us += q_dt;

    int16_t q_dadc = static_cast<int16_t>(adc_vals[i] - g_tracked_adc);
    g_tracked_adc += q_dadc;

    std::memcpy(&buf[offset], &q_dt, sizeof(int16_t));
    std::memcpy(&buf[offset + 2], &q_dadc, sizeof(int16_t));
    offset += 4;
  }

  BroadcastStreamBytes(buf, offset);
}

void EmitClockPongChunk(int64_t host_t0_us, int64_t mcu_time_100us, TransportSource src) {
  // 0x06 Link-local Clock Pong chunk: [0x10, 0x00, 0x06] + int64(host_t0_us) + int64(mcu_time_100us)
  uint8_t buf[19];
  buf[0] = 0x10;
  buf[1] = 0x00;
  buf[2] = 0x06;
  std::memcpy(&buf[3], &host_t0_us, sizeof(int64_t));
  std::memcpy(&buf[11], &mcu_time_100us, sizeof(int64_t));

  if (src == TransportSource::kBle) {
    EnqueueBleBytes(buf, sizeof(buf));
    FlushBleTxRing(true);  // Immediate notification for min-RTT precision
  } else {
    WriteUartBytes(buf, sizeof(buf));
  }
}

void EnableSourceStream(TransportSource src) {
  if (src == TransportSource::kBle) {
    g_ble_streaming_enabled = true;
  } else {
    g_uart_streaming_enabled = true;
    g_uart_watchdog_chunks = 0;
  }
}

void ParseAndExecuteCommand(const char* cmd, TransportSource src) {
  if (StartsWith(cmd, "S:")) {
    const char* end_ptr = nullptr;
    int64_t sync_time_100us = ParseInt64(cmd + 2, &end_ptr);
    EnterAppCritical();
    g_time_100us = sync_time_100us;
    NRF_TIMER1->TASKS_CAPTURE[0] = 1;
    g_last_dma_end_us = NRF_TIMER1->CC[0];
    ExitAppCritical();

    if (end_ptr != nullptr && *end_ptr == ':') {
      const char* mode_str = end_ptr + 1;
      if (StartsWith(mode_str, "EEG")) {
        SetSpikerModePins(SpikerMode::EEG);
      } else if (StartsWith(mode_str, "ECG")) {
        SetSpikerModePins(SpikerMode::ECG);
      } else if (StartsWith(mode_str, "EMG")) {
        SetSpikerModePins(SpikerMode::EMG);
      }
    }
    EnableSourceStream(src);
    g_send_magic = true;
    g_force_baseline = true;
  } else if (StartsWith(cmd, "P:")) {
    // Precision min-RTT clock probe: P:<host_t0_us>
    int64_t mcu_now_100us = GetExactMcuTime100us();
    int64_t host_t0_us = ParseInt64(cmd + 2, nullptr);
    EnableSourceStream(src);
    EmitClockPongChunk(host_t0_us, mcu_now_100us, src);
  } else if (StartsWith(cmd, "T:")) {
    const char* end_ptr = nullptr;
    int64_t offset_100us = ParseInt64(cmd + 2, &end_ptr);
    EnterAppCritical();
    g_time_100us += offset_100us;
    ExitAppCritical();
    if (end_ptr != nullptr && *end_ptr == ':') {
      int64_t cc_parsed = ParseInt64(end_ptr + 1, nullptr);
      if (cc_parsed >= static_cast<int64_t>(kMinTimerCc) &&
          cc_parsed <= static_cast<int64_t>(kMaxTimerCc)) {
        NRF_TIMER2->CC[0] = static_cast<uint32_t>(cc_parsed);
      }
    }
    EnableSourceStream(src);
  } else if (StartsWith(cmd, "M:")) {
    const char* mode_str = cmd + 2;
    if (StartsWith(mode_str, "EEG")) {
      SetSpikerModePins(SpikerMode::EEG);
    } else if (StartsWith(mode_str, "ECG")) {
      SetSpikerModePins(SpikerMode::ECG);
    } else if (StartsWith(mode_str, "EMG")) {
      SetSpikerModePins(SpikerMode::EMG);
    }
    EnableSourceStream(src);
  } else if (StartsWith(cmd, "PING")) {
    EnableSourceStream(src);
    g_send_magic = true;
    g_force_baseline = true;
  } else if (StartsWith(cmd, "STOP")) {
    if (src == TransportSource::kBle) {
      g_ble_streaming_enabled = false;
      g_ble_tx_head = 0;
      g_ble_tx_tail = 0;
    } else {
      g_uart_streaming_enabled = false;
    }
  }
}

void PollSerialCommands() {
  static char cmd_buf[64];
  static size_t cmd_len = 0;

  while (g_uart_rx_tail != g_uart_rx_head) {
    char ch = static_cast<char>(g_uart_rx_ring[g_uart_rx_tail]);
    g_uart_rx_tail = (g_uart_rx_tail + 1U) & (kUartRxRingSize - 1U);

    if (ch == '\n' || ch == '\r') {
      if (cmd_len > 0) {
        cmd_buf[cmd_len] = '\0';
        ParseAndExecuteCommand(cmd_buf, TransportSource::kUart);
        cmd_len = 0;
      }
    } else if (cmd_len + 1 < sizeof(cmd_buf)) {
      cmd_buf[cmd_len++] = ch;
    } else {
      cmd_len = 0;
    }
  }
}

void FeedBleCommandBytes(const uint8_t* data, uint16_t len) {
  static char ble_cmd_buf[64];
  static size_t ble_cmd_len = 0;

  for (uint16_t i = 0; i < len; ++i) {
    char ch = static_cast<char>(data[i]);
    if (ch == '\n' || ch == '\r') {
      if (ble_cmd_len > 0) {
        ble_cmd_buf[ble_cmd_len] = '\0';
        ParseAndExecuteCommand(ble_cmd_buf, TransportSource::kBle);
        ble_cmd_len = 0;
      }
    } else if (ble_cmd_len + 1 < sizeof(ble_cmd_buf)) {
      ble_cmd_buf[ble_cmd_len++] = ch;
    } else {
      ble_cmd_len = 0;
    }
  }
}

void PollBleEvents() {
  alignas(4) uint8_t evt_buf[BLE_EVT_LEN_MAX(kMaxAttMtu)];

  while (true) {
    uint16_t evt_len = sizeof(evt_buf);
    uint32_t err = sd_ble_evt_get(evt_buf, &evt_len);
    if (err != NRF_SUCCESS) {
      break;
    }

    const auto* p_ble_evt = reinterpret_cast<const ble_evt_t*>(evt_buf);
    switch (p_ble_evt->header.evt_id) {
      case BLE_GAP_EVT_CONNECTED: {
        g_ble_conn_handle = p_ble_evt->evt.gap_evt.conn_handle;
        g_ble_notify_enabled = false;
        g_ble_max_payload = kDefaultBlePayload;
        g_ble_tx_head = 0;
        g_ble_tx_tail = 0;
        (void)sd_ble_gap_tx_power_set(
            BLE_GAP_TX_POWER_ROLE_CONN, g_ble_conn_handle, 0);

        ble_gap_conn_params_t fast_cp{};
        fast_cp.min_conn_interval = 6;   // 7.5 ms
        fast_cp.max_conn_interval = 12;  // 15.0 ms
        fast_cp.slave_latency = 0;
        fast_cp.conn_sup_timeout = 400;
        (void)sd_ble_gap_conn_param_update(g_ble_conn_handle, &fast_cp);
        break;
      }

      case BLE_GAP_EVT_DISCONNECTED: {
        g_ble_conn_handle = BLE_CONN_HANDLE_INVALID;
        g_ble_notify_enabled = false;
        g_ble_streaming_enabled = false;
        g_ble_max_payload = kDefaultBlePayload;
        g_ble_tx_head = 0;
        g_ble_tx_tail = 0;
        StartBleAdvertising();
        break;
      }

      case BLE_GAP_EVT_PHY_UPDATE_REQUEST: {
        ble_gap_phys_t const phys = {BLE_GAP_PHY_AUTO, BLE_GAP_PHY_AUTO};
        (void)sd_ble_gap_phy_update(p_ble_evt->evt.gap_evt.conn_handle, &phys);
        break;
      }

      case BLE_GAP_EVT_DATA_LENGTH_UPDATE_REQUEST: {
        (void)sd_ble_gap_data_length_update(
            p_ble_evt->evt.gap_evt.conn_handle, nullptr, nullptr);
        break;
      }

      case BLE_GAP_EVT_SEC_PARAMS_REQUEST: {
        (void)sd_ble_gap_sec_params_reply(
            p_ble_evt->evt.gap_evt.conn_handle,
            BLE_GAP_SEC_STATUS_PAIRING_NOT_SUPP,
            nullptr,
            nullptr);
        break;
      }

      case BLE_GATTS_EVT_SYS_ATTR_MISSING: {
        (void)sd_ble_gatts_sys_attr_set(
            p_ble_evt->evt.gatts_evt.conn_handle, nullptr, 0, 0);
        break;
      }

      case BLE_GATTS_EVT_EXCHANGE_MTU_REQUEST: {
        uint16_t client_mtu =
            p_ble_evt->evt.gatts_evt.params.exchange_mtu_request.client_rx_mtu;
        if (client_mtu < BLE_GATT_ATT_MTU_DEFAULT) {
          client_mtu = BLE_GATT_ATT_MTU_DEFAULT;
        }
        uint16_t eff_mtu = (client_mtu < kMaxAttMtu) ? client_mtu : kMaxAttMtu;
        (void)sd_ble_gatts_exchange_mtu_reply(
            p_ble_evt->evt.gatts_evt.conn_handle, kMaxAttMtu);
        g_ble_max_payload = eff_mtu - 3U;
        break;
      }

      case BLE_GATTS_EVT_TIMEOUT: {
        (void)sd_ble_gap_disconnect(
            p_ble_evt->evt.gatts_evt.conn_handle,
            BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
        break;
      }

      case BLE_GATTS_EVT_WRITE: {
        const ble_gatts_evt_write_t& wr = p_ble_evt->evt.gatts_evt.params.write;
        if (wr.handle == g_tx_handles.cccd_handle && wr.len >= 2) {
          bool enabled = ((wr.data[0] & 0x01U) != 0U);
          g_ble_notify_enabled = enabled;
          g_ble_tx_head = 0;
          g_ble_tx_tail = 0;
          if (enabled) {
            g_send_magic = true;
            g_force_baseline = true;
          } else {
            g_ble_streaming_enabled = false;
          }
        } else if (wr.handle == g_rx_handles.value_handle && wr.len > 0) {
          FeedBleCommandBytes(wr.data, wr.len);
        }
        break;
      }

      case BLE_GATTS_EVT_HVN_TX_COMPLETE: {
        FlushBleTxRing(true);
        break;
      }

      default:
        break;
    }
  }

  // Drain any pending SoC events (e.g. flash/clock/radio notifications)
  uint32_t soc_evt = 0;
  while (sd_evt_get(&soc_evt) == NRF_SUCCESS) {
  }
}

}  // namespace

int main() {
  InitQuietBoardGpios();
  SetSpikerModePins(SpikerMode::EEG);

  // Start S113 SoftDevice + 16 MHz HFXO crystal + BLE GATT Service & Advertising
  InitSoftDeviceAndBle();

  // Start 115200-baud USB UART + 500 Hz TIMER2 -> PPI -> SAADC EasyDMA pipeline
  InitUsbSerial115200();
  InitHardwareTimersAndDmaAdc();

  int16_t local_samples[kSamplesPerChunk];
  int64_t local_times[kSamplesPerChunk];

  while (true) {
    if (g_block_ready) {
      EnterAppCritical();
      g_block_ready = false;
      for (int i = 0; i < kSamplesPerChunk; ++i) {
        int16_t raw = g_ready_samples[i];
        if (raw < 0) raw = 0;
        if (raw > 1023) raw = 1023;
        local_samples[i] = raw;
      }
      for (int i = 0; i < kSamplesPerChunk; ++i) {
        g_time_100us += kDefaultDt100us;
        local_times[i] = g_time_100us;
      }
      ExitAppCritical();

      if (g_uart_streaming_enabled) {
        if (g_uart_watchdog_chunks >= kUartWatchdogChunks) {
          g_uart_streaming_enabled = false;
        } else {
          g_uart_watchdog_chunks++;
        }
      }

      const bool any_active =
          g_uart_streaming_enabled ||
          (g_ble_streaming_enabled && g_ble_notify_enabled &&
           g_ble_conn_handle != BLE_CONN_HANDLE_INVALID);

      if (any_active) {
        bool urgent_ble_flush = false;
        if (g_send_magic) {
          g_send_magic = false;
          EmitMagicChunk();
          urgent_ble_flush = true;
        }

        if (g_force_baseline || g_chunks_since_baseline >= kBaselineIntervalChunks) {
          g_force_baseline = false;
          g_chunks_since_baseline = 0;
          EmitBaselineChunk(local_times[0], local_samples[0]);
          EmitDeltaBatchChunk(&local_times[1], &local_samples[1], kSamplesPerChunk - 1);
          urgent_ble_flush = true;
        } else {
          EmitDeltaBatchChunk(local_times, local_samples, kSamplesPerChunk);
        }
        g_chunks_since_baseline++;

        if (g_ble_streaming_enabled) {
          FlushBleTxRing(urgent_ble_flush);
        }
      }
    }

    PollBleEvents();
    PollSerialCommands();

    if (!g_block_ready && g_uart_rx_tail == g_uart_rx_head) {
      (void)sd_app_evt_wait();
    }
  }
}
