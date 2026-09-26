from __future__ import annotations

import time


class PIDController:
    def __init__(
        self,
        kp: float,
        ki: float = 0.0,
        kd: float = 0.0,
        output_limit: float | tuple[float, float] | None = None,
        integral_limit: float | None = None,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.output_limit = self._limits(output_limit)
        self.integral_limit = abs(integral_limit) if integral_limit is not None else None
        self.reset()

    @staticmethod
    def _limits(limit: float | tuple[float, float] | None) -> tuple[float, float] | None:
        if limit is None:
            return None
        if isinstance(limit, tuple):
            return limit
        return (-abs(limit), abs(limit))

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_error: float | None = None
        self.previous_time: float | None = None

    def update(self, error: float, dt: float | None = None) -> float:
        now = time.monotonic()
        if dt is None:
            dt = 0.0 if self.previous_time is None else now - self.previous_time
        dt = max(0.0, dt)
        if dt > 0:
            self.integral += error * dt
            if self.integral_limit is not None:
                self.integral = max(-self.integral_limit, min(self.integral_limit, self.integral))
        derivative = 0.0
        if dt > 0 and self.previous_error is not None:
            derivative = (error - self.previous_error) / dt
        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        if self.output_limit is not None:
            output = max(self.output_limit[0], min(self.output_limit[1], output))
        self.previous_error, self.previous_time = error, now
        return output
