"""The lens_z firmware (firmware/lens_z) as a Python object.

    from a4988 import LensZ
    lz = LensZ("auto")          # the one Mega 2560 on USB, found by its USB ID
    lz.open()                   # no reset: the board keeps its homing
    lz.status()                 # {"pos": {"z1": 0.5, ...}, "homed": True, ...}
    lz.move(3.0)                # starts a move; returns at once ("MOVING")
    lz.wait()                   # until the board reports it finished
    lz.close()

Positions are millimetres FROM THE MIN SWITCH: homing zeroes each axis where
its MIN switch trips on the slow approach, so 0 is the switch itself and +
runs toward MAX. Until the axis is homed the board's count is only "since
power-up", and it refuses absolute moves (``not_homed``).

The protocol (README, "Serial protocol"): every command gets exactly one reply
line, ``OK ...`` or ``ERR ...``, except the motion commands, whose first reply
is ``MOVING`` / ``HOMING`` / ``MEASURING`` (or an ``ERR`` refusal) and whose
outcome arrives later, by itself, as one more line:

    OK POS a b                 a move finished
    OK HOMED POS a b           homing finished
    OK TRAVEL a b              travel measured (and the retreat off MAX done)
    OK STOPPED POS a b         the reply to `stop`
    ERR limit z1min at POS a b a move ran into a switch and stopped
    ERR home <reason> POS a b  homing failed
    ERR measure <reason>       travel failed
    READY lens_z ...           the board (re)booted: homing is gone

Those lines can arrive in the middle of another exchange (a `status` poll
during a move, say), so :meth:`command` sorts every line it reads: the reply it
is waiting for ends the exchange, and outcomes go to :attr:`events` (and
:attr:`on_event`) whenever they turn up.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from .transport import TransportError, open_transport, port_name

FIRMWARE = "lens_z"
AXES = ("z1", "z2")
#: 400 steps/rev (PK243M-02BA, 0.9 deg) x 8 microsteps / 0.5 mm per turn
#: (Newport SM micrometer). The firmware's STEPS_PER_MM; READY reports it too.
STEPS_PER_MM = 6400.0
MAX_MM_S = 4000.0 / STEPS_PER_MM          # the firmware's step-rate ceiling, 0.625 mm/s
REPLY_TIMEOUT_S = 1.0

_EVENT_RE = re.compile(r"^(?:OK (?:POS|HOMED|TRAVEL|STOPPED) |ERR (?:limit|measure) |ERR home \S+ POS |READY\b)")
_STARTED = {"MOVING": "moving", "HOMING": "homing", "MEASURING": "measuring"}
_STATUS_RE = re.compile(
    r"^OK STATUS pos (\S+) (\S+) sw ([01])([01])([01])([01]) homed ([01]) moving ([01]) "
    r"motors ([01]) soft (\S+) (\S+) (\S+) (\S+)(?: axes (\d))?\s*$")
_POS_RE = re.compile(r"POS (\S+) (\S+)\s*$")


class LensZError(Exception):
    """Something went wrong talking to the board."""


class NoReply(LensZError):
    """The board did not answer in time."""


class Refused(LensZError):
    """The board said ERR to a command: well formed, but not possible now.

    ``line`` is the board's own line, ``code`` its second word (``not_homed``,
    ``busy``, ``soft_limit``, ...); ``str()`` is :func:`explain` of it.
    """

    def __init__(self, line: str):
        self.line = line
        parts = line.split()
        self.code = parts[1] if len(parts) > 1 else ""
        super().__init__(explain(line))


def _pos(line: str):
    m = _POS_RE.search(line)
    if not m:
        return None
    try:
        return {"z1": float(m.group(1)), "z2": float(m.group(2))}
    except ValueError:
        return None


def _switch_name(token: str) -> str:
    """z1min -> "Z1 MIN"."""
    m = re.fullmatch(r"(z\d)(min|max)", token or "")
    return f"{m.group(1).upper()} {m.group(2).upper()}" if m else token


_HOME_REASONS = {
    "switch_fault": "both switches of one axis read tripped (a switch cable unplugged?)",
    "wrong_direction": "reached MAX while seeking MIN: the direction, or the two switches, are swapped",
    "no_min_switch": "no MIN switch within 60 mm",
    "min_stuck": "MIN still tripped 3 mm after moving off it (its wire cut?)",
}


def explain(line: str) -> str:
    """An ERR line from the board, in words an operator can act on."""
    parts = line.split()
    code = parts[1] if len(parts) > 1 else ""
    if code == "not_homed":
        return "not homed: press home first (positions count from the MIN switch, so there are none until it is found)"
    if code == "busy":
        return "the stage is still moving: stop it, or wait for it to finish"
    if code == "soft_limit" and len(parts) >= 5:
        return f"{parts[2].upper()} can go only from {parts[3]} to {parts[4]} mm from MIN"
    if code == "on_limit" and len(parts) >= 3:
        side = "+" if parts[2].endswith("min") else "-"
        return f"{_switch_name(parts[2])} switch is tripped: that axis can only move away from it ({side})"
    if code == "not_fitted":
        return "Z2 is not fitted: this firmware drives Z1 alone (Z2_FITTED 0)"
    if code == "home" and len(parts) >= 3:
        why = _HOME_REASONS.get(parts[2], parts[2])
        if "POS" in parts:
            return f"homing failed: {why}"
        axis = parts[3].upper() if len(parts) > 3 else ""
        return f"homing refused: both {axis} switches read tripped (a switch cable unplugged?)"
    if code == "limit" and len(parts) >= 3:
        p = _pos(line)
        where = f" at {p['z1']:.4f} mm" if p else ""
        return f"stopped: the {_switch_name(parts[2])} switch tripped{where}"
    if code == "measure":
        reason = parts[2] if len(parts) > 2 else "?"
        return "travel failed: " + ("no MAX switch within 60 mm" if reason == "no_max_switch" else reason)
    return f"the board refused it: {line}"


def parse_event(line: str, t: Optional[float] = None) -> dict:
    """One outcome line as a dict: kind, ok, the line, its time, and what it carries."""
    ev = {"t": t if t is not None else time.time(), "line": line, "ok": line.startswith("OK")}
    if line.startswith("OK HOMED"):
        ev.update(kind="homed", pos=_pos(line))
    elif line.startswith("OK POS"):
        ev.update(kind="moved", pos=_pos(line))
    elif line.startswith("OK STOPPED"):
        ev.update(kind="stopped", pos=_pos(line))
    elif line.startswith("OK TRAVEL"):
        parts = line.split()
        try:
            ev.update(kind="travel", travel_mm={"z1": float(parts[2]), "z2": float(parts[3])})
        except (IndexError, ValueError):
            ev.update(kind="travel")
    elif line.startswith("ERR limit"):
        parts = line.split()
        ev.update(kind="limit", switch=parts[2] if len(parts) > 2 else "", pos=_pos(line),
                  message=explain(line))
    elif line.startswith("ERR home"):
        ev.update(kind="home_failed", reason=line.split()[2], pos=_pos(line), message=explain(line))
    elif line.startswith("ERR measure"):
        ev.update(kind="travel_failed", message=explain(line))
    elif line.startswith("READY"):
        parts = line.split()
        ev.update(kind="reset", ok=False, firmware=parts[2] if len(parts) > 2 else "",
                  message="the board restarted: it is no longer homed")
    else:
        ev.update(kind="other")
    return ev


def parse_status(line: str) -> dict:
    """``OK STATUS ...`` as a dict (positions in mm from MIN once homed)."""
    m = _STATUS_RE.match(line.strip())
    if not m:
        raise LensZError(f"not a status line: {line!r}")
    g = m.groups()
    return {
        "pos": {"z1": float(g[0]), "z2": float(g[1])},
        "sw": {"z1": {"min": g[2] == "1", "max": g[3] == "1"},
               "z2": {"min": g[4] == "1", "max": g[5] == "1"}},
        "homed": g[6] == "1",
        "moving": g[7] == "1",
        "motors": g[8] == "1",
        "soft": {"z1": [float(g[9]), float(g[10])], "z2": [float(g[11]), float(g[12])]},
        "axes": int(g[13]) if g[13] else 2,
    }


def _accepts(verb: str) -> Callable[[str], bool]:
    """Which line ends the exchange for a command starting with ``verb``."""
    def err(line):
        return line.startswith("ERR") and not _EVENT_RE.match(line)
    if verb == "ping":
        return lambda l: l.startswith("OK " + FIRMWARE) or err(l)
    if verb == "status":
        return lambda l: l.startswith("OK STATUS") or err(l)
    if verb == "stop":
        return lambda l: l.startswith("OK STOPPED") or err(l)
    if verb in ("move", "jog", "home", "travel"):
        return lambda l: l in _STARTED or err(l)
    word = {"enable": "OK enabled", "disable": "OK disabled", "speed": "OK speed"}.get(verb, "OK")
    return lambda l: l.startswith(word) or err(l)


class LensZ:
    """One Mega running lens_z, over its serial port. Thread-safe (one lock).

    ``port``: "auto" (the only Mega 2560 on USB, by USB ID), or "COM4".
    ``sim``: a :class:`a4988.sim.SimBoard` to talk to instead of hardware.
    """

    def __init__(self, port: str = "auto", *, sim=None, transport_factory=None,
                 reply_timeout: float = REPLY_TIMEOUT_S):
        self.spec = port
        self.sim = sim
        self._factory = transport_factory
        self.reply_timeout = reply_timeout
        self.lock = threading.RLock()
        self._t = None
        self._rx = b""
        self.port = ""
        self.error = ""
        self.firmware = ""
        #: What the board is busy with, as far as this link started it:
        #: "", "moving", "homing" or "measuring". Cleared by the outcome line.
        self.activity = ""
        self.events: Deque[dict] = deque(maxlen=200)
        self.on_event: Optional[Callable[[dict], None]] = None
        self.last_status: Optional[dict] = None

    # --- connection -------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._t is not None

    def _open_transport(self):
        if self.sim is not None:
            from .sim import SimTransport
            return SimTransport(self.sim)
        if self._factory is not None:
            return self._factory(self.spec)
        if (self.spec or "auto").lower() == "auto":
            from .ports import find_mega_port
            try:
                port = find_mega_port()
            except LookupError as exc:
                raise TransportError(str(exc)) from None
        else:
            port = port_name(self.spec)
        return open_transport(port)

    def open(self) -> None:
        """Open the port (no reset) and check that lens_z answers. Raises with the reason."""
        with self.lock:
            self.close()
            try:
                t = self._open_transport()
            except Exception as exc:
                self.error = str(exc)
                raise
            self._t, self._rx = t, b""
            self.port = getattr(t, "name", "") or str(self.spec)
            try:
                # End any half line left in the board's buffer, then let lines
                # that were queued before we opened (an outcome, a READY) arrive
                # and be sorted before the first exchange.
                t.write(b"\n")
                self._drain(0.15)
                reply = self.command("ping")
                if not reply.startswith("OK " + FIRMWARE):
                    raise LensZError(f"{self.port} answered {reply!r}: not the {FIRMWARE} firmware")
                parts = reply.split()
                self.firmware = parts[2] if len(parts) > 2 else ""
                self.status()
            except Exception as exc:
                self.close()
                self.error = str(exc)
                raise
            self.error = ""

    def close(self) -> None:
        """Release the port. The board carries on (a move in progress finishes)."""
        with self.lock:
            t, self._t = self._t, None
            if t is not None:
                try:
                    t.close()
                except Exception:
                    pass

    # --- lines ------------------------------------------------------------------
    def _readline(self, deadline: float) -> Optional[str]:
        while True:
            i = self._rx.find(b"\n")
            if i >= 0:
                raw, self._rx = self._rx[:i], self._rx[i + 1:]
                return raw.decode("ascii", "replace").strip()
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                chunk = self._t.read(min(left, 0.1))
            except TransportError as exc:
                self._lost(exc)
                raise
            if chunk:
                self._rx += chunk

    def _lost(self, exc) -> None:
        self.error = str(exc)
        t, self._t = self._t, None
        try:
            t.close()
        except Exception:
            pass

    def _drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            line = self._readline(deadline)
            if line is None:
                return
            self._sort(line)

    def _sort(self, line: str) -> None:
        """A line nobody was waiting for: an outcome, a progress word, or noise."""
        if not line:
            return
        if _EVENT_RE.match(line):
            ev = parse_event(line)
            if ev["kind"] != "other":
                self.activity = ""
            if ev.get("kind") == "travel" and ev.get("travel_mm"):
                self.travel_mm = ev["travel_mm"]
            self.events.append(ev)
            cb = self.on_event
            if cb is not None:
                try:
                    cb(ev)
                except Exception:
                    pass
        elif line in _STARTED:
            self.activity = _STARTED[line]

    def command(self, cmd: str, timeout: Optional[float] = None) -> str:
        """Send one command; return the line that answers it (OK..., ERR..., or a
        progress word for a motion command). Outcome lines met on the way go to
        :attr:`events`. Raises NoReply, or TransportError if the port died."""
        verb = cmd.split()[0].lower() if cmd.split() else ""
        accept = _accepts(verb)
        with self.lock:
            if self._t is None:
                raise LensZError(self.error or "not connected")
            try:
                self._t.write((cmd.strip() + "\n").encode("ascii"))
            except TransportError as exc:
                self._lost(exc)
                raise
            deadline = time.monotonic() + (timeout if timeout is not None else self.reply_timeout)
            while True:
                line = self._readline(deadline)
                if line is None:
                    raise NoReply(f"no reply to {cmd!r} from {self.port}")
                if accept(line):
                    if verb == "stop":
                        self._sort(line)          # it is also the end of whatever ran
                    return line
                self._sort(line)

    def _do(self, cmd: str) -> str:
        line = self.command(cmd)
        if line.startswith("ERR"):
            raise Refused(line)
        if line in _STARTED:
            self.activity = _STARTED[line]
        return line

    # --- commands ---------------------------------------------------------------
    travel_mm: Optional[Dict[str, float]] = None

    def ping(self) -> str:
        return self._do("ping")

    def status(self) -> dict:
        s = parse_status(self._do("status"))
        if not s["moving"]:
            self.activity = ""
        elif not self.activity:
            self.activity = "moving"          # started by someone else, or before we connected
        self.last_status = s
        return s

    def move(self, z1: float, z2: Optional[float] = None) -> str:
        """Start a move TO ``z1`` (and ``z2``) mm from MIN. One number moves both
        axes together (Z1 alone on a one-motor build)."""
        if z2 is None:
            return self._do(f"move {float(z1):.4f}")
        return self._do(f"move {float(z1):.4f} {float(z2):.4f}")

    def jog(self, axis: str, mm: float) -> str:
        """Start a move BY ``mm``: axis "z1", "z2" or "both"."""
        axis = str(axis).lower()
        if axis not in ("z1", "z2", "both"):
            raise ValueError(f"axis must be z1, z2 or both, not {axis!r}")
        return self._do(f"jog {axis} {float(mm):.4f}")

    def home(self) -> str:
        """Start homing: each axis seeks its MIN switch, backs off, finds it again
        slowly (that trip is 0), and parks at 0.5 mm."""
        return self._do("home")

    def travel(self) -> str:
        """Start measuring the range: crawl to MAX, then back off it by 0.5 mm."""
        return self._do("travel")

    def stop(self) -> str:
        return self._do("stop")

    def enable(self) -> str:
        return self._do("enable")

    def disable(self) -> str:
        return self._do("disable")

    def speed(self, mm_s: float) -> float:
        """Set the move speed; returns what the board set (clamped to its ceiling)."""
        line = self._do(f"speed {float(mm_s):.4f}")
        return float(line.split()[2])

    def wait(self, timeout: float = 120.0, poll_s: float = 0.2) -> Optional[dict]:
        """Poll until the board is idle; return the last outcome event (or None)."""
        n0 = len(self.events)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = self.status()
            if not s["moving"]:
                return self.events[-1] if len(self.events) > n0 or self.events else None
            time.sleep(poll_s)
        raise NoReply(f"still moving after {timeout:.0f} s")
