#!/usr/bin/env python3
"""One-shot voice-pipeline comparison: rules only, and rules + every installed Ollama model.

Put this file in the folder that holds phrases.csv and the folder of WAV files, then:

    python run_compare.py                    # rules only + every model installed in Ollama
    python run_compare.py --no-llm           # rules only
    python run_compare.py --models qwen2.5:3b gemma3:4b
    python run_compare.py --noise-repeats 5  # more noisy copies (longer run)

Steps
  1. Speech recognition (the slow part, done once): every clip is passed through the app's own Whisper
     model, settings, initial prompt and confidence gate, clean and with white noise and babble at
     20, 10, 5 and 0 dB SNR (each noisy condition repeated with fresh noise). Results are cached in
     asr_cache.csv, so a later run with other models skips this step.
  2. Interpretation, once per configuration: the app's rule grammar; for configurations with a model,
     every sentence the rules do not understand goes to that model with the app's prompt and schema,
     and the answer must pass the app's anchor checks. Answers are cached per model and sentence.
Outputs (folder results_<time>/): outcomes.csv (every clip x configuration), summary.json and
summary.md (tables for the report). The drone is never contacted.
"""
import argparse
import zlib
import csv
import json
import math
import platform
import random
import socket
import statistics
import sys
import time
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_APP = Path(r"C:\Users\Antirez\Documents\PlatformIO\Projects\Robomaster\Latest")
RATE = 16000
MODE = "flight"
SNRS = (20, 10, 5, 0)
CONDITIONS = [("clean", None)] + [(k, s) for k in ("white", "babble") for s in SNRS]


# ----------------------------------------------------------------- audio
def load_audio(path):
    import numpy as np
    try:
        with wave.open(str(path), "rb") as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            data = w.readframes(w.getnframes())
        if width != 2:
            raise wave.Error("not 16-bit")
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if rate != RATE:
            t_old = np.arange(len(x)) / rate
            t_new = np.arange(int(len(x) * RATE / rate)) / RATE
            x = np.interp(t_new, t_old, x).astype(np.float32)
        return x
    except (wave.Error, EOFError):
        from faster_whisper.audio import decode_audio
        return np.asarray(decode_audio(str(path), sampling_rate=RATE), dtype=np.float32)


def speech_part(x, thr=0.02):
    """Samples between the first and last sample above thr (for SNR and babble); whole clip if silent."""
    import numpy as np
    idx = np.where(np.abs(x) > thr)[0]
    return x[idx[0]:idx[-1] + 1] if len(idx) > RATE // 10 else x


def rms(x):
    import numpy as np
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def add_noise(x, kind, snr_db, babble_pool, rng):
    import numpy as np
    if kind == "clean":
        return x
    speech_rms = rms(speech_part(x))
    if kind == "white":
        noise = rng.normal(0.0, 1.0, len(x)).astype(np.float32)
    else:
        noise = np.zeros(len(x), np.float32)
        for k in rng.choice(len(babble_pool), size=4, replace=False):
            src = babble_pool[k]
            reps = int(math.ceil(len(x) / len(src))) + 1
            start = int(rng.integers(0, len(src)))
            noise += np.tile(src, reps)[start:start + len(x)]
    noise *= speech_rms / (rms(noise) * 10 ** (snr_db / 20.0) + 1e-12)
    y = x + noise
    peak = float(np.abs(y).max())
    return y / peak * 0.95 if peak > 0.95 else y


# ----------------------------------------------------------------- helpers
def wilson(k, n, z=1.959964):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 5), round(min(1.0, c + h), 5)]


def pct(k, n):
    return f"{100 * k / n:.1f}%" if n else "-"


def installed_models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
        data = json.loads(resp.read().decode())
    return sorted(m["name"] for m in data.get("models", []) if "embed" not in m["name"].lower())


def find_audio_dir(index_rows):
    first = index_rows[0]["file"]
    for d in [HERE] + sorted(p for p in HERE.iterdir() if p.is_dir()):
        if (d / first).is_file():
            return d
    raise SystemExit(f"Cannot find {first} next to this script or in a sub-folder of {HERE}")


