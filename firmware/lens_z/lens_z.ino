/*
  lens_z — one lens, two stepper motors on the same axis, four limit switches.

  Z1 and Z2 both push the SAME lens along the SAME direction. That is the whole
  design constraint and it is why this is not just the x/y firmware renamed:

  * the two motors are mechanically coupled through the lens mount, so they are
    homed TOGETHER and moved TOGETHER. Homing them one after the other would
    rack the mount against itself;
  * because each one homes onto its own MIN switch, homing also SQUARES the
    mount — if the two MIN switches sit at the same physical height, the mount
    ends up level however skewed it started. That is the standard dual-Z
    levelling trick and it is free here;
  * a `move` with one number sends both to the same place (what you want
    almost always). A `move` with two numbers drives them independently, which
    is how you tilt the lens or dial out a mechanical offset.

  TWO limit switches per motor (MIN and MAX), against one in the reference
  firmware. That buys three things: the far end of travel is protected as well
  as the near end; limits are checked on EVERY move rather than only while
  homing; and `travel` can measure the usable range by driving MIN to MAX, so
  the soft limits are measured rather than guessed.

  Derived from Moore-Lab/Arduino-Stepper-Motors (06_17_2025.ino). Differences
  from it are called out in comments marked [CHANGED].

  Serial: 115200 baud, newline-terminated commands. Every command ends with
  exactly ONE line beginning OK or ERR, so a program reads lines until it sees
  one of those and never has to guess whether more is coming. Commands that
  start the motors emit one progress line first (MOVING / HOMING / MEASURING)
  so a slow operation is visibly alive. See README.md for the protocol and the
  wiring.
*/

#include <AccelStepper.h>
#include <MultiStepper.h>

#define FIRMWARE_NAME "lens_z"
#define FIRMWARE_VERSION "1.2"

// ===========================================================================
//  Pins
// ===========================================================================
// Board: Arduino Mega 2560 (ATmega2560, 16 MHz, 5 V logic).
//
// D2..D10 mean the same thing on a Mega as on an Uno, so this map is the one
// you would use on either. Two Mega-specific reasons to keep the endstops on
// D7..D10 rather than scattering them over the big 22..53 header: they sit in
// one block four pins from the GND that is next to D13, which keeps the
// pull-up returns short; and the sketch polls them inside the step loop, so
// it wants no interrupt pin. (If a later version ever did want interrupts,
// the Mega's external-interrupt pins are 2, 3, 18, 19, 20, 21 — and 18..21
// are Serial1 and I2C, so A8..A15 / PCINT2 would be the quieter choice.)
//
// D0/D1 are the USB serial port — never use them. D13 has the on-board LED and
// its resistor, which spoils INPUT_PULLUP, so no endstop goes there.
// ---------------------------------------------------------------------------
// Wiring, both channels. This is Fig 2 of docs/wiring.html as a table; the
// two must agree, so change them together.
//
//   Mega   define          goes to
//   ----   -------------   ---------------------------------------------------
//   D2     Z1_DIR_PIN      A4988 #1 (Z1) DIR
//   D3     Z1_STEP_PIN     A4988 #1 (Z1) STEP
//   D4     Z2_DIR_PIN      A4988 #2 (Z2) DIR
//   D5     Z2_STEP_PIN     A4988 #2 (Z2) STEP
//   D6     Z1_ENA_PIN      ENABLE on BOTH drivers; 10k to 5V recommended
//          Z2_ENA_PIN
//   D7     Z1_MIN_PIN      Z1 MIN limit switch, NC terminal  (COM to GND)
//   D8     Z1_MAX_PIN      Z1 MAX limit switch, NC terminal
//   D9     Z2_MIN_PIN      Z2 MIN limit switch, NC terminal
//   D10    Z2_MAX_PIN      Z2 MAX limit switch, NC terminal
//   5V     -               VDD, MS1, MS2 on both drivers  (MS3 open = 1/8 step)
//   GND    -               common ground: drivers, switch COMs, supply minus
//
//   Each A4988: RST jumpered to SLP; VMOT to +24 V with 100 uF to GND.
//   Each PK243M-02BA: BLK->1A, GRN->1B, RED->2A, BLU->2B; YEL and WHT
//   (the centre taps) insulated, connected to nothing.
// ---------------------------------------------------------------------------
#define Z1_DIR_PIN 2
#define Z1_STEP_PIN 3
#define Z2_DIR_PIN 4
#define Z2_STEP_PIN 5
// One ENABLE line per driver, so that a board with per-driver enables (a
// RAMPS 1.4, say) needs only these two numbers changed. Wired directly, both
// drivers share D6 and the two writes below land on the same pin.
#define Z1_ENA_PIN 6
#define Z2_ENA_PIN 6

