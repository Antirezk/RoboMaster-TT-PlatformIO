from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float
    average_log_probability: float
    no_speech_probability: float
    duration_sec: float
