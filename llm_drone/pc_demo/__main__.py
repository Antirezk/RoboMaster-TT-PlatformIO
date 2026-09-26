import argparse
from dataclasses import replace
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import sys
import time

from .config import Config
from .console import ConsoleFilter
from .controller import Controller
from .network import CommandChannel, LOG, StateChannel
from .parser import parse
from .language import LanguageParser, submit_text
from .simulator import Simulator


def accept_transcription(result, config):
    return (bool(result.text.strip()) and result.language == "en"
            and math.isfinite(result.average_log_probability)
            and result.average_log_probability >= config.asr_min_log_probability
            and math.isfinite(result.no_speech_probability)
            and 0 <= result.no_speech_probability <= config.asr_max_no_speech_probability)


def main(argv=None):
    # Use the same encoding for Windows terminals, redirected output and log previews.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    parser = argparse.ArgumentParser(description="PC local English voice -> direct TT SDK UDP")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--voice", action="store_true", help="Enter records one utterance; typed commands also work")
    parser.add_argument("--dry-run", action="store_true", help="connect and receive telemetry; block flight")
    parser.add_argument("--simulate", action="store_true", help="use loopback UDP, never contact real drone")
    parser.add_argument("--sim-pad", type=int, choices=range(1, 9), default=1, help="visible simulated Mission Pad ID (1..8)")
    parser.add_argument("--demo", action="store_true", help="scripted text sequence; requires --simulate")
    parser.add_argument("--prepare-model", action="store_true", help="download/cache ASR while online; no drone connection")
    parser.add_argument("--list-audio-devices", action="store_true")
    parser.add_argument("--audio-device", type=int)
    parser.add_argument("--telemetry", action="store_true", help="show telemetry every second; default shows connection changes only")
    parser.add_argument("--no-llm", action="store_true", help="disable local language fallback")
    parser.add_argument("--check-llm", action="store_true", help="check installed local model without connecting to the drone")
    parser.add_argument("--log-file", type=Path, default=Path(__file__).resolve().parents[1] / "logs/pc_demo.log")
    args = parser.parse_args(argv)
    if args.demo and not args.simulate:
        parser.error("--demo requires --simulate")
    if args.sim_pad != 1 and not args.simulate:
        parser.error("--sim-pad requires --simulate")
    if args.list_audio_devices:
        import sounddevice
        print(sounddevice.query_devices())
        return 0
    config = Config.load(args.config)
    if args.no_llm:
        config = replace(config, llm_enabled=False)
    language = LanguageParser(config)
    if args.check_llm:
        healthy, message = language.client.health_check()
        print(message)
        return 0 if healthy else 1
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    console.addFilter(ConsoleFilter(telemetry=args.telemetry))
    file = RotatingFileHandler(args.log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    file.setFormatter(formatter)
    # main() can also be used repeatedly by integration tests or a launcher.
    for old_handler in LOG.handlers[:]:
        LOG.removeHandler(old_handler)
        old_handler.close()
    for handler in (console, file):
        LOG.addHandler(handler)
    LOG.setLevel(logging.DEBUG)
    LOG.info("[运行模式] %s", "本机模拟器（不会控制真实无人机）" if args.simulate
             else "真机只读测试（飞行指令会被拦截）" if args.dry_run
             else "真机控制（识别成功后直接执行）")
    LOG.info("[显示说明] 识别文字 → 指令预览 → 已发送 → 收到回复；输入 status 查看遥测")
    if config.llm_enabled:
        LOG.info("[语言理解] 常用命令直接解析；口语/纠错交给本地 %s，模型建议需 YES 确认", config.llm_model)
    asr = recorder = None
    if args.voice or args.prepare_model:
        from voice.asr_backend import FasterWhisperASR
        from voice.microphone import MicrophoneRecorder
        asr = FasterWhisperASR(model_name=config.asr_model, device=config.asr_device,
                               compute_type=config.asr_compute_type, language="en",
                               initial_prompt=config.asr_initial_prompt,
                               local_files_only=not args.prepare_model,
                               download_root=str(Path(__file__).resolve().parents[1] / ".cache/whisper"))
        LOG.info("ASR loading %s offline=%s", config.asr_model, not args.prepare_model)
        asr.load()  # Load BEFORE connecting: no model download/stall with a flying drone.
        if args.prepare_model:
            LOG.info("ASR cached. Run --simulate --voice to test microphone and English recognition.")
            return 0
        recorder = MicrophoneRecorder(device=args.audio_device if args.audio_device is not None else config.audio_device)
    simulator = commands = state = controller = None
    try:
        if args.simulate:
            simulator = Simulator(config, pad_id=args.sim_pad)
            config = simulator.config
            simulator.start()
            LOG.info("SIMULATION: loopback UDP only; synthesized Pad %s, no real flight", args.sim_pad)
        state = StateChannel(config)
        state.start()
        commands = CommandChannel(config)
        controller = Controller(config, commands, state, args.dry_run)
        controller.start()
        # Wait briefly for first complete state without holding the command lock.
        startup_sequence = state.snapshot()[2]
        deadline = time.monotonic() + config.command_timeout_sec
        while state.snapshot()[2] <= startup_sequence and time.monotonic() < deadline:
            with state.condition:
                state.condition.wait(timeout=0.1)
        state.fresh()
        if args.demo:
            for phrase in ("battery", "take off", "status", f"land on mission pad {args.sim_pad}"):
                LOG.info("\n---------- 本次输入 ----------\n[演示文字] %s", phrase)
                controller.execute(parse(phrase))
            return 0
        print("命令：take off | land | stop | battery | status | land on mission pad one")
        print("输入 q 退出；如本会话可能在飞行会尝试降落。Ctrl+C 同样退出。")
        if args.voice:
            print("按 Enter 开始录音，看到【请说话 / SPEAK now】后说一句英文；也可直接输入文字。")
        while True:
            try:
                text = input("TT > ").strip()
                if text.lower() in {"q", "quit", "exit"}:
                    break
                if not text and args.voice:
                    LOG.info("\n---------- 本次语音 ----------\n[麦克风] 正在校准环境噪声，请先保持安静…")
                    audio = recorder.record(on_ready=lambda threshold: LOG.info("[请说话 / SPEAK now] 说完停顿一下，自动结束录音"))
                    LOG.info("[识别中] 录音结束，正在本地识别英文…")
                    result = asr.transcribe(audio)
                    LOG.info("[识别文字] %s", result.text or "（没有识别到文字）")
                    LOG.debug("ASR text=%r avg_logprob=%.3f no_speech=%.3f duration=%.2fs",
                             result.text, result.average_log_probability,
                             result.no_speech_probability, result.duration_sec)
                    if not accept_transcription(result, config):
                        raise ValueError("识别结果为空、置信度不足或不是英文：没有提交控制指令")
                    text = result.text
                elif text:
                    LOG.info("\n---------- 本次输入 ----------\n[键盘文字] %s", text)
                if text:
                    LOG.debug("TEXT %r", text)
                    submit_text(text, language, controller, input_fn=input)
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as exc:
                LOG.error("[结果] 拒绝或执行失败：%s（实际发送情况请看 [已发送] 记录）", exc)
                if commands.poisoned:
                    return 1
        return 0
    finally:
        try:
            if controller:
                controller.close()
            else:
                if commands:
                    commands.close()
                if state:
                    state.close()
        finally:
            if simulator:
                simulator.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        LOG.error("STARTUP/DEMO failed: %s", exc)
        raise SystemExit(1)