#define Z1_MIN_PIN 7
#define Z1_MAX_PIN 8
#define Z2_MIN_PIN 9
#define Z2_MAX_PIN 10

// Drivers here are A4988 carriers. The datasheet is explicit: a logic LOW on
// ENABLE turns the output FETs on, a logic HIGH turns them off — so 1 is
// right and should be left alone. (STEP, DIR and MSx stay live either way;
// only the FETs are gated.) Flip to 0 only for a driver that inverts its
// enable, e.g. an opto-isolated brick wired common-anode.
#define ENABLE_ACTIVE_LOW 1

// ---------------------------------------------------------------------------
// Do the motors stay energised between moves?
//
// The reference firmware switched them off, and for an optical bench that is
// the right instinct: an idle stepper still dithers on its microstep and the
// driver still dumps a couple of watts into the stage. Both reach the lens.
//
// The cost is that an un-energised motor can be back-driven. A 0.5 mm lead
// screw is normally self-locking so the lens will not sag, but if this axis
// turns out to drift when the motors drop — or if anything pushes on the
// mount — set this to 1 and accept the heat. Check it on the real stage: home,
// disable, wait, and look at whether the image moved.
// ---------------------------------------------------------------------------
#define HOLD_WHEN_IDLE 0

// Set to 1 if an axis drives the wrong way — i.e. if `home` runs AWAY from the
// MIN switch. Check this before anything else; see the bring-up in README.md.
#define Z1_DIR_INVERT 0
#define Z2_DIR_INVERT 0

// ===========================================================================
//  Mechanics
// ===========================================================================
#define STEPS_PER_REV 400.0f     // PK243M-02BA: 0.9 deg/step. A 1.8 deg motor is 200.
#define MICROSTEPS 8             // must match the driver's DIP/MS jumpers

// [CHANGED] The reference multiplied by a bare 2 with a note that the comment
// above it was wrong. The 2 was "two turns per millimetre", i.e. a 0.5 mm
// lead. Saying it as a lead removes the puzzle and is the number stamped on
// the screw.
#define LEAD_MM_PER_REV 0.5f

const float STEPS_PER_MM = STEPS_PER_REV * MICROSTEPS / LEAD_MM_PER_REV;

// ---------------------------------------------------------------------------
// [CHANGED] Speed ceiling, and why it is not what the reference asked for.
//
// The reference set maxSpeed to SPEED_MAX * MICROSTEPS = 16000 steps/s. A
// 16 MHz AVR running AccelStepper tops out around 4000 steps/s — the library's
// own documentation says so — so that setting silently got about a quarter of
// what it asked for. The Mega 2560 does not help: it is the same 16 MHz core
// with the same digitalWrite, so the ceiling is the Uno's. What the Mega does
// buy is 8 KB of SRAM against 2 KB, which is why nothing here has to hide its
// strings behind F(). The number below is the honest ceiling, and
// everything is expressed in mm/s against it.
//
// At 400 steps/rev and a 0.5 mm lead, 4000 steps/s buys:
//
//     microsteps   steps/mm   top speed    resolution
//        1/16        12800     0.31 mm/s     78 nm
//        1/8          6400     0.63 mm/s    156 nm      <- default here
//        1/4          3200     1.25 mm/s    313 nm
//        1/2          1600     2.50 mm/s    625 nm
//        full          800     5.00 mm/s    1.25 um
//
// 1/16 gives 78 nm steps, which is far below anything the mechanics can
// actually repeat, and costs a factor of 2 in speed against 1/8. If the stage
// still feels slow, drop to 1/4 — 313 nm is still well under the positioning
// error of a leadscrew stage. Change MICROSTEPS here AND the MS1/MS2/MS3 wiring.
// ---------------------------------------------------------------------------
#define MAX_STEP_RATE_SPS 4000.0f

#define MOVE_SPEED_MM_S 0.50f
#define HOME_FAST_MM_S 0.30f
#define HOME_SLOW_MM_S 0.05f     // the pass that sets zero: slow = repeatable
#define ACCEL_MM_S2 2.0f

