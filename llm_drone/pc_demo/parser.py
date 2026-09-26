from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Intent:
    action: str
    sdk: str = ""
    pad_id: int | None = None

    def __post_init__(self):
        if self.action == "pad_land":
            if self.sdk or type(self.pad_id) is not int or not 1 <= self.pad_id <= 8:
                raise ValueError("Pad landing requires exactly one integer ID from 1 to 8")
        elif self.action == "status":
            if self.sdk or self.pad_id is not None:
                raise ValueError("status is local and has no SDK parameters")
        elif self.action == "sdk":
            if self.pad_id is not None:
                raise ValueError("SDK action cannot carry a Pad ID")
            if self.sdk in {"takeoff", "land", "stop", "battery?"}:
                return
            match = re.fullmatch(r"(up|down|left|right|forward|back|cw|ccw) ([1-9][0-9]{0,2})", self.sdk)
            if not match:
                raise ValueError("SDK command not in the demo allowlist")
            low, high = (1, 180) if match[1] in {"cw", "ccw"} else (20, 100)
            if not low <= int(match[2]) <= high:
                raise ValueError("SDK argument outside demo limits")
        else:
            raise ValueError("unknown intent action")


def parse(text: str) -> Intent:
    """Whole-utterance allowlist: negations/compound commands never match."""
    text = re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")
    aliases = {
        "take off": "takeoff", "takeoff": "takeoff", "land": "land",
        "land now": "land", "stop": "stop", "hover": "stop",
        "battery": "battery?", "battery level": "battery?",
        "what is the battery level": "battery?",
    }
    if text in aliases:
        return Intent("sdk", aliases[text])
    if text in {"status", "telemetry", "pad", "mission pad"}:
        return Intent("status")
    match = re.fullmatch(r"(?:land|landing) on (?:mission )?pad (?:number )?([1-8]|one|two|three|four|five|six|seven|eight)", text)
    if match:
        value = match[1]
        number = int(value) if value.isdigit() else (
            "one two three four five six seven eight".split().index(value) + 1)
        return Intent("pad_land", pad_id=number)
    match = re.fullmatch(r"(?:move )?(up|down|left|right|forward|back|backward) (\d{1,3}) (?:centimeters|centimetres|cm)", text)
    if match and 20 <= int(match[2]) <= 100:
        direction = "back" if match[1] == "backward" else match[1]
        return Intent("sdk", f"{direction} {int(match[2])}")
    match = re.fullmatch(r"(?:turn|rotate) (clockwise|counterclockwise) (\d{1,3}) degrees", text)
    if match and 1 <= int(match[2]) <= 180:
        return Intent("sdk", f"{'cw' if match[1] == 'clockwise' else 'ccw'} {int(match[2])}")
    raise ValueError(f"unsupported or ambiguous English command: {text!r}")
