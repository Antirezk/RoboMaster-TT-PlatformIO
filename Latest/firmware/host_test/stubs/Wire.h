#pragma once
#include <stdint.h>
#include <stddef.h>
extern uint8_t g_pca_channel;
class TwoWire {
public:
    bool begin(int, int) { return true; }
    void setClock(uint32_t) {}
    void setTimeOut(uint16_t) {}
    void beginTransmission(uint8_t) {}
    size_t write(uint8_t v) { for (int i = 0; i < 8; ++i) if (v == (1u << i)) g_pca_channel = static_cast<uint8_t>(i); return 1; }
    uint8_t endTransmission() { return 0; }
};
extern TwoWire Wire;
