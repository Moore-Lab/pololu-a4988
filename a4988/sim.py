"""A Mega running lens_z, in software: the same lines, refusals and motion, in time.

For the tests and the panel's ``--lens sim``. It follows firmware/lens_z
(1.3) closely enough that the link and the panel cannot tell: homing runs
its phases (fast seek, back-off, slow seek, park), moves stop on the switch
in their way, `travel` crawls to MAX and backs off, `stop` is always obeyed.
Not modelled: acceleration, lost steps, switch noise.

Each carriage has a TRUE position ``x`` (mm from its MIN switch, which trips
at x <= 0; MAX trips at x >= ``max_at``) and the board's COUNT ``pos``, which
starts at 0 on power-up wherever the carriage is. Homing is what makes the
two agree.

``clock`` may be a :class:`ManualClock`, so a test runs a 30 s homing in no time.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

STEPS_PER_MM = 6400.0
MAX_MM_S = 4000.0 / STEPS_PER_MM
HOME_FAST, HOME_SLOW, BACKOFF, CLEARANCE = 0.30, 0.05, 0.50, 0.50
SEEK_MAX, CLEAR_MAX, DEFAULT_TRAVEL = 60.0, 3.0, 25.0
DT = 0.004


class ManualClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def _steps(mm: float) -> int:
    return int(round(mm * STEPS_PER_MM))


class SimBoard:
    def __init__(self, *, axes: int = 1, start_mm=(6.0, 6.0), max_at=(24.2, 24.2),
                 clock: Callable[[], float] = time.monotonic, version: str = "1.3"):
        self.clock = clock
        self._t = clock()
        self.version = version
        self.fitted = [True, axes == 2]
        self.x = [float(start_mm[0]), float(start_mm[1])]
        self.max_at = [float(max_at[0]), float(max_at[1])]
        self.pos = [0.0, 0.0]
        self.homed = [False, False]
        self.travel = [DEFAULT_TRAVEL, DEFAULT_TRAVEL]
        self.soft = [[CLEARANCE, DEFAULT_TRAVEL - CLEARANCE] for _ in range(2)]
        self.speed = 0.5
        self.motors = False
        self.mode = "idle"                     # idle | moving | homing | measuring
        self.phase = ["done", "done"]
        self.target = [0.0, 0.0]
        self.phase_start = [0.0, 0.0]
        self.max_at_start = [False, False]
        self.fail = ""
        self.retreat = False
        self.override = {}                     # {(axis, "min"|"max"): bool}: a stuck or cut switch
        self.out = bytearray()
        self._line = bytearray()
        self._overflow = False
        self.received: List[str] = []
        self.println(f"READY lens_z {version} steps_per_mm={STEPS_PER_MM:.1f} max_mm_s={MAX_MM_S:.3f} "
                     f"axes={2 if self.fitted[1] else 1} not_homed")

    # --- I/O ----------------------------------------------------------------------
    def println(self, s: str) -> None:
        self.out += (s + "\r\n").encode("ascii")

    def feed(self, data: bytes) -> None:
        self.advance()
        for b in bytes(data):
            if b in (10, 13):
                if self._overflow:
                    self.println("ERR line_too_long")
                elif self._line:
                    line = self._line.decode("ascii").strip()
                    if line:
                        self.received.append(line)
                        self.handle(line)
                self._line = bytearray()
                self._overflow = False
            elif b < 32 or b > 126:
                pass
            elif len(self._line) < 63:
                self._line.append(b)
            else:
                self._overflow = True

    def take(self) -> bytes:
        self.advance()
        data, self.out = bytes(self.out), bytearray()
        return data

    # --- switches -------------------------------------------------------------------
    def tripped(self, i: int, which: str) -> bool:
        key = ("z1" if i == 0 else "z2", which)
        if key in self.override:
            return self.override[key]
        if not self.fitted[i]:
            return True                        # nothing plugged in: NC reads tripped
        return self.x[i] <= 0.0 if which == "min" else self.x[i] >= self.max_at[i]

    def blocking(self, i: int, direction: float) -> Optional[str]:
        if direction < 0 and self.tripped(i, "min"):
            return "min"
        if direction > 0 and self.tripped(i, "max"):
            return "max"
        return None

    def _pos_line(self) -> str:
        return f"POS {self.pos[0]:.4f} {self.pos[1]:.4f}"

    def _shift(self, i: int, d: float) -> None:
        self.pos[i] += d
        self.x[i] += d

    def _toward(self, i: int, target: float, v: float, dt: float) -> bool:
        """Move axis i toward target at v; True once there."""
        d = target - self.pos[i]
        step = v * dt
        if abs(d) <= step:
            self._shift(i, d)
            return True
        self._shift(i, step if d > 0 else -step)
        return False

    # --- time -----------------------------------------------------------------------
    def advance(self) -> None:
        now = self.clock()
        while self._t < now:
            dt = min(DT, now - self._t)
            self._t += dt
            if self.mode == "idle":
                self._t = now
                break
            self._tick(dt)

    def _tick(self, dt: float) -> None:
        if self.mode == "moving":
            self._tick_move(dt)
        elif self.mode == "homing":
            self._tick_home(dt)
        elif self.mode == "measuring":
            self._tick_measure(dt)

    def _idle(self) -> None:
        self.mode = "idle"
        self.motors = False

    def _tick_move(self, dt: float) -> None:
        for i in range(2):
            hit = self.blocking(i, self.target[i] - self.pos[i])
            if hit and abs(self.target[i] - self.pos[i]) > 0:
                self._idle()
                self.retreat = False
                self.println(f"ERR limit z{i + 1}{hit} at {self._pos_line()}")
                return
        done = all(self._toward(i, self.target[i], self.speed, dt) for i in range(2))
        if done:
            self._idle()
            if self.retreat:
                self.retreat = False
                self.println(f"OK TRAVEL {self.travel[0]:.4f} {self.travel[1]:.4f}")
            else:
                self.println(f"OK {self._pos_line()}")

    def _enter(self, i: int, p: str) -> None:
        self.phase[i] = p
        self.phase_start[i] = self.pos[i]
        if p == "backoff":
            self.target[i] = self.pos[i] + BACKOFF
        elif p == "park":
            self.pos[i] = 0.0                  # the count resets; the carriage stays: the slow trigger IS zero
            self.target[i] = CLEARANCE

    def _tick_home(self, dt: float) -> None:
        for i in range(2):
            p = self.phase[i]
            if p == "clear":
                if not self.tripped(i, "min"):
                    self._enter(i, "fast")
                elif self.tripped(i, "max"):
                    self.phase[i], self.fail = "fail", "switch_fault"
                elif abs(self.pos[i] - self.phase_start[i]) > CLEAR_MAX:
                    self.phase[i], self.fail = "fail", "min_stuck"
                else:
                    self._shift(i, HOME_SLOW * dt)
            elif p == "fast":
                if self.tripped(i, "min"):
                    self._enter(i, "backoff")
                    continue
                if self.max_at_start[i] and not self.tripped(i, "max"):
                    self.max_at_start[i] = False
                if self.tripped(i, "max") and not self.max_at_start[i]:
                    self.phase[i], self.fail = "fail", "wrong_direction"
                elif abs(self.pos[i] - self.phase_start[i]) > SEEK_MAX:
                    self.phase[i], self.fail = "fail", "no_min_switch"
                else:
                    self._shift(i, -HOME_FAST * dt)
            elif p == "backoff":
                if self._toward(i, self.target[i], HOME_FAST, dt):
                    self._enter(i, "slow")
            elif p == "slow":
                if self.tripped(i, "min"):
                    self._enter(i, "park")
                elif abs(self.pos[i] - self.phase_start[i]) > SEEK_MAX:
                    self.phase[i], self.fail = "fail", "no_min_switch"
                else:
                    self._shift(i, -HOME_SLOW * dt)
            elif p == "park":
                if self._toward(i, self.target[i], self.speed, dt):
                    self.homed[i] = True
                    self.soft[i] = [CLEARANCE, self.travel[i] - CLEARANCE]
                    self.phase[i] = "done"
        if any(p not in ("done", "fail") for p in self.phase):
            return
        self._idle()
        if self.all_homed():
            self.println(f"OK HOMED {self._pos_line()}")
        else:
            self.println(f"ERR home {self.fail} {self._pos_line()}")
            self.homed = [False, False]

    def _tick_measure(self, dt: float) -> None:
        for i in range(2):
            if self.phase[i] in ("done", "fail"):
                continue
            if self.tripped(i, "max"):
                self.travel[i] = self.pos[i]
                self.soft[i][1] = self.pos[i] - CLEARANCE
                self.phase[i] = "done"
            elif abs(self.pos[i] - self.phase_start[i]) > SEEK_MAX:
                self.phase[i], self.fail = "fail", "no_max_switch"
            else:
                self._shift(i, self.speed * dt)
        if any(p not in ("done", "fail") for p in self.phase):
            return
        if all(p == "done" for p in self.phase):
            self.target = [self.soft[i][1] if self.fitted[i] else self.pos[i] for i in range(2)]
            self.retreat = True
            self.mode = "moving"
        else:
            self._idle()
            self.println(f"ERR measure {self.fail}")

    # --- commands -------------------------------------------------------------------
    def all_homed(self) -> bool:
        return all(self.homed[i] or not self.fitted[i] for i in range(2))

    def begin_move(self, z1: float, z2: float) -> None:
        if self.mode != "idle":
            return self.println("ERR busy")
        if not self.all_homed():
            return self.println("ERR not_homed")
        t = [z1, z2]
        for i in range(2):
            if not self.fitted[i]:
                t[i] = self.pos[i]
                continue
            lo, hi = self.soft[i]
            if _steps(t[i]) < _steps(lo) or _steps(t[i]) > _steps(hi):
                return self.println(f"ERR soft_limit z{i + 1} {lo:.3f} {hi:.3f}")
            hit = self.blocking(i, t[i] - self.pos[i])
            if hit:
                return self.println(f"ERR on_limit z{i + 1}{hit}")
        self.target = [_steps(v) / STEPS_PER_MM for v in t]
        self.motors = True
        self.mode = "moving"
        self.println("MOVING")

    def handle(self, cmd: str) -> None:
        parts = cmd.split()
        verb = parts[0].lower()
        n = len(parts)

        def num(s: str) -> float:
            try:
                return float(s)
            except ValueError:
                return 0.0                     # Arduino's String.toFloat()

        if verb == "ping":
            self.println(f"OK lens_z {self.version}")
        elif verb == "status":
            sw = "".join("1" if self.tripped(i, w) else "0" for i in range(2) for w in ("min", "max"))
            s = self.soft
            self.println(f"OK STATUS pos {self.pos[0]:.4f} {self.pos[1]:.4f} sw {sw} "
                         f"homed {int(self.all_homed())} moving {int(self.mode != 'idle')} "
                         f"motors {int(self.motors)} soft {s[0][0]:.3f} {s[0][1]:.3f} "
                         f"{s[1][0]:.3f} {s[1][1]:.3f} axes {2 if self.fitted[1] else 1}")
        elif verb == "stop":
            self._idle()
            self.retreat = False
            self.phase = ["done", "done"]
            self.println(f"OK STOPPED {self._pos_line()}")
        elif verb == "home":
            if self.mode != "idle":
                return self.println("ERR busy")
            for i in range(2):
                if self.fitted[i] and self.tripped(i, "min") and self.tripped(i, "max"):
                    return self.println(f"ERR home switch_fault z{i + 1}")
            self.motors = True
            for i in range(2):
                if not self.fitted[i]:
                    self.phase[i] = "done"
                    continue
                self.max_at_start[i] = self.tripped(i, "max")
                self.homed[i] = False
                self.phase[i] = "clear" if self.tripped(i, "min") else "fast"
                self.phase_start[i] = self.pos[i]
            self.fail = ""
            self.mode = "homing"
            self.println("HOMING")
        elif verb == "travel":
            if self.mode != "idle":
                return self.println("ERR busy")
            if not self.all_homed():
                return self.println("ERR not_homed")
            self.motors = True
            for i in range(2):
                self.phase[i] = "done" if (not self.fitted[i] or self.tripped(i, "max")) else "slow"
                self.phase_start[i] = self.pos[i]
                if self.phase[i] == "done" and self.fitted[i]:
                    self.travel[i] = self.pos[i]
                    self.soft[i][1] = self.pos[i] - CLEARANCE
            self.mode = "measuring"
            self.println("MEASURING")
        elif verb == "move":
            if n == 2:
                z = num(parts[1])
                self.begin_move(z, z)
            elif n == 3:
                if not self.fitted[1]:
                    return self.println("ERR not_fitted z2")
                self.begin_move(num(parts[1]), num(parts[2]))
            else:
                self.println("ERR usage move <mm> | move <z1_mm> <z2_mm>")
        elif verb == "jog":
            if n != 3:
                return self.println("ERR usage jog <z1|z2|both> <dmm>")
            which, d = parts[1].lower(), num(parts[2])
            a, b = self.pos
            if which == "z2" and not self.fitted[1]:
                return self.println("ERR not_fitted z2")
            if which == "z1":
                self.begin_move(a + d, b)
            elif which == "z2":
                self.begin_move(a, b + d)
            elif which == "both":
                self.begin_move(a + d, b + d)
            else:
                self.println("ERR axis")
        elif verb == "enable":
            self.motors = True
            self.println("OK enabled")
        elif verb == "disable":
            if self.mode != "idle":
                return self.println("ERR busy")
            self.motors = False
            self.println("OK disabled")
        elif verb == "speed":
            if n != 2:
                return self.println("ERR usage speed <mm_per_s>")
            v = num(parts[1])
            if v <= 0:
                return self.println("ERR speed")
            self.speed = min(v, MAX_MM_S)
            self.println(f"OK speed {self.speed:.4f}")
        else:
            self.println("ERR unknown_command")


class SimTransport:
    """The byte pipe to a SimBoard, shaped like a4988.transport.Transport."""

    backend = "sim"

    def __init__(self, board: SimBoard):
        self.board = board
        self.name = "SIM"
        self.closed = False

    def write(self, data: bytes) -> None:
        self.board.feed(data)

    def read(self, timeout: float) -> bytes:
        data = self.board.take()
        if not data and timeout > 0:
            time.sleep(min(timeout, 0.005))
            data = self.board.take()
        return data

    def reset_input(self) -> None:
        self.board.take()

    def close(self) -> None:
        self.closed = True
