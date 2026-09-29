from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.exceptions import DroneError
from voice.models import TranscriptionResult


class FasterWhisperASR:
    """Lazy-loaded, local Faster-Whisper backend for complete utterances."""

    def __init__(self, model_name: str = "medium", device: str = "cpu",
                 compute_type: str = "int8", language: str = "zh",
                 beam_size: int = 5, initial_prompt: str = "",
                 model_factory: Callable[..., Any] | None = None,
                 local_files_only: bool = False,
                 download_root: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.model_factory = model_factory
        self.local_files_only = local_files_only
        self.download_root = download_root
        self._model: Any | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        factory = self.model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise DroneError(
                    "voice mode requires requirements-voice.txt (faster-whisper)"
                ) from exc
            factory = WhisperModel
        try:
            options = {"device": self.device, "compute_type": self.compute_type}
            if self.local_files_only:
                options["local_files_only"] = True
            if self.download_root is not None:
                options["download_root"] = self.download_root
            self._model = factory(self.model_name, **options)
        except Exception as exc:
            raise DroneError(f"failed to load ASR model {self.model_name}: {exc}") from exc

    def transcribe(self, audio: Any) -> TranscriptionResult:
        self.load()
        try:
            segments, info = self._model.transcribe(
                audio,
                language=self.language or None,
                beam_size=self.beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self.initial_prompt or None,
                temperature=0,
            )
            materialized = list(segments)
        except Exception as exc:
            raise DroneError(f"local ASR transcription failed: {exc}") from exc
        text = "".join(segment.text for segment in materialized).strip()
        if materialized:
            average_log_probability = sum(
                float(segment.avg_logprob) for segment in materialized
            ) / len(materialized)
            no_speech_probability = max(
                float(segment.no_speech_prob) for segment in materialized
            )
        else:
            average_log_probability = float("-inf")
            no_speech_probability = 1.0
        duration = float(getattr(info, "duration", 0.0))
        return TranscriptionResult(
            text=text,
            language=str(getattr(info, "language", self.language or "unknown")),
            language_probability=float(getattr(info, "language_probability", 0.0)),
            average_log_probability=average_log_probability,
            no_speech_probability=no_speech_probability,
            duration_sec=duration,
        )
