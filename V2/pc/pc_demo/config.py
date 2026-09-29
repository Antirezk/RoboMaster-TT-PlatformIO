from dataclasses import dataclass
import ipaddress
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    drone_ip: str = "192.168.10.1"
    command_port: int = 8889
    bind_host: str = "0.0.0.0"
    local_command_port: int = 8889
    state_port: int = 8890
    video_port: int = 11111  # Reserved only: no socket or streamon in this demo.
    command_timeout_sec: float = 8.0
    flight_timeout_sec: float = 30.0
    keepalive_sec: float = 5.0
    state_max_age_sec: float = 1.0
    minimum_battery: int = 25
    pad_height_cm: int = 60
    pad_speed_cm_s: int = 20
    pad_tolerance_cm: int = 10
    pad_stable_frames: int = 5
    pad_verify_timeout_sec: float = 5.0
    asr_model: str = "small.en"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    audio_device: int | str | None = None
    asr_min_log_probability: float = -1.0
    asr_max_no_speech_probability: float = 0.6
    asr_initial_prompt: str = "RoboMaster Tello drone commands: take off, land, stop, hover, battery, status, move left, move right, forward, back, up, down, land on mission pad one."
    llm_enabled: bool = True
    llm_model: str = "qwen3.5:9b"
    llm_timeout_sec: float = 30.0
    default_move_cm: int = 20

    def __post_init__(self):
        ipaddress.IPv4Address(self.drone_ip)
        ipaddress.IPv4Address(self.bind_host)
        for name in ("command_port", "local_command_port", "state_port", "video_port"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError(f"invalid {name}")
        if len({self.local_command_port, self.state_port, self.video_port}) != 3:
            raise ValueError("local command, state and video ports must be distinct")
        for name in ("command_timeout_sec", "flight_timeout_sec", "keepalive_sec",
                     "state_max_age_sec", "pad_verify_timeout_sec", "llm_timeout_sec"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")
        if self.keepalive_sec >= 10:
            raise ValueError("keepalive_sec must be below 10 seconds")
        for name, low, high in (("minimum_battery", 1, 100), ("pad_height_cm", 30, 120),
                                ("pad_speed_cm_s", 10, 30), ("pad_tolerance_cm", 1, 20),
                                ("pad_stable_frames", 1, 100), ("default_move_cm", 20, 100)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {name}: expected {low}..{high}")
        if not math.isfinite(self.asr_min_log_probability) or self.asr_min_log_probability > 0:
            raise ValueError("invalid ASR log probability threshold")
        if not 0 <= self.asr_max_no_speech_probability <= 1:
            raise ValueError("invalid ASR no-speech threshold")
        if type(self.llm_enabled) is not bool or not isinstance(self.llm_model, str) or not self.llm_model.strip():
            raise ValueError("invalid local LLM configuration")

    @classmethod
    def load(cls, path: Path):
        return cls(**json.loads(path.read_text(encoding="utf-8")))
