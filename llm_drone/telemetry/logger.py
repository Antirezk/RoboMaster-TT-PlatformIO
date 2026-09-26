from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


def configure_logging(log_dir: str | Path = "logs", verbose: bool = True) -> logging.Logger:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("llm_drone")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    file_handler = logging.FileHandler(directory / "drone.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    if verbose:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(logging.INFO)
        logger.addHandler(console)
    logger.propagate = False
    return logger


class NullLogger:
    def debug(self, *_: Any, **__: Any) -> None: pass
    def info(self, *_: Any, **__: Any) -> None: pass
    def warning(self, *_: Any, **__: Any) -> None: pass
    def error(self, *_: Any, **__: Any) -> None: pass
    def exception(self, *_: Any, **__: Any) -> None: pass
