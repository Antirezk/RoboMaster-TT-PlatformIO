from dataclasses import dataclass


def _clamp(value: int | float) -> int:
    return max(-100, min(100, int(round(value))))


@dataclass(frozen=True)
class RCCommand:
    left_right: int = 0
    forward_backward: int = 0
    up_down: int = 0
    yaw: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "left_right", _clamp(self.left_right))
        object.__setattr__(self, "forward_backward", _clamp(self.forward_backward))
        object.__setattr__(self, "up_down", _clamp(self.up_down))
        object.__setattr__(self, "yaw", _clamp(self.yaw))

    @classmethod
    def zero(cls) -> "RCCommand":
        return cls()

    @property
    def is_zero(self) -> bool:
        return not any((self.left_right, self.forward_backward, self.up_down, self.yaw))
