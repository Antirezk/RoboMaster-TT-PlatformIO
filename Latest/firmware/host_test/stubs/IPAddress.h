#pragma once
#include <stdint.h>
class IPAddress {
public:
    uint8_t o[4] = {0, 0, 0, 0};
    IPAddress() {}
    IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) { o[0] = a; o[1] = b; o[2] = c; o[3] = d; }
    bool operator==(const IPAddress& x) const { return o[0] == x.o[0] && o[1] == x.o[1] && o[2] == x.o[2] && o[3] == x.o[3]; }
};
