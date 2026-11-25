# xsphere-lens-z — Development Session Log

Running, chronological record of development for **this repo only**
(`xsphere-lens-z`, the two-motor lens axis). It is meant to sit alongside
`xsphere-daq` the way the other instrument repos do; changes to the parent's
panel, shell, dashboard or launcher are logged in *that* repo's
`docs/session-log.md` — write to the log of the repo you're modifying.

Scope of this log: the Arduino firmware, the wiring documentation, and (later)
the host-side link and its tests.

Newest entries first. Keep entries short and factual; convert relative dates to
absolute. See the [README](../README.md) for the wiring and the protocol.

---

## 2026-11-24 (later) — Drivers identified as A4988; wiring cut to that part

Lucas read `4988ET` off the driver module: Allegro **A4988**, 28-pin QFN
(`A4988SETTR-T`). The README's two driver families are replaced by one
A4988 section, every figure checked against the Allegro datasheet rather than
recalled (the PDF was fetched and its text extracted locally; facts taken from
it are marked **[DS]** in the README).

What the datasheet settled, some of it against what I would have written from
memory:
- `ITripMAX = VREF / (8 × RS)`. `RS` is a carrier-board choice, not a chip
  property, so the `4988ET` marking does not give it — 0.050 / 0.068 / 0.100 Ω
  are all in circulation and span a factor of two in current. Must be read off
  the board. VREF table for all three is in the README.
- **No internal pull-ups anywhere on the part.** The only internal resistors
  are pull-downs on MS1 (100 kΩ), MS2 (50 kΩ), MS3 (100 kΩ). RESET, SLEEP and
  ENABLE have none. So the usual RESET–SLEEP jumper works only because the
  *carrier* pulls SLEEP high — added a step to measure SLEEP and a 10 kΩ
  fallback, rather than asserting the jumper always works.
- ENABLE: logic LOW enables the outputs. `ENABLE_ACTIVE_LOW 1` is correct;
  the comment in the sketch now says so with the reason.
- Microstep truth table: 1/8 is MS1=H, MS2=H, MS3=L. With the pull-downs,
  floating means full step, so MS1/MS2 must be actively tied to 5 V.
- VBB operating range **8–35 V** — a 5 V supply will not run it.
- Current-trip error is ±5 % near full scale but **±15 % at the 38.27 %
  microstep level**, so 1/8 microstepping divides the step and not the error.
  The README now says to treat the camera, not the step count, as position.
- STEP minimum 1 µs high / 1 µs low, 200 ns setup and hold: AccelStepper on a
  16 MHz AVR cannot approach these, so nothing to tune.

Also added a bring-up step 0 (set VREF, check RESET/SLEEP, check MS wiring,
fit the 100 µF) to be done before a motor is ever plugged in.

---

## 2026-11-24 — Repo created; firmware for Z1/Z2 with four limit switches

**Why.** Lucas is setting up a lens on two stepper motors that push it along
one axis, with compact stepper drivers, to be folded into the DAQ later. The
starting point was Moore-Lab/Arduino-Stepper-Motors (`06_17_2025.ino`), which
drove an x/y stage with one limit switch per axis.

**What the reference needed changing.**
- Homing ran inside `setup()`, blocking, in a loop that could not end if a
  switch never closed. The board resets when a host opens its serial port, so
  under the DAQ that would re-home the lens on every connect, including
  automatic reconnects. Homing is now a non-blocking state machine run only on
  command, and `stop` is honoured at every step.
- The limit switches were read only while homing. A `move` with a wrong number
  drove into the hardware with nothing watching. The switch lying in each
  axis's direction is now checked every pass, and a trip on either axis stops
  **both** — they carry one lens, so letting the free one continue would twist
  the mount.
- `maxSpeed` asked for `1000 * 16 = 16000` steps/s. A 16 MHz AVR running
  AccelStepper tops out near 4000, so that silently delivered a quarter of
  what it asked for. Everything is in mm/s against an honest ceiling now, and
  the default is 1/8 microstepping (0.63 mm/s, 156 nm) rather than 1/16.
- The bare `2 *` in the step conversion is `LEAD_MM_PER_REV 0.5` — the number
  stamped on the screw.
- `ezButton` replaced by ~12 lines inline, because its `loop()` calls
  `millis()` per switch and these are read inside the step loop.

**What two switches per axis buys.** The far end of travel is protected as
well as the near end; limits are live during ordinary moves; and `travel` can
*measure* MIN→MAX so the soft limits are measured rather than assumed. Homing
both axes onto their own MIN switches also squares the mount, free.

**Board: Arduino Mega 2560.** D2–D10 mean the same on a Mega as on an Uno, so
the pin map is unchanged: DIR 2/4, STEP 3/5, ENABLE 6 (shared), endstops
7/8/9/10. The endstops stay in that block because the GND beside D13 is four
pins away, which keeps the pull-up returns short next to stepper cable. The
Mega does not raise the step-rate ceiling (same 16 MHz core) but does give
8 KB of SRAM, so no `F()` gymnastics. `ENABLE` is two defines (`Z1_ENA_PIN`,
`Z2_ENA_PIN`), both D6, so a board with per-driver enables needs only two new
numbers.

**Endstops are NC to GND with `INPUT_PULLUP`**, so a cut or unplugged wire
reads as tripped and stops the axis instead of quietly removing the
protection. `ENDSTOP_NORMALLY_CLOSED 1`.

**Not compiled.** There is no Arduino toolchain and no C++ compiler on the rig
PC. The sketch has been reviewed and structurally checked (braces balanced,
every identifier defined, every call resolved — which caught four defects)
but **not built**. Build it in the IDE before trusting it, and do the bring-up
in the README with the motors uncoupled from the lens.

**Still to do.** Confirm the driver model (the README documents both carrier
boards and opto-isolated bricks; it can be cut to one). Then a `LensZLink`
over `pyserial` and a `/lens` page in the parent, following the
`xsphere-rigol-dg1022z` session/lock pattern.
