#ifndef RC_COMMAND_H
#define RC_COMMAND_H

#include <Arduino.h>

// Tello SDK: rc <left/right> <forward/back> <up/down> <yaw>
struct RCCommand
{
    int16_t lr;
    int16_t fb;
    int16_t ud;
    int16_t yaw;

    RCCommand(
        int16_t leftRight = 0,
        int16_t forwardBack = 0,
        int16_t upDown = 0,
        int16_t yawRate = 0)
        : lr(leftRight),
          fb(forwardBack),
          ud(upDown),
          yaw(yawRate)
    {
    }
};

inline int16_t clampRCValue(int value)
{
    if (value < -100) return -100;
    if (value > 100) return 100;
    return static_cast<int16_t>(value);
}

inline RCCommand clampRC(const RCCommand& command)
{
    return {
        clampRCValue(command.lr),
        clampRCValue(command.fb),
        clampRCValue(command.ud),
        clampRCValue(command.yaw)
    };
}

inline bool operator==(const RCCommand& lhs, const RCCommand& rhs)
{
    return lhs.lr == rhs.lr && lhs.fb == rhs.fb &&
           lhs.ud == rhs.ud && lhs.yaw == rhs.yaw;
}

inline bool operator!=(const RCCommand& lhs, const RCCommand& rhs)
{
    return !(lhs == rhs);
}

#endif
