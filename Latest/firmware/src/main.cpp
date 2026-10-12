// RoboMaster TT ToF guard firmware V3 (ESP32 open-source controller).
//
// PC (Wi-Fi "TT-ToF-Demo", UDP 9000)  <->  this guard  <->  TT flight controller (UART)
//
// Protocol (one line per message, ASCII):
//   PC -> guard   HELLO                      claim the session        -> WELCOME TOFGUARD <ver> <bench|live> avoid=<0|1>
//                 PING                       heartbeat, every ~200 ms
//                 <id> SDK <tello command>   relay one whitelisted SDK 3.0 command
//                 <id> AVOID ON|OFF          enable / disable obstacle avoidance
//                 <id> STATUS
//   guard -> PC   ACK <id> OK|ERROR|REJECT|TIMEOUT|INTERRUPTED <detail>
//                 TOF f=<mm> b=<mm> l=<mm> r=<mm> ok=<ffff> cf=<mm/s> cb= cl= cr= avoid= air= busy= rs= bat=
//                 EVENT <name> <detail>
//
// Avoidance (when ON): pre-checks forward/back/left/right moves against the measured
// clearance, requires a clear perimeter for takeoff and rotation, stops a running move
// when the obstacle gets inside a speed-dependent braking distance, and backs off once
// when something comes closer than CRITICAL_MM while hovering. With avoidance OFF the
// guard only relays (the SDK whitelist and the PC heartbeat watchdog still apply).
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "Avoidance.h"
#include "Commands.h"
#include "GuardConfig.h"
#include "PcLink.h"
#include "TelloUart.h"
#include "ToFSensor.h"

#ifndef TOF_LIVE
#define TOF_LIVE 0
#endif

