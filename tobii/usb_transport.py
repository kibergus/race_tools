"""Zero-Tobii-DLL cross-platform USB transport (`WinUSB` on Windows, `libusb` on Linux)."""

from abc import ABC, abstractmethod
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time
from typing import List, Optional, Tuple
import uuid

from tobii.ttp_protocol import (
    TOBII_EP_IN,
    TOBII_EP_OUT,
    TOBII_PID,
    TOBII_VENDOR_IFACE,
    TOBII_VID,
)


DEFAULT_WINUSB_GUID = '{85C0F97C-E2B1-422A-92A9-5F96072E79D8}'
WINDOWS_TOBII_SERVICES: Tuple[str, ...] = (
    'TobiiIS5LEYETRACKER5',
    'Tobii Service',
    'TobiiGeneric',
)


class UsbTransportError(RuntimeError):
    """Base exception for direct USB transport errors."""


class UsbDeviceNotFoundError(UsbTransportError):
    """Raised when no Tobii Eye Tracker 5 (`2104:0313`) is connected."""


class UsbAccessDeniedError(UsbTransportError):
    """Raised when another process (e.g. Tobii Windows Service) holds the USB interface."""


class BaseUsbTransport(ABC):
    """Abstract bulk + vendor-control USB transport for Tobii TTP."""

    @abstractmethod
    def open(self) -> None:
        """Open USB device, claim vendor interface 0, and configure pipes."""

    @abstractmethod
    def close(self) -> None:
        """Release interface and close USB handle."""

    @abstractmethod
    def control_transfer(
        self,
        bm_request_type: int,
        b_request: int,
        w_value: int,
        w_index: int,
        data: bytes = b'',
        timeout_ms: int = 1000,
    ) -> int:
        """Send a vendor control transfer on endpoint 0."""

    @abstractmethod
    def bulk_write(self, ep_out: int, data: bytes, timeout_ms: int = 1000) -> int:
        """Write bytes to a bulk OUT endpoint."""

    @abstractmethod
    def bulk_read(self, ep_in: int, max_len: int = 16384, timeout_ms: int = 250) -> bytes:
        """Read up to `max_len` bytes from a bulk IN endpoint; returns `b''` on timeout."""


