"""`TelloVoiceConsole.exe --self-test`: proves a packaged build works on this computer.

Checks the bundled libraries, loads the bundled Whisper model and transcribes a short
clip, flies both routes against the built-in simulators, and opens the window once.
Writes self_test.log next to the program; exit code 0 = everything passed.
"""
from dataclasses import replace
import time
import traceback

from . import ROOT, __version__


def run():
    lines = []
    log = lines.append
    ok = True

    def step(name, fn):
        nonlocal ok
        start = time.monotonic()
        try:
            detail = fn()
            log(f"[OK]   {name} ({time.monotonic() - start:.1f}s){' - ' + detail if detail else ''}")
        except Exception:
            ok = False
            log(f"[FAIL] {name}\n{traceback.format_exc()}")

    log(f"TelloVoiceConsole {__version__} self-test in {ROOT}")
    step("import numpy / sounddevice / faster_whisper / ctranslate2", _imports)
    step("Whisper model loads and transcribes", _whisper)
    step("simulated flight, direct route", lambda: _flight("direct"))
    step("simulated flight, ESP32 guard route with avoidance", lambda: _flight("guard"))
    step("main window opens and closes", _window)
    log("RESULT: " + ("PASS" if ok else "FAIL"))
    (ROOT / "self_test.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def _imports():
    import ctranslate2
    import faster_whisper
    import numpy
    try:
        import sounddevice
        audio = f"sounddevice {sounddevice.__version__}"
    except OSError as exc:              # no PortAudio device on a headless build machine
        audio = f"sounddevice import: {exc}"
    return f"numpy {numpy.__version__}, faster-whisper {faster_whisper.__version__}, " \
           f"ctranslate2 {ctranslate2.__version__}, {audio}"


def _whisper():
    import numpy as np
    from .config import AppConfig
    from .speech.asr import FasterWhisperASR
    from .voice import find_whisper_dir
    config = AppConfig.load()
    folder, local = find_whisper_dir(config)
    if not local:
        raise RuntimeError(f"model not bundled in {folder}")
    asr = FasterWhisperASR(model_name=config.asr_model, language="en", local_files_only=True,
                           download_root=str(folder), compute_type="int8")
    asr.load()
    t = np.arange(16000 * 2) / 16000
    clip = (0.02 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)   # 2 s tone
    result = asr.transcribe(clip)
    return f"model in {folder.name}, transcript={result.text!r}"


def _flight(route):
    from .config import AppConfig
    from .controller import Controller
    from .parser import parse
    from .sdk import FLIGHT
    logs = []
    c = Controller(replace(AppConfig.load(), route=route), log=lambda k, t: logs.append((k, t)), sim_scale=0.2)
    try:
        c.connect(simulate=True)
        deadline = time.monotonic() + 10
        while not c.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        if not c.connected:
            raise RuntimeError("could not connect to the simulator: " + "; ".join(t for _, t in logs[-3:]))
        c.set_mode(FLIGHT)
        for text in ("take off", "move forward 50 centimeters", "turn left 90 degrees", "land"):
            c.submit(parse(text, FLIGHT, c.config))
            time.sleep(0.1)
            deadline = time.monotonic() + 20
            while (c.busy or c.pending) and time.monotonic() < deadline:
                time.sleep(0.05)
        done = [t for k, t in logs if k == "ok" and t.startswith("Done")]
        if len(done) != 4 or c.airborne:
            raise RuntimeError(f"unexpected flight log: {logs[-6:]}")
        return f"{len(done)} commands completed"
    finally:
        c.close()
        c.closed.wait(10)


def _window():
    import tkinter as tk
    from .gui import App
    root = tk.Tk()
    app = App(root, voice_factory=lambda: (_ for _ in ()).throw(RuntimeError("self-test: no microphone")))
    root.after(1500, app.close)
    root.after(12000, root.destroy)     # safety net
    root.mainloop()
    return "ok"
