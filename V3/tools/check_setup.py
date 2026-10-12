"""Check that this computer is ready to run the voice console.

    python tools/check_setup.py              # check only
    python tools/check_setup.py --download   # also download Whisper small.en (needs internet)
"""
import argparse
from pathlib import Path
import socket
import sys
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
results = []


def report(ok, name, detail=""):
    results.append(ok)
    print(f"  [{'OK' if ok else '!!'}] {name}" + (f"  - {detail}" if detail else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="download the Whisper model into models/whisper")
    args = parser.parse_args()
    print(f"RoboMaster TT Voice Console - setup check ({ROOT})\n")

    report(sys.version_info >= (3, 10), "Python >= 3.10", sys.version.split()[0])
    try:
        import tkinter
        report(True, "tkinter", f"Tk {tkinter.TkVersion}")
    except ImportError:
        report(False, "tkinter", "reinstall Python with 'tcl/tk and IDLE' ticked")
    for module in ("numpy", "sounddevice", "faster_whisper"):
        try:
            __import__(module)
            report(True, module)
        except Exception as exc:
            report(False, module, f"pip install -r requirements.txt ({exc})")

    try:
        import sounddevice as sd
        inputs = [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        report(bool(inputs), "microphone", inputs[0] if inputs else "no input device found")
    except Exception as exc:
        report(False, "microphone", str(exc))

    from tello_app.config import AppConfig
    from tello_app.voice import find_whisper_dir
    config = AppConfig.load()
    try:
        folder, _ = find_whisper_dir(config)
        report(True, f"Whisper model {config.asr_model}", str(folder))
    except RuntimeError as exc:
        if args.download:
            print(f"  ... downloading {config.asr_model} (about 500 MB) ...")
            from faster_whisper import WhisperModel
            target = ROOT / config.whisper_dir
            target.mkdir(parents=True, exist_ok=True)
            WhisperModel(config.asr_model, device="cpu", compute_type="int8", download_root=str(target))
            report(True, f"Whisper model {config.asr_model}", f"downloaded to {target}")
        else:
            report(False, f"Whisper model {config.asr_model}", str(exc))

    for port in (config.local_command_port, config.state_port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((config.bind_host, port))
            report(True, f"UDP port {port} free")
        except OSError:
            report(False, f"UDP port {port} free", "another drone program is running - close it")
        finally:
            s.close()

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:11434/api/tags", timeout=2) as resp:
            names = [m["name"] for m in json.load(resp).get("models", [])]
        print(f"  [--] Ollama (optional): running, {config.llm_model} "
              f"{'installed' if config.llm_model in names else 'NOT installed: ollama pull ' + config.llm_model}")
    except Exception:
        print("  [--] Ollama (optional): not running - only needed for the 'Use local Qwen' checkbox")

    print("\nAll required checks passed." if all(results) else "\nSome checks failed - see [!!] lines above.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
