from __future__ import annotations

import argparse

from core.drone_system import DroneSystem
from voice.asr_backend import FasterWhisperASR
from voice.microphone import MicrophoneRecorder, list_input_devices
from voice.voice_command_loop import VoiceCommandLoop


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RoboMaster TT Mission Pad controller")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true", help="run without drone hardware")
    modes.add_argument("--dry-run", action="store_true", help="connect to TT but block flight commands")
    parser.add_argument(
        "--direct-wifi", action="store_true",
        help="bypass ESP32 and use DJITelloPy directly (disables ToF arbitration)",
    )
    parser.add_argument("--config", help="path to an alternate YAML configuration")
    parser.add_argument("--voice", action="store_true", help="use local microphone and ASR input")
    parser.add_argument(
        "--audio-device", help="sounddevice input index or exact device name (overrides config)"
    )
    parser.add_argument(
        "--list-audio-devices", action="store_true", help="list microphone devices and exit"
    )
    return parser


def parse_audio_device(value: object) -> int | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    try:
        return int(text)
    except ValueError:
        return text


def run_voice_mode(system: DroneSystem, cli_audio_device: str | None) -> None:
    cfg = system.config.get("voice", {})
    backend = cfg.get("asr_backend", "faster_whisper")
    if backend != "faster_whisper":
        raise ValueError(f"unsupported voice.asr_backend: {backend}")
    recorder = MicrophoneRecorder(
        sample_rate=cfg.get("sample_rate", 16000),
        block_ms=cfg.get("block_ms", 30),
        calibration_sec=cfg.get("calibration_sec", 0.4),
        pre_roll_sec=cfg.get("pre_roll_sec", 0.25),
        speech_timeout_sec=cfg.get("speech_timeout_sec", 3.0),
        silence_stop_sec=cfg.get("silence_stop_sec", 0.8),
        max_record_sec=cfg.get("max_record_sec", 8.0),
        minimum_rms=cfg.get("minimum_rms", 0.012),
        noise_multiplier=cfg.get("noise_multiplier", 3.0),
        device=parse_audio_device(
            cli_audio_device if cli_audio_device is not None else cfg.get("audio_device")
        ),
    )
    asr = FasterWhisperASR(
        model_name=cfg.get("model", "medium"),
        device=cfg.get("device", "cpu"),
        compute_type=cfg.get("compute_type", "int8"),
        language=cfg.get("language", "zh"),
        beam_size=cfg.get("beam_size", 5),
        initial_prompt=cfg.get("initial_prompt", ""),
    )
    VoiceCommandLoop(
        system, recorder, asr,
        minimum_language_probability=cfg.get("minimum_language_probability", 0.50),
        minimum_average_log_probability=cfg.get("minimum_average_log_probability", -1.0),
        maximum_no_speech_probability=cfg.get("maximum_no_speech_probability", 0.60),
    ).run()


def run_text_mode(system: DroneSystem) -> None:
    while True:
        try:
            command = input("Drone > ").strip()
        except EOFError:
            break
        if command.lower() in {"exit", "quit", "退出"}:
            break
        if not command:
            continue
        try:
            system.execute_command(command)
        except Exception as exc:
            system.logger.error("ERROR: %s", exc)


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.list_audio_devices:
        devices = list_input_devices()
        if not devices:
            print("未发现可用的麦克风输入设备。")
        for index, name, channels in devices:
            print(f"{index}: {name} (input channels={channels})")
        return
    system = DroneSystem(
        mock=args.mock, dry_run=args.dry_run, direct_wifi=args.direct_wifi,
        config_path=args.config,
    )
    try:
        system.initialize()
        if args.voice:
            run_voice_mode(system, args.audio_device)
        else:
            run_text_mode(system)
    except KeyboardInterrupt:
        system.logger.warning("Keyboard interrupt: safe shutdown requested")
    finally:
        system.shutdown()


if __name__ == "__main__":
    main()
