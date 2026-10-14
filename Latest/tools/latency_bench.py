#!/usr/bin/env python3
"""Voice-pipeline latency benchmark: the same recordings on every machine.

    python tools/latency_bench.py record          # record the test phrases once -> bench_audio/
    python tools/latency_bench.py run             # Whisper + rule-parser latency on this machine
    python tools/latency_bench.py run --llm       # also time the local Qwen model (Ollama must run)
    python tools/latency_bench.py run --repeat 3  # repeat every clip 3 times

Copy the bench_audio/ folder to the other machine (PC <-> Jetson) and run "run" there too,
so both machines transcribe exactly the same audio. Results go to bench_results/.
The drone is never contacted.
"""
import argparse
import csv
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUDIO_DIR = ROOT / "bench_audio"
OUT_DIR = ROOT / "bench_results"
RATE = 16000
MODE = "flight"

# (what to say, SDK command the rule parser should produce; "" = no command expected)
PHRASES = [
    ("Take off.", "takeoff"),
    ("Go up 30 centimeters.", "up 30"),
    ("Move forward 50 centimeters.", "forward 50"),
    ("Move left 40 centimeters.", "left 40"),
    ("Turn right 90 degrees.", "cw 90"),
    ("Turn left 45 degrees.", "ccw 45"),
    ("Move back 30 centimeters.", "back 30"),
    ("Go down 20 centimeters.", "down 20"),
    ("Stop.", "stop"),
    ("Land.", "land"),
    ("Could you lift the drone into the air?", ""),
    ("Hello, how are you today?", ""),
]


# ----------------------------------------------------------------- helpers
def save_wav(path, audio):
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def load_wav(path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected 16 kHz mono 16-bit WAV")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def system_info():
    info = {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "cpu": platform.processor(),
    }
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(errors="ignore").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                info["cpu"] = line.split(":", 1)[1].strip()
                break
    tegra = Path("/etc/nv_tegra_release")
    if tegra.exists():
        info["l4t"] = tegra.read_text(errors="ignore").splitlines()[0][:100]
    try:
        out = subprocess.run(["nvpmodel", "-q"], capture_output=True, text=True, timeout=5).stdout
        info["power_mode"] = " | ".join(line.strip() for line in out.splitlines() if line.strip())[:120]
    except Exception:
        pass
    return info


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p90": p90, "min": ordered[0], "max": ordered[-1],
            "sd": statistics.stdev(values) if len(values) > 1 else 0.0}


# ----------------------------------------------------------------- record
def record(_args):
    import numpy as np
    from tello_app.config import AppConfig
    from tello_app.speech.microphone import MicrophoneRecorder

    config = AppConfig.load()
    AUDIO_DIR.mkdir(exist_ok=True)
    recorder = MicrophoneRecorder(device=config.audio_device, speech_timeout_sec=8.0, max_record_sec=10.0)
    print(f"Microphone: {config.audio_device or 'system default'}.  Speak at your normal distance and volume.")
    rows = []
    for i, (phrase, expected) in enumerate(PHRASES, 1):
        while True:
            input(f"\n[{i}/{len(PHRASES)}] Press Enter, stay quiet until 'SPEAK now', then say:   {phrase}")
            try:
                audio = recorder.record(on_ready=lambda _t: print("   SPEAK now ..."))
            except Exception as exc:
                print(f"   failed: {exc} - try again")
                continue
            peak = float(np.abs(audio).max())
            print(f"   recorded {len(audio) / RATE:.2f} s, peak level {peak:.3f}"
                  + ("   (very quiet - consider redoing)" if peak < 0.02 else ""))
            if input("   keep it? [Enter = yes, r = redo] ").strip().lower() != "r":
                break
        name = f"{i:02d}.wav"
        save_wav(AUDIO_DIR / name, audio)
        rows.append({"file": name, "phrase": phrase, "expected": expected})
    with open(AUDIO_DIR / "phrases.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "phrase", "expected"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {len(rows)} clips to {AUDIO_DIR}")
    print("Next: python tools/latency_bench.py run   (copy bench_audio/ to the other machine and run there too)")


