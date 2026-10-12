"""Tests for tello_app (voice console). Run from Latest/: python -m pytest tests -q"""
from dataclasses import replace
import re
import time

from tello_app import sdk
from tello_app.config import AppConfig
from tello_app.controller import Controller
from tello_app.llm import QwenInterpreter
from tello_app.parser import NotUnderstood, parse
from tello_app.sdk import FLIGHT, TEST, SDKError, make, validate

CONFIG = AppConfig()


def raises(fn, kind=SDKError):
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"{fn} did not raise {kind.__name__}")


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError("condition not reached")


# ------------------------------------------------------------------ SDK layer
def test_sdk_ranges_follow_tello_sdk_3():
    for ok in ("forward 20", "back 500", "up 100", "cw 1", "ccw 360", "speed 10", "speed 100",
               "takeoff", "land", "stop", "emergency", "command", "battery?", "sdk?"):
        validate(ok, FLIGHT)
    for bad in ("forward 19", "forward 501", "forward 050", "cw 0", "cw 361", "speed 9", "speed 101",
                "rc 0 0 0 0", "flip f", "go 50 0 0 30", "motoron", "forward 30 ", "reboot", "wifi a b"):
        raises(lambda b=bad: validate(b, FLIGHT))


def test_test_mode_only_allows_motor_and_reads():
    for ok in ("motoron", "motoroff", "command", "battery?", "wifi?", "time?"):
        validate(ok, TEST)
    for bad in ("takeoff", "land", "forward 50", "cw 90", "emergency", "speed 30"):
        raises(lambda b=bad: validate(b, TEST))


def test_reply_checking():
    assert sdk.check_reply("takeoff", "ok") == "ok"
    raises(lambda: sdk.check_reply("takeoff", "error"))
    raises(lambda: sdk.check_reply("battery?", "ok"))       # late ok is not a battery value
    assert sdk.check_reply("battery?", "87") == "87"


# ---------------------------------------------------------------- parser
def test_flight_phrases():
    cases = {"Take off.": "takeoff", "Land.": "land", "Hover.": "stop", "Stop!": "stop",
             "Move forward 50 centimeters.": "forward 50", "forward fifty": "forward 50",
             "Go up one meter": "up 100", "Fly back 1.5 meters": "back 150", "left 30cm": "left 30",
             "Go down twenty five centimeters": "down 25", "Turn left 90 degrees.": "ccw 90",
             "Rotate clockwise 45°": "cw 45", "turn right": "cw 90", "Turn around.": "cw 180",
             "90 degrees to the right": "cw 90", "Set speed to 50": "speed 50", "Battery?": "battery?",
             "turn left one hundred twenty degrees": "ccw 120"}
    for text, want in cases.items():
        assert parse(text, FLIGHT, CONFIG).sdk == want, text


def test_test_mode_phrases():
    assert parse("Power on.", TEST, CONFIG).sdk == "motoron"
    assert parse("Power off.", TEST, CONFIG).sdk == "motoroff"
    assert parse("stop", TEST, CONFIG).sdk == "motoroff"
    assert "Low-speed test" in str(raises(lambda: parse("Take off", TEST, CONFIG)))
    raises(lambda: parse("Power on", FLIGHT, CONFIG))


def test_parser_rejections():
    for text in ("Don't take off", "Take off and land", "Forward 600", "Forward 10", "Move left.",
                 "Motor test commands, power on, motor off, stop, battery, cancel, exit."):
        raises(lambda t=text: parse(t, FLIGHT, CONFIG))
    raises(lambda: parse("Hello.", FLIGHT, CONFIG), NotUnderstood)


# ------------------------------------------------------------------- Qwen
def qwen(reply):
    return QwenInterpreter(request=lambda text, mode: reply)


def test_qwen_needs_anchor_words():
    # The old bug: "Hello." was turned into power off. Now refused.
    raises(lambda: qwen({"action": "motoroff", "direction": None, "value": None, "reason": "x"})
           .interpret("Hello.", TEST, CONFIG))
    raises(lambda: qwen({"action": "takeoff", "direction": None, "value": None, "reason": "x"})
           .interpret("Thank you very much.", FLIGHT, CONFIG))
    ok = qwen({"action": "move", "direction": "forward", "value": 50, "reason": "x"}).interpret(
        "move for word fifty", FLIGHT, CONFIG)
    assert ok.sdk == "forward 50" and ok.source == "Qwen"


