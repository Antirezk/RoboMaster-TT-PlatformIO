from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.exceptions import TaskParseError
from models.task import Task, TaskType
from voice.models import TranscriptionResult


class VoiceCommandLoop:
    CONFIRM_TASKS = {
        TaskType.TAKEOFF, TaskType.SEARCH_MISSION_PAD, TaskType.GOTO_MISSION_PAD,
    }

    def __init__(self, system: Any, recorder: Any, asr: Any,
                 minimum_language_probability: float = 0.50,
                 minimum_average_log_probability: float = -1.0,
                 maximum_no_speech_probability: float = 0.60,
                 input_fn: Callable[[str], str] = input,
                 output_fn: Callable[[str], None] = print) -> None:
        self.system, self.recorder, self.asr = system, recorder, asr
        self.minimum_language_probability = minimum_language_probability
        self.minimum_average_log_probability = minimum_average_log_probability
        self.maximum_no_speech_probability = maximum_no_speech_probability
        self.input_fn, self.output_fn = input_fn, output_fn

    def run(self) -> None:
        self.output_fn("正在加载本地语音识别模型...")
        load = getattr(self.asr, "load", None)
        if load is not None:
            load()
        self.output_fn("语音模式：按 Enter 开始一次录音；输入 q 后 Enter 退出。")
        while True:
            choice = self.input_fn("Voice > ").strip().lower()
            if choice in {"q", "quit", "exit", "退出"}:
                return
            try:
                self.handle_once()
            except Exception as exc:
                self.system.logger.error("VOICE ERROR: %s", exc)

    def handle_once(self) -> Task | None:
        self.output_fn("环境噪声校准中，请暂时保持安静...")
        audio = self.recorder.record(
            on_ready=lambda threshold: self.output_fn(
                f"请说话（静音后自动停止，VAD threshold={threshold:.4f}）..."
            )
        )
        result = self.asr.transcribe(audio)
        self.system.logger.info(
            "ASR text=%r language=%s lang_prob=%.3f avg_logprob=%.3f no_speech=%.3f duration=%.2fs",
            result.text, result.language, result.language_probability,
            result.average_log_probability, result.no_speech_probability, result.duration_sec,
        )
        rejection = self.rejection_reason(result)
        if rejection:
            self.output_fn(f"语音识别已拒绝：{rejection}")
            return None
        self.output_fn(f"识别文字：{result.text}")
        try:
            task = self.system.parse_command(result.text)
        except TaskParseError as exc:
            self.output_fn(f"任务解析已拒绝：{exc}")
            return None
        self.output_fn(self.describe_task(task))
        if task.type in self.CONFIRM_TASKS:
            answer = self.input_fn("确认执行？输入 YES：").strip().upper()
            if answer != "YES":
                self.system.logger.warning("VOICE TASK cancelled by confirmation gate")
                self.output_fn("已取消，不执行。")
                return None
        self.system.execute_task(task)
        return task

    def rejection_reason(self, result: TranscriptionResult) -> str | None:
        if not result.text.strip():
            return "没有识别到文字"
        if result.language_probability < self.minimum_language_probability:
            return "语言识别置信度过低"
        if result.average_log_probability < self.minimum_average_log_probability:
            return "文字识别置信度过低"
        if result.no_speech_probability > self.maximum_no_speech_probability:
            return "录音更可能是静音或噪声"
        return None

    @staticmethod
    def describe_task(task: Task) -> str:
        return (
            f"结构化任务：type={task.type.value}, pad_id={task.pad_id}, "
            f"final_action={task.final_action}"
        )
