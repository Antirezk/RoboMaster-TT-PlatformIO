from __future__ import annotations

from collections import deque
import math
import queue
import time
from typing import Any, Callable

from core.exceptions import DroneError


class VoiceInputError(DroneError):
    pass


class MicrophoneRecorder:
    """Records one utterance after ambient calibration and stops on silence."""

    def __init__(self, sample_rate: int = 16000, block_ms: int = 30,
                 calibration_sec: float = 0.4, pre_roll_sec: float = 0.25,
                 speech_timeout_sec: float = 3.0, silence_stop_sec: float = 0.8,
                 max_record_sec: float = 8.0, minimum_rms: float = 0.012,
                 noise_multiplier: float = 3.0, device: int | str | None = None,
                 sounddevice_module: Any | None = None) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.calibration_sec = calibration_sec
        self.pre_roll_sec = pre_roll_sec
        self.speech_timeout_sec = speech_timeout_sec
        self.silence_stop_sec = silence_stop_sec
        self.max_record_sec = max_record_sec
        self.minimum_rms = minimum_rms
        self.noise_multiplier = noise_multiplier
        self.device = device
        self.sounddevice_module = sounddevice_module

    @property
    def block_size(self) -> int:
        return max(1, round(self.sample_rate * self.block_ms / 1000))

    def record(self, on_ready: Callable[[float], None] | None = None) -> Any:
        try:
            import numpy as np
        except ImportError as exc:
            raise VoiceInputError("voice mode requires numpy") from exc
        sd = self.sounddevice_module
        if sd is None:
            try:
                import sounddevice as sd
            except ImportError as exc:
                raise VoiceInputError(
                    "voice mode requires requirements-voice.txt (sounddevice)"
                ) from exc

        blocks: queue.Queue[Any] = queue.Queue()
        stream_errors: list[str] = []

        def callback(indata: Any, frames: int, timing: Any, status: Any) -> None:
            del frames, timing
            if status:
                stream_errors.append(str(status))
            blocks.put(indata[:, 0].copy())

        calibration_count = max(1, math.ceil(self.calibration_sec * 1000 / self.block_ms))
        pre_roll_count = max(1, math.ceil(self.pre_roll_sec * 1000 / self.block_ms))
        silence_count_needed = max(1, math.ceil(self.silence_stop_sec * 1000 / self.block_ms))
        speech_wait_count = max(1, math.ceil(self.speech_timeout_sec * 1000 / self.block_ms))
        max_count = max(1, math.ceil(self.max_record_sec * 1000 / self.block_ms))

        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32",
                blocksize=self.block_size, device=self.device, callback=callback,
            ):
                noise_levels = [self._rms(blocks.get(timeout=1.0)) for _ in range(calibration_count)]
                threshold = max(self.minimum_rms, (sum(noise_levels) / len(noise_levels)) * self.noise_multiplier)
                if on_ready:
                    on_ready(threshold)
                pre_roll: deque[Any] = deque(maxlen=pre_roll_count)
                captured: list[Any] = []
                speech_started = False
                silence_count = 0
                waited = 0
                for _ in range(max_count):
                    block = blocks.get(timeout=1.0)
                    level = self._rms(block)
                    if not speech_started:
                        pre_roll.append(block)
                        waited += 1
                        if level >= threshold:
                            speech_started = True
                            captured.extend(pre_roll)
                        elif waited >= speech_wait_count:
                            raise VoiceInputError("no speech detected before timeout")
                    else:
                        captured.append(block)
                        silence_count = silence_count + 1 if level < threshold else 0
                        if silence_count >= silence_count_needed:
                            break
        except VoiceInputError:
            raise
        except Exception as exc:
            raise VoiceInputError(f"microphone recording failed: {exc}") from exc

        if not captured:
            raise VoiceInputError("no speech audio captured")
        if stream_errors:
            raise VoiceInputError(f"audio stream reported: {stream_errors[-1]}")
        return np.concatenate(captured).astype(np.float32, copy=False)

    @staticmethod
    def _rms(block: Any) -> float:
        try:
            import numpy as np
            return float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
        except Exception:
            values = [float(value) for value in block]
            return math.sqrt(sum(value * value for value in values) / max(1, len(values)))


def list_input_devices(sounddevice_module: Any | None = None) -> list[tuple[int, str, int]]:
    sd = sounddevice_module
    if sd is None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceInputError("install requirements-voice.txt to list audio devices") from exc
    result = []
    for index, device in enumerate(sd.query_devices()):
        channels = int(device.get("max_input_channels", 0))
        if channels > 0:
            result.append((index, str(device.get("name", "unknown")), channels))
    return result