def test_qwen_cannot_invent_numbers_or_flip_direction():
    raises(lambda: qwen({"action": "move", "direction": "left", "value": 50, "reason": "x"})
           .interpret("go forward fifty", FLIGHT, CONFIG))
    raises(lambda: qwen({"action": "move", "direction": "forward", "value": 200, "reason": "x"})
           .interpret("go forward a bit", FLIGHT, CONFIG))
    raises(lambda: qwen({"action": "motoron", "direction": None, "value": None, "reason": "x"})
           .interpret("power of", TEST, CONFIG))
    raises(lambda: qwen({"action": "takeoff", "direction": None, "value": None, "reason": "x"})
           .interpret("take of", TEST, CONFIG))           # not allowed in test mode
    raises(lambda: qwen({"action": "reject", "direction": None, "value": None, "reason": "chat"})
           .interpret("whatever", FLIGHT, CONFIG))
    raises(lambda: qwen({"action": "land", "direction": None, "value": None, "reason": "x", "sdk": "emergency"})
           .interpret("land", FLIGHT, CONFIG))


# -------------------------------------------------------------- controller
class Rig:
    def __init__(self, **cfg):
        self.logs = []
        self.c = Controller(replace(CONFIG, **cfg), log=lambda k, t: self.logs.append((k, t)), sim_scale=0.1)
        self.c.connect(simulate=True)
        wait_for(lambda: self.c.connected)
        self.sim = self.c.sim

    def run(self, text):
        self.c.submit(parse(text, self.c.mode, self.c.config))
        time.sleep(0.05)
        wait_for(lambda: not self.c.busy and not self.c.pending, 10)

    def close(self):
        self.c.close()
        self.c.closed.wait(10)


def test_full_flight_sequence_matches_sdk():
    rig = Rig()
    try:
        raises(lambda: rig.run("take off"))                 # test mode is the default
        rig.c.set_mode(FLIGHT)
        for text in ("take off", "move forward 50 centimeters", "turn left 90 degrees", "go up 30", "land"):
            rig.run(text)
        sent = [h for h in rig.sim.history if not h.endswith("?")]
        assert sent == ["command", "speed 30", "takeoff", "forward 50", "ccw 90", "up 30", "land"]
        assert not rig.c.airborne and not rig.sim.flying
    finally:
        rig.close()


def test_motor_test_mode():
    rig = Rig()
    try:
        rig.run("power on")
        assert rig.sim.motors_on and rig.c.motors_on
        raises(lambda: rig.c.set_mode(FLIGHT))              # cannot switch while spinning
        rig.run("power off")
        assert not rig.sim.motors_on
        assert "takeoff" not in rig.sim.history
    finally:
        rig.close()


def test_stop_interrupts_motion_without_claiming_success():
    rig = Rig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        rig.c.submit(parse("forward 200", FLIGHT, rig.c.config))
        wait_for(lambda: rig.c.busy == "forward 200")
        raises(lambda: rig.c.submit(parse("turn left 90", FLIGHT, rig.c.config)))   # no queueing
        rig.c.submit(parse("hover", FLIGHT, rig.c.config))
        wait_for(lambda: not rig.c.busy)
        assert "interrupted forward 200" in rig.sim.events
        assert not any("Done: Forward 200" in t for _, t in rig.logs)
        rig.run("turn right 45")                            # resyncs, then works
        assert rig.sim.history[-1] == "cw 45" and rig.sim.history[-2] == "battery?"
    finally:
        rig.close()


def test_mode_switch_blocked_in_air_and_close_lands():
    rig = Rig()
    rig.c.set_mode(FLIGHT)
    rig.run("take off")
    raises(lambda: rig.c.set_mode(TEST))
    rig.close()
    assert rig.sim.history[-1] == "land" and not rig.sim.flying


def test_keepalive_prevents_sdk_auto_land():
    rig = Rig(keepalive_sec=1.0)
    try:
        rig.sim.autoland_sec = 2.5
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        time.sleep(4)
        assert rig.sim.flying and "AUTO-LAND 15s" not in rig.sim.events
    finally:
        rig.close()


