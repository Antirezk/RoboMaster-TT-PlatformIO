#pragma once
// Four-direction ToF avoidance policy. Pure logic (no Arduino calls) so it is host-testable.
#include <stdint.h>
#include <stdio.h>

#include "Commands.h"
#include "GuardConfig.h"

struct RangeSample {
    bool ok = false;        // valid reading or out-of-range (treated as SENSOR_MAX_MM)
    uint16_t mm = 0;
    uint32_t at = 0;        // millis() of the last good sample
    float closing = 0.f;    // mm/s towards the obstacle (positive = getting closer)
};

inline const char* sideName(int i) {
    static const char* const names[4] = {"front", "back", "left", "right"};
    return i >= 0 && i < 4 ? names[i] : "?";
}

class Avoidance {
public:
    enum class Action : uint8_t { None, Stop, Retreat };

    bool enabled = true;
    RangeSample r[4];

    // valid: real distance; outOfRange: nothing within range. Neither -> sensor fault.
    void update(int i, bool valid, bool outOfRange, uint16_t mm, uint32_t now) {
        RangeSample& s = r[i];
        if (!valid && !outOfRange) { s.ok = false; s.closing = 0.f; return; }
        const uint16_t value = outOfRange || mm > guardcfg::SENSOR_MAX_MM ? guardcfg::SENSOR_MAX_MM : mm;
        if (s.ok && now > s.at && !outOfRange) {
            float v = (static_cast<float>(s.mm) - value) * 1000.f / static_cast<float>(now - s.at);
            if (v > 3000.f) v = 3000.f;
            if (v < -3000.f) v = -3000.f;
            s.closing = 0.6f * s.closing + 0.4f * v;
        } else if (outOfRange) {
            s.closing = 0.f;
        }
        s.ok = true; s.mm = value; s.at = now;
    }

    bool fresh(int i, uint32_t now) const {
        return r[i].ok && static_cast<uint32_t>(now - r[i].at) <= guardcfg::SENSOR_STALE_MS;
    }

    uint16_t stopDistance(int i) const {
        float extra = r[i].closing > 0 ? r[i].closing * guardcfg::STOP_REACTION_S : 0.f;
        float d = guardcfg::STOP_BASE_MM + extra;
        return d > guardcfg::STOP_MAX_MM ? guardcfg::STOP_MAX_MM : static_cast<uint16_t>(d);
    }

    // Clearance a move must leave at its end: the in-motion braking distance at this speed,
    // so an admitted move is never stopped by the guard unless something changes.
    static uint32_t endClearance(int speedCmS) {
        float d = guardcfg::MOVE_MARGIN_MM + speedCmS * 10.f * guardcfg::STOP_REACTION_S;
        return d > guardcfg::STOP_MAX_MM ? guardcfg::STOP_MAX_MM : static_cast<uint32_t>(d);
    }

    // Pre-flight check of one command. Returns true if allowed; otherwise fills `why`.
    bool admit(const Command& c, uint32_t now, int speedCmS, char* why, size_t n) const {
        if (!enabled) return true;
        if (c.kind == Kind::Move && c.axis <= RIGHT) {
            for (int i = 0; i < 4; ++i) {
                if (!fresh(i, now)) { snprintf(why, n, "sensor-%s-invalid", sideName(i)); return false; }
            }
            const uint32_t clearance = endClearance(speedCmS);
            const uint32_t need = static_cast<uint32_t>(c.value) * 10 + clearance;
            const RangeSample& s = r[c.axis];
            if (need >= guardcfg::SENSOR_MAX_MM) {
                if (s.mm < guardcfg::SENSOR_MAX_MM) {
                    snprintf(why, n, "obstacle-%s-%umm-need-%lumm", sideName(c.axis), s.mm, (unsigned long)need);
                } else {
                    snprintf(why, n, "beyond-sensor-range-max-%dcm",
                             static_cast<int>((guardcfg::SENSOR_MAX_MM - clearance) / 10) - 1);
                }
                return false;
            }
            if (s.mm <= need) {
                snprintf(why, n, "obstacle-%s-%umm-need-%lumm", sideName(c.axis), s.mm, (unsigned long)need);
                return false;
            }
            return true;
        }
        if (c.kind == Kind::Turn || c.kind == Kind::Takeoff) {
            for (int i = 0; i < 4; ++i) {
                if (!fresh(i, now)) { snprintf(why, n, "sensor-%s-invalid", sideName(i)); return false; }
                if (r[i].mm <= guardcfg::PERIMETER_MM) {
                    snprintf(why, n, "too-close-%s-%umm-need-%umm", sideName(i), r[i].mm, guardcfg::PERIMETER_MM);
                    return false;
                }
            }
        }
        return true;   // up/down (no vertical sensors), land, stop, reads, settings
    }

    // Called continuously while airborne. `active` is the running command (Invalid when hovering).
    Action monitor(const Command& active, uint32_t now, uint32_t& lastRetreat, int& retreatAxis,
                   char* why, size_t n) const {
        if (!enabled) return Action::None;
        const bool moving = active.moving();
        for (int i = 0; i < 4; ++i) {
            if (!fresh(i, now)) {
                if (moving) { snprintf(why, n, "sensor-%s-lost", sideName(i)); return Action::Stop; }
                return Action::None;   // hovering: new moves are refused by admit()
            }
        }
        if (moving && active.kind == Kind::Move && active.axis <= RIGHT) {
            const int a = active.axis;
            const uint16_t limit = stopDistance(a);
            if (r[a].mm <= limit) {
                snprintf(why, n, "obstacle-%s-%umm-limit-%umm", sideName(a), r[a].mm, limit);
                return Action::Stop;
            }
        }
        for (int i = 0; i < 4; ++i) {
            if (r[i].mm >= guardcfg::CRITICAL_MM) continue;
            // Moving straight away from this side (e.g. the retreat itself) is what we want.
            if (moving && active.kind == Kind::Move && active.axis <= RIGHT && i == (active.axis ^ 1)) continue;
            if (moving) {
                snprintf(why, n, "critical-%s-%umm", sideName(i), r[i].mm);
                return Action::Stop;
            }
            if (active.kind != Kind::Invalid) return Action::None;   // another command is running
            if (lastRetreat && static_cast<uint32_t>(now - lastRetreat) < guardcfg::RETREAT_COOLDOWN_MS) return Action::None;
            const int opposite = i ^ 1;   // front<->back, left<->right
            if (r[opposite].mm > guardcfg::RETREAT_CM * 10 + guardcfg::MOVE_MARGIN_MM) {
                retreatAxis = opposite;
                snprintf(why, n, "critical-%s-%umm-retreat-%s", sideName(i), r[i].mm, sideName(opposite));
                return Action::Retreat;
            }
            snprintf(why, n, "trapped-%s-%umm-%s-%umm", sideName(i), r[i].mm, sideName(opposite), r[opposite].mm);
            return Action::None;
        }
        return Action::None;
    }
};
