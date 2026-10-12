"""RoboMaster TT SDK 3.0 command subset used by this app.

Every string that leaves the PC is built and checked here. Ranges follow the
official Tello SDK 3.0 User Guide (RoboMaster TT):
  up/down/left/right/forward/back x   x = 20..500 cm
  cw/ccw x                            x = 1..360 degrees
  speed x                             x = 10..100 cm/s
  takeoff / land / stop / emergency / motoron / motoroff / command
  read: battery? speed? time? wifi? sdk? sn? hardware?
"""
from dataclasses import dataclass
import re

TEST, FLIGHT = "test", "flight"
MODES = (TEST, FLIGHT)
MODE_NAMES = {TEST: "Low-speed test", FLIGHT: "Flight"}

MOVES = ("forward", "back", "left", "right", "up", "down")
TURNS = ("cw", "ccw")
READS = ("battery?", "speed?", "time?", "wifi?", "sdk?", "sn?", "hardware?")

# Commands each mode may put on the wire. "command" (SDK entry / keep-alive)
# and the read-only queries are harmless and allowed in both.
COMMON = {"command", *READS}
TEST_ONLY = {"motoron", "motoroff"}
FLIGHT_FIXED = {"takeoff", "land", "stop", "emergency"}

MOVE_RANGE = (20, 500)
TURN_RANGE = (1, 360)
SPEED_RANGE = (10, 100)

CN_MOVE = {"forward": "Forward", "back": "Back", "left": "Left", "right": "Right",
           "up": "Up", "down": "Down"}


class SDKError(RuntimeError):
    """Command refused locally or answered with error by the drone."""


class SDKTimeout(SDKError):
    """No answer in time. The command may or may not have been executed."""


def validate(command, mode):
    """Last gate before UDP. Raises SDKError if the string is not allowed."""
    if not isinstance(command, str) or command != command.strip() or not command.isascii():
        raise SDKError(f"Invalid command string: {command!r}")
    if mode not in MODES:
        raise SDKError(f"Unknown mode: {mode!r}")
    if command in COMMON:
        return
    if mode == TEST:
        if command in TEST_ONLY:
            return
        raise SDKError(f"Not allowed in low-speed test mode: {command}")
    if command in FLIGHT_FIXED:
        return
    match = re.fullmatch(r"(forward|back|left|right|up|down|cw|ccw|speed) ([1-9][0-9]{0,2})", command)
    if match:
        name, value = match[1], int(match[2])
        low, high = (TURN_RANGE if name in TURNS else SPEED_RANGE if name == "speed" else MOVE_RANGE)
        if low <= value <= high:
            return
        raise SDKError(f"{name} {value} is outside the SDK range {low}–{high}")
    raise SDKError(f"Command not enabled in flight mode: {command}")


def timeout_for(command, speed_cm_s=None):
    """Generous reply deadline. Motion replies arrive only after the motion ends."""
    name, _, arg = command.partition(" ")
    if name == "takeoff":
        return 20.0
    if name == "land":
        return 20.0
    if name in MOVES:
        speed = speed_cm_s or 10   # SDK default speed is unknown -> assume slow
        return 8.0 + 1.5 * int(arg) / speed
    if name in TURNS:
        return 8.0 + int(arg) / 30.0
    if name in {"motoron", "motoroff"}:
        return 5.0
    if command == "command":
        return 5.0
    return 4.0


def check_reply(command, reply):
    """Return the reply if it is a success for this command, else raise SDKError."""
    text = reply.strip()
    low = text.lower()
    if command == "battery?":
        if text.isdigit() and 0 <= int(text) <= 100:
            return text
        raise SDKError(f"Invalid battery? reply: {text!r}")
    if command in READS:
        if not text or low == "ok" or low.startswith(("error", "unactive")):
            raise SDKError(f"Invalid {command} reply: {text!r}")
        return text
    if low == "ok":
        return text
    if command == "command" and low == "unactive":
        raise SDKError("Drone replied unactive: activate it once with the official app first")
    raise SDKError(f"{command} rejected by the drone: {text!r}")


@dataclass(frozen=True)
class Intent:
    """One understood operator request. `sdk` is None for local-only actions."""
    action: str            # takeoff, land, stop, emergency, move, turn, speed, read, motoron, motoroff, status
    sdk: str | None
    label: str             # Chinese description for the UI
    source: str = "rules"
    heard: str = ""

    @property
    def urgent(self):
        return self.action in {"stop", "land", "emergency", "motoroff"}


def make(action, value=None, *, direction=None, query=None, source="rules", heard=""):
    """Build a validated Intent. Values are range-checked against the SDK."""
    if action == "takeoff":
        return Intent("takeoff", "takeoff", "Take off", source, heard)
    if action == "land":
        return Intent("land", "land", "Land", source, heard)
    if action == "stop":
        return Intent("stop", "stop", "Hover (stop current action)", source, heard)
    if action == "emergency":
        return Intent("emergency", "emergency", "Emergency motor stop", source, heard)
    if action == "motoron":
        return Intent("motoron", "motoron", "Motors on (low speed)", source, heard)
    if action == "motoroff":
        return Intent("motoroff", "motoroff", "Motors off", source, heard)
    if action == "status":
        return Intent("status", None, "Show status", source, heard)
    if action == "read":
        if query not in READS:
            raise SDKError(f"Unsupported query: {query!r}")
        names = {"battery?": "Battery", "speed?": "Speed", "time?": "Motor run time",
                 "wifi?": "Wi-Fi SNR", "sdk?": "SDK version", "sn?": "Serial number",
                 "hardware?": "Hardware"}
        return Intent("read", query, names[query], source, heard)
    if type(value) is not int:
        raise SDKError("A number is required")
    if action == "move":
        if direction not in MOVES:
            raise SDKError(f"Unknown direction: {direction!r}")
        low, high = MOVE_RANGE
        if not low <= value <= high:
            raise SDKError(f"Distance {value} cm is outside the SDK range {low}–{high} cm")
        return Intent("move", f"{direction} {value}", f"{CN_MOVE[direction]} {value} cm", source, heard)
    if action == "turn":
        if direction not in TURNS:
            raise SDKError(f"Unknown rotation direction: {direction!r}")
        low, high = TURN_RANGE
        if not low <= value <= high:
            raise SDKError(f"Angle {value}° is outside the SDK range {low}–{high}°")
        word = "Turn right (CW)" if direction == "cw" else "Turn left (CCW)"
        return Intent("turn", f"{direction} {value}", f"{word} {value}°", source, heard)
    if action == "speed":
        low, high = SPEED_RANGE
        if not low <= value <= high:
            raise SDKError(f"Speed {value} is outside the SDK range {low}–{high} cm/s")
        return Intent("speed", f"speed {value}", f"Set speed {value} cm/s", source, heard)
    raise SDKError(f"Unknown action: {action!r}")


# Which local intents are allowed in each mode (before any wire validation).
MODE_ACTIONS = {
    TEST: {"motoron", "motoroff", "read", "status"},
    FLIGHT: {"takeoff", "land", "stop", "emergency", "move", "turn", "speed", "read", "status"},
}