def get_windows_tobii_services_running() -> List[str]:
    """Return names of currently running Windows Tobii background services."""
    if sys.platform != 'win32':
        return []
    try:
        proc = subprocess.run(
            ['sc', 'query', 'state=', 'active'],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        out = proc.stdout or ''
        running: List[str] = []
        for svc in WINDOWS_TOBII_SERVICES:
            if f'SERVICE_NAME: {svc}' in out:
                running.append(svc)
        return running
    except Exception:
        return []


def toggle_windows_tobii_services_elevated(stop: bool) -> bool:
    """Prompt UAC elevation to stop or start the Windows Tobii services holding WinUSB."""
    if sys.platform != 'win32':
        return False
    names_ps = ', '.join(f"'{s}'" for s in WINDOWS_TOBII_SERVICES)
    if stop:
        cmd = f'Stop-Service -Name {names_ps} -Force -ErrorAction SilentlyContinue'
    else:
        cmd = f'Start-Service -Name {names_ps} -ErrorAction SilentlyContinue'

    shell32 = ctypes.windll.shell32
    rc = shell32.ShellExecuteW(
        None,
        'runas',
        'powershell.exe',
        f'-NoProfile -WindowStyle Hidden -Command "{cmd}"',
        None,
        0,
    )
    if rc <= 32:
        return False

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        running = get_windows_tobii_services_running()
        if stop and not running:
            return True
        if not stop and running:
            return True
        time.sleep(0.25)
    return stop != bool(get_windows_tobii_services_running())


if sys.platform == 'win32':
    class _GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', wintypes.DWORD),
            ('Data2', wintypes.WORD),
            ('Data3', wintypes.WORD),
            ('Data4', ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_string(cls, guid_str: str) -> '_GUID':
            u = uuid.UUID(guid_str)
            d1, d2, d3, d4_0, d4_1, rest = u.fields
            arr = (ctypes.c_ubyte * 8)()
            arr[0] = d4_0
            arr[1] = d4_1
            for idx in range(6):
                arr[2 + idx] = (rest >> (8 * (5 - idx))) & 0xFF
            return cls(d1, d2, d3, arr)

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('InterfaceClassGuid', _GUID),
            ('Flags', wintypes.DWORD),
            (
                'Reserved',
                ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.ULONG,
            ),
        ]

    class _WINUSB_SETUP_PACKET(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ('RequestType', ctypes.c_ubyte),
            ('Request', ctypes.c_ubyte),
            ('Value', ctypes.c_ushort),
            ('Index', ctypes.c_ushort),
            ('Length', ctypes.c_ushort),
        ]


def discover_windows_winusb_paths(vid: int = TOBII_VID, pid: int = TOBII_PID) -> List[str]:
    """Discover WinUSB device interface paths matching `VID_xxxx&PID_xxxx&MI_00` on Windows."""
    if sys.platform != 'win32':
        return []

    import winreg

    guid_strings: List[str] = []
    enum_subkey = f'SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_{vid:04X}&PID_{pid:04X}&MI_00'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_subkey) as base_key:
            idx = 0
            while True:
                try:
                    inst_name = winreg.EnumKey(base_key, idx)
                    idx += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(base_key, f'{inst_name}\\Device Parameters') as dp_key:
                        for val_name in ('DeviceInterfaceGUIDs', 'DeviceInterfaceGUID'):
                            try:
                                val, _ = winreg.QueryValueEx(dp_key, val_name)
                                if isinstance(val, list):
                                    guid_strings.extend(str(x) for x in val if x)
                                elif isinstance(val, str) and val:
                                    guid_strings.append(val)
                            except OSError:
                                pass
                except OSError:
                    pass
    except OSError:
        pass

    if DEFAULT_WINUSB_GUID not in guid_strings:
        guid_strings.append(DEFAULT_WINUSB_GUID)

    setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
    ]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID),
        wintypes.DWORD, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    digcf_present_deviface = 0x00000002 | 0x00000010
    invalid_handle = ctypes.c_void_p(-1).value
    needle = f'vid_{vid:04x}&pid_{pid:04x}'.lower()
    found_paths: List[str] = []

    for g_str in guid_strings:
        try:
            guid_obj = _GUID.from_string(g_str)
        except ValueError:
            continue
        h_info = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid_obj), None, None, digcf_present_deviface
        )
        if not h_info or h_info == invalid_handle:
            continue
        try:
            member_idx = 0
            while True:
                iface_data = _SP_DEVICE_INTERFACE_DATA()
                iface_data.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
                ok = setupapi.SetupDiEnumDeviceInterfaces(
                    h_info, None, ctypes.byref(guid_obj), member_idx, ctypes.byref(iface_data)
                )
                if not ok:
                    break
                member_idx += 1

                req_size = wintypes.DWORD(0)
                setupapi.SetupDiGetDeviceInterfaceDetailW(
                    h_info, ctypes.byref(iface_data), None, 0, ctypes.byref(req_size), None
                )
                if req_size.value < 6:
                    continue

                buf = ctypes.create_string_buffer(req_size.value)
                cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = cb_size

                if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    h_info, ctypes.byref(iface_data), buf, req_size.value, None, None
                ):
                    path_ptr = ctypes.byref(buf, 4)
                    dev_path = ctypes.wstring_at(path_ptr)
                    if needle in dev_path.lower() and dev_path not in found_paths:
                        found_paths.append(dev_path)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(h_info)

    return found_paths


