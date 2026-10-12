"""Background microphone -> faster-whisper (small.en, offline) loop.

Uses tello_app.speech (recorder + ASR). Every transcript is tagged with a
generation number; the GUI bumps the generation on stop / mode switch / mic off
so a sentence recorded before that can never be executed afterwards.
"""
from pathlib import Path
import threading

from . import ROOT

PROMPTS = {
    "flight": "Drone voice command, for example: take off. Move forward 50 centimeters. Turn left 90 degrees. Land.",
    "test": "Motor test voice command, for example: power on. Power off.",
}


def flat_model_dir(config):
    """Plain copy of the model (model.bin + tokenizer, no Hugging Face cache layout).
    Used by the packaged exe: one copy of the weights instead of blobs + snapshots."""
    configured = Path(config.whisper_dir)
    primary = configured if configured.is_absolute() else ROOT / configured
    flat = primary / f"faster-whisper-{config.asr_model}"
    return flat if (flat / "model.bin").is_file() else None


def resolve_model(config):
    """-> (model name or path for WhisperModel, download_root, local_files_only)."""
    flat = flat_model_dir(config)
    if flat is not None:
        return str(flat), None, True
    folder, local_only = find_whisper_dir(config)
    return config.asr_model, str(folder), local_only


def find_whisper_dir(config):
    """Return (directory, local_only). Searches <project>/<whisper_dir>, then the old V2-era cache."""
    configured = Path(config.whisper_dir)
    primary = configured if configured.is_absolute() else ROOT / configured
    for candidate in (primary, ROOT.parent / "llm_drone/.cache/whisper"):
        if candidate.is_dir() and any(p.is_dir() for p in candidate.glob(f"*{config.asr_model}*")):
            return candidate, True
    if config.asr_allow_download:
        primary.mkdir(parents=True, exist_ok=True)
        return primary, False
    raise RuntimeError(f"Whisper model {config.asr_model} not found in {primary}. "
                       "Run: python tools/check_setup.py --download")


class VoiceWorker:
    def __init__(self, config, mode_getter, on_text, on_status, factory=None):
        self.config = config
        self.mode_getter = mode_getter
        self.on_text = on_text
        self.on_status = on_status
        self.factory = factory or self._default_factory
        self.enabled = threading.Event()
        self.closing = threading.Event()
        self.cancel = threading.Event()
        self.generation = 0
        self.thread = threading.Thread(target=self._run, name="tt-asr", daemon=True)
        self.thread.start()

    def _default_factory(self):
        from .speech.asr import FasterWhisperASR
        from .speech.microphone import MicrophoneRecorder
        model, root, local_only = resolve_model(self.config)
        asr = FasterWhisperASR(model_name=model, device=self.config.asr_device,
                               compute_type=self.config.asr_compute_type, language="en",
                               local_files_only=local_only, download_root=root)
        asr.load()
        recorder = MicrophoneRecorder(device=self.config.audio_device, speech_timeout_sec=8.0,
                                      max_record_sec=10.0)
        return asr, recorder

    def set_enabled(self, on):
        self.generation += 1
        if on:
            self.enabled.set()
        else:
            self.enabled.clear()
            self.cancel.set()

    def invalidate(self):
        """Drop whatever is being recorded/recognised right now."""
        self.generation += 1
        self.cancel.set()

    def close(self):
        self.closing.set()
        self.enabled.clear()
        self.cancel.set()

    def _accept(self, result):
        import math
        return (bool(result.text.strip()) and result.language == "en"
                and math.isfinite(result.average_log_probability)
                and result.average_log_probability >= self.config.asr_min_log_probability
                and 0 <= result.no_speech_probability <= self.config.asr_max_no_speech_probability)

    def _run(self):
        engine = None
        while not self.closing.wait(0.05):
            if not self.enabled.is_set():
                continue
            try:
                if engine is None:
                    self.on_status("loading", "Loading Whisper small.en offline… (buttons still work)")
                    engine = self.factory()
                if not self.enabled.is_set():
                    continue
                asr, recorder = engine
                generation = self.generation
                self.cancel = threading.Event()
                self.on_status("calibrating", "Calibrating background noise, please stay quiet…")
                try:
                    audio = recorder.record(on_ready=lambda _t: self.on_status("listening", "SPEAK now"),
                                            cancel_event=self.cancel)
                except Exception as exc:
                    if "no speech" in str(exc) or "cancelled" in str(exc):
                        continue
                    raise
                if generation != self.generation or not self.enabled.is_set():
                    continue
                self.on_status("recognizing", "Recognising…")
                asr.initial_prompt = PROMPTS.get(self.mode_getter(), "")
                result = asr.transcribe(audio)
                if generation != self.generation or not self.enabled.is_set():
                    continue
                if not self._accept(result):
                    self.on_status("listening", "Didn't catch that (silence or low confidence) - please repeat")
                    continue
                self.on_text(result.text.strip(), generation)
            except Exception as exc:
                self.enabled.clear()
                engine = None if "load" in str(exc) else engine
                self.on_status("error", f"Voice error: {exc}")
