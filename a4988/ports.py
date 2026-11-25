"""Find the Arduino Mega 2560 by its USB ID, never by COM number.

This PC has other serial devices (the tip-tilt's MDT693A on an FTDI adapter,
the chopper, a Prolific adapter), and a COM number can change when a cable
moves. The USB vendor:product ID cannot. Windows keeps each USB device's COM
port under ``HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_xxxx&PID_xxxx\\<serial>``
(including devices unplugged long ago), and ``HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM``
lists only the ports present now, so a Mega is "here" when its port is in both.
Reads the registry only: opens no port.
"""

from __future__ import annotations

import sys
from typing import Dict, List

#: Mega 2560 R3 (2341:0042), early R1/R2 (2341:0010), arduino.org boards (2A03:0042).
#: The same list as tools/flash.ps1.
MEGA_IDS = ("2341:0042", "2341:0010", "2A03:0042")


def _present_ports() -> set:
    import winreg
    out = set()
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
    except OSError:
        return out
    i = 0
    while True:
        try:
            _, val, _ = winreg.EnumValue(k, i)
        except OSError:
            break
        out.add(str(val).upper())
        i += 1
    return out


def find_megas(ids=MEGA_IDS) -> List[Dict[str, str]]:
    """Every Mega 2560 plugged in now: [{"port": "COM4", "id": "2341:0042", "serial": ...}]."""
    if sys.platform != "win32":
        return _find_pyserial(ids)
    import winreg
    present = _present_ports()
    found = []
    for usb_id in ids:
        vid, pid = usb_id.split(":")
        path = rf"SYSTEM\CurrentControlSet\Enum\USB\VID_{vid}&PID_{pid}"
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                inst = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                dp = winreg.OpenKey(k, inst + r"\Device Parameters")
                port, _ = winreg.QueryValueEx(dp, "PortName")
            except OSError:
                continue
            port = str(port).upper()
            if port in present:
                found.append({"port": port, "id": usb_id, "serial": inst})
    return found


def _find_pyserial(ids) -> List[Dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    want = {i.upper() for i in ids}
    out = []
    for p in list_ports.comports():
        if p.vid is None:
            continue
        usb_id = f"{p.vid:04X}:{p.pid:04X}"
        if usb_id in want:
            out.append({"port": p.device, "id": usb_id, "serial": p.serial_number or ""})
    return out


def find_mega_port() -> str:
    """The one Mega's port. Raises LookupError if there is none, or more than one."""
    megas = find_megas()
    if len(megas) == 1:
        return megas[0]["port"]
    if not megas:
        raise LookupError(f"no Arduino Mega 2560 on USB (looked for {', '.join(MEGA_IDS)})")
    raise LookupError("more than one Mega 2560 on USB ("
                      + ", ".join(m["port"] for m in megas) + "): name the port")
