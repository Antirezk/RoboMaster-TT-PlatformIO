#!/usr/bin/env python3
"""Automatic stress test of the voice pipeline: thousands of spoken commands, no drone, no microphone.

    pip install pywin32                              (once; Windows text-to-speech)
    python tools/voice_stress.py generate            # speak every phrase with every English Windows voice
    python tools/voice_stress.py run                 # ~1 hour: clean + noisy copies through Whisper + rules
    python tools/voice_stress.py run --llm           # also let the local Qwen model try what the rules miss
    python tools/voice_stress.py run --max-minutes 10   # shorter run (clips are shuffled, so any prefix is balanced)
    python tools/voice_stress.py run --audio-dir compare --noise-repeats 5
                                                     # your own recordings: compare/phrases.csv + WAV/MP3 files

What it does
  1. generate: every phrase in PHRASES is spoken by each installed English SAPI voice at three speaking
     rates and saved as 16 kHz mono WAV in stress_audio/ (with phrases.csv as the index).
  2. run: each clip is played into the real recogniser (faster-whisper, same model, settings, initial prompt
     and confidence gate as the app), then the real grammar (and optionally the anchored Qwen fallback),
     in 9 acoustic conditions: clean, white noise and babble (other voices talking) at 20, 10, 5 and 0 dB SNR.
     Every result is appended to stress_results/stress_<host>_<time>.csv as it happens, so an interrupted
     run still leaves usable data. A summary JSON is written at the end.
The drone is never contacted.

Outcome of each clip
  correct      the expected command (or, for a non-command phrase, nothing) came out
  missed       a command phrase produced no command (gate, rules or anchors refused it) - safe
  wrong        a different command came out - the safety-relevant failure
"""
import argparse
import csv
import json
import math
import os
import platform
import random
import socket
import statistics
import sys
import time
import wave
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUDIO_DIR = ROOT / "stress_audio"
OUT_DIR = ROOT / "stress_results"
RATE = 16000
MODE = "flight"
SPEAK_RATES = {"slow": -2, "normal": 0, "fast": 3}            # SAPI rate, -10..10
CONDITIONS = [("clean", None)] + [(kind, snr) for kind in ("white", "babble") for snr in (20, 10, 5, 0)]

# (text spoken, SDK command the app should produce; "" = must produce no command)
PHRASES = [
    # take-off / land / stop
    ("Take off.", "takeoff"), ("Launch.", "takeoff"), ("Lift off.", "takeoff"),
    ("Land.", "land"), ("Land now.", "land"), ("Touch down.", "land"),
    ("Stop.", "stop"), ("Hover.", "stop"), ("Hold position.", "stop"), ("Freeze.", "stop"),
    # moves, centimetres and metres, digits and words
    ("Move forward 50 centimeters.", "forward 50"), ("Go forward 30 centimeters.", "forward 30"),
    ("Fly forward one meter.", "forward 100"), ("Forward 20.", "forward 20"),
    ("Move back 40 centimeters.", "back 40"), ("Go backward 60 centimeters.", "back 60"), ("Back 20.", "back 20"),
    ("Move left 30 centimeters.", "left 30"), ("Fly left 80 centimeters.", "left 80"), ("Left 50.", "left 50"),
    ("Move right 40 centimeters.", "right 40"), ("Go right half a meter.", "right 50"), ("Right 20.", "right 20"),
    ("Go up 30 centimeters.", "up 30"), ("Move up one meter.", "up 100"), ("Up 20.", "up 20"),
    ("Go down 20 centimeters.", "down 20"), ("Move down 50 centimeters.", "down 50"), ("Down 30.", "down 30"),
    ("Move forward 1.5 meters.", "forward 150"),
    # turns
    ("Turn left 90 degrees.", "ccw 90"), ("Turn right 90 degrees.", "cw 90"), ("Turn left 45 degrees.", "ccw 45"),
    ("Turn right 30 degrees.", "cw 30"), ("Rotate clockwise 180 degrees.", "cw 180"),
    ("Rotate counterclockwise 60 degrees.", "ccw 60"), ("Turn around.", "cw 180"),
    ("Turn left 20.", "ccw 20"), ("Turn right 120 degrees.", "cw 120"),
    # speed and queries
    ("Set speed to 50.", "speed 50"), ("Speed 30.", "speed 30"), ("Battery.", "battery?"),
    ("Check battery.", "battery?"), ("Wifi signal.", "wifi?"),
    # must NOT become a command: chat, negations, compounds, polite fragments
    ("Hello, how are you?", ""), ("Thank you very much.", ""), ("Nice job.", ""), ("Can you hear me?", ""),
    ("I think it is working.", ""), ("What a nice day.", ""), ("Let me check something.", ""),
    ("Okay, good.", ""), ("Hold on a second.", ""), ("That was close.", ""),
    ("Do not take off.", ""), ("Don't move forward.", ""), ("Never land here.", ""),
    ("Take off and move forward 50 centimeters.", ""), ("Turn left then land.", ""),
    ("Move forward.", ""), ("Go up.", ""),
]


