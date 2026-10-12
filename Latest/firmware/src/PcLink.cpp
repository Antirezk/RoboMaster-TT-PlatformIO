#include "PcLink.h"

#include <string.h>

#include "GuardConfig.h"

void PcLink::begin(WiFiUDP& udp, uint16_t port) {
    udp_ = &udp;
    udp_->begin(port);
}

void PcLink::replyTo(Owner to, const IPAddress& ip, uint16_t port, const char* line) {
    if (to == Owner::Udp && udp_) {
        udp_->beginPacket(ip, port);
        udp_->write(reinterpret_cast<const uint8_t*>(line), strlen(line));
        udp_->write(reinterpret_cast<const uint8_t*>("\n"), 1);
        udp_->endPacket();
    } else if (to == Owner::Serial) {
        Serial.println(line);
    }
}

void PcLink::send(const char* line) {
    if (owner_ == Owner::Udp) replyTo(Owner::Udp, ip_, port_, line);
    Serial.println(line);    // always mirrored to USB for debugging
}

void PcLink::handle(const char* line, Owner from, const IPAddress& ip, uint16_t port, uint32_t now) {
    const bool same = owner_ == from && (from == Owner::Serial || (ip == ip_ && port == port_));
    if (strcmp(line, "HELLO") == 0) {
        const bool silent = static_cast<uint32_t>(now - lastSeen_) > guardcfg::OWNER_TAKEOVER_MS;
        if (owner_ == Owner::None || same || silent) {
            owner_ = from; ip_ = ip; port_ = port; lastSeen_ = now;
            if (handler_) handler_(ctx_, line);
        } else {
            replyTo(from, ip, port, "BUSY another-pc-owns-this-guard");
        }
        return;
    }
    if (!same) {
        if (lastNotOwner_ == 0 || static_cast<uint32_t>(now - lastNotOwner_) > 1000) {
            lastNotOwner_ = now ? now : 1;
            replyTo(from, ip, port, "NOTOWNER send-HELLO-first");
        }
        return;
    }
    lastSeen_ = now;
    if (handler_ && strcmp(line, "PING") != 0) handler_(ctx_, line);
}

static void trimInPlace(char* s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\r' || s[n - 1] == '\n' || s[n - 1] == ' ')) s[--n] = '\0';
}

void PcLink::poll(uint32_t now) {
    if (udp_) {
        for (int guard = 0; guard < 8; ++guard) {          // bounded work per loop
            const int size = udp_->parsePacket();
            if (size <= 0) break;
            char buf[100];
            const int n = udp_->read(reinterpret_cast<uint8_t*>(buf), sizeof(buf) - 1);
            if (size >= static_cast<int>(sizeof(buf)) || n != size) continue;   // oversize: drop
            buf[n] = '\0';
            if (strlen(buf) != static_cast<size_t>(n)) continue;              // embedded NUL
            trimInPlace(buf);
            handle(buf, Owner::Udp, udp_->remoteIP(), udp_->remotePort(), now);
        }
    }
    while (Serial.available()) {
        const char ch = static_cast<char>(Serial.read());
        if (ch == '\n') {
            serialLine_[serialLen_] = '\0';
            if (!serialOverflow_ && serialLen_) {
                trimInPlace(serialLine_);
                handle(serialLine_, Owner::Serial, IPAddress(), 0, now);
            }
            serialLen_ = 0;
            serialOverflow_ = false;
        } else if (ch != '\r') {
            if (serialLen_ + 1 < sizeof(serialLine_)) serialLine_[serialLen_++] = ch;
            else serialOverflow_ = true;
        }
    }
}
