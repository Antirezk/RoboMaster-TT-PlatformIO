#pragma once
#include <deque>
#include <string>
#include <vector>
#include "Arduino.h"
#include "IPAddress.h"

struct Packet { IPAddress ip; uint16_t port; std::string data; };

class WiFiUDP {
public:
    std::deque<Packet> inbox;
    std::vector<Packet> outbox;
    Packet current, building;
    uint8_t begin(uint16_t) { return 1; }
    int parsePacket() {
        if (inbox.empty()) return 0;
        current = inbox.front(); inbox.pop_front();
        return static_cast<int>(current.data.size());
    }
    int read(uint8_t* buf, size_t n) {
        size_t k = current.data.size() < n ? current.data.size() : n;
        memcpy(buf, current.data.data(), k); return static_cast<int>(k);
    }
    IPAddress remoteIP() { return current.ip; }
    uint16_t remotePort() { return current.port; }
    int beginPacket(IPAddress ip, uint16_t port) { building = Packet{ip, port, ""}; return 1; }
    size_t write(const uint8_t* b, size_t n) { building.data.append(reinterpret_cast<const char*>(b), n); return n; }
    int endPacket() { outbox.push_back(building); return 1; }
};
