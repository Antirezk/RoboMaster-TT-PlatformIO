"""Opt-in accuracy regression against the locally installed model.

Run with: $env:RUN_LOCAL_LLM_TESTS='1'; python -m pytest tests/test_llm_live.py -q
"""

import os

import pytest

from core.exceptions import TaskParseError
from llm.llm_client import OllamaLLMClient
from llm.task_parser import TaskParser


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCAL_LLM_TESTS") != "1", reason="requires local Ollama model",
)


@pytest.mark.parametrize(("command", "expected"), [
    ("麻烦看一下飞机还剩多少电", ("battery", None, None)),
    ("告诉我无人机现在是什么状态", ("status", None, None)),
    ("看看下方现在识别到哪块垫子", ("pad_telemetry", None, None)),
    ("让飞机升空", ("takeoff", None, None)),
    ("现在就让它着陆", ("land", None, None)),
    ("马上停止当前动作", ("stop", None, None)),
    ("帮我找到二号任务垫，找到以后悬停", ("search_mission_pad", 2, None)),
    ("移动到第三块挑战垫上方，不要降落", ("goto_mission_pad", 3, None)),
    ("去四号任务垫然后在那里着陆", ("goto_mission_pad", 4, "land")),
    ("前往第五号垫，但不要在那里降落", ("goto_mission_pad", 5, None)),
    ("寻找六号挑战垫", ("search_mission_pad", 6, None)),
])
def test_local_qwen_valid_commands(command, expected):
    client = OllamaLLMClient(
        os.getenv("OLLAMA_MODEL", "qwen3.5:9b"),
        os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    task = TaskParser(client).parse(command)
    assert (task.type.value, task.pad_id, task.final_action) == expected


@pytest.mark.parametrize("command", [
    "起飞并同时降落",
    "去九号垫",
    "飞到挑战垫",  # missing ID
    "绕着房间飞一圈",
    "以最大速度向前冲",
    "不要起飞",
    "别降落",
    "查看电量然后起飞",
    "如果电量足够就起飞",
    "去一号或者二号挑战垫",
])
def test_local_qwen_rejects_unsafe_or_unsupported_commands(command):
    client = OllamaLLMClient(
        os.getenv("OLLAMA_MODEL", "qwen3.5:9b"),
        os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    with pytest.raises(TaskParseError):
        TaskParser(client).parse(command)
