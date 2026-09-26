from dataclasses import dataclass


@dataclass
class DroneState:
    connected: bool = False
    flying: bool = False
    battery: int | None = None
    height_cm: int | None = None
    mission_pads_enabled: bool = False
    dry_run: bool = False
    safety_status: str = "UNKNOWN"
    safety_intervening: bool = False
    firmware_state: str = "UNKNOWN"