#define HOME_BACKOFF_MM 0.50f    // retreat between the fast and slow passes
#define HOME_CLEARANCE_MM 0.50f  // where the axis parks after homing = soft min
#define MAX_CLEARANCE_MM 0.50f   // soft max sits this far inside the MAX switch
#define HOME_SEEK_MM 60.0f       // give up if no switch within this distance
#define HOME_CLEAR_MAX_MM 3.0f   // a switch releases within ~1 mm; longer = fault
#define DEFAULT_TRAVEL_MM 25.0f  // assumed range until `travel` measures it

// ===========================================================================
//  Endstops
// ===========================================================================
// [CHANGED] The reference used the ezButton library. This is a dozen lines
// instead, for two reasons: one less library to install on whichever machine
// rebuilds this, and — the real one — ezButton::loop() calls millis() for each
// switch on every pass. Here the switches are read inside the step loop, where
// the budget is a few microseconds per step, so the debounce is done on
// micros() against a per-switch deadline and costs one digitalRead.
//
// NORMALLY CLOSED switches, wired COM->GND and NC->pin, with INPUT_PULLUP:
//
//     clear     switch closed   pin pulled to GND   reads LOW
//     tripped   switch opens    pullup wins         reads HIGH
//     wire cut  no circuit      pullup wins         reads HIGH   <- fail-safe
//
// A broken or unplugged limit switch therefore reads as TRIPPED and stops the
// axis, instead of silently removing the protection. This is the one wiring
// choice worth being fussy about. The reference's `getState() == HIGH` test
// implies the same convention; this just makes it explicit and selectable.
#define ENDSTOP_NORMALLY_CLOSED 1
#define ENDSTOP_DEBOUNCE_US 2000UL

struct Endstop {
  uint8_t pin;
  bool raw;
  bool stable;
  unsigned long changed_at;

  void begin(uint8_t p) {
    pin = p;
    pinMode(pin, INPUT_PULLUP);
    raw = stable = read_raw();
    changed_at = micros();
  }

  bool read_raw() const {
#if ENDSTOP_NORMALLY_CLOSED
    return digitalRead(pin) == HIGH;
#else
    return digitalRead(pin) == LOW;
#endif
  }

  // Call often. Unsigned subtraction makes the micros() rollover harmless.
  void poll() {
    bool r = read_raw();
    unsigned long now = micros();
    if (r != raw) {
      raw = r;
      changed_at = now;
    } else if (r != stable && (now - changed_at) >= ENDSTOP_DEBOUNCE_US) {
      stable = r;
    }
  }

  bool tripped() const { return stable; }
};

// ===========================================================================
//  Axes
// ===========================================================================
#define NAXIS 2
#define Z1 0
#define Z2 1

AccelStepper stepper1(AccelStepper::DRIVER, Z1_STEP_PIN, Z1_DIR_PIN);
AccelStepper stepper2(AccelStepper::DRIVER, Z2_STEP_PIN, Z2_DIR_PIN);
AccelStepper *axis[NAXIS] = {&stepper1, &stepper2};
MultiStepper pair;                       // coordinated moves, so the lens stays square

Endstop sw_min[NAXIS];
Endstop sw_max[NAXIS];
const char *axis_name[NAXIS] = {"z1", "z2"};

bool homed[NAXIS] = {false, false};
float travel_mm[NAXIS] = {DEFAULT_TRAVEL_MM, DEFAULT_TRAVEL_MM};
long soft_min[NAXIS];                    // steps; set by home()
long soft_max[NAXIS];

// ===========================================================================
//  State machine
// ===========================================================================
// [CHANGED] The reference homed from inside setup(), in a loop that could not
// end if a switch never closed, and that ignored the serial port throughout.
// Two consequences, and the second is the one that matters once a computer is
// driving this:
//
//   * the Mega RESETS when a host opens its serial port, exactly as an Uno
//     does — DTR pulls the reset line. Auto-homing in setup() therefore drives
//     the lens every single time the DAQ connects — including when it
//     reconnects by itself after a dropout.
//   * while homing blocked, nothing could stop it.
//
// So homing is a state machine here, nothing moves until something asks, and
// `stop` is honoured at every step.
enum Mode { M_IDLE, M_MOVING, M_HOMING, M_MEASURING };
Mode mode = M_IDLE;

enum Phase { PH_CLEAR, PH_FAST, PH_BACKOFF, PH_SLOW, PH_PARK, PH_DONE, PH_FAIL };
Phase phase[NAXIS];
long phase_start_pos[NAXIS];
const char *fail_reason = "";

bool motors_on = false;

// The speed for ordinary moves. A variable rather than the #define so that the
// `speed` command lasts: in 1.0 every move reset maxSpeed from the #define, so
// `speed` was overwritten before it could do anything.
float move_mm_s = MOVE_SPEED_MM_S;