def raw_command(data, config):
    if not isinstance(data, dict):
        return "(invalid)"
    a = data.get("action")
    if not a or a == "reject":
        return ""
    d, v = data.get("direction"), data.get("value")
    if a == "move":
        return f"{d} {v}"
    if a == "turn":
        return f"{d} {v if v is not None else config.turn_default_deg}"
    if a == "speed":
        return f"speed {v}"
    if a == "battery":
        return "battery?"
    return str(a)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default=str(DEFAULT_APP), help="the Latest folder of the project")
    ap.add_argument("--no-llm", action="store_true", help="rules only")
    ap.add_argument("--models", nargs="*", default=None, help="Ollama models (default: all installed)")
    ap.add_argument("--noise-repeats", type=int, default=3, help="noisy copies per clip and condition (default 3)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--llm-timeout", type=float, default=30.0)
    args = ap.parse_args()

    app = Path(args.app)
    if not (app / "tello_app").is_dir():
        raise SystemExit(f"tello_app not found in {app}; pass --app <path to Latest>")
    sys.path.insert(0, str(app))
    import numpy as np
    from tello_app.config import AppConfig
    from tello_app.llm import QwenInterpreter
    from tello_app.parser import NotUnderstood, parse
    from tello_app.sdk import SDKError
    from tello_app.speech.asr import FasterWhisperASR
    from tello_app.voice import PROMPTS, resolve_model

    index = list(csv.DictReader(open(HERE / "phrases.csv", encoding="utf-8-sig")))
    audio_dir = find_audio_dir(index)
    index = [r for r in index if (audio_dir / r["file"]).is_file()]
    print(f"{len(index)} clips in {audio_dir}")
    config = AppConfig.load()

    # ---------- 1. speech recognition (cached)
    cache_path = HERE / "asr_cache.csv"
    asr_fields = ["key", "file", "phrase", "expected", "handled_by", "noise", "snr_db", "rep", "transcript",
                  "gate", "asr_s", "avg_logprob", "no_speech"]
    cache = {}
    if cache_path.exists():
        for r in csv.DictReader(open(cache_path, encoding="utf-8")):
            cache[r["key"]] = r
    jobs = []
    for c in index:
        for kind, snr in CONDITIONS:
            for rep in range(1 if kind == "clean" else max(1, args.noise_repeats)):
                jobs.append((c, kind, snr, rep))
    todo = [j for j in jobs if f"{j[0]['file']}|{j[1]}|{j[2]}|{j[3]}|{args.seed}" not in cache]
    print(f"Speech recognition: {len(jobs)} recognitions, {len(jobs) - len(todo)} already cached")
    if todo:
        audio = {c["file"]: load_audio(audio_dir / c["file"]) for c in index}
        babble_pool = [speech_part(audio[c["file"]]) for c in index if c["expected"] == ""] \
            or [speech_part(a) for a in audio.values()]
        model, root, local_only = resolve_model(config)
        asr = FasterWhisperASR(model_name=model, device=config.asr_device, compute_type=config.asr_compute_type,
                               language="en", local_files_only=local_only, download_root=root)
        asr.initial_prompt = PROMPTS.get(MODE, "")
        asr.load()
        new_file = not cache_path.exists()
        f = open(cache_path, "a", newline="", encoding="utf-8")
        w = csv.DictWriter(f, fieldnames=asr_fields)
        if new_file:
            w.writeheader()
        t0 = time.time()
        try:
            for i, (c, kind, snr, rep) in enumerate(todo, 1):
                key = f"{c['file']}|{kind}|{snr}|{rep}|{args.seed}"
                seed = zlib.crc32(key.encode())
                rng = np.random.default_rng(seed)
                x = add_noise(audio[c["file"]], kind, snr, babble_pool, rng)
                t = time.perf_counter()
                res = asr.transcribe(x)
                dt = time.perf_counter() - t
                ok = (bool(res.text.strip()) and res.language == "en" and math.isfinite(res.average_log_probability)
                      and res.average_log_probability >= config.asr_min_log_probability
                      and 0 <= res.no_speech_probability <= config.asr_max_no_speech_probability)
                row = {"key": key, "file": c["file"], "phrase": c["phrase"], "expected": c["expected"],
                       "handled_by": c.get("handled_by", ""), "noise": kind, "snr_db": "" if snr is None else snr,
                       "rep": rep, "transcript": res.text.strip(), "gate": int(ok), "asr_s": round(dt, 4),
                       "avg_logprob": round(res.average_log_probability, 3)
                       if math.isfinite(res.average_log_probability) else "",
                       "no_speech": round(res.no_speech_probability, 3)}
                w.writerow(row); cache[key] = {k: str(v) for k, v in row.items()}
                if i % 25 == 0 or i == len(todo):
                    f.flush()
                    el = time.time() - t0
                    print(f"  ASR {i}/{len(todo)}  {el / 60:4.1f} min, ~{el / i * (len(todo) - i) / 60:4.1f} min left",
                          flush=True)
        finally:
            f.close()
    rows = [cache[f"{c['file']}|{k}|{s}|{rep}|{args.seed}"] for c, k, s, rep in jobs
            if f"{c['file']}|{k}|{s}|{rep}|{args.seed}" in cache]

    # ---------- 2. configurations
    configs = ["rules"]
    if not args.no_llm:
        try:
            models = args.models if args.models else installed_models()
        except Exception as exc:
            print(f"Ollama not reachable ({exc}) - rules only")
            models = []
        configs += models
    print("Configurations:", ", ".join(configs))

    out_dir = HERE / f"results_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir()
    ofields = ["config", "file", "phrase", "expected", "handled_by", "noise", "snr_db", "rep", "transcript", "gate",
               "rule_output", "llm_raw", "final", "source", "outcome", "llm_s"]
    of = open(out_dir / "outcomes.csv", "w", newline="", encoding="utf-8")
    ow = csv.DictWriter(of, fieldnames=ofields); ow.writeheader()

    rule_cache = {}
    results = {}
    for cfg in configs:
        llm = None if cfg == "rules" else QwenInterpreter(cfg, args.llm_timeout)
        llm_cache, llm_times, failed = {}, [], 0
        if llm is not None:
            t = time.perf_counter()
            try:
                llm.request("take off", MODE)
            except SDKError as exc:
                print(f"\n{cfg}: cannot be used ({str(exc)[:100]}) - skipped")
                results[cfg] = {"error": str(exc)[:200]}
                continue
            print(f"\n=== {cfg}  (load {time.perf_counter() - t:.1f} s)")
        out = []
        shown_wrong = 0
        for i, r in enumerate(rows, 1):
            text, rule_out, raw, final, source, llm_s = r["transcript"], "", "", "", "", ""
            if r["gate"] == "1":
                if text not in rule_cache:
                    try:
                        it = parse(text, MODE, config)
                        rule_cache[text] = ("ok", it.sdk or it.action)
                    except NotUnderstood:
                        rule_cache[text] = ("nu", "")
                    except SDKError:
                        rule_cache[text] = ("refused", "")
                status, cmd = rule_cache[text]
                rule_out = cmd if status == "ok" else f"({status})"
                if status == "ok":
                    final, source = cmd, "rules"
                elif status == "nu" and llm is not None:
                    if text not in llm_cache:
                        t = time.perf_counter()
                        try:
                            data = llm.request(text, MODE)
                        except SDKError:
                            data, failed = None, failed + 1
                        dt = time.perf_counter() - t
                        llm_times.append(dt)
                        anchored = ""
                        if data is not None:
                            try:
                                it = QwenInterpreter(cfg, request=lambda _t, _m: data).interpret(text, MODE, config)
                                anchored = it.sdk or it.action
                            except SDKError:
                                anchored = ""
                        llm_cache[text] = (raw_command(data, config) if data is not None else "(error)", anchored,
                                           round(dt, 3))
                    raw, final, llm_s = llm_cache[text]
                    source = "llm" if final else ""
            exp = r["expected"]
            outcome = "correct" if final == exp else ("missed" if final == "" else "wrong")
            row = {"config": cfg, "file": r["file"], "phrase": r["phrase"], "expected": exp,
                   "handled_by": r["handled_by"], "noise": r["noise"], "snr_db": r["snr_db"], "rep": r["rep"],
                   "transcript": text, "gate": r["gate"], "rule_output": rule_out, "llm_raw": raw, "final": final,
                   "source": source, "outcome": outcome, "llm_s": llm_s}
            ow.writerow(row); out.append(row)
            if outcome == "wrong":
                shown_wrong += 1
                if shown_wrong <= 15:
                    print(f"  WRONG [{cfg}] \"{r['phrase']}\" heard \"{text}\" -> {final}")
            if llm is not None and i % 500 == 0:
                print(f"  {i}/{len(rows)} clips, {len(llm_cache)} distinct sentences sent to the model", flush=True)
        of.flush()

        def block(sub):
            cmd = [x for x in sub if x["expected"]]
            non = [x for x in sub if not x["expected"]]
            wrong = sum(x["outcome"] == "wrong" for x in sub)
            raw_wrong = sum(1 for x in sub if x["llm_raw"] not in ("", "(error)", "(invalid)") and x["llm_raw"] != x["expected"])
            return {"n": len(sub), "command_clips": len(cmd),
                    "command_correct": sum(x["outcome"] == "correct" for x in cmd),
                    "noncommand_clips": len(non), "noncommand_moved": sum(x["final"] != "" for x in non),
                    "wrong": wrong, "wrong_ci95": wilson(wrong, len(sub)),
                    "model_proposed_wrong": raw_wrong,
                    "llm_used": sum(x["source"] == "llm" for x in sub)}

        res = {"overall": block(out),
               "by_group": {g: block([x for x in out if x["handled_by"] == g]) for g in ("rules", "llm", "none")},
               "by_condition": {}}
        for kind, snr in CONDITIONS:
            name = kind if snr is None else f"{kind} {snr} dB"
            res["by_condition"][name] = block([x for x in out if x["noise"] == kind and
                                               (x["snr_db"] == "" if snr is None else x["snr_db"] == str(snr))])
        if llm is not None:
            res["llm_calls"] = len(llm_cache)
            res["llm_failures"] = failed
            res["llm_latency_s"] = ({"mean": round(statistics.fmean(llm_times), 3),
                                     "median": round(statistics.median(llm_times), 3),
                                     "max": round(max(llm_times), 3)} if llm_times else None)
        results[cfg] = res
    of.close()

    asr_times = [float(r["asr_s"]) for r in rows]
    summary = {"host": socket.gethostname(), "platform": platform.platform(), "finished": datetime.now().isoformat(),
               "clips": len(index), "recognitions": len(rows), "noise_repeats": args.noise_repeats,
               "asr": {"model": config.asr_model, "mean_s": round(statistics.fmean(asr_times), 3) if asr_times else None,
                       "gate_pass": round(sum(r["gate"] == "1" for r in rows) / len(rows), 4) if rows else None},
               "configs": results}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # ---------- 3. report
    lines = [f"# Voice pipeline comparison ({len(index)} clips, {len(rows)} recognitions, "
             f"{datetime.now():%d %b %Y})", "",
             f"ASR: {config.asr_model}, mean {summary['asr']['mean_s']} s per clip, confidence gate passed "
             f"{100 * summary['asr']['gate_pass']:.1f} %.", "",
             "## By configuration (all conditions)", "",
             "| Configuration | Rule commands correct | LLM-only commands correct | Non-commands that moved | "
             "Wrong commands (95 % CI) | Model proposed wrong | LLM mean s |",
             "|---|---|---|---|---|---|---|"]
    for cfg, res in results.items():
        if "error" in res:
            lines.append(f"| {cfg} | skipped: {res['error'][:60]} | | | | | |")
            continue
        o, g = res["overall"], res["by_group"]
        lo, hi = o["wrong_ci95"]
        lat = res.get("llm_latency_s")
        lines.append(f"| {cfg} | {g['rules']['command_correct']}/{g['rules']['command_clips']} "
                     f"({pct(g['rules']['command_correct'], g['rules']['command_clips'])}) | "
                     f"{g['llm']['command_correct']}/{g['llm']['command_clips']} "
                     f"({pct(g['llm']['command_correct'], g['llm']['command_clips'])}) | "
                     f"{o['noncommand_moved']}/{o['noncommand_clips']} | {o['wrong']}/{o['n']} "
                     f"({100 * lo:.2f}-{100 * hi:.2f} %) | {o['model_proposed_wrong']} | "
                     f"{lat['mean'] if lat else '-'} |")
    lines += ["", "## Rules only, by acoustic condition", "",
              "| Condition | Commands correct | Non-commands that moved | Wrong |", "|---|---|---|---|"]
    for name, b in results["rules"]["by_condition"].items():
        lines.append(f"| {name} | {b['command_correct']}/{b['command_clips']} "
                     f"({pct(b['command_correct'], b['command_clips'])}) | {b['noncommand_moved']}/{b['noncommand_clips']} "
                     f"| {b['wrong']} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nSaved in {out_dir}")


if __name__ == "__main__":
    main()
