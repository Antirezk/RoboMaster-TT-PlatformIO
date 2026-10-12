"""Offline speech input: microphone recording + faster-whisper transcription."""


class SpeechError(RuntimeError):
    """Microphone or speech-recognition failure."""