// Was this axis already sitting on its MAX switch when homing began? Then
// MAX being tripped is where it started, not evidence that it ran the wrong
// way. Cleared as soon as the axis moves off it.
bool max_at_start[NAXIS];

// The retreat off the MAX switches at the end of `travel` is an ordinary
// coordinated move, but it belongs to the travel command and must not post an
// OK of its own -- one terminal line per command.
bool retreat_after_measure = false;

// ---------------------------------------------------------------------------
float sps(float mm_s) {
  float v = mm_s * STEPS_PER_MM;
  return v > MAX_STEP_RATE_SPS ? MAX_STEP_RATE_SPS : v;
}

long mm_to_steps(float mm) { return lround(mm * STEPS_PER_MM); }
float steps_to_mm(long s) { return (float)s / STEPS_PER_MM; }

void set_motors(bool on) {
  motors_on = on;
#if ENABLE_ACTIVE_LOW
  uint8_t level = on ? LOW : HIGH;
#else
  uint8_t level = on ? HIGH : LOW;
#endif
  digitalWrite(Z1_ENA_PIN, level);
  digitalWrite(Z2_ENA_PIN, level);
}

// Freeze both axes exactly where they are. Setting the current position to
// itself clears AccelStepper's target and speed, which is what ends a
// MultiStepper move as well — its run() returns false once nothing has
// anywhere left to go.
void halt_all() {
  for (int i = 0; i < NAXIS; i++) axis[i]->setCurrentPosition(axis[i]->currentPosition());
}

void poll_endstops() {
  for (int i = 0; i < NAXIS; i++) {
    sw_min[i].poll();
    sw_max[i].poll();
  }
}

// The switch that is in the way of going THIS direction, or 0 if the road is
// clear. Direction is taken from what the axis still has left to travel.
const char *blocking(int i, long direction) {
  if (direction < 0 && sw_min[i].tripped()) return "min";
  if (direction > 0 && sw_max[i].tripped()) return "max";
  return 0;
}

// ===========================================================================
//  setup
// ===========================================================================
void setup() {
  Serial.begin(115200);

  pinMode(Z1_ENA_PIN, OUTPUT);
  pinMode(Z2_ENA_PIN, OUTPUT);
  set_motors(false);                     // nothing moves on power-up or reset

  sw_min[Z1].begin(Z1_MIN_PIN);
  sw_max[Z1].begin(Z1_MAX_PIN);
  sw_min[Z2].begin(Z2_MIN_PIN);
  sw_max[Z2].begin(Z2_MAX_PIN);

  stepper1.setPinsInverted(Z1_DIR_INVERT, false, false);
  stepper2.setPinsInverted(Z2_DIR_INVERT, false, false);

  for (int i = 0; i < NAXIS; i++) {
    axis[i]->setMaxSpeed(sps(move_mm_s));
    axis[i]->setAcceleration(ACCEL_MM_S2 * STEPS_PER_MM);
    axis[i]->setCurrentPosition(0);
    soft_min[i] = mm_to_steps(HOME_CLEARANCE_MM);
    soft_max[i] = mm_to_steps(DEFAULT_TRAVEL_MM - MAX_CLEARANCE_MM);
  }
  pair.addStepper(stepper1);
  pair.addStepper(stepper2);

  Serial.print("READY ");
  Serial.print(FIRMWARE_NAME);
  Serial.print(" ");
  Serial.print(FIRMWARE_VERSION);
  Serial.print(" steps_per_mm=");
  Serial.print(STEPS_PER_MM, 1);
  Serial.print(" max_mm_s=");
  Serial.print(MAX_STEP_RATE_SPS / STEPS_PER_MM, 3);
  Serial.println(" not_homed");
}

// ===========================================================================
//  loop
// ===========================================================================
// Commands are gathered a byte at a time. 1.0 used readStringUntil(), which
// blocks the loop -- and with it every step and every switch read -- until the
// newline arrives, or for a full second if it never does (the Serial
// Monitor's "No line ending"). Either CR or LF ends a line here, so every
// Serial Monitor setting except "No line ending" works.
#define RX_MAX 64
char rx_buf[RX_MAX];
uint8_t rx_len = 0;
bool rx_overflow = false;

