"""English voice transcript -> Intent. Whole-sentence, deterministic rules.

Examples (flight mode):
  "Take off."                         -> takeoff
  "Move forward 50 centimeters."      -> forward 50
  "Go up one meter"                   -> up 100
  "Turn left 90 degrees" / "rotate counterclockwise 45" -> ccw 90 / ccw 45
  "Turn right" (no number)            -> cw <turn_default_deg>
  "Turn around"                       -> cw 180
  "Hover" / "Stop"                    -> stop
  "Set speed to 50"                   -> speed 50
  "Land"                              -> land
Low-speed test mode:
  "Power on" / "motor on"   -> motoron      "Power off" / "stop" -> motoroff
Both: "battery", "status", "wifi signal", "sdk version", "serial number", "hardware".
"""
import re

from . import sdk
from .sdk import SDKError, make


class NotUnderstood(SDKError):
    """Rules could not map the sentence. Only this case may be passed to the local LLM."""

UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19, "a": 1, "an": 1}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}

NEGATION = re.compile(r"\b(?:no|not|never|don'?t|do not|can'?t|cannot|won'?t|without)\b")
COMPOUND = re.compile(r"\b(?:and|then|after|before|also|plus|or|if|unless|while)\b")


def words_to_numbers(text):
    """'one hundred twenty' -> '120', 'forty five' -> '45', 'half a meter' -> '50 cm'."""
    text = re.sub(r"\bhalf (?:a )?(?:meter|metre)s?\b", "50 cm", text)
    tokens, out, i = text.split(), [], 0
    number_words = set(UNITS) | set(TENS) | {"hundred", "zero"}
    while i < len(tokens):
        tok = tokens[i]
        if tok in number_words and not (tok in {"a", "an"} and (i + 1 >= len(tokens) or tokens[i + 1] not in {"hundred", "meter", "metre", "meters", "metres"})):
            total, current, used = 0, 0, 0
            while i < len(tokens) and tokens[i] in number_words:
                w = tokens[i]
                if w in {"a", "an"} and used:
                    break
                if w == "hundred":
                    current = max(current, 1) * 100
                elif w in TENS:
                    current += TENS[w]
                elif w == "zero":
                    current += 0
                else:
                    current += UNITS[w]
                used += 1
                i += 1
            total += current
            out.append(str(total))
            continue
        out.append(tok)
        i += 1
    return " ".join(out)


def normalize(text):
    text = text.strip().lower()
    text = text.replace("°", " degrees ").replace("’", "'")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)          # 1,000 -> 1000
    text = re.sub(r"(\d)(cm|m|meters?|metres?|centimeters?|centimetres?|degrees?|deg)\b", r"\1 \2", text)
    text = re.sub(r"[^a-z0-9.' ]+", " ", text)              # punctuation, hyphens
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)         # keep 1.5, drop sentence dots
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:please|okay|ok|hey|drone|tello|now|robomaster)\s+", "", text)
    text = re.sub(r"\s+(?:please|now)$", "", text)
    return words_to_numbers(text)


def _amount_cm(number, unit):
    value = float(number)
    if unit and unit.startswith(("m", "meter", "metre")) and not unit.startswith("mm"):
        value *= 100
    if not value.is_integer():
        raise SDKError("Distance must be a whole number of centimeters")
    return int(value)


READ_PHRASES = {
    "battery": "battery?", "battery level": "battery?", "check battery": "battery?",
    "how much battery": "battery?", "what's the battery": "battery?", "battery status": "battery?",
    "speed": "speed?", "current speed": "speed?", "what's the speed": "speed?",
    "motor time": "time?", "flight time": "time?",
    "wifi": "wifi?", "wifi signal": "wifi?", "signal": "wifi?", "signal strength": "wifi?",
    "sdk version": "sdk?", "version": "sdk?",
    "serial number": "sn?", "hardware": "hardware?",
}
DIR_WORDS = {"forward": "forward", "forwards": "forward", "ahead": "forward",
             "back": "back", "backward": "back", "backwards": "back",
             "left": "left", "right": "right", "up": "up", "upward": "up", "upwards": "up",
             "higher": "up", "down": "down", "downward": "down", "downwards": "down", "lower": "down"}
UNIT = r"(cm|centimeters?|centimetres?|m|meters?|metres?)"