class WinUsbTransport(BaseUsbTransport):
    """Direct OS `winusb.dll` transport on Windows (zero Tobii DLLs, zero extra pip deps)."""

    PIPE_TRANSFER_TIMEOUT = 0x03

    def __init__(self, device_path: Optional[str] = None) -> None:
        self._requested_path = device_path
        self._file_handle: Optional[int] = None
        self._winusb_handle = ctypes.c_void_p()
        self._read_timeout_ms = -1
        self._write_timeout_ms = -1
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._winusb = ctypes.WinDLL('winusb', use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        self._winusb.WinUsb_Initialize.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        self._winusb.WinUsb_Initialize.restype = wintypes.BOOL
        self._winusb.WinUsb_Free.argtypes = [ctypes.c_void_p]
        self._winusb.WinUsb_Free.restype = wintypes.BOOL
        self._winusb.WinUsb_SetPipePolicy.argtypes = [
            ctypes.c_void_p, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p
        ]
        self._winusb.WinUsb_SetPipePolicy.restype = wintypes.BOOL
        self._winusb.WinUsb_ControlTransfer.argtypes = [
            ctypes.c_void_p, _WINUSB_SETUP_PACKET, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        self._winusb.WinUsb_ControlTransfer.restype = wintypes.BOOL
        self._winusb.WinUsb_ReadPipe.argtypes = [
            ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        self._winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
        self._winusb.WinUsb_WritePipe.argtypes = [
            ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        self._winusb.WinUsb_WritePipe.restype = wintypes.BOOL

    def open(self) -> None:
        paths = [self._requested_path] if self._requested_path else discover_windows_winusb_paths()
        if not paths:
            raise UsbDeviceNotFoundError(
                'Tobii Eye Tracker 5 (2104:0313 MI_00) not found on WinUSB.'
            )

        generic_rw = 0x80000000 | 0x40000000
        share_rw = 0x00000001 | 0x00000002
        open_existing = 3
        flag_overlapped = 0x00000080 | 0x40000000
        invalid_handle = ctypes.c_void_p(-1).value

        h_file = self._kernel32.CreateFileW(
            paths[0], generic_rw, share_rw, None, open_existing, flag_overlapped, None
        )
        if not h_file or h_file == invalid_handle:
            err = ctypes.get_last_error()
            if err == 5:  # ERROR_ACCESS_DENIED
                running = get_windows_tobii_services_running()
                svc_note = f' ({", ".join(running)})' if running else ''
                raise UsbAccessDeniedError(
                    f'WinUSB access denied{svc_note}. Stop Windows Tobii services to claim USB.'
                )
            raise UsbTransportError(f'CreateFileW failed on {paths[0]} (WinError {err})')

        self._file_handle = h_file
        if not self._winusb.WinUsb_Initialize(h_file, ctypes.byref(self._winusb_handle)):
            err = ctypes.get_last_error()
            self._kernel32.CloseHandle(h_file)
            self._file_handle = None
            raise UsbTransportError(f'WinUsb_Initialize failed (WinError {err})')

        self._set_pipe_timeout(TOBII_EP_IN, 250)
        self._set_pipe_timeout(TOBII_EP_OUT, 1000)

    def _set_pipe_timeout(self, pipe_id: int, timeout_ms: int) -> None:
        if not self._winusb_handle.value:
            return
        val = wintypes.ULONG(max(1, int(timeout_ms)))
        self._winusb.WinUsb_SetPipePolicy(
            self._winusb_handle,
            pipe_id & 0xFF,
            self.PIPE_TRANSFER_TIMEOUT,
            ctypes.sizeof(val),
            ctypes.byref(val),
        )

    def close(self) -> None:
        if self._winusb_handle.value:
            self._winusb.WinUsb_Free(self._winusb_handle)
            self._winusb_handle = ctypes.c_void_p()
        if self._file_handle is not None:
            self._kernel32.CloseHandle(self._file_handle)
            self._file_handle = None

    def control_transfer(
        self,
        bm_request_type: int,
        b_request: int,
        w_value: int,
        w_index: int,
        data: bytes = b'',
        timeout_ms: int = 1000,
    ) -> int:
        if not self._winusb_handle.value:
            raise UsbTransportError('WinUSB handle is not open')
        setup = _WINUSB_SETUP_PACKET(
            bm_request_type & 0xFF,
            b_request & 0xFF,
            w_value & 0xFFFF,
            w_index & 0xFFFF,
            len(data) & 0xFFFF,
        )
        buf = ctypes.create_string_buffer(data, len(data)) if data else None
        transferred = wintypes.ULONG(0)
        ok = self._winusb.WinUsb_ControlTransfer(
            self._winusb_handle, setup, buf, len(data), ctypes.byref(transferred), None
        )
        if not ok:
            err = ctypes.get_last_error()
            raise UsbTransportError(
                f'WinUsb_ControlTransfer 0x{b_request:02x} failed (WinError {err})'
            )
        return int(transferred.value)

    def bulk_write(self, ep_out: int, data: bytes, timeout_ms: int = 1000) -> int:
        if not self._winusb_handle.value:
            raise UsbTransportError('WinUSB handle is not open')
        if timeout_ms != self._write_timeout_ms:
            self._set_pipe_timeout(ep_out, timeout_ms)
            self._write_timeout_ms = timeout_ms
        buf = ctypes.create_string_buffer(data, len(data))
        transferred = wintypes.ULONG(0)
        ok = self._winusb.WinUsb_WritePipe(
            self._winusb_handle, ep_out & 0xFF, buf, len(data), ctypes.byref(transferred), None
        )
        if not ok:
            err = ctypes.get_last_error()
            raise UsbTransportError(f'WinUsb_WritePipe failed (WinError {err})')
        return int(transferred.value)

    def bulk_read(self, ep_in: int, max_len: int = 16384, timeout_ms: int = 250) -> bytes:
        if not self._winusb_handle.value:
            raise UsbTransportError('WinUSB handle is not open')
        if timeout_ms != self._read_timeout_ms:
            self._set_pipe_timeout(ep_in, timeout_ms)
            self._read_timeout_ms = timeout_ms
        buf = ctypes.create_string_buffer(max_len)
        transferred = wintypes.ULONG(0)
        ok = self._winusb.WinUsb_ReadPipe(
            self._winusb_handle, ep_in & 0xFF, buf, max_len, ctypes.byref(transferred), None
        )
        if not ok:
            err = ctypes.get_last_error()
            if err in (121, 995):  # ERROR_SEM_TIMEOUT / ERROR_OPERATION_ABORTED
                return b''
            raise UsbTransportError(f'WinUsb_ReadPipe failed (WinError {err})')
        return buf.raw[:transferred.value]


class LibUsbTransport(BaseUsbTransport):
    """Direct `libusb-1.0` ctypes transport for Linux / POSIX."""

    LIBUSB_ERROR_TIMEOUT = -7
    LIBUSB_ERROR_ACCESS = -3
    LIBUSB_ERROR_BUSY = -6

    def __init__(self) -> None:
        lib_names = ('libusb-1.0.so.0', 'libusb-1.0.so', 'libusb-1.0.dylib')
        self._lib = None
        for name in lib_names:
            try:
                self._lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if self._lib is None:
            raise UsbTransportError('libusb-1.0 shared library not found on system')

        self._ctx = ctypes.c_void_p()
        self._handle = ctypes.c_void_p()
        self._claimed = False
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        lib = self._lib
        lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.libusb_init.restype = ctypes.c_int
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        lib.libusb_exit.restype = None
        lib.libusb_open_device_with_vid_pid.argtypes = [
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16
        ]
        lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        lib.libusb_close.argtypes = [ctypes.c_void_p]
        lib.libusb_close.restype = None
        lib.libusb_set_auto_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_set_auto_detach_kernel_driver.restype = ctypes.c_int
        lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_claim_interface.restype = ctypes.c_int
        lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_release_interface.restype = ctypes.c_int
        lib.libusb_control_transfer.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8,
            ctypes.c_uint16, ctypes.c_uint16, ctypes.c_void_p,
            ctypes.c_uint16, ctypes.c_uint,
        ]
        lib.libusb_control_transfer.restype = ctypes.c_int
        lib.libusb_bulk_transfer.argtypes = [
            ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p,
            ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
        ]
        lib.libusb_bulk_transfer.restype = ctypes.c_int

    def open(self) -> None:
        rc = self._lib.libusb_init(ctypes.byref(self._ctx))
        if rc != 0:
            raise UsbTransportError(f'libusb_init failed ({rc})')
        dev = self._lib.libusb_open_device_with_vid_pid(self._ctx, TOBII_VID, TOBII_PID)
        if not dev:
            self._lib.libusb_exit(self._ctx)
            self._ctx = ctypes.c_void_p()
            raise UsbDeviceNotFoundError('Tobii Eye Tracker 5 (2104:0313) not found via libusb')
        self._handle = ctypes.c_void_p(dev)
        self._lib.libusb_set_auto_detach_kernel_driver(self._handle, 1)
        rc = self._lib.libusb_claim_interface(self._handle, TOBII_VENDOR_IFACE)
        if rc != 0:
            self.close()
            if rc in (self.LIBUSB_ERROR_ACCESS, self.LIBUSB_ERROR_BUSY):
                raise UsbAccessDeniedError(f'libusb_claim_interface denied/busy ({rc})')
            raise UsbTransportError(f'libusb_claim_interface failed ({rc})')
        self._claimed = True

    def close(self) -> None:
        if self._handle.value:
            if self._claimed:
                self._lib.libusb_release_interface(self._handle, TOBII_VENDOR_IFACE)
                self._claimed = False
            self._lib.libusb_close(self._handle)
            self._handle = ctypes.c_void_p()
        if self._ctx.value:
            self._lib.libusb_exit(self._ctx)
            self._ctx = ctypes.c_void_p()

    def control_transfer(
        self,
        bm_request_type: int,
        b_request: int,
        w_value: int,
        w_index: int,
        data: bytes = b'',
        timeout_ms: int = 1000,
    ) -> int:
        buf = ctypes.create_string_buffer(data, len(data)) if data else None
        rc = self._lib.libusb_control_transfer(
            self._handle, bm_request_type, b_request, w_value, w_index, buf, len(data), timeout_ms
        )
        if rc < 0:
            raise UsbTransportError(f'libusb_control_transfer 0x{b_request:02x} failed ({rc})')
        return int(rc)

    def bulk_write(self, ep_out: int, data: bytes, timeout_ms: int = 1000) -> int:
        buf = ctypes.create_string_buffer(data, len(data))
        transferred = ctypes.c_int(0)
        rc = self._lib.libusb_bulk_transfer(
            self._handle, ep_out, buf, len(data), ctypes.byref(transferred), timeout_ms
        )
        if rc != 0:
            raise UsbTransportError(f'libusb_bulk_transfer write failed ({rc})')
        return int(transferred.value)

    def bulk_read(self, ep_in: int, max_len: int = 16384, timeout_ms: int = 250) -> bytes:
        buf = ctypes.create_string_buffer(max_len)
        transferred = ctypes.c_int(0)
        rc = self._lib.libusb_bulk_transfer(
            self._handle, ep_in, buf, max_len, ctypes.byref(transferred), timeout_ms
        )
        if rc == self.LIBUSB_ERROR_TIMEOUT:
            return b''
        if rc != 0:
            raise UsbTransportError(f'libusb_bulk_transfer read failed ({rc})')
        return buf.raw[:transferred.value]


def is_tobii_usb_present() -> bool:
    """Return True if a physical Tobii Eye Tracker 5 (`2104:0313`) is detected on USB."""
    if sys.platform == 'win32':
        return bool(discover_windows_winusb_paths())
    usb_sysfs = '/sys/bus/usb/devices'
    if os.path.isdir(usb_sysfs):
        for entry in os.listdir(usb_sysfs):
            vid_p = os.path.join(usb_sysfs, entry, 'idVendor')
            pid_p = os.path.join(usb_sysfs, entry, 'idProduct')
            try:
                if os.path.isfile(vid_p) and os.path.isfile(pid_p):
                    with open(vid_p, 'r', encoding='utf-8') as fv:
                        vid_s = fv.read().strip().lower()
                    with open(pid_p, 'r', encoding='utf-8') as fp:
                        pid_s = fp.read().strip().lower()
                    if vid_s == f'{TOBII_VID:04x}' and pid_s == f'{TOBII_PID:04x}':
                        return True
            except OSError:
                continue
    return False


def create_usb_transport() -> BaseUsbTransport:
    """Instantiate the OS-native direct USB transport (`WinUSB` on Windows, `libusb` on Linux)."""
    if sys.platform == 'win32':
        return WinUsbTransport()
    return LibUsbTransport()