void poll_serial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rx_overflow) {
        Serial.println("ERR line_too_long");
      } else if (rx_len) {
        rx_buf[rx_len] = 0;
        String line(rx_buf);
        line.trim();
        if (line.length()) handle(line);
      }
      rx_len = 0;
      rx_overflow = false;
    } else if (rx_len < RX_MAX - 1) {
      rx_buf[rx_len++] = c;
    } else {
      rx_overflow = true;              // swallow the rest, then refuse the line
    }
  }
}

void loop() {
  poll_endstops();
  poll_serial();

  switch (mode) {
    case M_MOVING: run_move(); break;
    case M_HOMING: run_home(); break;
    case M_MEASURING: run_measure(); break;
    case M_IDLE: break;
  }
}

// ---------------------------------------------------------------------------
//  Coordinated move, with the limits live
// ---------------------------------------------------------------------------
// [CHANGED] The reference checked the switches during homing only, so a `move`
// command with a bad number drove into the hardware with nothing to stop it.
// Here every pass checks the switch that lies in each axis's direction of
// travel, and either axis tripping stops BOTH — they carry one lens between
// them, so letting the free one keep going would twist the mount.
void run_move() {
  for (int i = 0; i < NAXIS; i++) {
    const char *hit = blocking(i, axis[i]->distanceToGo());
    if (hit) {
      halt_all();
      mode = M_IDLE;
      if (!HOLD_WHEN_IDLE) set_motors(false);
      retreat_after_measure = false;
      Serial.print("ERR limit ");
      Serial.print(axis_name[i]);
      Serial.print(hit);
      Serial.print(" at ");
      report_positions();
      return;
    }
  }

  if (pair.run()) return;                // still going

  mode = M_IDLE;
  if (!HOLD_WHEN_IDLE) set_motors(false);
  if (retreat_after_measure) {
    retreat_after_measure = false;
    Serial.print("OK TRAVEL ");
    Serial.print(travel_mm[Z1], 4);
    Serial.print(" ");
    Serial.println(travel_mm[Z2], 4);
    return;
  }
  Serial.print("OK ");
  report_positions();
}

void report_positions() {
  Serial.print("POS ");
  Serial.print(steps_to_mm(stepper1.currentPosition()), 4);
  Serial.print(" ");
  Serial.println(steps_to_mm(stepper2.currentPosition()), 4);
}

// ---------------------------------------------------------------------------
//  Homing
// ---------------------------------------------------------------------------
// Both axes run their own sequence at the same time and each stops on its own
// MIN switch, so the mount finishes square against the two switches:
//
//   PH_CLEAR    already sitting on MIN -> crawl off it first
//   PH_FAST     seek MIN at speed
//   PH_BACKOFF  retreat a fixed distance
//   PH_SLOW     seek MIN again slowly; THIS trigger is zero
//   PH_PARK     move to HOME_CLEARANCE_MM, which becomes the soft minimum
//
// Zero is the MIN switch itself, not the park position, so the number means
// something physical and `travel` can measure against it. The reference zeroed
// after its back-off, which left the origin one back-off away from anything
// you could point at.
void start_home() {
  // Both switches of one axis tripped at once is not a position, it is a
  // fault: almost always an unplugged cable, which the NC wiring reports as
  // tripped on purpose. Refuse before anything moves.
  for (int i = 0; i < NAXIS; i++) {
    if (sw_min[i].tripped() && sw_max[i].tripped()) {
      Serial.print("ERR home switch_fault ");
      Serial.println(axis_name[i]);
      return;
    }
  }
  set_motors(true);
  for (int i = 0; i < NAXIS; i++) {
    max_at_start[i] = sw_max[i].tripped();
    homed[i] = false;
    phase[i] = sw_min[i].tripped() ? PH_CLEAR : PH_FAST;
    phase_start_pos[i] = axis[i]->currentPosition();
    axis[i]->setSpeed(phase[i] == PH_CLEAR ? sps(HOME_SLOW_MM_S) : -sps(HOME_FAST_MM_S));
  }
  fail_reason = "";
  mode = M_HOMING;
  Serial.println("HOMING");
}

bool seek_overrun(int i) {
  return labs(axis[i]->currentPosition() - phase_start_pos[i]) > mm_to_steps(HOME_SEEK_MM);
}

