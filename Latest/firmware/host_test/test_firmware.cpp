// Whole-firmware host test: main.cpp + TelloUart + PcLink + ToFSensor linked against stubs/.
// Build: g++ -std=c++11 -DTOF_LIVE=1 -DGUARD_HOST_TEST -Istubs -I../src test_firmware.cpp ../src/*.cpp
#include <assert.h>
#include <stdio.h>
#include <string>
#include "Arduino.h"
#include "WiFi.h"
#include "WiFiUdp.h"
#include "Wire.h"

uint32_t g_millis = 1;
HardwareSerial Serial, Serial1;
WiFiClass WiFi;
TwoWire Wire;
uint16_t g_tof_mm[8] = {1500, 1500, 8190, 1500, 1500, 8190, 8190, 8190};   // ch0 f, ch1 b, ch4 l, ch3 r
uint8_t g_pca_channel = 0;

void setup();
void loop();
// The firmware keeps its WiFiUDP in an anonymous namespace; reach it through this hook.
WiFiUDP* firmwareUdp();

static const IPAddress PC(192, 168, 4, 2);
static std::string received;   // everything the PC got so far

static void pump(uint32_t ms, bool ping = true) {
    for (uint32_t t = 0; t < ms; t += 5) {
        g_millis += 5;
        if (ping && g_millis % 200 < 5) firmwareUdp()->inbox.push_back(Packet{PC, 50000, "PING"});
        loop();
    }
    for (auto& p : firmwareUdp()->outbox) received += p.data;
    firmwareUdp()->outbox.clear();
}
static void send(const char* line) { firmwareUdp()->inbox.push_back(Packet{PC, 50000, line}); pump(10); }
static bool got(const char* s) { return received.find(s) != std::string::npos; }
static bool wire(const char* s) { return Serial1.tx.find(s) != std::string::npos; }
static void reply(const char* s) { Serial1.feed(s); Serial1.feed("\r\n"); pump(10); }
static void setFront(uint16_t mm) { g_tof_mm[0] = mm; }

int main() {
    setup();
    pump(100);
    send("1 SDK command");
    assert(got("NOTOWNER"));                        // must say HELLO first
    send("HELLO");
    assert(got("WELCOME TOFGUARD 3.0.0 live avoid=1"));
    send("1 SDK takeoff");
    assert(got("ACK 1 REJECT sdk-not-ready"));
    send("2 SDK command");
    assert(wire("[TELLO] command"));
    reply("ETT ok");
    assert(got("ACK 2 OK ok"));
    send("2 SDK battery?");
    assert(got("ACK 2 REJECT duplicate-or-old-id"));
    send("3 SDK rc 0 0 0 0");
    assert(got("ACK 3 REJECT not-whitelisted"));

    send("4 SDK takeoff");
    reply("ETT ok");
    assert(got("ACK 4 OK ok"));
    pump(200);
    assert(got("air=1"));

    send("5 SDK forward 200");                      // needs 2400 mm: cannot be verified
    assert(got("ACK 5 REJECT avoid:obstacle-front-1500mm-need-2460mm"));
    send("6 SDK forward 50");                       // needs 900 mm, have 1500
    assert(wire("[TELLO] forward 50"));
    send("7 SDK cw 90");
    assert(got("ACK 7 REJECT busy:forward 50"));    // no queueing

    for (uint16_t d = 1500; d > 600 && !got("AVOID_STOP"); d -= 40) { setFront(d); pump(50); }
    assert(got("EVENT AVOID_STOP obstacle-front"));
    assert(got("ACK 6 INTERRUPTED avoid:obstacle-front"));
    assert(wire("[TELLO] stop"));
    reply("ETT ok");                                // reply to stop (or the late move reply)
    setFront(900);
    pump(400);

    Serial1.tx.clear();
    send("8 SDK cw 90");                            // must re-align first
    assert(wire("[TELLO] battery?") && !wire("[TELLO] cw 90"));
    reply("ETT ok");                                // a late "ok" is ignored
    assert(!wire("[TELLO] cw 90"));
    reply("ETT 80");
    assert(wire("[TELLO] cw 90"));
    reply("ETT ok");
    assert(got("ACK 8 OK ok"));

    // hovering with something 200 mm in front -> back off once
    setFront(200);
    pump(300);
    assert(got("EVENT AVOID_RETREAT critical-front") && wire("[TELLO] back 20"));
    reply("ETT ok");
    setFront(900);
    pump(300);

    // avoidance OFF: pure relay
    send("9 AVOID OFF");
    assert(got("ACK 9 OK avoid=0") && got("EVENT AVOID OFF"));
    send("10 SDK forward 200");
    assert(wire("[TELLO] forward 200"));
    reply("ETT ok");
    assert(got("ACK 10 OK"));
    send("11 AVOID ON");

    // keep-alive while hovering idle; a PC command arriving meanwhile is not refused
    Serial1.tx.clear();
    pump(5600);
    assert(wire("[TELLO] command"));
    send("12 SDK up 30");
    assert(!got("ACK 12 REJECT") && !wire("[TELLO] up 30"));
    reply("ETT ok");                                // keep-alive reply ...
    assert(wire("[TELLO] up 30"));                  // ... then the queued command runs
    reply("ETT ok");
    assert(got("ACK 12 OK ok"));

    // PC goes silent: hover, then land
    Serial1.tx.clear();
    pump(1700, false);
    assert(got("EVENT HEARTBEAT_LOST hover") && wire("[TELLO] stop"));
    reply("ETT ok");
    pump(4600, false);
    assert(got("EVENT HEARTBEAT_LOST land") && wire("[TELLO] land"));
    reply("ETT ok");
    pump(200, false);
    assert(got("air=0"));

    // another PC may take over a silent session without a reboot
    firmwareUdp()->inbox.push_back(Packet{IPAddress(192, 168, 4, 3), 50001, "HELLO"});
    pump(10);
    assert(got("WELCOME"));
    puts("firmware integration test passed");
    return 0;
}