# ----------------------------------------------------------------- audio helpers
def save_wav(path, audio):
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())


def load_wav(path):
    import numpy as np
    try:
        w = wave.open(str(path), "rb")
    except (wave.Error, EOFError):                      # not a plain PCM WAV (MP3, float WAV, ...)
        from faster_whisper.audio import decode_audio
        return np.asarray(decode_audio(str(path), sampling_rate=RATE), dtype=np.float32)
    with w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        from faster_whisper.audio import decode_audio
        return np.asarray(decode_audio(str(path), sampling_rate=RATE), dtype=np.float32)
    x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != RATE:                                   # linear resampling is enough for speech at 16 kHz
        t_old = np.arange(len(x)) / rate
        t_new = np.arange(int(len(x) * RATE / rate)) / RATE
        x = np.interp(t_new, t_old, x).astype(np.float32)
    return x


def rms(x):
    import numpy as np
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def pad(x, before=0.4, after=0.4):
    import numpy as np
    return np.concatenate([np.zeros(int(before * RATE), np.float32), x, np.zeros(int(after * RATE), np.float32)])


def make_condition(clean, kind, snr_db, babble_pool, rng):
    """Add noise at the requested SNR, measured against the speech RMS (padding excluded)."""
    import numpy as np
    x = pad(clean)
    if kind == "clean":
        return x
    speech_rms = rms(clean)
    if kind == "white":
        noise = rng.normal(0.0, 1.0, len(x)).astype(np.float32)
    else:                                              # babble: four other utterances, summed and looped
        noise = np.zeros(len(x), np.float32)
        for other in rng.choice(len(babble_pool), size=4, replace=False):
            src = babble_pool[other]
            reps = int(math.ceil(len(x) / len(src))) + 1
            start = int(rng.integers(0, len(src)))
            noise += np.tile(src, reps)[start:start + len(x)]
    noise *= speech_rms / (rms(noise) * 10 ** (snr_db / 20.0) + 1e-12)
    y = x + noise
    peak = float(np.abs(y).max())
    return y / peak * 0.95 if peak > 0.95 else y


