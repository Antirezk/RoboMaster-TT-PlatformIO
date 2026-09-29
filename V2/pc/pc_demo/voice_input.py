"""Hands-free, complete-utterance local input shared by both PC entry points."""
import math
from pathlib import Path
import re

from .network import LOG
from voice.microphone import VoiceInputError


def normalize_phrase(text):
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")


def accept_transcription(result, config):
    return (bool(result.text.strip()) and result.language == "en"
            and math.isfinite(result.average_log_probability)
            and result.average_log_probability >= config.asr_min_log_probability
            and math.isfinite(result.no_speech_probability)
            and 0 <= result.no_speech_probability <= config.asr_max_no_speech_probability)


class VoiceInput:
    def __init__(self, config, asr, recorder):
        self.config, self.asr, self.recorder = config, asr, recorder

    def read(self):
        LOG.info("[麦克风] 校准中请安静，等 SPEAK now 再说话（无需回车）")
        try:
            audio = self.recorder.record(on_ready=lambda _: LOG.info("[请说话 / SPEAK now] 说一句英文，停顿后自动识别"))
        except VoiceInputError as exc:
            if str(exc) == "no speech detected before timeout":
                return ""
            raise
        LOG.info("[识别中] 正在本地识别…")
        result = self.asr.transcribe(audio)
        LOG.info("[识别文字] %s", result.text or "（没有识别到文字）")
        LOG.debug("ASR text=%r avg_logprob=%.3f no_speech=%.3f duration=%.2fs",
                  result.text, result.average_log_probability, result.no_speech_probability, result.duration_sec)
        if not accept_transcription(result, self.config):
            LOG.info("[未执行] 空白或低置信度识别，请重新说话")
            return ""
        return result.text

    def confirm(self, _prompt):
        LOG.info("[语音确认] 核对候选指令后说 confirm；说 cancel 取消，exit 退出")
        phrase = normalize_phrase(self.read())
        if phrase in {"exit", "quit", "exit program"}:
            raise EOFError("voice exit")
        return "YES" if phrase in {"confirm", "confirm command"} else "NO"


def create_voice_input(config, audio_device=None, *, motor_test=False):
    from voice.asr_backend import FasterWhisperASR
    from voice.microphone import MicrophoneRecorder
    # Do not seed the transcript with an arming phrase that could be hallucinated.
    prompt = "Motor test commands: power on, power off, motor on, motor off, stop, battery, cancel, exit." if motor_test else config.asr_initial_prompt
    asr = FasterWhisperASR(model_name=config.asr_model, device=config.asr_device,
                          compute_type=config.asr_compute_type, language="en", initial_prompt=prompt,
                          local_files_only=True,
                          download_root=str(Path(__file__).resolve().parents[3] / "llm_drone/.cache/whisper"))
    LOG.info("[语音模型] 正在离线加载 %s", config.asr_model)
    asr.load()
    recorder = MicrophoneRecorder(device=audio_device if audio_device is not None else config.audio_device,
                                  speech_timeout_sec=8.0, max_record_sec=16.0)
    return VoiceInput(config, asr, recorder)
