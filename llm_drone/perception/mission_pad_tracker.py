from __future__ import annotations

import time
from typing import Any, Callable

from models.mission_pad import MissionPadObservation


class MissionPadTracker:
    def __init__(
        self,
        drone: Any,
        invert_x: bool = False,
        invert_y: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.drone = drone
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.clock = clock

    def observe(self) -> MissionPadObservation:
        timestamp = self.clock()
        pad_id = self.drone.get_mission_pad_id()
        if pad_id < 1:
            return MissionPadObservation.missing(timestamp)
        x = float(self.drone.get_mission_pad_distance_x())
        y = float(self.drone.get_mission_pad_distance_y())
        z = float(self.drone.get_mission_pad_distance_z())
        # VERIFY_ON_HARDWARE: confirm TT x/y signs before real closed-loop flight.
        if self.invert_x:
            x = -x
        if self.invert_y:
            y = -y
        return MissionPadObservation(True, pad_id, x, y, z, timestamp)
