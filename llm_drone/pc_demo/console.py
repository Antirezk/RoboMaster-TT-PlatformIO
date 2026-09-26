"""Keep the operator's speech/command trail readable; full diagnostics stay in file."""
import logging


class ConsoleFilter(logging.Filter):
    def __init__(self, telemetry=False):
        super().__init__()
        self.telemetry = telemetry

    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True
        if record.threadName == "tt-keepalive":
            return False
        if getattr(record, "telemetry_summary", False):
            return self.telemetry
        return True


def describe_intent(intent, config):
    if intent.action == "status":
        return "[解析动作] 查看本地遥测（不发送 Tello 指令）"
    if intent.action == "pad_land":
        return (f"[解析动作] 对准并降落到 {intent.pad_id} 号 Mission Pad\n"
                f"[指令预览 / 尚未发送] go 0 0 {config.pad_height_cm} "
                f"{config.pad_speed_cm_s} m{intent.pad_id}\n"
                "[后续步骤] 收到 ok 且新遥测确认对准后，才发送 land")
    action = {"takeoff": "起飞", "land": "降落", "stop": "停止移动并悬停",
              "battery?": "查询电量"}.get(intent.sdk, "移动或旋转")
    return f"[解析动作] {action}\n[指令预览 / 尚未发送] {intent.sdk}"
