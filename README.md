# pololu-a4988

The **lens Z stage** of the xsphere rig: two stepper motors (**Z1** and **Z2**)
driving **one lens along one axis**, with **two limit switches per motor**, on
**Pololu A4988** carriers and an **Arduino Mega 2560**. Firmware in
[`firmware/lens_z/lens_z.ino`](firmware/lens_z/lens_z.ino); the Python link the
DAQ uses in [`a4988/`](a4988/). A submodule of
[xsphere-daq](https://github.com/Moore-Lab/xsphere-daq), whose Tip-tilt tab
carries the stage's controls.

**Wiring schematic and bench checklist:** [`docs/wiring.html`](docs/wiring.html)
(open it in a browser; four figures, every Mega pin, and the checks to run
before motor power goes on).

Derived from [Moore-Lab/Arduino-Stepper-Motors](https://github.com/Moore-Lab/Arduino-Stepper-Motors)
(`06_17_2025.ino`), which drove an x/y stage with one switch per axis.

> **Status, 2026-11-24.** Firmware `lens_z 1.3` is on the Mega, built for one
> motor (`Z2_FITTED 0`: Z2 is not wired yet). Z1 drives a Newport 423 stage
> through its SM micrometer (0.5 mm per turn, so 6400 microsteps per mm,
> 0.156 µm each). Both Z1 switches pass the press test and Z1 has homed. The
> first homing tripped MIN 6.5 mm earlier on the fast pass than on the slow
> one; that is not explained yet (see the session log), so check the zero
> against the micrometer before trusting absolute positions.

---

## What changed from the reference, and why

| | reference | here |
|---|---|---|
| axes | X, Y — independent | Z1, Z2 — **one lens**, so coupled: homed and moved together |
| limit switches | 1 per axis (MIN) | **2 per axis** (MIN + MAX) |
| limits during a `move` | not checked at all | checked every pass, either axis stops both |
| homing | blocking, inside `setup()` | non-blocking state machine, on command only |
| homing passes | single | fast seek, back off, **slow seek sets zero** (repeatable) |
| zero | after the back-off | the MIN switch itself |
| travel range | assumed | `travel` **measures** it MIN→MAX, sets the soft limits |
| max speed | asked for 16000 steps/s | honest 4000 steps/s ceiling, everything in mm/s |
| debounce | `ezButton` | ~12 lines inline, no library, cheap inside the step loop |

Four of those are worth a sentence each.

**Homing out of `setup()` was the important one.** The Mega **resets when a
host opens its serial port** — DTR pulls the reset line, exactly as on an Uno. Homing from `setup()` therefore drives the lens every
time the DAQ connects — including automatic reconnections after a dropout.
Nothing moves here until something sends `home`.

**Limits are now live.** In the reference the switches were read only while
homing, so a `move` with a wrong number drove into the hardware with nothing
watching. Here the switch lying in each axis's direction of travel is checked
on every pass, and a trip on either axis stops **both**, because they carry one
lens between them and letting the free one continue would twist the mount.

**Homing both axes against their own MIN switches squares the mount.** If the
two switches sit at the same physical height, the lens ends up level however
skewed it started — the standard dual-Z levelling trick, free here because you
already have two motors on one axis.

**The speed ceiling is real.** The reference asked for `1000 * 16 = 16000`
steps/s. A 16 MHz AVR running AccelStepper manages about **4000** — the
library's own documentation says so — so that setting silently delivered a
quarter of what it asked for. The Mega does not lift this: same 16 MHz core,
same `digitalWrite`, same ceiling. What it does give is 8 KB of SRAM against
the Uno's 2 KB, so nothing here needs `F()` to keep its strings out of RAM. At 400 steps/rev and a 0.5 mm lead:

| microsteps | steps/mm | top speed | resolution |
|---|---|---|---|
| 1/16 | 12800 | 0.31 mm/s | 78 nm |
| **1/8** | **6400** | **0.63 mm/s** | **156 nm** ← default here |
| 1/4 | 3200 | 1.25 mm/s | 313 nm |
| 1/2 | 1600 | 2.50 mm/s | 625 nm |
| full | 800 | 5.00 mm/s | 1.25 µm |

1/16 buys 78 nm steps, far below what a leadscrew stage can repeat, and costs a
factor of two in speed. 1/8 is the default; drop to 1/4 if the stage feels
slow. **`MICROSTEPS` in the sketch must match the driver's jumpers.**

---

## Pin map

Arduino **Mega 2560**. D0 and D1 are the USB serial port; D13 has the on-board
LED and its resistor, which spoils `INPUT_PULLUP`, so no switch goes there.

The Mega has 54 digital pins, but D2–D10 are the right nine to use anyway:
they mean the same thing on a Mega as on an Uno (so the sketch moves between
boards unchanged), they sit in one block, and the **GND pin immediately beside
D13** is four pins from the endstop group — which keeps the pull-up return
paths short, next to stepper cables that are electrically loud.

| Mega | firmware | goes to | note |
|---|---|---|---|
| D2 | `Z1_DIR_PIN` | Z1 driver **DIR** | |
| D3 | `Z1_STEP_PIN` | Z1 driver **STEP** | |
| D4 | `Z2_DIR_PIN` | Z2 driver **DIR** | |
| D5 | `Z2_STEP_PIN` | Z2 driver **STEP** | |
| D6 | `Z1_ENA_PIN`, `Z2_ENA_PIN` | **ENABLE** on *both* drivers | active LOW; 10 kΩ to 5 V recommended |
| D7 | `Z1_MIN_PIN` | Z1 **MIN** switch, NC | COM to GND |
| D8 | `Z1_MAX_PIN` | Z1 **MAX** switch, NC | COM to GND |
| D9 | `Z2_MIN_PIN` | Z2 **MIN** switch, NC | COM to GND |
| D10 | `Z2_MAX_PIN` | Z2 **MAX** switch, NC | COM to GND |
| 5V | | VDD, MS1, MS2 on both drivers | |
| GND | | switch commons, driver logic GND, **motor supply −** | |

The full two-channel schematic is Fig 2 of [`docs/wiring.html`](docs/wiring.html),
and the same table sits at the top of the sketch.

Everything else — D11–D53, A0–A15, the three spare hardware serial ports and
I2C — is free.

**ENABLE.** The sketch has `Z1_ENA_PIN` and `Z2_ENA_PIN`, both D6: wired
directly the two drivers share one enable line and both writes land on the
same pin. They are separate defines only so that a board with per-driver
enables needs nothing but two new numbers.

**If you happen to have a RAMPS 1.4** (the Mega stepper shield — it takes
exactly these compact carrier drivers and already has six endstop connectors,
two per axis, which is the topology this firmware wants), the map becomes
X and Y: `DIR` 55/61, `STEP` 54/60, `ENA` 38/56, and endstops `X_MIN` 3,
`X_MAX` 2, `Y_MIN` 14, `Y_MAX` 15. Change the nine defines at the top of the
sketch and nothing else. Worth knowing about, not worth buying for this.

---

## Wiring the limit switches

Use the **NC (normally closed)** contact of each microswitch, COM to GND:

```
     Arduino D7 ──────────────┬──── NC ─┐
                              │         │  microswitch
                        100nF ⏚    COM ─┘
                                        │
     Arduino GND ───────────────────────┘
```

The firmware uses `INPUT_PULLUP`, so:

| | circuit | pin reads | firmware says |
|---|---|---|---|
| clear | switch closed | LOW | clear |
| tripped | switch opens | HIGH | **tripped** |
| **wire cut / unplugged** | open | HIGH | **tripped** |

A broken or unplugged switch therefore **stops the axis** instead of silently
removing the protection. This is the one wiring choice worth being fussy about,
and the reason the firmware defaults to `ENDSTOP_NORMALLY_CLOSED 1`. (If you
must use NO switches, set that to 0 — and accept that a cut wire then looks
exactly like a healthy limit.)

Practical notes:

* a **100 nF capacitor from each signal pin to GND, at the Arduino end**, is
  cheap insurance. Stepper cables are electrically noisy and limit switch runs
  are usually routed alongside them;
* keep switch wiring away from the motor leads; twisted pair or shielded if the
  run is more than ~30 cm;
* the four switches go to four separate pins, so a trip tells you *which* end
  of *which* motor. Wiring them in series into one pin would be cheaper but you
  would lose that, and you need it to know which way to drive off the switch.

**Optional hardware interlock.** You can put an axis's two NC switches in
series into the driver's ENABLE so a trip kills the driver no matter what the
firmware does. It is genuinely safer, but then you cannot drive *off* the
switch either, and recovering means pulling the interlock. Worth it only if
crashing the lens would destroy something expensive.

---

## The motor: Oriental Motor PK243M-02BA

From Oriental Motor's PK-series catalog (42 mm frame, 0.9° high-resolution table):

| | bipolar series (use this) | unipolar |
|---|---|---|
| current per phase | **0.42 A** | 0.6 A |
| resistance per phase | 20 Ω | 10 Ω |
| inductance per phase | 38.8 mH | 9.7 mH |
| holding torque | 0.2 N·m | 0.16 N·m |

It steps 0.9°, so `STEPS_PER_REV 400` is right. It has 6 lead wires and a
double shaft (`BA`; `AA` is the single-shaft version).

**It is a six-wire motor, and the A4988 drives four.** Each phase is a winding
with a centre tap. Oriental Motor's colour code is **black–yellow–green** for
one phase and **red–white–blue** for the other, with **yellow and white as the
centre taps**. For bipolar series:

| wire | goes to |
|---|---|
| black | 1A |
| green | 1B |
| red | 2A |
| blue | 2B |
| yellow | nothing: insulate the end |
| white | nothing: insulate the end, separately from yellow |

**Check with a meter before trusting the colours.** Black–green and red–blue
should each read about **20 Ω**. Black or green to yellow reads about
**10 Ω**, and so does red or blue to white. Anything between the two groups is
open. If a pair you have wired to 1A/1B reads 10 Ω, one of the two is a centre
tap. The motor still turns, on half a winding, which is why the check matters:
it is easy to miss.

**Current.** 0.42 A per phase is low for an A4988, so VREF is small:

| RS | 0.30 A (start here) | 0.42 A (rated) |
|---|---|---|
| `R050` | 0.12 V | 0.17 V |
| `R068` | 0.16 V | 0.23 V |
| `R100` | 0.24 V | 0.34 V |

Turn the pot slowly: a few degrees is tens of millivolts here. 0.30 A is
plenty. The motor holds 0.2 N·m at full current, and on a 0.5 mm lead that is
far more axial force than a lens mount needs. At 0.30 A the two windings
dissipate about 1.8 W while moving, against 3.5 W at the full rating (I²R with
20 Ω).

**Supply.** The winding is slow (38.8 mH), and a chopper driver pushes current
into it with supply voltage. Either 12 V or 24 V runs it at this firmware's
speeds: at most 500 full steps/s, or 75 r/min. Oriental Motor measures its own
bipolar torque curve for this motor at 24 V, so use 24 V if you have it.

---

## Wiring the drivers — A4988

The top-side marking **`4988ET`** is the Allegro **A4988** in the 28-pin QFN
(`A4988SETTR-T`). Every figure below marked **[DS]** is from the
[Allegro datasheet](https://www.allegromicro.com/-/media/files/datasheets/a4988-datasheet.pdf)
and was read off it, not recalled; the rest is carrier-board practice, which
the datasheet does not cover.

One carrier per motor. The 16-pin carriers (Pololu and the StepStick clones)
all use this layout:

```
          ┌─────────────┐
   ENABLE │1          16│ VMOT  ── motor supply +   (12–24 V; 8 V minimum)
      MS1 │2          15│ GND   ── motor supply −   AND Arduino GND
      MS2 │3          14│ 2B ┐
      MS3 │4          13│ 2A ┘ coil B
    RESET │5          12│ 1A ┐
    SLEEP │6          11│ 1B ┘ coil A
     STEP │7          10│ VDD   ── Arduino 5V
      DIR │8           9│ GND   ── Arduino GND
          └─────────────┘
```

### Arduino → driver

| Mega pin | Z1 driver | Z2 driver |
|---|---|---|
| D2 / D4 | **DIR** | **DIR** |
| D3 / D5 | **STEP** | **STEP** |
| D6 | **ENABLE** | **ENABLE** (same wire) |
| 5V | **VDD** | **VDD** |
| GND | **GND** (pin 9) | **GND** (pin 9) |

Three signals and two supplies per driver. That is the whole interface — the
A4988 has no feedback of any kind, which is why the limit switches are the
only thing that knows where the stage really is.

* **VDD operating range is 3–5.5 V [DS]**, so the Mega's 5 V rail is right.
  A logic high must be ≥ 0.7 × VDD = **3.5 V**, a low ≤ 1.5 V [DS]; a Mega
  output swings the full 0–5 V, so no level shifting and no pull-ups.
* **The motor supply − must be tied to Arduino GND.** STEP and DIR are
  referenced to it; without a shared ground they mean nothing.
* **1A/1B is one phase, 2A/2B the other.** For the PK243M-02BA that is
  black/green and red/blue, with yellow and white insulated (see the motor
  section above). Swapping the two wires *within* a phase reverses that axis,
  which is what `Z1_DIR_INVERT` is for. Swapping wires *between* phases makes
  the motor buzz and not turn.
* **STEP timing [DS]:** minimum 1 µs high, 1 µs low, with 200 ns setup and
  hold on DIR/MSx. AccelStepper on a 16 MHz AVR spends several microseconds
  per `digitalWrite`, so it cannot get near these limits. Nothing to tune.

### RESET and SLEEP — the pins that make a new board look dead

Both are **active low [DS]**, and the datasheet is blunt about RESET: *all
STEP inputs are ignored until RESET is set high*. A board whose RESET sits low
therefore accepts your step pulses and silently discards every one.

**The datasheet lists no internal pull-ups anywhere on this chip.** The only
internal resistors are pull-*downs*, on MS1 (100 kΩ), MS2 (50 kΩ) and MS3
(100 kΩ) [DS]. RESET, SLEEP and ENABLE have none, so they must be driven or
pulled high by the carrier or by you.

The usual fix is **a wire jumper from RESET to SLEEP**, which works only
because the carrier pulls SLEEP high. Check that yours actually does, because
the clones vary: with VDD on and nothing else connected, **measure SLEEP to
GND — it should read about 5 V.** If it reads 0 V, run a **10 kΩ from each of
RESET and SLEEP up to VDD** instead of the jumper.

Coming out of sleep the datasheet asks for **1 ms before the first STEP** [DS],
so the charge pump can settle. The firmware never uses sleep, so this only
matters if you add it later.

### ENABLE

**Logic LOW enables the outputs; logic HIGH disables them [DS].** So
`#define ENABLE_ACTIVE_LOW 1` in the sketch is correct for these — leave it
alone.

**Recommended: a 10 kΩ from the ENABLE line up to 5 V** (one resistor; both
drivers share D6). After a reset the Mega's pins float as inputs for the
second or two the bootloader runs, and ENABLE has no internal pull [DS]. A
reset happens *every time the DAQ opens the port*. Floating, the drivers may
energise while STEP is floating too, and pick up stray steps. The pull-up
holds them off until `setup()` drives D6. It is not required, but it costs
one resistor.

Worth knowing: STEP, DIR and MSx stay live regardless of ENABLE [DS]. The
translator keeps counting while the FETs are off, so the chip's own idea of
microstep position survives a disable. The firmware does not rely on that.

### Microstepping — MS1, MS2, MS3

Verbatim from the datasheet's Table 1:

| MS1 | MS2 | MS3 | resolution |
|---|---|---|---|
| L | L | L | full step |
| H | L | L | 1/2 |
| L | H | L | 1/4 |
| **H** | **H** | **L** | **1/8** ← `MICROSTEPS 8` |
| H | H | H | 1/16 |

The internal pull-downs mean **floating = full step**, so MS1 and MS2 must be
actively pulled up. For 1/8: **tie MS1 and MS2 to VDD (5 V), leave MS3 open.**
Two wires per driver.

If you change this later, change `MICROSTEPS` in the sketch to match — they
are two halves of one number and nothing checks that they agree.

> **What 1/8 does and does not buy.** It divides the *step*, not the *error*.
> The datasheet's own current-trip accuracy is **±5 % near full current but
> ±15 % at the 38.27 % microstep level [DS]**, and that error lands straight on
> the microstep position. The nominal 156 nm is a commanded increment; the
> achieved one is some multiple of it. Use microstepping here for smoothness
> and quiet, and treat the camera — not the step count — as the position
> measurement.

### Current limit — set it before the motor ever turns

From the datasheet:

```
ITripMAX = VREF / (8 × RS)        rearranged:   VREF = 8 × RS × I
```

`RS` is the pair of sense resistors on the carrier, **not** a property of the
chip — so `4988ET` identifies the IC and tells you nothing about the number you
actually need. Read it off the two small SMD resistors beside the chip:

| marking | RS | VREF per amp | for 1.0 A | for 1.5 A |
|---|---|---|---|---|
| `R050` | 0.050 Ω | 0.400 V/A | 0.40 V | 0.60 V |
| `R068` | 0.068 Ω | 0.544 V/A | 0.54 V | 0.82 V |
| `R100` | 0.100 Ω | 0.800 V/A | 0.80 V | 1.20 V |

A factor of two sits between the top and bottom rows. Guessing it is how
motors cook.

**Procedure:** motor **disconnected**, VDD on (from the Arduino), motor supply
connected. Measure DC volts between the **trimpot wiper** — or the metal pot
body, which is the same node on most carriers — and **GND**. Turn the pot until
VREF matches the table, then power down before plugging the motor in.

Set it to the motor's rated phase current *or below*. For this axis start at
**60–70 % of the rating**: a lens on a leadscrew needs very little torque, and
every watt you do not put in is a watt that does not end up as drift in an
optical mount. If it skips steps, raise it. For the PK243M-02BA (0.42 A
rated) that means starting near 0.30 A. The VREF values are in the motor
section.

### Supplies, and the two things that kill these boards

* **VBB (motor supply) operating range is 8–35 V [DS]**, 35 V absolute max.
  **A 5 V or 6 V supply will not run it** — this catches people. Use 12 V or
  24 V.
* **Output current is ±2 A absolute max [DS]**, with overcurrent protection
  tripping at ≥ 2.1 A and thermal shutdown at 165 °C [DS]. In practice a bare
  carrier handles roughly 1 A before it wants a heatsink and some air.
* **The 100 µF is polarised.** The stripe on the can marks **−**: stripe to
  GND, other leg to VMOT. Backwards, at 12–24 V, it heats and can vent.
* **A 100 µF electrolytic across VMOT/GND, physically close to the driver,
  before you first apply motor power.** The datasheet asks for a bulk capacitor
  in parallel with a ceramic one, ceramic nearest the pins [DS]; on a carrier
  fed through long leads, lead inductance can ring VMOT past the 35 V maximum
  at power-up. Not optional.
* **Never plug or unplug a motor while the driver is powered.** The datasheet
  does not discuss it; every carrier vendor warns about it, and it destroys
  A4988s reliably.

### These drivers under an optical bench

The A4988 regulates current with fixed off-time PWM in mixed decay [DS]. That
is audible, and what is audible is also mechanical. Two things already help:
the firmware de-energises the motors between moves by default
(`HOLD_WHEN_IDLE 0`), and you can run the current low. If the lens turns out to
be sensitive to the drivers *while moving*, a TMC2209 is a drop-in on the same
carrier footprint and is dramatically quieter — worth knowing, not worth
pre-empting. Measure first.

---

## Bring-up

Do this **with the motors uncoupled from the lens**. Half of it is checking
that the things meant to stop the stage actually do.

0. **Drivers, with no motor plugged in.** Read `RS` off the sense resistors
   and set VREF per the table above. Confirm the **RESET–SLEEP** jumper (or
   that SLEEP measures ~5 V). Confirm **MS1 and MS2 are tied to 5 V** and MS3
   is open, for 1/8. Fit the **100 µF across VMOT/GND**. Only then power down
   and plug the motors in.
1. **Switches before motors.** Upload, open the serial monitor at **115200**
   with the line ending set to **Newline**,
   send `status`. The `sw` field is four digits: `z1min z1max z2min z2max`.
   With nothing pressed it should read `0000`. Press each switch by hand and
   confirm the digit you expect is the one that changes. **Unplug one and
   confirm it reads `1`** — that is the fail-safe working.
2. **Direction.** `enable`, then `home`. Each motor must run **toward its MIN
   switch**. If one runs away instead you will get `ERR home wrong_direction`
   when it finds MAX; set `Z1_DIR_INVERT` or `Z2_DIR_INVERT` to 1 and
   re-upload. (If it finds nothing: `ERR home no_min_switch`.)
3. **Scale.** `home`, then `move 5`. Measure the actual travel with a dial
   indicator. If it moves twice as far as asked, `LEAD_MM_PER_REV` is wrong; if
   it moves by a neat factor of 2, 4 or 8, `MICROSTEPS` does not match how MS1,
   MS2 and MS3 are actually wired.
4. **Range.** `travel` drives each axis to its MAX switch and reports the
   usable range. Put those numbers in `DEFAULT_TRAVEL_MM` if you would rather
   not re-measure after every reset.
5. **Couple the lens.** Home again — that is what squares the mount.
6. **Check the idle policy.** `home`, `disable`, wait, look at whether the
   image moved. If it did, set `HOLD_WHEN_IDLE 1` and accept the heat.

---

## One motor for now: `Z2_FITTED`

Until Z2's driver, motor and switches are connected, the sketch is built with
`#define Z2_FITTED 0`. Everything then runs on Z1 alone. `home` and `travel`
use Z1 only, and `move <mm>` moves Z1. Z2's switch inputs, which read tripped
while unplugged, are ignored. The READY banner says `axes=1` and `status` ends
`axes 1`, so the mode is always visible. Once Z2 is wired, set `Z2_FITTED 1`
and reflash.

---

## Building and flashing

From the repo, in PowerShell:

```powershell
.\tools\flash.ps1                # build, flash, check that it answers
.\tools\flash.ps1 -CompileOnly   # build only; touches no hardware
```

It uses Arduino's `arduino-cli`, unzipped to `%USERPROFILE%\tools\arduino-cli\`
(no installer, nothing on PATH; set `ARDUINO_CLI` to use another copy). The
script finds the Mega by its USB ID (`2341:0042`), never by COM number, and
refuses unless exactly one is plugged in. This PC also has the tip-tilt piezo
controller and the K-Cube adapters on serial ports, and avrdude will talk its
bootloader protocol at whatever port it is handed. After the upload the script
resets the board, waits for `READY`, and checks `ping` and `status`.

**Motor supply off while flashing.** The Mega's pins float during the
bootloader, and the A4988 carriers enable their outputs when ENABLE floats.

Built with arduino-cli 1.6.0, `arduino:avr` 1.8.8 (avr-gcc 7.3.0, avrdude
8.0.0) and AccelStepper 1.64. On another machine:

```powershell
arduino-cli core install arduino:avr
arduino-cli lib install AccelStepper
```

---

## Serial protocol

115200 baud; a line ends at CR or LF, so any Serial Monitor setting except
"No line ending" works. Every command ends with exactly **one** line
beginning `OK` or `ERR`. Commands that start the motors emit one progress line
first (`MOVING` / `HOMING` / `MEASURING`), so read lines until `OK` or `ERR`.

| command | does |
|---|---|
| `ping` | `OK lens_z 1.3` |
| `status` | `OK STATUS pos ...`: positions, the four switches, homed/moving/motors, soft limits, and `axes 1` or `axes 2` |
| `home` | both axes to their MIN switches; sets zero. **Required before any move.** Works from anywhere, including sitting on MAX |
| `travel` | measures MIN→MAX on each axis and sets the soft limits |
| `move <mm>` | both axes to the same absolute position — the usual case |
| `move <z1> <z2>` | axes independently, for tilt or to dial out an offset |
| `jog <z1\|z2\|both> <dmm>` | relative, same limits apply |
| `stop` | immediate, obeyed in any state |
| `enable` / `disable` | energise / de-energise. See below for what `disable` does to `homed` |
| `speed <mm_per_s>` | speed for later moves; clamped to the step-rate ceiling, reports what it got |

Errors: `ERR busy`, `ERR not_homed`, `ERR soft_limit <axis> <min> <max>`,
`ERR on_limit <axis><min|max>`, `ERR limit <axis><min|max> at POS ...`,
`ERR home <reason>`, `ERR unknown_command`, `ERR line_too_long` (over 63
characters), `ERR not_fitted z2` (a two-axis `move` or a `jog z2` while only Z1 is
wired).

`ERR home` reasons:

| reason | means |
|---|---|
| `switch_fault <axis>` | both of that axis's switches read tripped **before anything moved**. Nothing moves. Almost always an unplugged cable, which NC wiring reports as tripped on purpose |
| `wrong_direction` | the axis found MAX while seeking MIN: DIR is inverted (`Zn_DIR_INVERT`), or MIN and MAX are swapped |
| `no_min_switch` | 60 mm of seeking and MIN never tripped |
| `min_stuck` | started on MIN and it was still tripped after 3 mm of crawling off: the MIN wire is cut, not pressed |

**`disable` and `homed`** follow the idle policy. With `HOLD_WHEN_IDLE 0` (the
default) the firmware already de-energises after every move and trusts the
self-locking lead screw to hold, so an explicit `disable` keeps `homed` too.
With `HOLD_WHEN_IDLE 1` you have decided the axis *can* be back-driven, so
`disable` clears `homed` and the next move needs a `home`.

---

## The Python link: `a4988`

```python
import sys; sys.path.insert(0, r"path\to\pololu-a4988")
from a4988 import LensZ
lz = LensZ("auto")        # the one Mega 2560 on USB, found by USB ID (registry; opens nothing else)
lz.open()                 # DTR held low: the board is NOT reset, homing survives
lz.status()               # {"pos": {"z1": 0.5, "z2": 0.0}, "homed": True, "moving": False, "sw": ..., "soft": ...}
lz.home(); lz.wait()      # motion commands return at once ("HOMING"); wait() polls until idle
lz.move(3.0); lz.wait()   # mm FROM THE MIN SWITCH
lz.jog("z1", -0.010)      # by 10 um
lz.stop()
lz.close()                # the board carries on; a move in progress finishes
```

**Coordinates are mm from the MIN switch**: homing zeroes each axis where MIN
trips on the slow approach, + runs toward MAX, and homing parks at 0.5 mm (the
soft minimum). Before homing the count is only "since power-up", and the board
refuses absolute moves.

**Opening the port does not reset the board.** The Mega's USB chip resets it
through DTR, so `a4988.transport` opens the port with DTR and RTS never
asserted (Win32 directly, or pyserial when installed; adapted from
thorlabs-mc2000b's transport). Checked on the rig 2026-11-24: state set before
closing (`enable`) was still there after reopening, and no `READY` arrived.
`tools/flash.ps1` still resets the board on purpose, to check new firmware.

**Outcomes arrive by themselves.** A motion command's first reply is
`MOVING` / `HOMING` / `MEASURING`; its outcome (`OK POS`, `OK HOMED`,
`OK TRAVEL`, `ERR limit ...`, `ERR home ...`) comes later as its own line,
often in the middle of a `status` exchange. `LensZ.command` sorts every line:
the reply it waits for ends the exchange, outcomes go to `lz.events` (and
`lz.on_event`). Refusals raise `a4988.Refused`, whose text says what to do
(`explain()`).

`a4988.SimBoard` is the firmware in software (homing phases, limits, travel,
stop, the same lines), on a real or a manual clock: the tests and the DAQ's
`--lens sim` use it. Tests: `python -m pytest tests` (hardware-free).

## In the DAQ

The xsphere-daq panel (`xsphere_daq/lens.py`) opens the Mega at start-up
(`--lens auto`, the default; `sim`, `off`, or a COM port), polls it, and puts
its controls on the **Tip-tilt tab**: position in mm from MIN, the switches,
arrows in µm steps, a position box, home, measure travel, stop, speed. The API
is `/lens/api` (`state`, `move {z}`, `step {axis, mm}`, `home`, `travel`,
`stop`, `speed {mm_s}`, `release`, `reconnect`); every command and outcome is
logged in `D:\xsphere-data\lens\<day>.jsonl`, and each acquisition's manifest
records the position. Connecting moves nothing; shutting down only lets the
port go.

**To reflash while the panel runs**, press **release** on the lens card first
(the port is exclusive), run `tools/flash.ps1` with the motor supply off, then
**reconnect**. The flash resets the board, so home again afterwards.