# ----------------------------------------------------------------- run
def run(args):
    from tello_app.config import AppConfig
    from tello_app.parser import NotUnderstood, parse
    from tello_app.sdk import SDKError
    from tello_app.speech.asr import FasterWhisperASR
    from tello_app.voice import PROMPTS, resolve_model

    index = AUDIO_DIR / "phrases.csv"
    if not index.exists():
        raise SystemExit("No recordings yet. Run:  python tools/latency_bench.py record")
    with open(index, encoding="utf-8") as f:
        clips = list(csv.DictReader(f))

    config = AppConfig.load()
    info = system_info()
    info.update({"asr_model": config.asr_model, "asr_device": config.asr_device,
                 "asr_compute_type": config.asr_compute_type,
                 "llm_model": config.llm_model if args.llm else None, "repeat": args.repeat})
    print("System:", json.dumps(info, indent=2))

    model, root, local_only = resolve_model(config)
    asr = FasterWhisperASR(model_name=model, device=config.asr_device, compute_type=config.asr_compute_type,
                           language="en", local_files_only=local_only, download_root=root)
    asr.initial_prompt = PROMPTS.get(MODE, "")
    t = time.perf_counter()
    asr.load()
    info["asr_load_s"] = round(time.perf_counter() - t, 3)
    print(f"\nWhisper loaded in {info['asr_load_s']:.2f} s; warming up ...")
    asr.transcribe(load_wav(AUDIO_DIR / clips[0]["file"]))

    llm = None
    if args.llm:
        from tello_app.llm import QwenInterpreter
        llm = QwenInterpreter(config.llm_model, config.llm_timeout_sec)
        t = time.perf_counter()
        try:
            llm.request("take off", MODE)
        except SDKError as exc:
            raise SystemExit(f"\nQwen is not working, so its latency cannot be measured:\n  {exc}\n"
                             f"Check `ollama list` and set \"llm_model\" in tello_app/config.json "
                             f"(now: {config.llm_model}), or run without --llm.")
        info["llm_warmup_s"] = round(time.perf_counter() - t, 3)
        print(f"Qwen warm-up took {info['llm_warmup_s']:.2f} s")

    rows = []
    for rep in range(1, args.repeat + 1):
        print(f"\n--- run {rep}/{args.repeat} ---")
        for clip in clips:
            audio = load_wav(AUDIO_DIR / clip["file"])
            audio_s = len(audio) / RATE
            t0 = time.perf_counter()
            result = asr.transcribe(audio)
            asr_s = time.perf_counter() - t0
            text = result.text.strip()

            t1 = time.perf_counter()
            try:
                intent = parse(text, MODE, config)
                rule_out = getattr(intent, "sdk", str(intent))
            except NotUnderstood:
                rule_out = "(not understood)"
            except SDKError as exc:
                rule_out = f"(refused: {exc})"
            rule_ms = (time.perf_counter() - t1) * 1000

            llm_s, llm_out = None, ""
            if llm is not None:
                t2 = time.perf_counter()
                try:
                    llm_out = json.dumps(llm.request(text, MODE))
                    llm_s = time.perf_counter() - t2
                except SDKError as exc:
                    llm_out = f"error: {exc}"   # failed calls are not counted as latency

            expected = clip["expected"]
            correct = (rule_out == expected) if expected else not rule_out.startswith(("takeoff", "up", "down", "forward", "back", "left", "right", "cw", "ccw", "land", "stop"))
            rows.append({"run": rep, "file": clip["file"], "phrase": clip["phrase"], "expected": expected,
                         "transcript": text, "rule_output": rule_out, "correct": correct,
                         "audio_s": round(audio_s, 3), "asr_s": round(asr_s, 4),
                         "rtf": round(asr_s / audio_s, 3) if audio_s else None,
                         "rule_ms": round(rule_ms, 3), "llm_s": round(llm_s, 4) if llm_s is not None else "",
                         "llm_output": llm_out,
                         "avg_logprob": round(result.average_log_probability, 3),
                         "no_speech_prob": round(result.no_speech_probability, 3)})
            r = rows[-1]
            print(f"  {r['file']}  ASR {asr_s * 1000:6.0f} ms"
                  + (f"  LLM {llm_s * 1000:6.0f} ms" if llm_s is not None else ("  LLM  error" if llm else ""))
                  + f"  {'OK ' if correct else 'XX '} \"{text}\" -> {rule_out}")

    summary = {"system": info,
               "asr_s": stats([r["asr_s"] for r in rows]),
               "rtf": stats([r["rtf"] for r in rows if r["rtf"] is not None]),
               "rule_ms": stats([r["rule_ms"] for r in rows]),
               "llm_s": stats([r["llm_s"] for r in rows if r["llm_s"] != ""]),
               "llm_errors": sum(1 for r in rows if r["llm_output"].startswith("error")),
               "correct": f"{sum(r['correct'] for r in rows)}/{len(rows)}"}

    OUT_DIR.mkdir(exist_ok=True)
    stamp = f"{info['host']}_{datetime.now():%Y%m%d_%H%M%S}"
    csv_path = OUT_DIR / f"latency_{stamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    json_path = OUT_DIR / f"summary_{stamp}.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n================ SUMMARY ================")
    for key, unit in (("asr_s", "s"), ("rtf", "x"), ("rule_ms", "ms"), ("llm_s", "s")):
        s = summary[key]
        if s:
            print(f"{key:8s} n={s['n']:3d}  mean {s['mean']:.3f}  median {s['median']:.3f}  "
                  f"p90 {s['p90']:.3f}  max {s['max']:.3f} {unit}")
    print(f"correct  {summary['correct']}")
    if summary["llm_errors"]:
        print(f"WARNING  {summary['llm_errors']} Qwen calls failed and were left out of llm_s (see llm_output in the CSV)")
    print(f"\nSaved {csv_path}\n      {json_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="record the test phrases once")
    p_run = sub.add_parser("run", help="measure latency on this machine")
    p_run.add_argument("--llm", action="store_true", help="also time the local Qwen model")
    p_run.add_argument("--repeat", type=int, default=3, help="passes over all clips (default 3)")
    args = parser.parse_args()
    record(args) if args.cmd == "record" else run(args)


if __name__ == "__main__":
    main()
