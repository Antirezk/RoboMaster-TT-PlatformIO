from __future__ import annotations

import re
from typing import Any

from core.exceptions import TaskParseError
from models.task import Task, TaskType


class TaskParser:
    TERMINAL_PUNCTUATION = re.compile(r"[，。！？,.!?;；:：]+")
    DIRECT_COMMANDS = {
        "起飞": TaskType.TAKEOFF,
        "takeoff": TaskType.TAKEOFF,
        "降落": TaskType.LAND,
        "land": TaskType.LAND,
        "停止": TaskType.STOP,
        "急停": TaskType.STOP,
        "stop": TaskType.STOP,
        "电量": TaskType.BATTERY,
        "查看电量": TaskType.BATTERY,
        "battery": TaskType.BATTERY,
        "状态": TaskType.STATUS,
        "查看状态": TaskType.STATUS,
        "status": TaskType.STATUS,
        "挑战垫": TaskType.PAD_TELEMETRY,
        "查看挑战垫": TaskType.PAD_TELEMETRY,
        "pad": TaskType.PAD_TELEMETRY,
    }
    PAD_PATTERN = re.compile(r"([+-]?\d+|[零一二三四五六七八九])\s*号.*?(?:挑战垫|任务垫)")
    ENGLISH_PAD_PATTERN = re.compile(r"(?:pad|mission\s*pad)\s*([+-]?\d+)", re.IGNORECASE)
    CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client
        self.last_source = "none"

    def parse(self, command: str) -> Task:
        raw = command
        normalized = re.sub(
            r"\s+", "", self.TERMINAL_PUNCTUATION.sub("", command.strip().lower())
        )
        if not normalized:
            raise TaskParseError("command is empty")
        if normalized in self.DIRECT_COMMANDS:
            self.last_source = "mechanical"
            return Task(self.DIRECT_COMMANDS[normalized], raw_command=raw)
        if re.fullmatch(r"(?:请)?(?:马上|立即|现在)?(?:停止|急停)(?:当前)?(?:任务|动作)?", normalized):
            self.last_source = "mechanical"
            return Task(TaskType.STOP, raw_command=raw)
        if re.fullmatch(r"(?:请)?(?:马上|立即|现在)?(?:降落|着陆|落地)", normalized):
            self.last_source = "mechanical"
            return Task(TaskType.LAND, raw_command=raw)
        mechanical = self._parse_pad_command(command.strip(), raw)
        if mechanical is not None:
            self.last_source = "mechanical"
            return mechanical
        if self.llm_client is not None and self.llm_client.available:
            self.last_source = "local_llm"
            return self._validate_llm(self.llm_client.parse(command), raw)
        raise TaskParseError("unsupported command and optional LLM parser is not configured")

    def _parse_pad_command(self, command: str, raw: str) -> Task | None:
        match = self.PAD_PATTERN.search(command) or self.ENGLISH_PAD_PATTERN.search(command)
        if not match:
            return None
        token = match.group(1)
        pad_id = self.CHINESE_DIGITS[token] if token in self.CHINESE_DIGITS else int(token)
        if not 1 <= pad_id <= 8:
            raise TaskParseError("Mission Pad ID must be between 1 and 8")
        compact = re.sub(
            r"\s+", "", self.TERMINAL_PUNCTUATION.sub("", command.lower())
        )
        is_search = any(word in compact for word in ("搜索", "寻找", "查找", "search", "find"))
        is_goto = any(word in compact for word in ("飞到", "前往", "去", "goto", "go to", "flyto", "fly to"))
        if is_search and not is_goto:
            return Task(TaskType.SEARCH_MISSION_PAD, pad_id=pad_id, raw_command=raw)
        if is_goto:
            final_action = "land" if any(word in compact for word in ("降落", "着陆", "land")) else None
            return Task(TaskType.GOTO_MISSION_PAD, pad_id=pad_id, final_action=final_action, raw_command=raw)
        return None

    @staticmethod
    def _validate_llm(data: dict[str, Any], raw: str) -> Task:
        if not isinstance(data, dict):
            raise TaskParseError("LLM response must be a JSON object")
        allowed_keys = {"recognized", "type", "pad_id", "final_action", "reason"}
        if set(data) - allowed_keys:
            raise TaskParseError("LLM response contains unsupported fields")
        recognized = data.get("recognized", True)
        if not isinstance(recognized, bool):
            raise TaskParseError("LLM recognized field must be boolean")
        if not recognized:
            if any(data.get(key) is not None for key in ("type", "pad_id", "final_action")):
                raise TaskParseError("unrecognized LLM task must not contain an action")
            reason = data.get("reason", "unsupported or ambiguous command")
            raise TaskParseError(f"LLM safely rejected command: {reason}")
        try:
            task_type = TaskType(data["type"])
            pad_id = data.get("pad_id")
            if pad_id is not None and (isinstance(pad_id, bool) or not isinstance(pad_id, int)):
                raise ValueError("pad_id must be an integer")
            return Task(task_type, pad_id=pad_id, final_action=data.get("final_action"), raw_command=raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskParseError(f"invalid LLM task: {exc}") from exc
