"""Real loopback UDP integration; no hardware, microphone, model or Internet needed."""
from dataclasses import replace
import socket
import threading
import time

import pytest

from pc_demo.__main__ import accept_transcription
from pc_demo.config import Config
from pc_demo.controller import Controller
from pc_demo.network import CommandChannel, SDKError, StateChannel, parse_state
from pc_demo.parser import parse
from pc_demo.simulator import Simulator
from voice.models import TranscriptionResult


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition did not become true")


@pytest.fixture
def rig():
    sim = Simulator(Config(command_timeout_sec=0.25, flight_timeout_sec=0.5,
                           state_max_age_sec=0.15, pad_verify_timeout_sec=0.6))
    sim.start()
    state = StateChannel(sim.config)
    state.start()
    channel = CommandChannel(sim.config)
    controller = Controller(sim.config, channel, state)
    yield sim, state, channel, controller
    controller.close()
    sim.close()


def start(rig):
    sim, state, channel, controller = rig
    controller.start()
    wait_until(lambda: state.snapshot()[0].get("mid") == 1)
    return sim, state, channel, controller


@pytest.mark.parametrize("phrase,sdk", [("take off", "takeoff"), ("Take off.", "takeoff"),
    ("land!", "land"), ("hover", "stop"), ("battery?", "battery?"),
    ("move forward 30 centimeters", "forward 30"),
    ("rotate counterclockwise 90 degrees", "ccw 90")])
def test_parse_sdk(phrase, sdk):
    assert parse(phrase).sdk == sdk


@pytest.mark.parametrize("phrase", ["don't take off", "take off and land", "if ready take off",
    "land on mission pad nine", "land on mission pad 0", "land on mission pad 12",
    "land on mission pad one or two", "emergency", "forward 500 cm", "takeoff\nland"])
def test_reject_ambiguous(phrase):
    with pytest.raises(ValueError):
        parse(phrase)


@pytest.mark.parametrize("value", ["one", "1", "number one"])
def test_pad_parse(value):
    intent = parse(f"land on mission pad {value}")
    assert intent.action == "pad_land" and intent.pad_id == 1


def test_handshake_blocks_all_commands(rig):
    sim, state, channel, controller = rig
    with pytest.raises(SDKError, match="handshake"):
        channel.send("takeoff")
    assert sim.history == []
    sim.response_overrides["command"] = "error"
    with pytest.raises(SDKError):
        controller.start()
    assert sim.history == ["command"]
    assert not channel.ready


def test_full_pad_landing_over_real_udp(rig):
    sim, state, channel, controller = start(rig)
    assert sim.history[:3] == ["command", "mon", "mdirection 0"]
    assert state.thread.is_alive()
    assert state.socket is not channel.socket
    controller.execute(parse("take off"))
    controller.execute(parse("land on mission pad one"))
    assert sim.history[-3:] == ["takeoff", "go 0 0 60 20 m1", "land"]
    assert controller.flight_state == "grounded"


def test_state_continues_while_command_waits_and_timeout_poisons(rig):
    sim, state, channel, controller = start(rig)
    before = state.snapshot()[2]
    sim.response_overrides["battery?"] = None
    with pytest.raises(TimeoutError):
        channel.send("battery?")
    assert state.snapshot()[2] > before + 1
    assert channel.poisoned
    # Late ACK must never authorize another flight command.
    sim.socket.sendto(b"ok", channel.socket.getsockname())
    with pytest.raises(SDKError, match="uncertain"):
        channel.send("takeoff")
    assert "takeoff" not in sim.history


def test_error_response_does_not_become_success(rig):
    sim, state, channel, controller = start(rig)
    sim.response_overrides["stop"] = "error motor stop"
    with pytest.raises(SDKError, match="error motor stop"):
        controller.execute(parse("stop"))
    assert not channel.poisoned
    assert channel.send("battery?") == "85"


def test_dry_run_blocks_at_execution_boundary(rig):
    sim, state, channel, controller = start(rig)
    controller.dry_run = True
    for phrase in ("take off", "land", "stop", "land on mission pad one", "forward 30 cm"):
        with pytest.raises(SDKError, match="dry-run"):
            controller.execute(parse(phrase))
    controller.execute(parse("battery"))
    assert sim.history == ["command", "mon", "mdirection 0", "battery?"]


def test_wrong_pad_no_go_or_land(rig):
    sim, state, channel, controller = start(rig)
    controller.execute(parse("take off"))
    with pytest.raises(SDKError, match="not currently visible"):
        controller.execute(parse("land on mission pad two"))
    assert "land" not in sim.history
    assert not any(x.startswith("go ") for x in sim.history)


def test_stale_state_blocks_takeoff(rig):
    sim, state, channel, controller = start(rig)
    sim.emit_state = False
    time.sleep(0.2)
    with pytest.raises(SDKError, match="stale"):
        controller.execute(parse("take off"))
    assert "takeoff" not in sim.history


def test_go_error_does_not_land(rig):
    sim, state, channel, controller = start(rig)
    controller.execute(parse("take off"))
    sim.response_overrides["go 0 0 60 20 m1"] = "error"
    with pytest.raises(SDKError):
        controller.execute(parse("land on mission pad one"))
    assert "land" not in sim.history


def test_pad_lost_after_go_does_not_land(rig):
    sim, state, channel, controller = start(rig)
    controller.execute(parse("take off"))
    original_send = channel.send

    def lose_pad(command, **kwargs):
        response = original_send(command, **kwargs)
        if command.startswith("go "):
            sim.mid = -1
        return response

    channel.send = lose_pad
    with pytest.raises(SDKError, match="not currently visible"):
        controller.execute(parse("land on mission pad one"))
    assert "land" not in sim.history


