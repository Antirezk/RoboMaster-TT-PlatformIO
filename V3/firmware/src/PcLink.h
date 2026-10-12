#pragma once
// PC <-> guard text protocol over Wi-Fi UDP 9000 (or USB serial for bench tests).
// One owner at a time. "HELLO" claims the session; a different PC may take over only
// after the current owner has been silent for OWNER_TAKEOVER_MS (no reboot needed).
#include <Arduino.h>
#include <WiFiUdp.h>

class PcLink {
public:
    typedef void (*FrameFn)(void* ctx, const char* line);

    void begin(WiFiUDP& udp, uint16_t port);
    void setHandler(FrameFn fn, void* ctx) { handler_ = fn; ctx_ = ctx; }
    void poll(uint32_t now);
    void send(const char* line);

    bool hasOwner() const { return owner_ != Owner::None; }
    uint32_t lastSeen() const { return lastSeen_; }
    bool viaSerial() const { return owner_ == Owner::Serial; }

private:
    enum class Owner : uint8_t { None, Udp, Serial };
    void handle(const char* line, Owner from, const IPAddress& ip, uint16_t port, uint32_t now);
    void replyTo(Owner to, const IPAddress& ip, uint16_t port, const char* line);

    WiFiUDP* udp_ = nullptr;
    FrameFn handler_ = nullptr;
    void* ctx_ = nullptr;
    Owner owner_ = Owner::None;
    IPAddress ip_;
    uint16_t port_ = 0;
    uint32_t lastSeen_ = 0;
    uint32_t lastNotOwner_ = 0;
    char serialLine_[100];
    size_t serialLen_ = 0;
    bool serialOverflow_ = false;
};
