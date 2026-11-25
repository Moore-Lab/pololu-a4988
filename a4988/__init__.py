"""pololu-a4988: the lens Z stage of the xsphere rig, from Python.

Two PK243M-02BA steppers on Pololu A4988 carriers, driven by an Arduino Mega
2560 running firmware/lens_z, each moving one Newport 423 stage (SM micrometer,
0.5 mm per turn) that carries the lens. This package talks to that firmware:

    LensZ      the link: open without resetting the board, status, move, jog,
               home, travel, stop (positions in mm from the MIN switch)
    SimBoard   the firmware in software, for tests and the DAQ's --lens sim
    find_megas the Mega(s) on USB, by USB ID, never by COM number
"""

from .link import (AXES, FIRMWARE, MAX_MM_S, STEPS_PER_MM, LensZ, LensZError, NoReply,
                   Refused, explain, parse_event, parse_status)
from .ports import MEGA_IDS, find_mega_port, find_megas
from .sim import ManualClock, SimBoard
from .transport import TransportError

__all__ = ["AXES", "FIRMWARE", "MAX_MM_S", "STEPS_PER_MM", "LensZ", "LensZError", "NoReply",
           "Refused", "explain", "parse_event", "parse_status", "MEGA_IDS", "find_mega_port",
           "find_megas", "ManualClock", "SimBoard", "TransportError"]