void enter_phase(int i, Phase p) {
  phase[i] = p;
  phase_start_pos[i] = axis[i]->currentPosition();
  switch (p) {
    case PH_FAST: axis[i]->setSpeed(-sps(HOME_FAST_MM_S)); break;
    case PH_SLOW: axis[i]->setSpeed(-sps(HOME_SLOW_MM_S)); break;
    case PH_BACKOFF:
      axis[i]->setMaxSpeed(sps(HOME_FAST_MM_S));
      axis[i]->moveTo(axis[i]->currentPosition() + mm_to_steps(HOME_BACKOFF_MM));
      break;
    case PH_PARK:
      axis[i]->setCurrentPosition(0);    // the slow trigger IS zero
      axis[i]->setMaxSpeed(sps(move_mm_s));
      axis[i]->moveTo(mm_to_steps(HOME_CLEARANCE_MM));
      break;
    default: break;
  }
}

void run_home() {
  bool all_done = true;

  for (int i = 0; i < NAXIS; i++) {
    switch (phase[i]) {
      case PH_CLEAR:                     // crawl off a switch we started on
        if (!sw_min[i].tripped()) { enter_phase(i, PH_FAST); break; }
        // Crawling off MIN runs toward MAX, so MAX is the switch in the way.
        if (sw_max[i].tripped()) { phase[i] = PH_FAIL; fail_reason = "switch_fault"; break; }
        // A real switch releases within a millimetre. Still "tripped" after
        // HOME_CLEAR_MAX_MM means the MIN wire is cut, not that it is pressed.
        if (labs(axis[i]->currentPosition() - phase_start_pos[i]) > mm_to_steps(HOME_CLEAR_MAX_MM)) {
          phase[i] = PH_FAIL; fail_reason = "min_stuck"; break;
        }
        axis[i]->runSpeed();
        break;

      case PH_FAST:
        if (sw_min[i].tripped()) { enter_phase(i, PH_BACKOFF); break; }
        // Reaching MAX while seeking MIN means the axis is running the wrong
        // way: the direction pin or the two switches are swapped. Unless it
        // started on MAX, in which case it is still leaving it.
        if (max_at_start[i] && !sw_max[i].tripped()) max_at_start[i] = false;
        if (sw_max[i].tripped() && !max_at_start[i]) { phase[i] = PH_FAIL; fail_reason = "wrong_direction"; break; }
        if (seek_overrun(i)) { phase[i] = PH_FAIL; fail_reason = "no_min_switch"; break; }
        axis[i]->runSpeed();
        break;

      case PH_BACKOFF:
        if (axis[i]->distanceToGo() == 0) { enter_phase(i, PH_SLOW); break; }
        axis[i]->run();
        break;

      case PH_SLOW:
        if (sw_min[i].tripped()) { enter_phase(i, PH_PARK); break; }
        if (seek_overrun(i)) { phase[i] = PH_FAIL; fail_reason = "no_min_switch"; break; }
        axis[i]->runSpeed();
        break;

      case PH_PARK:
        if (axis[i]->distanceToGo() == 0) {
          homed[i] = true;
          soft_min[i] = mm_to_steps(HOME_CLEARANCE_MM);
          soft_max[i] = mm_to_steps(travel_mm[i] - MAX_CLEARANCE_MM);
          phase[i] = PH_DONE;
          break;
        }
        axis[i]->run();
        break;

      case PH_DONE:
      case PH_FAIL:
        break;
    }
    if (phase[i] != PH_DONE && phase[i] != PH_FAIL) all_done = false;
  }

  if (!all_done) return;

  halt_all();
  mode = M_IDLE;
  bool ok = homed[Z1] && homed[Z2];
  if (!HOLD_WHEN_IDLE) set_motors(false);
  if (ok) {
    Serial.print("OK HOMED ");
    report_positions();
  } else {
    Serial.print("ERR home ");
    Serial.print(fail_reason);
    Serial.print(" ");
    report_positions();
    homed[Z1] = homed[Z2] = false;
  }
}

// ---------------------------------------------------------------------------
//  Travel measurement — what the second switch per axis is really for
// ---------------------------------------------------------------------------
// From a homed axis, crawl toward MAX until it trips. That position is the
// usable range, so the soft limits stop being a guess. Run it once after the
// stage is built, then read the numbers back with `status` and put them in
// DEFAULT_TRAVEL_MM if you want them without re-measuring.
void start_measure() {
  if (!homed[Z1] || !homed[Z2]) { Serial.println("ERR not_homed"); return; }
  set_motors(true);
  for (int i = 0; i < NAXIS; i++) {
    phase[i] = sw_max[i].tripped() ? PH_DONE : PH_SLOW;
    phase_start_pos[i] = axis[i]->currentPosition();
    // Seek at the normal move speed, not the homing crawl: this only has to
    // be good to a few microns (the stop is abrupt and may drop a step or
    // two), and HOME_SLOW_MM_S would take eight minutes over 25 mm.
    axis[i]->setSpeed(sps(move_mm_s));
    if (phase[i] == PH_DONE) {
      travel_mm[i] = steps_to_mm(axis[i]->currentPosition());
      soft_max[i] = mm_to_steps(travel_mm[i] - MAX_CLEARANCE_MM);
    }
  }
  mode = M_MEASURING;
  Serial.println("MEASURING");
}

