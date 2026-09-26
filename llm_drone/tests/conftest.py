from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def config() -> dict:
    path = Path(__file__).resolve().parents[1] / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["mission"]["takeoff_settle_sec"] = 0.1
    data["mission"]["search_timeout_sec"] = 1.0
    data["mission"]["total_timeout_sec"] = 5.0
    data["mission_pad"]["stable_frames"] = 3
    data["mission_pad"]["lost_timeout_sec"] = 0.1
    return data


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0)
