"""The link against the simulated board: protocol, outcomes, refusals. Hardware-free."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a4988 import LensZ, ManualClock, Refused, SimBoard, explain, parse_event, parse_status  # noqa: E402


@pytest.fixture
def rig():
    clock = ManualClock()
    board = SimBoard(clock=clock, start_mm=(6.0, 6.0), max_at=(24.2, 24.2))
    lz = LensZ(sim=board)
    lz.open()
    return lz, board, clock


def run_until_idle(lz, clock, limit_s=200.0, step=0.5):
    t = 0.0
    while t < limit_s:
        clock.advance(step)
        t += step
        if not lz.status()["moving"]:
            return lz.events[-1]
    raise AssertionError("still moving")


def test_open_checks_firmware_and_reads_status(rig):
    lz, board, _ = rig
    assert lz.firmware == "1.3"
    assert board.received[0] == "ping"
    s = lz.last_status
    assert s["axes"] == 1 and s["homed"] is False
    assert s["sw"]["z2"] == {"min": True, "max": True}      # nothing plugged into Z2's inputs


def test_moves_refused_until_homed(rig):
    lz, _, _ = rig
    with pytest.raises(Refused) as e:
        lz.move(3.0)
    assert e.value.code == "not_homed"
    assert "home first" in str(e.value)


def test_home_zeroes_at_min_and_parks(rig):
    lz, board, clock = rig
    assert lz.home() == "HOMING" and lz.activity == "homing"
    ev = run_until_idle(lz, clock)
    assert ev["kind"] == "homed" and ev["pos"]["z1"] == pytest.approx(0.5)
    s = lz.status()
    assert s["homed"] and s["pos"]["z1"] == pytest.approx(0.5)
    # 0 is the MIN switch: the carriage really is 0.5 mm off it
    assert board.x[0] == pytest.approx(0.5, abs=0.01)
    assert lz.activity == ""


def test_move_and_outcome_arrive_through_status_polls(rig):
    lz, _, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    assert lz.move(3.25) == "MOVING"
    clock.advance(1.0)
    mid = lz.status()
    assert mid["moving"] and 0.5 < mid["pos"]["z1"] < 3.25
    ev = run_until_idle(lz, clock)
    assert ev["kind"] == "moved" and ev["pos"]["z1"] == pytest.approx(3.25)


def test_soft_limit_and_on_limit_explained(rig):
    lz, board, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    with pytest.raises(Refused) as e:
        lz.move(0.2)
    assert e.value.code == "soft_limit" and "0.500 to 24.500 mm from MIN" in str(e.value)
    board.override[("z1", "max")] = True
    with pytest.raises(Refused) as e:
        lz.jog("z1", 0.1)
    assert e.value.code == "on_limit" and "Z1 MAX" in str(e.value)


def test_limit_trip_mid_move_is_an_event(rig):
    lz, board, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    lz.move(10.0)
    clock.advance(2.0)
    lz.status()
    board.override[("z1", "max")] = True                # a switch trips under way
    ev = run_until_idle(lz, clock)
    assert ev["kind"] == "limit" and not ev["ok"]
    assert "Z1 MAX switch tripped" in ev["message"]


def test_stop_is_obeyed_mid_move(rig):
    lz, _, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    lz.move(20.0)
    clock.advance(1.0)
    assert lz.stop().startswith("OK STOPPED")
    s = lz.status()
    assert not s["moving"] and s["pos"]["z1"] < 2.0
    assert lz.events[-1]["kind"] == "stopped"


def test_travel_measures_max(rig):
    lz, _, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    assert lz.travel() == "MEASURING"
    ev = run_until_idle(lz, clock, limit_s=120)
    assert ev["kind"] == "travel" and ev["travel_mm"]["z1"] == pytest.approx(24.2, abs=0.01)
    s = lz.status()
    assert s["soft"]["z1"][1] == pytest.approx(23.7, abs=0.01)
    assert s["pos"]["z1"] == pytest.approx(23.7, abs=0.01)


def test_unfitted_z2_refused(rig):
    lz, _, clock = rig
    lz.home()
    run_until_idle(lz, clock)
    with pytest.raises(Refused) as e:
        lz.move(1.0, 2.0)
    assert e.value.code == "not_fitted"


def test_two_axes_move_independently():
    clock = ManualClock()
    lz = LensZ(sim=SimBoard(axes=2, clock=clock, start_mm=(5.0, 7.0)))
    lz.open()
    lz.home()
    run_until_idle(lz, clock)
    lz.move(2.0, 2.5)
    run_until_idle(lz, clock)
    s = lz.status()
    assert s["axes"] == 2 and s["pos"] == {"z1": pytest.approx(2.0), "z2": pytest.approx(2.5)}


def test_home_refused_on_switch_fault(rig):
    lz, board, _ = rig
    board.override[("z1", "min")] = True
    board.override[("z1", "max")] = True
    with pytest.raises(Refused) as e:
        lz.home()
    assert "both Z1 switches" in str(e.value)


def test_parsers():
    s = parse_status("OK STATUS pos 0.5000 0.0000 sw 0011 homed 1 moving 0 motors 0 "
                     "soft 0.500 24.500 0.500 24.500 axes 1")
    assert s["pos"]["z1"] == 0.5 and s["sw"]["z1"] == {"min": False, "max": False}
    assert s["soft"]["z1"] == [0.5, 24.5] and s["axes"] == 1
    # the 1.2 firmware's line, without "axes"
    assert parse_status("OK STATUS pos 0 0 sw 0000 homed 0 moving 0 motors 0 "
                        "soft 0.5 24.5 0.5 24.5")["axes"] == 2
    ev = parse_event("ERR home no_min_switch POS -60.0000 0.0000")
    assert ev["kind"] == "home_failed" and "no MIN switch" in ev["message"]
    assert explain("ERR busy").startswith("the stage is still moving")


def test_stray_bytes_and_events_between_replies(rig):
    lz, board, clock = rig
    # an outcome queued before a command is sorted, not taken as its reply
    board.println("OK POS 1.0000 0.0000")
    assert lz.command("ping") == "OK lens_z 1.3"
    assert lz.events[-1]["kind"] == "moved"