void run_measure() {
  bool all_done = true;
  for (int i = 0; i < NAXIS; i++) {
    if (phase[i] == PH_DONE || phase[i] == PH_FAIL) continue;
    if (sw_max[i].tripped()) {
      travel_mm[i] = steps_to_mm(axis[i]->currentPosition());
      soft_max[i] = mm_to_steps(travel_mm[i] - MAX_CLEARANCE_MM);
      phase[i] = PH_DONE;
      continue;
    }
    if (seek_overrun(i)) { phase[i] = PH_FAIL; fail_reason = "no_max_switch"; continue; }
    axis[i]->runSpeed();
    all_done = false;
  }
  if (!all_done) return;

  halt_all();
  mode = M_IDLE;
  bool ok = phase[Z1] == PH_DONE && phase[Z2] == PH_DONE;
  // Retreat off the MAX switches so the axes are not left sitting on a limit.
  if (ok) {
    long t[NAXIS] = {soft_max[Z1], soft_max[Z2]};
    for (int i = 0; i < NAXIS; i++) axis[i]->setMaxSpeed(sps(move_mm_s));
    pair.moveTo(t);
    retreat_after_measure = true;
    mode = M_MOVING;   // completion posts the one OK TRAVEL line
  } else {
    if (!HOLD_WHEN_IDLE) set_motors(false);
    Serial.print("ERR measure ");
    Serial.println(fail_reason);
  }
}

// ===========================================================================
//  Commands
// ===========================================================================
String token(String &s, int n) {
  int start = 0;
  for (int i = 0; i < n; i++) {
    int sp = s.indexOf(' ', start);
    if (sp < 0) return "";
    start = sp + 1;
    while (start < (int)s.length() && s[start] == ' ') start++;
  }
  int sp = s.indexOf(' ', start);
  return sp < 0 ? s.substring(start) : s.substring(start, sp);
}

int token_count(String &s) {
  int n = 0, i = 0;
  while (i < (int)s.length()) {
    while (i < (int)s.length() && s[i] == ' ') i++;
    if (i < (int)s.length()) { n++; while (i < (int)s.length() && s[i] != ' ') i++; }
  }
  return n;
}

void send_status() {
  // Starts with OK like every other reply, so a reader can always wait for
  // OK/ERR. (1.1 answered "STATUS ...", which broke that rule.)
  Serial.print("OK STATUS pos ");
  Serial.print(steps_to_mm(stepper1.currentPosition()), 4);
  Serial.print(" ");
  Serial.print(steps_to_mm(stepper2.currentPosition()), 4);
  Serial.print(" sw ");
  Serial.print(sw_min[Z1].tripped() ? "1" : "0");
  Serial.print(sw_max[Z1].tripped() ? "1" : "0");
  Serial.print(sw_min[Z2].tripped() ? "1" : "0");
  Serial.print(sw_max[Z2].tripped() ? "1" : "0");
  Serial.print(" homed ");
  Serial.print(homed[Z1] && homed[Z2] ? "1" : "0");
  Serial.print(" moving ");
  Serial.print(mode == M_IDLE ? "0" : "1");
  Serial.print(" motors ");
  Serial.print(motors_on ? "1" : "0");
  Serial.print(" soft ");
  Serial.print(steps_to_mm(soft_min[Z1]), 3);
  Serial.print(" ");
  Serial.print(steps_to_mm(soft_max[Z1]), 3);
  Serial.print(" ");
  Serial.print(steps_to_mm(soft_min[Z2]), 3);
  Serial.print(" ");
  Serial.println(steps_to_mm(soft_max[Z2]), 3);
}

