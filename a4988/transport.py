"""The byte pipe to the Mega: open its serial port WITHOUT resetting it, write, read, close.

Nothing here knows the lens_z protocol (that is :mod:`a4988.link`). This only
moves bytes, and turns every operating-system failure into a
:class:`TransportError` whose text an operator can act on.

Why DTR matters here: the Mega 2560's USB chip pulls RESET through a capacitor
on DTR, so a port opened the usual way (DTR asserted) reboots the board. A
reboot forgets the homing, and the position with it. Both backends below open
the port with DTR and RTS never asserted, so connecting, reconnecting or
restarting the DAQ leaves the lens exactly as it was, homed or not.
(tools/flash.ps1 does reset the board, on purpose, to check the new firmware.)

Two backends, chosen by :func:`open_transport`:

``pyserial``
    Used whenever ``import serial`` works. Optional: the rig PC's Python does
    not have it, and nothing here requires it.
``win32``
    ctypes over kernel32 (CreateFileW / ReadFile / WriteFile on ``\\\\.\\COMn``),
    so it runs on a stock Windows Python with no extra package.

Adapted from thorlabs-mc2000b's ``mc2000b/transport.py`` (the chopper's link,
the same lab's code), with the chopper-specific messages replaced.
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
from typing import Optional

WRITE_TIMEOUT_S = 1.0
_BUFFER_BYTES = 4096
PYSERIAL_POLL_S = 0.02
_WINDOWS = sys.platform == "win32"


class TransportError(Exception):
    """The port could not be opened, or stopped working. The text is for the operator."""


class Transport:
    """A byte pipe to the board. Not thread-safe: the link serialises."""

    name: str = ""
    backend: str = ""

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout: float) -> bytes:
        """Bytes as soon as at least one is available, or b"" after ``timeout`` s."""
        raise NotImplementedError

    def reset_input(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} ({self.backend})>"


def open_transport(port: str, *, baudrate: int = 115200, backend: str = "auto") -> Transport:
    """Open ``port`` (e.g. "COM4") with DTR/RTS low, and return a :class:`Transport`."""
    name = port_name(port)
    if backend not in ("auto", "pyserial", "win32"):
        raise TransportError(f"unknown serial backend {backend!r} (auto, pyserial or win32)")
    if backend in ("auto", "pyserial"):
        serial = _import_pyserial()
        if serial is not None:
            return PySerialTransport(name, serial, baudrate=baudrate)
        if backend == "pyserial":
            raise TransportError("pyserial is not installed (pip install pyserial)")
    if _WINDOWS:
        return Win32Transport(name, baudrate=baudrate)
    raise TransportError("no serial backend on this platform: install pyserial")


def port_name(port: str) -> str:
    r"""Normalise com4, COM4 and \\.\COM4 to "COM4"; other names pass through."""
    text = (port or "").strip()
    if not text:
        raise TransportError("no serial port given (e.g. COM4)")
    m = re.fullmatch(r"(?:\\\\\.\\)?(com\d+)", text, re.IGNORECASE)
    return m.group(1).upper() if m else text


def _import_pyserial():
    try:
        import serial
    except ImportError:
        return None
    if not hasattr(serial, "Serial") or not hasattr(serial, "SerialException"):
        return None                       # an unrelated module called "serial"
    return serial


def _not_found(name: str) -> TransportError:
    return TransportError(f"{name} does not exist (is the Mega's USB cable plugged in?)")


def _in_use(name: str) -> TransportError:
    return TransportError(f"{name} is in use by another program: if that is the DAQ panel, "
                          f"press release on the lens card of its Tip-tilt tab (the lens "
                          f"stays where it is); else close the Arduino IDE's serial monitor "
                          f"or whatever else holds it")


def _gone(name: str, detail: str = "") -> TransportError:
    tail = f" [{detail}]" if detail else ""
    return TransportError(f"{name}: the Mega stopped answering (USB unplugged?){tail}")


# --------------------------------------------------------------------- Win32
_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_BYTE = ctypes.c_uint8
_BOOL = ctypes.c_int
_HANDLE = ctypes.c_void_p

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_MAXDWORD = 0xFFFFFFFF
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_PURGE_TXABORT, _PURGE_RXABORT, _PURGE_TXCLEAR, _PURGE_RXCLEAR = 0x1, 0x2, 0x4, 0x8
_DTR_CONTROL_DISABLE = 0
_RTS_CONTROL_DISABLE = 0
_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND, _ERROR_ACCESS_DENIED = 2, 3, 5
_ERROR_OPERATION_ABORTED = 995
_DEVICE_GONE = (22, 31, 433, 1167)


class DCB(ctypes.Structure):
    """winbase.h DCB, 28 bytes. The bitfields share one DWORD in this order."""

    _fields_ = [
        ("DCBlength", _DWORD), ("BaudRate", _DWORD),
        ("fBinary", _DWORD, 1), ("fParity", _DWORD, 1), ("fOutxCtsFlow", _DWORD, 1),
        ("fOutxDsrFlow", _DWORD, 1), ("fDtrControl", _DWORD, 2), ("fDsrSensitivity", _DWORD, 1),
        ("fTXContinueOnXoff", _DWORD, 1), ("fOutX", _DWORD, 1), ("fInX", _DWORD, 1),
        ("fErrorChar", _DWORD, 1), ("fNull", _DWORD, 1), ("fRtsControl", _DWORD, 2),
        ("fAbortOnError", _DWORD, 1), ("fDummy2", _DWORD, 17),
        ("wReserved", _WORD), ("XonLim", _WORD), ("XoffLim", _WORD),
        ("ByteSize", _BYTE), ("Parity", _BYTE), ("StopBits", _BYTE),
        ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", _WORD),
    ]


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [("ReadIntervalTimeout", _DWORD), ("ReadTotalTimeoutMultiplier", _DWORD),
                ("ReadTotalTimeoutConstant", _DWORD), ("WriteTotalTimeoutMultiplier", _DWORD),
                ("WriteTotalTimeoutConstant", _DWORD)]


class COMSTAT(ctypes.Structure):
    _fields_ = [("flags", _DWORD), ("cbInQue", _DWORD), ("cbOutQue", _DWORD)]


def configure_dcb(dcb: DCB, baudrate: int) -> DCB:
    """``dcb`` at ``baudrate`` 8N1, no flow control, DTR and RTS never asserted."""
    dcb.DCBlength = ctypes.sizeof(DCB)
    dcb.BaudRate = baudrate
    dcb.fBinary = 1
    dcb.fParity = 0
    dcb.fOutxCtsFlow = 0
    dcb.fOutxDsrFlow = 0
    dcb.fDtrControl = _DTR_CONTROL_DISABLE      # DTR high would reset the Mega
    dcb.fDsrSensitivity = 0
    dcb.fTXContinueOnXoff = 0
    dcb.fOutX = 0
    dcb.fInX = 0
    dcb.fErrorChar = 0
    dcb.fNull = 0
    dcb.fRtsControl = _RTS_CONTROL_DISABLE
    dcb.fAbortOnError = 0
    dcb.ByteSize = 8
    dcb.Parity = 0
    dcb.StopBits = 0
    dcb.XonChar = b"\x11"                       # SetCommState refuses XonChar == XoffChar
    dcb.XoffChar = b"\x13"
    return dcb


def comm_timeouts(read_timeout: float, write_timeout: float = WRITE_TIMEOUT_S) -> COMMTIMEOUTS:
    """"Return what is buffered, else wait up to T for the first byte"."""
    t = COMMTIMEOUTS()
    t.ReadIntervalTimeout = _MAXDWORD
    if read_timeout <= 0:
        t.ReadTotalTimeoutMultiplier = 0
        t.ReadTotalTimeoutConstant = 0
    else:
        t.ReadTotalTimeoutMultiplier = _MAXDWORD
        t.ReadTotalTimeoutConstant = min(max(1, round(read_timeout * 1000)), _MAXDWORD - 1)
    t.WriteTotalTimeoutMultiplier = 0
    t.WriteTotalTimeoutConstant = max(1, round(write_timeout * 1000))
    return t


def win32_error(name: str, code: int, action: str) -> TransportError:
    if code in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
        return _not_found(name)
    if action == "open" and code == _ERROR_ACCESS_DENIED:
        return _in_use(name)
    if code in _DEVICE_GONE or code == _ERROR_ACCESS_DENIED:
        return _gone(name, f"Windows error {code}")
    try:
        text = ctypes.FormatError(code).strip()
    except (AttributeError, OSError):
        text = ""
    return TransportError(f"{name}: {action} failed (Windows error {code}{': ' + text if text else ''})")


class _Kernel32:
    """kernel32 with argtypes declared (HANDLE is pointer-sized on 64-bit Python)."""

    def __init__(self) -> None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        p = ctypes.POINTER

        def bind(fn_name, restype, *argtypes):
            fn = getattr(k, fn_name)
            fn.restype = restype
            fn.argtypes = argtypes
            setattr(self, fn_name, fn)

        bind("CreateFileW", _HANDLE, ctypes.c_wchar_p, _DWORD, _DWORD, ctypes.c_void_p,
             _DWORD, _DWORD, _HANDLE)
        bind("SetupComm", _BOOL, _HANDLE, _DWORD, _DWORD)
        bind("GetCommState", _BOOL, _HANDLE, p(DCB))
        bind("SetCommState", _BOOL, _HANDLE, p(DCB))
        bind("SetCommTimeouts", _BOOL, _HANDLE, p(COMMTIMEOUTS))
        bind("PurgeComm", _BOOL, _HANDLE, _DWORD)
        bind("ClearCommError", _BOOL, _HANDLE, p(_DWORD), p(COMSTAT))
        bind("ReadFile", _BOOL, _HANDLE, ctypes.c_void_p, _DWORD, p(_DWORD), ctypes.c_void_p)
        bind("WriteFile", _BOOL, _HANDLE, ctypes.c_char_p, _DWORD, p(_DWORD), ctypes.c_void_p)
        bind("CloseHandle", _BOOL, _HANDLE)
        self.last_error = ctypes.get_last_error


_kernel32: Optional[_Kernel32] = None


def _k32() -> _Kernel32:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = _Kernel32()
    return _kernel32


class Win32Transport(Transport):
    """A COM port through kernel32."""

    backend = "win32"

    def __init__(self, port: str, *, baudrate: int = 115200, api=None) -> None:
        self._handle: Optional[int] = None
        self.name = port_name(port)
        self._api = api if api is not None else _k32()
        self._read_ms: Optional[int] = None
        path = self.name if self.name.startswith("\\\\.\\") else "\\\\.\\" + self.name
        a = self._api
        handle = a.CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if handle is None or handle == _INVALID_HANDLE:
            raise win32_error(self.name, a.last_error(), "open")
        self._handle = handle
        try:
            self._check(a.SetupComm(handle, _BUFFER_BYTES, _BUFFER_BYTES), "SetupComm")
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            self._check(a.GetCommState(handle, ctypes.byref(dcb)), "GetCommState")
            configure_dcb(dcb, baudrate)
            self._check(a.SetCommState(handle, ctypes.byref(dcb)), f"setting {baudrate} 8N1")
            self._set_read_timeout(0.0)
            self._clear_errors()
            self._check(a.PurgeComm(handle, _PURGE_RXABORT | _PURGE_RXCLEAR
                                    | _PURGE_TXABORT | _PURGE_TXCLEAR), "PurgeComm")
        except BaseException:
            self.close()
            raise

    def _check(self, ok, action: str) -> None:
        if not ok:
            raise win32_error(self.name, self._api.last_error(), action)

    def _require_open(self) -> int:
        if self._handle is None:
            raise TransportError(f"{self.name} is closed")
        return self._handle

    def _set_read_timeout(self, timeout: float) -> None:
        t = comm_timeouts(timeout)
        if t.ReadTotalTimeoutConstant == self._read_ms:
            return
        self._check(self._api.SetCommTimeouts(self._require_open(), ctypes.byref(t)), "SetCommTimeouts")
        self._read_ms = t.ReadTotalTimeoutConstant

    def _clear_errors(self) -> int:
        errors = _DWORD(0)
        stat = COMSTAT()
        self._check(self._api.ClearCommError(self._require_open(), ctypes.byref(errors),
                                             ctypes.byref(stat)), "ClearCommError")
        return stat.cbInQue

    def write(self, data: bytes) -> None:
        handle = self._require_open()
        data = bytes(data)
        written = _DWORD(0)
        if not self._api.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            raise win32_error(self.name, self._api.last_error(), "write")
        if written.value != len(data):
            raise _gone(self.name, f"write timed out after {written.value} of {len(data)} bytes")

    def read(self, timeout: float) -> bytes:
        handle = self._require_open()
        self._set_read_timeout(timeout)
        buf = ctypes.create_string_buffer(_BUFFER_BYTES)
        got = _DWORD(0)
        if not self._api.ReadFile(handle, buf, _BUFFER_BYTES, ctypes.byref(got), None):
            code = self._api.last_error()
            if code != _ERROR_OPERATION_ABORTED:
                raise win32_error(self.name, code, "read")
            self._clear_errors()
            if not self._api.ReadFile(handle, buf, _BUFFER_BYTES, ctypes.byref(got), None):
                raise win32_error(self.name, self._api.last_error(), "read")
        return buf.raw[:got.value]

    def reset_input(self) -> None:
        handle = self._require_open()
        self._clear_errors()
        self._check(self._api.PurgeComm(handle, _PURGE_RXABORT | _PURGE_RXCLEAR), "PurgeComm")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.CloseHandle(handle)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ------------------------------------------------------------------ pyserial
class PySerialTransport(Transport):
    """A serial port through pyserial, DTR/RTS set low BEFORE open (so never raised)."""

    backend = "pyserial"

    def __init__(self, port: str, serial, *, baudrate: int = 115200) -> None:
        self.name = port_name(port)
        self._serial = serial
        s = serial.Serial()
        s.port = self.name
        s.baudrate = baudrate
        s.timeout = PYSERIAL_POLL_S
        s.write_timeout = WRITE_TIMEOUT_S
        s.dtr = False
        s.rts = False
        try:
            s.open()
        except (serial.SerialException, OSError, ValueError) as exc:
            text = str(exc)
            m = re.search(r"None, (\d+)\)", text)
            if m:
                raise win32_error(self.name, int(m.group(1)), "open") from None
            if "FileNotFoundError" in text:
                raise _not_found(self.name) from None
            if "PermissionError" in text:
                raise _in_use(self.name) from None
            raise TransportError(f"{self.name}: could not open the port ({text})") from None
        self._port = s
        try:
            s.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            self.close()
            raise _gone(self.name, str(exc)) from None

    def _require_open(self):
        port = getattr(self, "_port", None)
        if port is None:
            raise TransportError(f"{self.name} is closed")
        return port

    def write(self, data: bytes) -> None:
        s = self._require_open()
        try:
            s.write(bytes(data))
        except (self._serial.SerialException, OSError) as exc:
            raise _gone(self.name, str(exc)) from None

    def read(self, timeout: float) -> bytes:
        s = self._require_open()
        deadline = time.monotonic() + max(0.0, float(timeout))
        try:
            while True:
                waiting = s.in_waiting
                if waiting:
                    return bytes(s.read(waiting))
                if time.monotonic() >= deadline:
                    return b""
                data = s.read(1)
                if data:
                    waiting = s.in_waiting
                    return bytes(data + (s.read(waiting) if waiting else b""))
        except (self._serial.SerialException, OSError) as exc:
            raise _gone(self.name, str(exc)) from None

    def reset_input(self) -> None:
        s = self._require_open()
        try:
            s.reset_input_buffer()
        except (self._serial.SerialException, OSError) as exc:
            raise _gone(self.name, str(exc)) from None

    def close(self) -> None:
        port = getattr(self, "_port", None)
        self._port = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
