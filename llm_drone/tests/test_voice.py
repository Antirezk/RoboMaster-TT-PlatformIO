from types import SimpleNamespace

import numpy as np
import pytest

from main import parse_audio_device
from models.task import Task, TaskType
from voice.asr_backend import FasterWhisperASR
from voice.microphone import MicrophoneRecorder, list_input_devices
from voice.models import TranscriptionResult
from voice.voice_command_loop import VoiceCommandLoop


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, *args):
        self.messages.append(("info", args))

    def warning(self, *args):
        self.messages.append(("warning", args))

    def error(self, *args):
        self.messages.append(("error", args))


class FakeSystem:
    def __init__(self, task):
        self.task = task
        self.parsed = []
        self.executed = []
        self.logger = FakeLogger()

    def parse_command(self, text):
        self.parsed.append(text)
        return self.task

    def execute_task(self, task):
        self.executed.append(task)


class FakeRecorder:
    def record(self, on_ready=None):
        if on_ready:
            on_ready(0.02)
        return np.zeros(1600, dtype=np.float32)


class FakeASR:
    def __init__(self, result):
        self.result = result
        self.loaded = False

    def load(self):
        self.loaded = True

    def transcribe(self, audio):
        assert audio.dtype == np.float32
        return self.result


def result(text="查看电量", language_probability=0.99,
           average_log_probability=-0.1, no_speech_probability=0.01):
    return TranscriptionResult(
        text=text, language="zh", language_probability=language_probability,
        average_log_probability=average_log_probability,
        no_speech_probability=no_speech_probability, duration_sec=1.2,
    )


def test_read_only_voice_task_executes_without_confirmation():
    system = FakeSystem(Task(TaskType.BATTERY, raw_command="查看电量"))
    loop = VoiceCommandLoop(
        system, FakeRecorder(), FakeASR(result()),
        input_fn=lambda _: pytest.fail("confirmation must not be requested"),
        output_fn=lambda _: None,
    )
    task = loop.handle_once()
    assert task.type == TaskType.BATTERY
    assert system.parsed == ["查看电量"]
    assert system.executed == [task]


@pytest.mark.parametrize("answer", ["", "确认", "NO"])
def test_takeoff_requires_exact_keyboard_yes(answer):
    system = FakeSystem(Task(TaskType.TAKEOFF, raw_command="起飞"))
    loop = VoiceCommandLoop(
        system, FakeRecorder(), FakeASR(result("起飞")),
        input_fn=lambda _: answer,
        output_fn=lambda _: None,
    )
    assert loop.handle_once() is None
    assert system.executed == []


def test_takeoff_executes_after_keyboard_yes():
    task = Task(TaskType.TAKEOFF, raw_command="起飞")
    system = FakeSystem(task)
    loop = VoiceCommandLoop(
        system, FakeRecorder(), FakeASR(result("起飞")),
        input_fn=lambda _: "YES", output_fn=lambda _: None,
    )
    assert loop.handle_once() == task
    assert system.executed == [task]


@pytest.mark.parametrize("bad_result", [
    result(language_probability=0.2),
    result(average_log_probability=-2.0),
    result(no_speech_probability=0.9),
    result(text=""),
])
def test_low_confidence_voice_is_rejected_before_task_parser(bad_result):
    system = FakeSystem(Task(TaskType.BATTERY))
    loop = VoiceCommandLoop(
        system, FakeRecorder(), FakeASR(bad_result), output_fn=lambda _: None,
    )
    assert loop.handle_once() is None
    assert system.parsed == []
    assert system.executed == []


def test_faster_whisper_materializes_segments_and_reports_confidence():
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            segments = iter([
                SimpleNamespace(text="飞到一号", avg_logprob=-0.2, no_speech_prob=0.1),
                SimpleNamespace(text="挑战垫", avg_logprob=-0.4, no_speech_prob=0.2),
            ])
            info = SimpleNamespace(language="zh", language_probability=0.98, duration=1.5)
            return segments, info

    factory_calls = []

    def factory(name, **kwargs):
        factory_calls.append((name, kwargs))
        return FakeModel()

    backend = FasterWhisperASR(
        model_name="medium", device="cpu", compute_type="int8", model_factory=factory,
    )
    transcription = backend.transcribe(np.zeros(100, dtype=np.float32))
    assert transcription.text == "飞到一号挑战垫"
    assert transcription.average_log_probability == pytest.approx(-0.3)
    assert transcription.no_speech_probability == pytest.approx(0.2)
    assert factory_calls == [("medium", {"device": "cpu", "compute_type": "int8"})]
    assert calls[0][1]["condition_on_previous_text"] is False
    assert calls[0][1]["vad_filter"] is True


def test_microphone_rms_and_device_listing():
    assert MicrophoneRecorder._rms(np.array([1.0, -1.0], dtype=np.float32)) == pytest.approx(1.0)
    fake_sd = SimpleNamespace(query_devices=lambda: [
        {"name": "Speaker", "max_input_channels": 0},
        {"name": "USB Mic", "max_input_channels": 2},
    ])
    assert list_input_devices(fake_sd) == [(1, "USB Mic", 2)]


@pytest.mark.parametrize(("value", "expected"), [
    (None, None), ("", None), ("3", 3), (3, 3), ("USB Mic", "USB Mic"),
])
def test_parse_audio_device(value, expected):
    assert parse_audio_device(value) == expected
