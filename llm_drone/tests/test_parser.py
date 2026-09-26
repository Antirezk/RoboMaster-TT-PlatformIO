import pytest

from core.exceptions import TaskParseError
from llm.task_parser import TaskParser
from models.task import TaskType


@pytest.mark.parametrize(("command", "kind"), [
    ("起飞", TaskType.TAKEOFF), ("降落", TaskType.LAND), ("停止", TaskType.STOP),
    ("查看电量", TaskType.BATTERY), ("查看状态", TaskType.STATUS), ("pad", TaskType.PAD_TELEMETRY),
])
def test_direct_mechanical_commands(command, kind):
    assert TaskParser().parse(command).type == kind


def test_goto_pad_and_land_is_mechanical():
    task = TaskParser().parse("飞到1号挑战垫并降落")
    assert task.type == TaskType.GOTO_MISSION_PAD
    assert task.pad_id == 1
    assert task.final_action == "land"


def test_spoken_chinese_pad_number_is_mechanical():
    parser = TaskParser()
    task = parser.parse("飞到一号挑战垫并降落")
    assert (task.type, task.pad_id, task.final_action) == (
        TaskType.GOTO_MISSION_PAD, 1, "land",
    )
    assert parser.last_source == "mechanical"


@pytest.mark.parametrize(("command", "kind"), [
    ("停止！", TaskType.STOP), ("请立即停止。", TaskType.STOP), ("降落。", TaskType.LAND),
])
def test_asr_punctuation_keeps_emergency_commands_mechanical(command, kind):
    parser = TaskParser()
    assert parser.parse(command).type == kind
    assert parser.last_source == "mechanical"


def test_search_pad():
    task = TaskParser().parse("搜索8号挑战垫")
    assert (task.type, task.pad_id, task.final_action) == (TaskType.SEARCH_MISSION_PAD, 8, None)


@pytest.mark.parametrize("command", [
    "飞到0号挑战垫", "飞到9号挑战垫", "搜索-1号任务垫", "飞到九号挑战垫",
])
def test_pad_id_range(command):
    with pytest.raises(TaskParseError, match="between 1 and 8"):
        TaskParser().parse(command)


def test_unknown_command_works_without_api_key_but_is_rejected():
    with pytest.raises(TaskParseError, match="not configured"):
        TaskParser().parse("请随便飞一下")


def test_llm_task_is_strictly_validated():
    with pytest.raises(TaskParseError):
        TaskParser._validate_llm({"type": "send_rc", "left_right": 100}, "x")
    with pytest.raises(TaskParseError, match="final_action"):
        TaskParser._validate_llm({"type": "status", "final_action": "land"}, "x")


def test_llm_can_safely_reject_ambiguous_command():
    data = {
        "recognized": False, "type": None, "pad_id": None,
        "final_action": None, "reason": "动作不明确",
    }
    with pytest.raises(TaskParseError, match="safely rejected"):
        TaskParser._validate_llm(data, "随便飞飞")


def test_unrecognized_llm_response_cannot_smuggle_action():
    data = {
        "recognized": False, "type": "takeoff", "pad_id": None,
        "final_action": None, "reason": "",
    }
    with pytest.raises(TaskParseError, match="must not contain an action"):
        TaskParser._validate_llm(data, "x")
