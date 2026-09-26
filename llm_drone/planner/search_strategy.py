from __future__ import annotations

from abc import ABC, abstractmethod

from control.rc_command import RCCommand


class SearchStrategy(ABC):
    @abstractmethod
    def command(self, elapsed_sec: float) -> RCCommand:
        raise NotImplementedError

    def reset(self) -> None:
        pass


class YawSearchStrategy(SearchStrategy):
    """MVP search. Ground-pad visibility while yawing must be verified on hardware."""

    def __init__(self, yaw_speed: int = 20) -> None:
        self.yaw_speed = yaw_speed

    def command(self, elapsed_sec: float) -> RCCommand:
        del elapsed_sec
        return RCCommand(yaw=self.yaw_speed)
