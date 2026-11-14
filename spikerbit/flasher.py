"""Cross-platform BBC micro:bit USB Mass Storage drive detector and .hex firmware flasher."""

import ctypes
import os
import sys
import time
from typing import List, Optional


DEFAULT_HEX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'firmware',
    'spikerbit_firmware.hex',
)


def is_microbit_mount(mount_path: str, volume_label: Optional[str] = None) -> bool:
    """Return True if a mounted path corresponds to a BBC micro:bit DAPLink drive."""
    if volume_label and 'MICROBIT' in volume_label.upper():
        return True
    if not os.path.isdir(mount_path):
        return False
    base = os.path.basename(os.path.normpath(mount_path)).upper()
    if 'MICROBIT' in base:
        return True
    details_txt = os.path.join(mount_path, 'DETAILS.TXT')
    microbit_htm = os.path.join(mount_path, 'MICROBIT.HTM')
    return os.path.isfile(details_txt) and os.path.isfile(microbit_htm)


def find_microbit_drives(search_roots: Optional[List[str]] = None) -> List[str]:
    """Discover mounted MICROBIT USB Mass Storage paths on Windows and Linux."""
    found: List[str] = []

    if search_roots is not None:
        for root in search_roots:
            if is_microbit_mount(root):
                found.append(root)
            elif os.path.isdir(root):
                try:
                    for entry in os.listdir(root):
                        candidate = os.path.join(root, entry)
                        if is_microbit_mount(candidate):
                            found.append(candidate)
                except OSError:
                    pass
        return found

    if sys.platform.startswith('win'):
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for idx in range(26):
                if not (bitmask & (1 << idx)):
                    continue
                drive = f'{chr(65 + idx)}:\\'
                vol_buf = ctypes.create_unicode_buffer(261)
                fs_buf = ctypes.create_unicode_buffer(261)
                ok = ctypes.windll.kernel32.GetVolumeInformationW(
                    ctypes.c_wchar_p(drive),
                    vol_buf,
                    ctypes.sizeof(vol_buf),
                    None,
                    None,
                    None,
                    fs_buf,
                    ctypes.sizeof(fs_buf),
                )
                label = vol_buf.value if ok else ''
                if is_microbit_mount(drive, label):
                    found.append(drive)
        except Exception:
            pass
    else:
        proc_mounts = '/proc/mounts'
        if os.path.isfile(proc_mounts):
            try:
                with open(proc_mounts, 'r', encoding='utf-8', errors='ignore') as f_mounts:
                    for line in f_mounts:
                        parts = line.split()
                        if len(parts) >= 2:
                            mnt = parts[1].replace('\\040', ' ')
                            if is_microbit_mount(mnt):
                                found.append(mnt)
            except OSError:
                pass

        for base_dir in ('/run/media', '/media', '/mnt'):
            if not os.path.isdir(base_dir):
                continue
            try:
                for entry in os.listdir(base_dir):
                    path1 = os.path.join(base_dir, entry)
                    if is_microbit_mount(path1) and path1 not in found:
                        found.append(path1)
                    elif os.path.isdir(path1):
                        try:
                            for sub in os.listdir(path1):
                                path2 = os.path.join(path1, sub)
                                if is_microbit_mount(path2) and path2 not in found:
                                    found.append(path2)
                        except OSError:
                            pass
            except OSError:
                pass

    return found


def validate_intel_hex(hex_path: str) -> bool:
    """Verify that the firmware file exists and has valid Intel HEX framing and checksums."""
    if not os.path.isfile(hex_path):
        return False
    try:
        with open(hex_path, 'r', encoding='ascii', errors='strict') as f_hex:
            lines = [ln.strip() for ln in f_hex if ln.strip()]
        if not lines:
            return False

        has_data_record = False
        has_eof_record = False
        for ln in lines:
            if not ln.startswith(':') or len(ln) < 11 or (len(ln) % 2) != 1:
                return False
            raw = bytes.fromhex(ln[1:])
            byte_count = raw[0]
            record_type = raw[3]
            if len(raw) != byte_count + 5:
                return False
            if (sum(raw) & 0xFF) != 0:
                return False
            if record_type == 0x00 and byte_count > 0:
                has_data_record = True
            elif record_type == 0x01:
                if byte_count != 0:
                    return False
                has_eof_record = True

        return has_data_record and has_eof_record
    except Exception:
        return False


def _wait_and_check_daplink_result(target_drive: str, timeout_sec: float = 12.0) -> None:
    """Wait for DAPLink to remount after flashing and raise RuntimeError if FAIL.TXT appears."""
    deadline = time.monotonic() + timeout_sec
    saw_unmount = False

    while time.monotonic() < deadline:
        time.sleep(0.35)
        mounted = os.path.isdir(target_drive) and os.path.isfile(
            os.path.join(target_drive, 'DETAILS.TXT')
        )
        if not mounted:
            saw_unmount = True
            continue
        if saw_unmount:
            time.sleep(0.25)
            break

    fail_txt = os.path.join(target_drive, 'FAIL.TXT')
    if os.path.isfile(fail_txt):
        try:
            with open(fail_txt, 'r', encoding='utf-8', errors='replace') as f_fail:
                details = f_fail.read().strip()
        except OSError:
            details = 'FAIL.TXT present on MICROBIT drive'
        raise RuntimeError(f'micro:bit DAPLink reported a flash failure:\n{details}')


def flash_firmware(
    target_drive: Optional[str] = None,
    hex_path: str = DEFAULT_HEX_PATH,
    verify_remount: bool = False,
) -> str:
    """Copy the prebuilt .hex firmware file to the MICROBIT drive and return destination path."""
    if not validate_intel_hex(hex_path):
        raise ValueError(f'Invalid or corrupted Intel HEX firmware file: {hex_path}')

    if target_drive is None:
        drives = find_microbit_drives()
        if not drives:
            raise FileNotFoundError(
                'No BBC micro:bit (MICROBIT) USB drive detected. '
                'Please connect your micro:bit via USB and try again.'
            )
        target_drive = drives[0]

    if not os.path.isdir(target_drive):
        raise FileNotFoundError(f'Target MICROBIT drive directory does not exist: {target_drive}')

    with open(hex_path, 'rb') as f_src:
        payload = f_src.read()

    dest_path = os.path.join(target_drive, 'spikerbit_firmware.hex')
    try:
        with open(dest_path, 'wb') as f_dst:
            f_dst.write(payload)
            f_dst.flush()
            os.fsync(f_dst.fileno())
    except OSError:
        # DAPLink VFS sometimes disconnects immediately upon receiving the EOF record
        if not verify_remount:
            raise

    if verify_remount:
        _wait_and_check_daplink_result(target_drive)

    return dest_path
