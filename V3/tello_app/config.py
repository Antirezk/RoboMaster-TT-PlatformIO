from dataclasses import dataclass, fields
import ipaddress
import json
import math
from pathlib import Path

BUNDLED_PATH = Path(__file__).with_name("config.json")


def default_path():
    """A config.json next to the packaged exe wins over the copy inside the package."""
    from . import ROOT
    beside = ROOT / "config.json"
    return beside if beside.exists() else BUNDLED_PATH


DEFAULT_PATH = BUNDLED_PATH


@dataclass(frozen=True)
class AppConfig:
    drone_ip: str = "192.168.10.1"
    command_port: int = 8889
    bind_host: str = "0.0.0.0"
    local_command_port: int = 8889
    state_port: int = 8890
    route: str = "direct"               # "direct" (PC -> TT Wi-Fi) or "guard" (PC -> ESP32 ToF guard -> TT)
    esp32_host: str = "192.168.4.1"     # guard access point "TT-ToF-Demo"
    esp32_port: int = 9000
    keepalive_sec: float = 5.0          # SDK auto-lands after 15 s without a command
    state_max_age_sec: float = 1.5
    motor_min_battery: int = 25         # min battery to start the low-speed motor test
    takeoff_min_battery: int = 30       # min battery to take off
    auto_land_battery: int = 15         # auto-land when airborne battery drops to this
    max_move_cm: int = 200              # indoor safety cap; the SDK itself allows up to 500
    flight_speed_cm_s: int = 30         # sent as `speed x` before takeoff
    turn_default_deg: int = 90          # angle used for "turn left" without a number
    asr_model: str = "small.en"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    audio_device: int | str | None = None
    asr_min_log_probability: float = -1.0
    asr_max_no_speech_probability: float = 0.6
    whisper_dir: str = "models/whisper"     # relative to the V3 folder; ../llm_drone/.cache/whisper is also searched
    asr_allow_download: bool = False        # True: download small.en once if it is not found
    llm_model: str = "qwen3.5:9b"
    llm_timeout_sec: float = 15.0

    def __post_init__(self):
        ipaddress.IPv4Address(self.drone_ip)
        ipaddress.IPv4Address(self.bind_host)
        for name in ("command_port", "local_command_port", "state_port"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError(f"invalid {name}")
        if self.route not in {"direct", "guard"}:
            raise ValueError("route must be 'direct' or 'guard'")
        ipaddress.IPv4Address(self.esp32_host)
        if type(self.esp32_port) is not int or not 1 <= self.esp32_port <= 65535:
            raise ValueError("invalid esp32_port")
        if self.local_command_port == self.state_port:
            raise ValueError("local command port and state port must differ")
        if not 1 <= self.keepalive_sec < 10:
            raise ValueError("keepalive_sec must be 1..10 (SDK auto-land is 15 s)")
        for name, low, high in (("motor_min_battery", 10, 100), ("takeoff_min_battery", 10, 100),
                                ("auto_land_battery", 5, 50), ("max_move_cm", 20, 500),
                                ("flight_speed_cm_s", 10, 100), ("turn_default_deg", 1, 360)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {name}: expected {low}..{high}")
        if type(self.asr_allow_download) is not bool:
            raise ValueError("asr_allow_download must be true/false")
        for name in ("state_max_age_sec", "llm_timeout_sec"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path is not None else default_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