# ----------------------------------------------------------------- generate
def generate(args):
    try:
        import win32com.client
    except ImportError:
        raise SystemExit("Windows text-to-speech needs pywin32:  pip install pywin32")
    AUDIO_DIR.mkdir(exist_ok=True)
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    voices = [speaker.GetVoices().Item(i) for i in range(speaker.GetVoices().Count)]
    english = [v for v in voices if "english" in v.GetDescription().lower()
               or v.GetAttribute("Language").lower().split(";")[0] in {"409", "809", "c09", "1009", "4009"}]
    if not english:
        raise SystemExit("No English SAPI voice found (Settings > Time & language > Speech > add voices)")
    print("English voices:", ", ".join(v.GetDescription() for v in english))
    rows = []
    for vi, voice in enumerate(english):
        vname = voice.GetDescription().split(" - ")[0].replace("Microsoft ", "").replace(" Desktop", "")
        for rname, rvalue in SPEAK_RATES.items():
            for pi, (text, expected) in enumerate(PHRASES):
                name = f"v{vi}_{rname}_{pi:03d}.wav"
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
                fmt.Type = 18                                  # SAFT16kHz16BitMono
                stream.Format = fmt
                stream.Open(str(AUDIO_DIR / name), 3)          # SSFMCreateForWrite
                speaker.AudioOutputStream = stream
                speaker.Voice = voice
                speaker.Rate = rvalue
                speaker.Speak(text)
                stream.Close()
                rows.append({"file": name, "voice": vname, "rate": rname, "phrase_id": pi,
                             "phrase": text, "expected": expected})
            print(f"  {vname:10s} {rname:6s}: {len(PHRASES)} clips")
    with open(AUDIO_DIR / "phrases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    n = len(rows) * len(CONDITIONS)
    print(f"\nSaved {len(rows)} clips to {AUDIO_DIR}; a full run is {n} recognitions "
          f"(about {n * 0.75 / 60:.0f} min on a desktop CPU).")


# ----------------------------------------------------------------- run
def wilson(k, n, z=1.959964):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def run(args):
    import numpy as np
    from tello_app.config import AppConfig
    from tello_app.parser import NotUnderstood, parse
    from tello_app.sdk import SDKError
    from tello_app.speech.asr import FasterWhisperASR
    from tello_app.voice import PROMPTS, resolve_model

    audio_dir = Path(args.audio_dir) if args.audio_dir else AUDIO_DIR
    if not audio_dir.is_absolute():
        audio_dir = ROOT / audio_dir
    index = audio_dir / "phrases.csv"
    if not index.exists():
        raise SystemExit(f"No {index}. Run:  python tools/voice_stress.py generate  (or put phrases.csv there)")
    clips = [c for c in csv.DictReader(open(index, encoding="utf-8-sig"))]
    missing = [c["file"] for c in clips if not (audio_dir / c["file"]).is_file()]
    if missing:
        print(f"Skipping {len(missing)} missing files: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    clips = [c for c in clips if c["file"] not in set(missing)]
    for c in clips:
        c.setdefault("voice", audio_dir.name); c.setdefault("rate", "-"); c.setdefault("phrase_id", c["file"])
        c["voice"] = c.get("voice") or audio_dir.name
        c["rate"] = c.get("rate") or "-"
    config = AppConfig.load()
    audio = {c["file"]: load_wav(audio_dir / c["file"]) for c in clips}
    babble_pool = [audio[c["file"]] for c in clips if c["expected"] == ""] or list(audio.values())

    rng = random.Random(args.seed)
    jobs = [(c, kind, snr) for c in clips for kind, snr in CONDITIONS
            for _ in range(1 if kind == "clean" else max(1, args.noise_repeats))]
    rng.shuffle(jobs)
    noise_rng = np.random.default_rng(args.seed)

    model, root, local_only = resolve_model(config)
    asr = FasterWhisperASR(model_name=model, device=config.asr_device, compute_type=config.asr_compute_type,
                           language="en", local_files_only=local_only, download_root=root)
    asr.initial_prompt = PROMPTS.get(MODE, "")
    asr.load()
    llm = None
    if args.llm:
        from tello_app.llm import QwenInterpreter
        llm = QwenInterpreter(args.llm_model or config.llm_model, config.llm_timeout_sec)

    def gate(r):
        return (bool(r.text.strip()) and r.language == "en" and math.isfinite(r.average_log_probability)
                and r.average_log_probability >= config.asr_min_log_probability
                and 0 <= r.no_speech_probability <= config.asr_max_no_speech_probability)

    OUT_DIR.mkdir(exist_ok=True)
    stamp = f"{socket.gethostname()}_{datetime.now():%Y%m%d_%H%M%S}"
    csv_path = OUT_DIR / f"stress_{stamp}.csv"
    fields = ["n", "file", "voice", "rate", "phrase_id", "phrase", "expected", "handled_by", "noise", "snr_db", "transcript",
              "gate", "rule_output", "llm_output", "final", "source", "outcome", "asr_s", "rule_ms", "llm_s"]
    f = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()

    info = {"host": socket.gethostname(), "platform": platform.platform(), "cpu": platform.processor(),
            "asr_model": config.asr_model, "compute_type": config.asr_compute_type,
            "llm": (args.llm_model or config.llm_model) if args.llm else None, "clips": len(clips), "jobs": len(jobs),
            "conditions": [f"{k} {s} dB" if s is not None else k for k, s in CONDITIONS],
            "started": datetime.now().isoformat(timespec="seconds"), "seed": args.seed}
    print(json.dumps(info, indent=1))
    t_start = time.time()
    deadline = t_start + args.max_minutes * 60 if args.max_minutes else None
    done = 0
    counts = defaultdict(lambda: defaultdict(int))
    try:
        for c, kind, snr in jobs:
            if deadline and time.time() > deadline:
                print("\nTime budget reached.")
                break
            x = make_condition(audio[c["file"]], kind, snr, babble_pool, noise_rng)
            t0 = time.perf_counter()
            res = asr.transcribe(x)
            asr_s = time.perf_counter() - t0
            text = res.text.strip()
            passed = gate(res)
            rule_out, llm_out, final, source, rule_ms, llm_s = "", "", "", "", 0.0, ""
            if passed:
                t1 = time.perf_counter()
                try:
                    intent = parse(text, MODE, config)
                    rule_out = intent.sdk or intent.action
                    final, source = rule_out, "rules"
                except NotUnderstood:
                    rule_out = "(not understood)"
                    if llm is not None:
                        t2 = time.perf_counter()
                        try:
                            intent = llm.interpret(text, MODE, config)
                            llm_out = intent.sdk or intent.action
                            final, source = llm_out, "llm"
                        except SDKError as exc:
                            llm_out = f"(refused: {str(exc)[:60]})"
                        llm_s = round(time.perf_counter() - t2, 4)
                except SDKError as exc:
                    rule_out = f"(refused: {str(exc)[:60]})"
                rule_ms = (time.perf_counter() - t1) * 1000
            expected = c["expected"]
            if final == expected:
                outcome = "correct"
            elif final == "":
                outcome = "missed"
            else:
                outcome = "wrong"
            done += 1
            cond = f"{kind} {snr} dB" if snr is not None else kind
            counts[cond][outcome] += 1
            counts[cond]["n"] += 1
            writer.writerow({"n": done, "file": c["file"], "voice": c["voice"], "rate": c["rate"],
                             "phrase_id": c["phrase_id"], "phrase": c["phrase"], "expected": expected,
                             "handled_by": c.get("handled_by", ""),
                             "noise": kind, "snr_db": "" if snr is None else snr, "transcript": text,
                             "gate": int(passed), "rule_output": rule_out, "llm_output": llm_out, "final": final,
                             "source": source, "outcome": outcome, "asr_s": round(asr_s, 4),
                             "rule_ms": round(rule_ms, 3), "llm_s": llm_s})
            if done % 25 == 0:
                f.flush()
                el = time.time() - t_start
                eta = el / done * (len(jobs) - done)
                wrong = sum(v["wrong"] for v in counts.values())
                print(f"  {done}/{len(jobs)}  {el / 60:5.1f} min elapsed, ~{eta / 60:5.1f} min left, "
                      f"wrong commands so far: {wrong}", flush=True)
            if outcome == "wrong":
                print(f"  WRONG: [{cond}] \"{c['phrase']}\" heard \"{text}\" -> {final}", flush=True)
    except KeyboardInterrupt:
        print("\nStopped by user - partial results kept.")
    finally:
        f.close()

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    summary = {"system": info, "finished": datetime.now().isoformat(timespec="seconds"),
               "minutes": round((time.time() - t_start) / 60, 1), "n": len(rows), "by_condition": {}}
    order = [f"{k} {s} dB" if s is not None else k for k, s in CONDITIONS]

    def block(sub):
        cmd = [r for r in sub if r["expected"]]
        non = [r for r in sub if not r["expected"]]
        wrong = sum(r["outcome"] == "wrong" for r in sub)
        ok_cmd = sum(r["outcome"] == "correct" for r in cmd)
        lo, hi = wilson(wrong, len(sub))
        return {"n": len(sub), "command_clips": len(cmd), "command_correct": ok_cmd,
                "command_accuracy": round(ok_cmd / len(cmd), 4) if cmd else None,
                "noncommand_clips": len(non), "noncommand_moved": sum(r["final"] != "" for r in non),
                "wrong": wrong, "wrong_rate_ci95": [round(lo, 5), round(hi, 5)] if lo is not None else None,
                "gate_pass": round(sum(r["gate"] == "1" for r in sub) / len(sub), 4) if sub else None,
                "asr_mean_s": round(statistics.fmean(float(r["asr_s"]) for r in sub), 3) if sub else None}

    for cond in order:
        sub = [r for r in rows if (f"{r['noise']} {r['snr_db']} dB" if r["snr_db"] else r["noise"]) == cond]
        if sub:
            summary["by_condition"][cond] = block(sub)
    summary["overall"] = block(rows)
    summary["by_voice"] = {v: block([r for r in rows if r["voice"] == v]) for v in sorted({r["voice"] for r in rows})}
    summary["by_handled_by"] = {k: block([r for r in rows if r["handled_by"] == k])
                                for k in sorted({r["handled_by"] for r in rows if r["handled_by"]})}
    summary["by_rate"] = {k: block([r for r in rows if r["rate"] == k]) for k in SPEAK_RATES}
    summary["wrong_examples"] = [{k: r[k] for k in ("phrase", "noise", "snr_db", "transcript", "final", "source")}
                                 for r in rows if r["outcome"] == "wrong"][:50]
    json_path = OUT_DIR / f"stress_summary_{stamp}.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n================ SUMMARY ================")
    print(f"{'condition':14s} {'n':>5s} {'cmd acc':>8s} {'wrong':>6s} {'gate':>6s} {'ASR s':>6s}")
    for cond, b in summary["by_condition"].items():
        acc = f"{100 * b['command_accuracy']:.1f}%" if b["command_accuracy"] is not None else "-"
        print(f"{cond:14s} {b['n']:5d} {acc:>8s} {b['wrong']:6d} {100 * b['gate_pass']:5.1f}% {b['asr_mean_s']:6.2f}")
    o = summary["overall"]
    lo, hi = o["wrong_rate_ci95"]
    print(f"\nOverall: {o['n']} recognitions, {o['wrong']} wrong commands (95% CI {100 * lo:.2f}-{100 * hi:.2f} %), "
          f"{o['noncommand_moved']} of {o['noncommand_clips']} non-command clips produced a command.")
    print(f"Saved {csv_path}\n      {json_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="speak every phrase with the Windows voices")
    p_run = sub.add_parser("run", help="run all clips and noise conditions through the pipeline")
    p_run.add_argument("--llm", action="store_true", help="let the local Qwen model try what the rules miss")
    p_run.add_argument("--llm-model", default="", help="Ollama model for --llm (default: config.json)")
    p_run.add_argument("--max-minutes", type=float, default=0, help="stop after this many minutes (0 = all)")
    p_run.add_argument("--seed", type=int, default=1)
    p_run.add_argument("--audio-dir", default="", help="folder with phrases.csv and the clips (default stress_audio)")
    p_run.add_argument("--noise-repeats", type=int, default=1,
                       help="noisy copies per clip and condition, each with fresh noise (default 1)")
    args = parser.parse_args()
    generate(args) if args.cmd == "generate" else run(args)


if __name__ == "__main__":
    main()