def test_low_battery_auto_lands_and_blocks_takeoff():
    rig = Rig(keepalive_sec=1.0)
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        rig.sim.battery = 12
        wait_for(lambda: not rig.sim.flying, 6)
        assert not rig.c.airborne
        rig.run("take off")
        assert rig.sim.history.count("takeoff") == 1
    finally:
        rig.close()


def test_emergency_and_takeoff_timeout():
    rig = Rig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        rig.c.submit(make("emergency"))
        assert not rig.c.airborne
        wait_for(lambda: "EMERGENCY" in rig.sim.events)
        rig.sim.overrides["takeoff"] = None                 # drone stays silent
        rig.c.config = replace(rig.c.config)
        import tello_app.controller as ctl
        original = ctl.timeout_for
        ctl.timeout_for = lambda cmd, speed=None: 0.5 if cmd == "takeoff" else original(cmd, speed)
        try:
            rig.run("take off")
        finally:
            ctl.timeout_for = original
        assert rig.c.airborne                               # unknown -> assume flying, land allowed
        rig.run("land")
        assert rig.sim.history[-1] == "land"
    finally:
        rig.close()


# ------------------------------------------------------------ packaging (V3)
def test_speech_package_imports_without_heavy_deps():
    from tello_app.speech.asr import FasterWhisperASR
    from tello_app.speech.microphone import MicrophoneRecorder, VoiceInputError
    from tello_app.speech import SpeechError
    assert issubclass(VoiceInputError, SpeechError)
    assert FasterWhisperASR and MicrophoneRecorder


def test_whisper_dir_lookup(tmp_path=None):
    import tempfile
    from pathlib import Path
    from tello_app.voice import find_whisper_dir
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "whisper"
        raises(lambda: find_whisper_dir(replace(CONFIG, whisper_dir=str(folder), asr_model="no-such-model")), RuntimeError)
        (folder / "models--Systran--faster-whisper-small.en").mkdir(parents=True)
        assert find_whisper_dir(replace(CONFIG, whisper_dir=str(folder))) == (folder, True)
        empty = Path(tmp) / "empty"
        found, local = find_whisper_dir(replace(CONFIG, whisper_dir=str(empty), asr_model="no-such-model",
                                                asr_allow_download=True))
        assert found == empty and local is False


# ------------------------------------------------------- ESP32 guard route
from tello_app.guard import AvoidRejected, friendly_reason, parse_tof
from tello_app.guard_sim import Policy


class GuardRig(Rig):
    def __init__(self, **cfg):
        cfg.setdefault("route", "guard")
        super().__init__(**cfg)
        self.tt = self.sim.tt

    def wait_tof(self):
        wait_for(lambda: self.c.snapshot()["tof"].get("air") is not None)


def test_guard_route_prechecks_moves_against_walls():
    rig = GuardRig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        rig.run("forward 150")                         # 1.5 m + 0.58 m braking > 2 m sensor range
        assert any("max 141 cm" in t for _, t in rig.logs)
        rig.run("forward 100")
        rig.run("forward 100")                         # now 0.9 m from the wall
        rig.run("forward 40")                          # needs 0.4 + 0.58 m
        # the last 50 ms sample may still be a few mm behind the final position
        assert any(re.search(r"obstacle front at 0\.9[0-4] m \(needs > 0\.98 m\)", t) for _, t in rig.logs)
        assert not any("AVOID_STOP" in e for e in rig.sim.events)      # admitted moves are never cut short
        assert rig.tt.history.count("forward 40") == 0 and rig.tt.history.count("forward 100") == 2
        rig.c.set_avoidance(False)
        wait_for(lambda: rig.c.snapshot()["avoid"] is False)
        rig.run("forward 40")                          # OFF: relayed without checks
        assert rig.tt.history.count("forward 40") == 1
    finally:
        rig.close()


