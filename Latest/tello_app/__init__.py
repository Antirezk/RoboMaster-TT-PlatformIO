"""RoboMaster TT voice console (V3)."""
from pathlib import Path
import sys

__version__ = "3.0.0"
FROZEN = bool(getattr(sys, "frozen", False))
# Source checkout: the project folder (Latest/). Packaged exe: the folder that contains TelloVoiceConsole.exe,
# so config.json, logs/ and models/ live next to the program and travel with it.
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
