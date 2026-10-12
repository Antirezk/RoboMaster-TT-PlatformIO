// Minimal host-side stand-ins for the Arduino-ESP32 API used by the guard firmware.
// Only for compile checks and logic tests on a PC - not a hardware emulator.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <string>
#include <deque>

extern uint32_t g_millis;
inline uint32_t millis() { return g_millis; }
inline void delay(uint32_t ms) { g_millis += ms; }
#define SERIAL_8N1 0x800001c

class Stream {
public:
    std::deque<char> rx;      // bytes the firmware will read
    std::string tx;           // bytes the firmware wrote
    int available() { return static_cast<int>(rx.size()); }
    int read() { if (rx.empty()) return -1; char c = rx.front(); rx.pop_front(); return static_cast<unsigned char>(c); }
    size_t print(const char* s) { tx += s; return strlen(s); }
    size_t println(const char* s) { tx += s; tx += "\n"; return strlen(s) + 1; }
    size_t write(const uint8_t* b, size_t n) { tx.append(reinterpret_cast<const char*>(b), n); return n; }
    int printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
        char buf[512]; va_list a; va_start(a, fmt); int n = vsnprintf(buf, sizeof(buf), fmt, a); va_end(a);
        tx += buf; return n;
    }
    void feed(const char* s) { while (*s) rx.push_back(*s++); }
};

class HardwareSerial : public Stream {
public:
    void begin(unsigned long) {}
    void begin(unsigned long, uint32_t, int, int) {}
};
extern HardwareSerial Serial;
extern HardwareSerial Serial1;

template <typename T> T min(T a, T b) { return a < b ? a : b; }