def test_guard_stops_a_move_when_an_obstacle_appears():
    rig = GuardRig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        rig.c.submit(parse("forward 100", FLIGHT, rig.c.config))
        wait_for(lambda: rig.c.busy == "forward 100")
        time.sleep(0.05)
        rig.sim.inject[0] = 450                        # someone walks in front of the drone
        wait_for(lambda: not rig.c.busy, 10)
        assert any(e.startswith("AVOID_STOP obstacle-front") for e in rig.sim.events)
        assert "interrupted forward 100" in rig.tt.events
        assert any("interrupted (obstacle front" in t for k, t in rig.logs if k == "warn")
        assert not any("Done: Forward 100" in t for _, t in rig.logs)
        rig.sim.inject[0] = 200                        # even closer while hovering -> back off
        wait_for(lambda: "back 20" in rig.tt.history, 5)
        assert any(e.startswith("AVOID_RETREAT") for e in rig.sim.events)
        rig.sim.inject.clear()
        time.sleep(0.4)
        rig.run("turn left 90")                        # guard re-aligns, then relays
        assert rig.tt.history[-1] == "ccw 90"
    finally:
        rig.close()


def test_guard_rejects_takeoff_when_a_sensor_is_dead():
    rig = GuardRig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.sim.blocked_sensor = 2
        time.sleep(0.5)
        rig.run("take off")
        assert "takeoff" not in rig.tt.history
        assert any("left sensor has no valid reading" in t for _, t in rig.logs)
        rig.sim.blocked_sensor = None
        time.sleep(0.4)
        rig.run("take off")
        assert rig.tt.flying
    finally:
        rig.close()


def test_guard_motor_test_mode_and_avoidance_needs_guard():
    rig = GuardRig()
    try:
        rig.run("power on")
        assert rig.tt.motors_on
        rig.run("power off")
        assert not rig.tt.motors_on
    finally:
        rig.close()
    direct = Rig()
    try:
        raises(lambda: direct.c.set_avoidance(True))
    finally:
        direct.close()


def test_guard_protocol_helpers_and_python_policy():
    assert "obstacle front at 0.65 m" in friendly_reason("avoid:obstacle-front-650mm-need-900mm")
    tof = parse_tof("TOF f=812 b=1500 l=2000 r=640 ok=1101 cf=120 cb=0 cl=0 cr=-30 avoid=1 air=0 busy=0 rs=0 bat=85")
    assert tof["f"] == 812 and tof["fresh"] == [True, True, False, True] and tof["avoid"] == 1
    p = Policy()                                   # same numbers as firmware host tests
    for i in range(4):
        p.update(i, 1500, 1.0)
    assert p.admit("forward 100", 1.0) is None and "obstacle-front" in p.admit("forward 110", 1.0)
    assert "obstacle-front" in p.admit("forward 100", 1.0, speed=30)
    p2 = Policy()
    for i in range(4):
        p2.update(i, 5000, 1.0)
    assert p2.admit("back 160", 1.0) == "beyond-sensor-range-max-153cm"     # same as firmware
    d, now, action = 1500, 1.0, None
    while d > 300 and action is None:
        now += 0.05
        d -= 50
        for i in range(4):
            p.update(i, d if i == 0 else 1500, now)
        action, _, _ = p.monitor("forward 100", now, 0)
    assert action == "stop" and d == 950                # identical to firmware test_policy.cpp


def test_guard_keepalive_never_blocks_operator_commands():
    rig = GuardRig()
    try:
        rig.c.set_mode(FLIGHT)
        rig.run("take off")
        sim = rig.sim
        # Hold the guard's own keep-alive "in flight", then send an operator command.
        sim.tt.overrides["command"] = None
        sim.last_control_tx = 0
        wait_for(lambda: sim.pending and sim.pending["cmd"] == "command")
        rig.c.submit(parse("up 30", FLIGHT, rig.c.config))
        time.sleep(0.3)
        assert "up 30" not in sim.tt.history
        del sim.tt.overrides["command"]
        sim.tt.socket.sendto(b"ok", sim.uart.getsockname())          # the late keep-alive reply
        wait_for(lambda: not rig.c.busy, 10)
        assert "up 30" in sim.tt.history
        assert not any("busy" in t for _, t in rig.logs)
    finally:
        rig.close()


def test_flat_model_dir_preferred():
    import tempfile
    from pathlib import Path
    from tello_app.voice import resolve_model
    with tempfile.TemporaryDirectory() as tmp:
        flat = Path(tmp) / "faster-whisper-small.en"
        flat.mkdir()
        (flat / "model.bin").write_bytes(b"x")
        assert resolve_model(replace(CONFIG, whisper_dir=tmp)) == (str(flat), None, True)
