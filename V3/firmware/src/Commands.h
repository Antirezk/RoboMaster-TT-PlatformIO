#pragma once
// Tello SDK 3.0 whitelist used by the guard. Header-only so it can be unit-tested on a PC.
#include <stdint.h>
#include <string.h>

enum class Kind : uint8_t {
    Invalid, Enter, Takeoff, Land, Stop, Emergency, MotorOn, MotorOff, Move, Turn, Speed, Read
};

// Move axes match the sensor indices: 0 forward, 1 back, 2 left, 3 right; 4 up, 5 down.
enum Axis : int8_t { FRONT = 0, BACK = 1, LEFT = 2, RIGHT = 3, UP = 4, DOWN = 5 };

struct Command {
    Kind kind = Kind::Invalid;
    int8_t axis = -1;          // Move: Axis; Turn: 0 = cw, 1 = ccw
    int16_t value = 0;         // cm, degrees or cm/s
    char text[24] = {0};       // exact SDK string that goes on the wire

    bool urgent() const {
        return kind == Kind::Stop || kind == Kind::Land || kind == Kind::Emergency || kind == Kind::MotorOff;
    }
    bool isRead() const { return kind == Kind::Read; }
    bool moving() const {      // the drone changes position/attitude while this runs
        return kind == Kind::Move || kind == Kind::Turn || kind == Kind::Takeoff;
    }
};

namespace sdkcmd {

static const char* const MOVE_NAMES[6] = {"forward", "back", "left", "right", "up", "down"};

inline bool parseNumber(const char* p, int low, int high, int& out) {
    if (*p < '1' || *p > '9') return false;           // no sign, no leading zero
    int value = 0, digits = 0;
    while (*p >= '0' && *p <= '9') {
        if (++digits > 3) return false;
        value = value * 10 + (*p++ - '0');
    }
    if (*p != '\0' || value < low || value > high) return false;
    out = value;
    return true;
}

// Ranges follow the Tello SDK 3.0 user guide.
inline bool parse(const char* text, Command& out) {
    Command c;
    const size_t n = strlen(text);
    if (n == 0 || n >= sizeof(c.text)) return false;
    memcpy(c.text, text, n + 1);
    struct Fixed { const char* s; Kind k; };
    static const Fixed fixed[] = {
        {"command", Kind::Enter}, {"takeoff", Kind::Takeoff}, {"land", Kind::Land},
        {"stop", Kind::Stop}, {"emergency", Kind::Emergency}, {"motoron", Kind::MotorOn},
        {"motoroff", Kind::MotorOff}, {"battery?", Kind::Read}, {"speed?", Kind::Read},
        {"time?", Kind::Read}, {"wifi?", Kind::Read}, {"sdk?", Kind::Read}, {"sn?", Kind::Read},
        {"hardware?", Kind::Read}};
    for (const Fixed& f : fixed) {
        if (strcmp(text, f.s) == 0) { c.kind = f.k; out = c; return true; }
    }
    int v = 0;
    for (int i = 0; i < 6; ++i) {
        const size_t l = strlen(MOVE_NAMES[i]);
        if (strncmp(text, MOVE_NAMES[i], l) == 0 && text[l] == ' ') {
            if (!parseNumber(text + l + 1, 20, 500, v)) return false;
            c.kind = Kind::Move; c.axis = static_cast<int8_t>(i); c.value = static_cast<int16_t>(v);
            out = c; return true;
        }
    }
    if (strncmp(text, "cw ", 3) == 0 || strncmp(text, "ccw ", 4) == 0) {
        const bool cw = text[1] == 'w';
        if (!parseNumber(text + (cw ? 3 : 4), 1, 360, v)) return false;
        c.kind = Kind::Turn; c.axis = cw ? 0 : 1; c.value = static_cast<int16_t>(v);
        out = c; return true;
    }
    if (strncmp(text, "speed ", 6) == 0) {
        if (!parseNumber(text + 6, 10, 100, v)) return false;
        c.kind = Kind::Speed; c.value = static_cast<int16_t>(v);
        out = c; return true;
    }
    return false;
}

// Reply deadline. Motion replies arrive only when the motion has finished.
inline uint32_t timeoutMs(const Command& c, int speedCmS) {
    const uint32_t speed = speedCmS > 0 ? static_cast<uint32_t>(speedCmS) : 10;
    switch (c.kind) {
        case Kind::Takeoff: case Kind::Land: return 20000;
        case Kind::Move: return 8000 + 1500UL * static_cast<uint32_t>(c.value) / speed;
        case Kind::Turn: return 8000 + static_cast<uint32_t>(c.value) * 1000UL / 30;
        case Kind::Enter: case Kind::MotorOn: case Kind::MotorOff: return 5000;
        case Kind::Emergency: return 0;   // SDK lists no reply
        default: return 4000;
    }
}

// Validate a reply ("ETT " already stripped). Returns true for success.
inline bool replyOk(const Command& c, const char* reply) {
    if (c.isRead()) {
        if (strcmp(c.text, "battery?") == 0) {
            int v = 0, digits = 0;
            for (const char* p = reply; *p; ++p) {
                if (*p < '0' || *p > '9' || ++digits > 3) return false;
                v = v * 10 + (*p - '0');
            }
            return digits > 0 && v <= 100;
        }
        return reply[0] && strcmp(reply, "ok") != 0 && strncmp(reply, "error", 5) != 0;
    }
    return strcmp(reply, "ok") == 0;
}

}  // namespace sdkcmd