namespace {

ToFManager sensors;
Avoidance avoid;
WiFiUDP udp;
PcLink pc;
TelloUart tt(Serial1, TOF_LIVE != 0);

uint32_t lastId = 0;
uint32_t lastSample = 0, lastStatus = 0, lastRetreat = 0, lastTrapped = 0;
bool hoverSent = false, landSent = false;

void sendf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void sendf(const char* fmt, ...) {
    char line[200];
    va_list args;
    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    pc.send(line);
}

void onDone(void*, uint32_t id, const char* cmd, const char* status, const char* detail) {
    if (id) sendf("ACK %lu %s %s", static_cast<unsigned long>(id), status, detail);
    else sendf("EVENT INTERNAL %s %s %s", cmd, status, detail);
}

void internal(const char* text, uint32_t now, const char* reason = nullptr) {
    Command c;
    if (sdkcmd::parse(text, c)) tt.submit(0, c, now, reason);
}

void handleSdk(uint32_t id, const char* text, uint32_t now) {
    Command c;
    if (!sdkcmd::parse(text, c)) { sendf("ACK %lu REJECT not-whitelisted", (unsigned long)id); return; }
    if (!tt.sdkReady && c.kind != Kind::Enter && !c.urgent()) {
        sendf("ACK %lu REJECT sdk-not-ready-send-command-first", (unsigned long)id);
        return;
    }
    char why[72];
    if (!c.urgent() && !avoid.admit(c, now, tt.speed, why, sizeof(why))) {
        sendf("ACK %lu REJECT avoid:%s", (unsigned long)id, why);
        return;
    }
    if (tt.submit(id, c, now) == TelloUart::Submit::Busy) {
        sendf("ACK %lu REJECT busy:%s", (unsigned long)id, tt.active().text);
    }
}

void onFrame(void*, const char* line) {
    const uint32_t now = millis();
    if (strcmp(line, "HELLO") == 0) {
        lastId = 0;
        hoverSent = landSent = false;
        sendf("WELCOME TOFGUARD %s %s avoid=%d", guardcfg::FIRMWARE_VERSION, TOF_LIVE ? "live" : "bench",
              avoid.enabled ? 1 : 0);
        return;
    }
    char* rest = nullptr;
    const unsigned long id = strtoul(line, &rest, 10);
    if (id == 0 || rest == line || *rest != ' ') { pc.send("PROTOCOL_ERROR expected-<id>-<verb>"); return; }
    if (id <= lastId) { sendf("ACK %lu REJECT duplicate-or-old-id", id); return; }
    lastId = id;
    ++rest;
    if (strncmp(rest, "SDK ", 4) == 0) {
        handleSdk(id, rest + 4, now);
    } else if (strcmp(rest, "AVOID ON") == 0 || strcmp(rest, "AVOID OFF") == 0) {
        avoid.enabled = rest[7] == 'N';
        sendf("ACK %lu OK avoid=%d", id, avoid.enabled ? 1 : 0);
        sendf("EVENT AVOID %s", avoid.enabled ? "ON" : "OFF");
    } else if (strcmp(rest, "STATUS") == 0) {
        sendf("ACK %lu OK fw=%s mode=%s sdk=%d air=%d motors=%d avoid=%d bat=%d", id,
              guardcfg::FIRMWARE_VERSION, TOF_LIVE ? "live" : "bench", tt.sdkReady, tt.airborne,
              tt.motorsOn, avoid.enabled, tt.battery);
    } else {
        sendf("ACK %lu REJECT unknown-verb", id);
    }
}

void sampleSensors() {
    sensors.update();
    const ToFData* data[4] = {&sensors.front(), &sensors.rear(), &sensors.left(), &sensors.right()};
    for (int i = 0; i < 4; ++i) {
        const ToFData& d = *data[i];
        const bool valid = d.status == ToFStatus::VALID && d.raw > 0;
        const bool outOfRange = d.status == ToFStatus::SAFE || (valid && d.raw > guardcfg::SENSOR_MAX_MM);
        const uint16_t mm = d.raw < d.filtered ? d.raw : d.filtered;   // never wait for the filter
        avoid.update(i, valid && !outOfRange, outOfRange, mm, d.lastUpdate);
    }
}

void supervise(uint32_t now) {
    // PC heartbeat watchdog: hover first, then land.
    if (tt.airborne && pc.hasOwner()) {
        const uint32_t silent = now - pc.lastSeen();
        if (silent > guardcfg::HEARTBEAT_HOVER_MS && !hoverSent) {
            hoverSent = true;
            internal("stop", now, "heartbeat-lost");
            pc.send("EVENT HEARTBEAT_LOST hover");
        }
        if (silent > guardcfg::HEARTBEAT_LAND_MS && !landSent) {
            landSent = true;
            internal("land", now, "heartbeat-lost");
            pc.send("EVENT HEARTBEAT_LOST land");
        }
        if (silent < guardcfg::HEARTBEAT_HOVER_MS) hoverSent = landSent = false;
    }

    if (tt.airborne) {
        char why[72] = {0};
        int axis = -1;
        const Avoidance::Action action = avoid.monitor(tt.active(), now, lastRetreat, axis, why, sizeof(why));
        if (action == Avoidance::Action::Stop) {
            const Kind k = tt.active().kind;
            if (k != Kind::Stop && k != Kind::Land && k != Kind::Emergency) {
                char reason[80];
                snprintf(reason, sizeof(reason), "avoid:%s", why);
                internal("stop", now, reason);
                sendf("EVENT AVOID_STOP %s", why);
            }
        } else if (action == Avoidance::Action::Retreat && !tt.busy()) {
            char text[16];
            snprintf(text, sizeof(text), "%s %u", sdkcmd::MOVE_NAMES[axis], guardcfg::RETREAT_CM);
            lastRetreat = now;
            internal(text, now);
            sendf("EVENT AVOID_RETREAT %s", why);
        } else if (why[0] == 't' && now - lastTrapped > 2000) {
            lastTrapped = now;
            sendf("EVENT AVOID_TRAPPED %s", why);
        }
    }

    // Keep-alive: the SDK lands automatically after 15 s without a command.
    if (tt.sdkReady && (tt.airborne || tt.motorsOn) && !tt.busy() &&
        now - tt.lastControlTx > guardcfg::KEEPALIVE_MS) {
        internal("command", now);
    }
}

void reportStatus() {
    const RangeSample* r = avoid.r;
    sendf("TOF f=%u b=%u l=%u r=%u ok=%d%d%d%d cf=%d cb=%d cl=%d cr=%d avoid=%d air=%d busy=%d rs=%d bat=%d",
          r[0].mm, r[1].mm, r[2].mm, r[3].mm,
          avoid.fresh(0, millis()), avoid.fresh(1, millis()), avoid.fresh(2, millis()), avoid.fresh(3, millis()),
          (int)r[0].closing, (int)r[1].closing, (int)r[2].closing, (int)r[3].closing,
          avoid.enabled, tt.airborne, tt.busy(), tt.resyncNeeded, tt.battery);
}

}  // namespace

#ifdef GUARD_HOST_TEST
WiFiUDP* firmwareUdp() { return &udp; }   // host tests only
#endif

void setup() {
    Serial.begin(115200);
    Serial1.begin(guardcfg::TT_BAUD, SERIAL_8N1, guardcfg::TT_RX_PIN, guardcfg::TT_TX_PIN);
    Wire.begin(guardcfg::SDA_PIN, guardcfg::SCL_PIN);
    Wire.setClock(100000);
    Wire.setTimeOut(20);
    sensors.begin();
    WiFi.mode(WIFI_AP);
    WiFi.setSleep(false);
    WiFi.softAP(guardcfg::AP_SSID, guardcfg::AP_PASSWORD);
    pc.begin(udp, guardcfg::UDP_PORT);
    pc.setHandler(onFrame, nullptr);
    tt.setCallback(onDone, nullptr);
    Serial.printf("TOFGUARD %s %s ap=%s udp=%u front=0 back=1 left=4 right=3\n", guardcfg::FIRMWARE_VERSION,
                  TOF_LIVE ? "LIVE" : "BENCH(no flight controller)", guardcfg::AP_SSID, guardcfg::UDP_PORT);
}

void loop() {
    uint32_t now = millis();
    pc.poll(now);
    tt.loop(now);
    if (now - lastSample >= guardcfg::SAMPLE_PERIOD_MS) {
        sampleSensors();
        lastSample = now = millis();
    }
    supervise(now);
    if (now - lastStatus >= guardcfg::STATUS_PERIOD_MS) {
        lastStatus = now;
        reportStatus();
    }
}
