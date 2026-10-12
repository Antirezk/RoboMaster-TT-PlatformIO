#pragma once
#include <stdint.h>
#include "Wire.h"
// Host stand-in: every sensor reports `g_tof_mm[channel]` (8190 = out of range).
extern uint16_t g_tof_mm[8];
extern uint8_t g_pca_channel;
class VL53L0X {
public:
    enum regAddr { SYSTEM_INTERRUPT_CLEAR = 0x0B, SOFT_RESET_GO2_SOFT_RESET_N = 0xBF, IDENTIFICATION_MODEL_ID = 0xC0 };
    uint8_t last_status = 0;
    void setBus(TwoWire*) {}
    void setTimeout(uint16_t) {}
    bool init(bool = true) { return true; }
    void setAddress(uint8_t) {}
    void startContinuous(uint32_t = 0) {}
    void stopContinuous() {}
    uint8_t readReg(uint8_t) { return 0xEE; }
    void writeReg(uint8_t, uint8_t) {}
    uint16_t readRangeContinuousMillimeters() { return g_tof_mm[g_pca_channel]; }
    bool timeoutOccurred() { return false; }
};