def parse(text, mode, config):
    """Return an Intent or raise SDKError with a Chinese explanation."""
    if not isinstance(text, str) or not text.strip():
        raise SDKError("Nothing was recognised")
    if len(text) > 120:
        raise SDKError("Sentence too long - say one command at a time")
    phrase = normalize(text)
    if NEGATION.search(phrase):
        raise SDKError("Contains a negation - not executed")
    if COMPOUND.search(phrase):
        raise SDKError("One command at a time (found and/then/...)")
    phrase = phrase.rstrip("?").strip()
    # Whisper sometimes "hallucinates" its own initial prompt on silence; that
    # looks like a list of commands. Never let a list reach the LLM.
    keywords = {"power", "motor", "motors", "stop", "battery", "cancel", "exit", "take", "land",
                "hover", "forward", "back", "turn", "status", "commands", "rotate"}
    if len(keywords & set(phrase.split())) >= 3 or text.count(",") >= 2:
        raise SDKError("Looks like a speech-model prompt echo (several commands in one line) - ignored")

    if phrase in {"status", "state", "report"}:
        return make("status", heard=text)
    if phrase in READ_PHRASES:
        return make("read", query=READ_PHRASES[phrase], heard=text)

    if mode == sdk.TEST:
        if phrase in {"power on", "motor on", "motors on", "start motor", "start motors", "start the motors", "turn on motors", "turn on the motors"}:
            return make("motoron", heard=text)
        if phrase in {"power off", "motor off", "motors off", "stop", "stop motor", "stop motors",
                      "stop the motors", "cancel", "turn off motors", "turn off the motors", "halt"}:
            return make("motoroff", heard=text)
        if re.search(r"\b(take ?off|launch|fly|land|forward|backward|turn|rotate|up|down|left|right)\b", phrase):
            raise SDKError("Low-speed test mode: flight commands are disabled. Switch to Flight mode first.")
        raise NotUnderstood("Low-speed test accepts only power on / power off / stop / battery")

    # ---- flight mode ----
    if phrase in {"take off", "takeoff", "take of", "launch", "lift off", "liftoff"}:
        return make("takeoff", heard=text)
    if phrase in {"land", "land now", "landing", "touch down", "touchdown", "come down and land"}:
        return make("land", heard=text)
    if phrase in {"stop", "hover", "halt", "freeze", "hold", "hold position", "stay", "wait", "stop moving", "brake"}:
        return make("stop", heard=text)
    if phrase in {"power on", "motor on", "motors on", "power off", "motor off", "motors off"}:
        raise SDKError("Motor commands are only available in low-speed test mode")
    if phrase in {"turn around", "spin around", "about face"}:
        return make("turn", 180, direction="cw", heard=text)

    m = re.fullmatch(r"(?:set )?(?:the )?speed (?:to )?(\d{1,3})(?: (?:cm|centimeters?|centimetres?)(?: per second)?)?", phrase)
    if m:
        return make("speed", int(m[1]), heard=text)

    m = re.fullmatch(r"(?:turn|rotate|yaw|spin|face)(?: to)?(?: the)? (left|right|clockwise|counterclockwise|counter clockwise|anticlockwise|anti clockwise)(?: by)?(?: (\d{1,3})(?: (?:degrees?|deg))?)?", phrase) \
        or re.fullmatch(r"(?:rotate |turn )?(\d{1,3}) (?:degrees?|deg) (?:to the )?(left|right|clockwise|counterclockwise|anticlockwise)", phrase)
    if m:
        groups = m.groups()
        if groups[0] and groups[0][0].isdigit():
            number, way = groups
        else:
            way, number = groups
        direction = "cw" if way in {"right", "clockwise"} else "ccw"
        degrees = int(number) if number else config.turn_default_deg
        return make("turn", degrees, direction=direction, heard=text)

    m = re.fullmatch(rf"(?:go |move |fly |head |climb |descend |rise )?(?:to the )?({'|'.join(DIR_WORDS)})(?: by)? (\d+(?:\.\d+)?)(?: {UNIT})?", phrase) \
        or re.fullmatch(rf"(?:go |move |fly )?(\d+(?:\.\d+)?)(?: {UNIT})? ({'|'.join(DIR_WORDS)})", phrase)
    if m:
        g = m.groups()
        if g[0][0].isdigit():
            number, unit, word = g
        else:
            word, number, unit = g
        cm = _amount_cm(number, unit)
        if cm > config.max_move_cm:
            raise SDKError(f"{cm} cm exceeds the indoor safety cap of {config.max_move_cm} cm (change max_move_cm in config.json; SDK max 500)")
        return make("move", cm, direction=DIR_WORDS[word], heard=text)

    if re.fullmatch(rf"(?:go |move |fly )?(?:{'|'.join(DIR_WORDS)})", phrase):
        raise SDKError("Please say a distance, e.g. move forward 50 centimeters")
    raise NotUnderstood("Not understood (see the command list on the left)")