def test_no_repeated_counting_of_cached_alignment(rig):
    sim, state, channel, controller = start(rig)
    controller.execute(parse("take off"))
    original_send = channel.send

    def freeze_after_go(command, **kwargs):
        response = original_send(command, **kwargs)
        if command.startswith("go "):
            sim.emit_state = False
        return response

    channel.send = freeze_after_go
    with pytest.raises(SDKError, match="stale"):
        controller.execute(parse("land on mission pad one"))
    assert "land" not in sim.history


def test_foreign_response_ignored(rig):
    sim, state, channel, controller = start(rig)
    sim.response_overrides["battery?"] = None
    foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    timer = threading.Timer(0.05, lambda: foreign.sendto(b"85", channel.socket.getsockname()))
    timer.start()
    try:
        with pytest.raises(TimeoutError):
            channel.send("battery?")
    finally:
        timer.join()
        foreign.close()


def test_state_parser_invalid_and_missing_fields():
    assert parse_state(b"mid:-1;x:-100;y:-100;z:-100;mpry:0,0,0;bat:85;garbage;baro:1.23;")["mid"] == -1
    assert parse_state(b"x:nan;y:inf;z:no;bat:85;mpry:0,nan,0;") == {"bat": 85}


def test_partial_frame_does_not_retain_pad(rig):
    sim, state, channel, controller = start(rig)
    sim.emit_state = False
    time.sleep(0.05)
    sim.socket.sendto(b"bat:80;", state.socket.getsockname())
    wait_until(lambda: state.snapshot()[0] == {"bat": 80})
    assert "mid" not in state.fresh()


def test_low_battery_blocks_takeoff(rig):
    sim, state, channel, controller = start(rig)
    sim.emit_state = False
    time.sleep(0.05)
    sim.socket.sendto(b"bat:10;h:0;", state.socket.getsockname())
    wait_until(lambda: state.snapshot()[0].get("bat") == 10)
    with pytest.raises(SDKError, match="battery"):
        controller.execute(parse("take off"))
    assert "takeoff" not in sim.history


def test_asr_confidence():
    good = TranscriptionResult("take off", "en", 1, -0.3, 0.1, 1)
    assert accept_transcription(good, Config())
    for changes in ({"text": ""}, {"language": "zh"}, {"average_log_probability": -2},
                    {"average_log_probability": float("nan")}, {"no_speech_probability": 0.9}):
        assert not accept_transcription(replace(good, **changes), Config())


def test_config_rejects_channel_collision_and_bad_pad_parameters():
    for changes in ({"state_port": 8889}, {"pad_height_cm": 0}, {"pad_speed_cm_s": 200},
                    {"state_max_age_sec": float("nan")}, {"keepalive_sec": 20}):
        with pytest.raises(ValueError):
            Config(**changes)


def test_idle_keepalive_is_sent_on_command_channel(rig):
    sim, state, channel, controller = start(rig)
    with channel.lock:
        channel.last_send = time.monotonic() - 6
    wait_until(lambda: sim.history.count("command") >= 2)
    assert state.thread.is_alive()


def test_offline_model_options_forwarded(tmp_path):
    from voice.asr_backend import FasterWhisperASR
    calls = []

    def factory(name, **options):
        calls.append((name, options))
        return object()

    FasterWhisperASR(model_name="small.en", language="en", local_files_only=True,
                    download_root=str(tmp_path), model_factory=factory).load()
    assert calls == [("small.en", {"device": "cpu", "compute_type": "int8",
                                  "local_files_only": True, "download_root": str(tmp_path)})]


def test_voice_to_udp_entrypoint_with_fake_audio(monkeypatch, tmp_path):
    from pc_demo.__main__ import main
    from voice.asr_backend import FasterWhisperASR
    from voice.microphone import MicrophoneRecorder
    phrases = iter(["take off", "land on mission pad one"])
    inputs = iter(["", "", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(FasterWhisperASR, "load", lambda self: None)
    monkeypatch.setattr(MicrophoneRecorder, "record", lambda self, on_ready: object())
    monkeypatch.setattr(FasterWhisperASR, "transcribe", lambda self, audio:
                        TranscriptionResult(next(phrases), "en", 1, -0.2, 0.1, 1))
    log = tmp_path / "voice.log"
    assert main(["--simulate", "--voice", "--log-file", str(log)]) == 0
    content = log.read_text(encoding="utf-8")
    assert "ASR text='take off'" in content
    assert "DONE pad landing acknowledged" in content


@pytest.mark.parametrize("command", ["land m1", "landing", "take off", "go 0 0 60 20 m9",
    "go 0 0 0 20 m1", "go 0 0 60 200 m1", "takeoff\nland", "emergency", "left -20", "left 101"])
def test_udp_boundary_rejects_invalid_sdk_before_send(rig, command):
    sim, state, channel, controller = rig
    with pytest.raises(SDKError):
        channel.send(command)
    assert sim.history == []


@pytest.mark.parametrize("pad_id", range(1, 9))
def test_each_pad_id_reaches_correct_sdk_sequence(rig, pad_id):
    sim, state, channel, controller = start(rig)
    sim.mid = pad_id
    wait_until(lambda: state.snapshot()[0].get("mid") == pad_id)
    controller.execute(parse("take off"))
    controller.execute(parse(f"landing on mission pad {pad_id}"))
    assert sim.history[-2:] == [f"go 0 0 60 20 m{pad_id}", "land"]
