from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from core.exceptions import TaskParseError


ALLOWED_TYPES = [
    "takeoff", "land", "stop", "battery", "status", "pad_telemetry",
    "search_mission_pad", "goto_mission_pad",
]

TASK_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recognized": {"type": "boolean"},
        "type": {"type": ["string", "null"], "enum": ALLOWED_TYPES + [None]},
        "pad_id": {"type": ["integer", "null"], "minimum": 1, "maximum": 8},
        "final_action": {"enum": [None, "land"]},
        "reason": {"type": "string", "maxLength": 120},
    },
    "required": ["recognized", "type", "pad_id", "final_action", "reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是 RoboMaster TT 的受限任务解析器，只把用户话语转换为 JSON，不进行飞行控制。
允许任务：
- takeoff：仅起飞
- land：立即降落
- stop：停止当前任务/零 RC
- battery：查看电量
- status：查看系统状态
- pad_telemetry：查看当前挑战垫遥测
- search_mission_pad：搜索指定挑战垫，找到后悬停
- goto_mission_pad：飞到指定挑战垫上方；只有用户明确要求降落/着陆时 final_action 才为 land

严格规则：
1. Mission Pad ID 必须是 1 到 8 的明确整数；不得猜测、补全或更改。
2. 不得生成 RC、速度、坐标、航向、路径或任何白名单之外的动作。
3. 指令含糊、互相冲突、超出能力、缺少必要 Pad ID，或要求 9 号以上时，recognized=false。
4. recognized=false 时 type/pad_id/final_action 必须全为 null，并在 reason 简短说明。
5. recognized=true 时非 Pad 任务的 pad_id 必须为 null；只有 goto_mission_pad 可带 final_action=land。
6. “当前/下方识别到哪块垫、查看垫子数据”属于 pad_telemetry，不需要 Pad ID，绝不能解释为 search。
7. 单独要求“降落/着陆/落地”属于当前位置 land，不需要 Pad ID；只有同时明确提到 1..8 号 Pad 时才是 goto_mission_pad + land。
8. search/goto 必须有 Pad ID；pad_telemetry、land 不得因为没有 Pad ID 而拒绝。
9. 否定命令（如“不要起飞”“别降落”）不是动作，必须 recognized=false，绝不能忽略否定词。
10. 每次只允许一个原子任务；条件命令、多动作组合、候选多个 Pad 或先后序列必须 recognized=false。

示例：
“帮我看看还剩多少电” => battery
“看看下方识别到哪块垫子” => pad_telemetry, pad_id=null
“现在就在这里着陆” => land, pad_id=null
“找一下二号垫，找到后别降落” => search_mission_pad, pad_id=2
“去三号挑战垫上方停住” => goto_mission_pad, pad_id=3, final_action=null
“降落到四号任务垫” => goto_mission_pad, pad_id=4, final_action=land
“随便飞一圈” => recognized=false
“去九号垫” => recognized=false
"""


class OllamaLLMClient:
    """Local Ollama structured-output parser; never participates in the control loop."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 timeout_sec: float = 90.0, keep_alive: str = "30m",
                 think: bool = False, system_prompt: str = SYSTEM_PROMPT,
                 json_schema: dict[str, Any] | None = None) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.keep_alive = keep_alive
        self.think = think
        self.system_prompt = system_prompt
        self.json_schema = TASK_JSON_SCHEMA if json_schema is None else json_schema

    @property
    def available(self) -> bool:
        return True

    def health_check(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as response:
                body = json.loads(response.read().decode("utf-8"))
            names = {item.get("name") for item in body.get("models", [])}
            if self.model not in names:
                return False, f"model {self.model!r} is not installed"
            return True, f"Ollama model {self.model} ready"
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            return False, f"Ollama unavailable: {exc}"

    def parse(self, command: str) -> dict[str, Any]:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": command},
            ],
            "stream": False,
            "think": self.think,
            "format": self.json_schema,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "seed": 42, "num_predict": 160},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
            return json.loads(body["message"]["content"])
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise TaskParseError(f"local Ollama parser HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise TaskParseError(f"local Ollama parser failed: {exc}") from exc


class OpenAICompatibleLLMClient:
    """Optional OpenAI-compatible backend, useful for a Jetson vLLM server."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 timeout_sec: float = 60.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "EMPTY"
        self.timeout_sec = timeout_sec

    @property
    def available(self) -> bool:
        return True

    def health_check(self) -> tuple[bool, str]:
        return True, f"OpenAI-compatible backend configured at {self.base_url}"

    def parse(self, command: str) -> dict[str, Any]:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": command},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "drone_task", "strict": True, "schema": TASK_JSON_SCHEMA},
            },
            "temperature": 0,
            "max_tokens": 160,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
            return json.loads(body["choices"][0]["message"]["content"])
        except (urllib.error.URLError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise TaskParseError(f"OpenAI-compatible parser failed: {exc}") from exc


LLMClient = OpenAICompatibleLLMClient