bool begin_move(float z1_mm, float z2_mm) {
  if (mode != M_IDLE) { Serial.println("ERR busy"); return false; }
  if (!homed[Z1] || !homed[Z2]) { Serial.println("ERR not_homed"); return false; }

  long t[NAXIS] = {mm_to_steps(z1_mm), mm_to_steps(z2_mm)};
  for (int i = 0; i < NAXIS; i++) {
    if (t[i] < soft_min[i] || t[i] > soft_max[i]) {
      Serial.print("ERR soft_limit ");
      Serial.print(axis_name[i]);
      Serial.print(" ");
      Serial.print(steps_to_mm(soft_min[i]), 3);
      Serial.print(" ");
      Serial.println(steps_to_mm(soft_max[i]), 3);
      return false;
    }
    // Refuse to start INTO a switch that is already tripped. Without this the
    // move would begin and stop one pass later with a limit error, which is
    // the same outcome but looks like a fault rather than a refusal.
    const char *hit = blocking(i, t[i] - axis[i]->currentPosition());
    if (hit) {
      Serial.print("ERR on_limit ");
      Serial.print(axis_name[i]);
      Serial.println(hit);
      return false;
    }
    axis[i]->setMaxSpeed(sps(move_mm_s));
  }
  set_motors(true);
  pair.moveTo(t);
  mode = M_MOVING;
  Serial.println("MOVING");
  return true;
}

void handle(String cmd) {
  String verb = token(cmd, 0);
  verb.toLowerCase();
  int n = token_count(cmd);

  if (verb == "ping") {
    Serial.print("OK ");
    Serial.print(FIRMWARE_NAME);
    Serial.print(" ");
    Serial.println(FIRMWARE_VERSION);

  } else if (verb == "status") {
    send_status();

  } else if (verb == "stop") {
    // Always obeyed, in any mode. The only command that is.
    halt_all();
    mode = M_IDLE;
    retreat_after_measure = false;
    for (int i = 0; i < NAXIS; i++) phase[i] = PH_DONE;
    if (!HOLD_WHEN_IDLE) set_motors(false);
    Serial.print("OK STOPPED ");
    report_positions();

  } else if (verb == "home") {
    if (mode != M_IDLE) { Serial.println("ERR busy"); return; }
    start_home();

  } else if (verb == "travel") {
    if (mode != M_IDLE) { Serial.println("ERR busy"); return; }
    start_measure();

  } else if (verb == "move") {
    // `move 3.0`        both axes to 3.0 mm   (the usual case)
    // `move 3.0 3.1`    independently, for tilt or to dial out an offset
    if (n == 2) {
      float z = token(cmd, 1).toFloat();
      begin_move(z, z);
    } else if (n == 3) {
      begin_move(token(cmd, 1).toFloat(), token(cmd, 2).toFloat());
    } else {
      Serial.println("ERR usage move <mm> | move <z1_mm> <z2_mm>");
    }

  } else if (verb == "jog") {
    // `jog z1 -0.05`, `jog both 0.1` — relative, same limits apply.
    if (n != 3) { Serial.println("ERR usage jog <z1|z2|both> <dmm>"); return; }
    String which = token(cmd, 1);
    which.toLowerCase();
    float d = token(cmd, 2).toFloat();
    float a = steps_to_mm(stepper1.currentPosition());
    float b = steps_to_mm(stepper2.currentPosition());
    if (which == "z1") begin_move(a + d, b);
    else if (which == "z2") begin_move(a, b + d);
    else if (which == "both") begin_move(a + d, b + d);
    else Serial.println("ERR axis");

  } else if (verb == "enable") {
    set_motors(true);
    Serial.println("OK enabled");

  } else if (verb == "disable") {
    if (mode != M_IDLE) { Serial.println("ERR busy"); return; }
    set_motors(false);
    // Consistent with the idle policy. With HOLD_WHEN_IDLE 0 the firmware
    // already de-energises after every move and keeps trusting the position
    // (the lead screw is self-locking), so an explicit disable is no different
    // and keeps homed. With HOLD_WHEN_IDLE 1 you have decided the axis CAN be
    // back-driven, so an un-energised motor means the position is no longer
    // known. (The A4988 keeps counting STEP/DIR while disabled, so re-enabling
    // does not by itself move the rotor.)
#if HOLD_WHEN_IDLE
    homed[Z1] = homed[Z2] = false;
    Serial.println("OK disabled not_homed");
#else
    Serial.println("OK disabled");
#endif

  } else if (verb == "speed") {
    if (n != 2) { Serial.println("ERR usage speed <mm_per_s>"); return; }
    float v = token(cmd, 1).toFloat();
    if (v <= 0) { Serial.println("ERR speed"); return; }
    // Takes effect from the next move; sps() clamps it to the step-rate
    // ceiling, and the reply says what was actually set.
    move_mm_s = sps(v) / STEPS_PER_MM;
    Serial.print("OK speed ");
    Serial.println(move_mm_s, 4);

  } else {
    Serial.println("ERR unknown_command");
  }
}
