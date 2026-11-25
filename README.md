# xsphere-lens-z

Two stepper motors (**Z1** and **Z2**) driving **one lens along one axis**, with
**two limit switches per motor**, on an **Arduino Mega 2560**. Firmware in
[`firmware/lens_z/lens_z.ino`](firmware/lens_z/lens_z.ino).

Derived from [Moore-Lab/Arduino-Stepper-Motors](https://github.com/Moore-Lab/Arduino-Stepper-Motors)
(`06_17_2025.ino`), which drove an x/y stage with one switch per axis.

> **Not yet built or run.** There is no Arduino toolchain on the rig PC, so the
> sketch has been reviewed and structurally checked but **not compiled**.
> Build it in the Arduino IDE before trusting any of it, and work through the
> bring-up below with the motors uncoupled from the lens.

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

| Arduino | goes to | note |
|---|---|---|
| D2 | Z1 driver **DIR** | |
| D3 | Z1 driver **STEP** / **PUL** | |
| D4 | Z2 driver **DIR** | |
| D5 | Z2 driver **STEP** / **PUL** | |
| D6 | **ENABLE** on *both* drivers | active LOW by default |
| D7 | Z1 **MIN** switch | |
| D8 | Z1 **MAX** switch | |
| D9 | Z2 **MIN** switch | |
| D10 | Z2 **MAX** switch | |
| GND | switch commons, driver logic GND, **motor supply −** | |

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

## Wiring the drivers

> **Which drivers are these?** "Compact" covers two families that wire very
> differently. Tell me the model and I will cut this down to the one that
> applies. Both are below in the meantime. The firmware is the same either
> way — it only emits STEP, DIR and ENABLE.

### Family A — carrier boards (A4988, DRV8825, TMC2208/2209)

The small ~15 × 20 mm boards with a trimpot. One per motor.

```
          ┌─────────────┐
   ENABLE │1          16│ VMOT  ── motor supply +  (12–24 V)
      MS1 │2          15│ GND   ── motor supply −  AND Arduino GND
      MS2 │3          14│ 2B ┐
      MS3 │4          13│ 2A ┘ coil B
    RESET │5          12│ 1A ┐
    SLEEP │6          11│ 1B ┘ coil A
     STEP │7          10│ VDD   ── Arduino 5V
      DIR │8           9│ GND   ── Arduino GND
          └─────────────┘
```

Per driver:

* **VDD → Arduino 5V**, the **GND beside VDD → Arduino GND**
* **VMOT → motor supply +**, the **GND beside VMOT → motor supply −**
* **tie the motor supply − to Arduino GND.** They must share a ground or the
  STEP/DIR signals have no reference
* **RESET and SLEEP jumpered together.** RESET floats low from the factory and
  the driver simply sits in reset if you skip this — the single most common
  reason an A4988 does nothing
* **STEP** → D3 (Z1) / D5 (Z2); **DIR** → D2 (Z1) / D4 (Z2); **ENABLE** →
  D6 on *both*
* **1A/1B is one coil, 2A/2B the other.** Find the pairs with a multimeter:
  the two wires that read a few ohms to each other are a coil. Getting a wire
  from each coil swapped just reverses that axis
* **MS1/MS2/MS3** set microstepping — table below. Must match `MICROSTEPS`

| microstep | A4988 MS1,MS2,MS3 | DRV8825 M0,M1,M2 |
|---|---|---|
| full | L L L | L L L |
| 1/2 | H L L | H L L |
| 1/4 | L H L | L H L |
| **1/8** | **H H L** | **H H L** |
| 1/16 | H H H | L L H |
| 1/32 | — | H L H |

**Two things that destroy these boards:**

1. **A 100 µF electrolytic across VMOT/GND, physically close to the driver**,
   before you apply motor power. Not optional — the inductive kick from the
   motor will otherwise take out the chip.
2. **Never plug or unplug a motor with the driver powered.** It kills A4988s
   and DRV8825s reliably.

**Set the current limit before the motor ever turns.** With the motor
disconnected and only logic power applied, measure Vref between the trimpot
wiper and GND:

* **A4988:** `Vref = I × 8 × Rcs`. Check the sense resistors on *your* board —
  0.068 Ω and 0.05 Ω are both common, and they give different answers. At
  0.068 Ω, 1.0 A wants **0.54 V**.
* **DRV8825:** `Vref = I / 2`. 1.0 A wants **0.50 V**.

Set it to the motor's rated phase current or a little under. Above ~1 A these
boards need a heatsink and some airflow.

### Family B — opto-isolated bricks (TB6600, DM542T, DM320T)

The boxed drivers with screw terminals and DIP switches. Wire **common
anode**, which is what works directly from a 5 V Arduino:

```
   Arduino 5V ──┬── PUL+
                ├── DIR+
                └── ENA+

   Arduino D3 ───── PUL−        (D5 for Z2)
   Arduino D2 ───── DIR−        (D4 for Z2)
   Arduino D6 ───── ENA−        (or leave ENA− disconnected, see below)

   motor supply + ── VCC        (typically 24 V)
   motor supply − ── GND
   motor coil A ──── A+ / A−
   motor coil B ──── B+ / B−
```

* the inputs are **opto-isolated**, so the Arduino GND does *not* need to go to
  the driver. The motor supply ground does
* **the logic is inverted** — pulling the `−` pin LOW turns the opto *on*.
  Step pulses work either way (the driver counts edges), but **ENABLE does
  not**: on most of these, driving ENA *active* **disables** the motor. So set
  `#define ENABLE_ACTIVE_LOW 0` in the sketch, or simply **leave ENA−
  disconnected**, which leaves the driver permanently enabled. For a first
  bring-up, leave it disconnected
* **current and microstepping are DIP switches**, not a trimpot — read the
  table printed on the case. No Vref to set
* these want a proper supply, usually 24 V at 2–4 A

---

## Bring-up

Do this **with the motors uncoupled from the lens**. Half of it is checking
that the things meant to stop the stage actually do.

1. **Switches before motors.** Upload, open the serial monitor at **115200**,
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
   it moves by a neat factor of 2, 4 or 8, `MICROSTEPS` does not match the
   driver jumpers.
4. **Range.** `travel` drives each axis to its MAX switch and reports the
   usable range. Put those numbers in `DEFAULT_TRAVEL_MM` if you would rather
   not re-measure after every reset.
5. **Couple the lens.** Home again — that is what squares the mount.
6. **Check the idle policy.** `home`, `disable`, wait, look at whether the
   image moved. If it did, set `HOLD_WHEN_IDLE 1` and accept the heat.

---

## Serial protocol

115200 baud, newline-terminated. Every command ends with exactly **one** line
beginning `OK` or `ERR`. Commands that start the motors emit one progress line
first (`MOVING` / `HOMING` / `MEASURING`), so read lines until `OK` or `ERR`.

| command | does |
|---|---|
| `ping` | `OK lens_z 1.0` |
| `status` | positions, the four switches, homed/moving/motors, soft limits |
| `home` | both axes to their MIN switches; sets zero. **Required before any move** |
| `travel` | measures MIN→MAX on each axis and sets the soft limits |
| `move <mm>` | both axes to the same absolute position — the usual case |
| `move <z1> <z2>` | axes independently, for tilt or to dial out an offset |
| `jog <z1\|z2\|both> <dmm>` | relative, same limits apply |
| `stop` | immediate, obeyed in any state |
| `enable` / `disable` | energise / de-energise. `disable` clears the homed flag |
| `speed <mm_per_s>` | clamped to the step-rate ceiling; reports what it got |

Errors: `ERR busy`, `ERR not_homed`, `ERR soft_limit <axis> <min> <max>`,
`ERR on_limit <axis><min|max>`, `ERR limit <axis><min|max> at POS ...`,
`ERR home <reason>`, `ERR unknown_command`.

`disable` clears `homed` on purpose: an un-energised motor can be back-driven
and nothing would notice, so the position is no longer trustworthy.

---

## Later, in the DAQ

The protocol above is deliberately the shape the other subsystems use — one
line in, one terminal line out, no unsolicited chatter — so a `LensZLink`
class mounting at `/lens` would be a thin wrapper over `pyserial` plus the
same session/lock pattern as `xsphere-rigol-dg1022z`.

Two things to carry over from the generator work when that happens:

* **opening the serial port resets the board.** The link must re-`home` after
  any reconnect, and must never assume the position survived. (If that becomes
  a nuisance, the Mega's auto-reset can be defeated with a ~10 µF capacitor
  from RESET to GND — but then the bootloader cannot be reached either, so
  uploading means pulling it off again. Re-homing is the cheaper habit;)
* **decide what a limit trip means to the DAQ** before wiring it in. On the
  generator the equivalent question — what is the safe state — turned out to be
  the whole design.
