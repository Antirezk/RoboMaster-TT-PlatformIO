// Host unit tests for Commands.h and Avoidance.h:  g++ -std=c++11 -I../src test_policy.cpp
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "Avoidance.h"
#include "Commands.h"

static Command C(const char* s) { Command c; assert(sdkcmd::parse(s, c)); return c; }

int main() {
    Command c;
    // ---- SDK 3.0 whitelist and ranges
    const char* good[] = {"command", "takeoff", "land", "stop", "emergency", "motoron", "motoroff", "battery?",
                          "forward 20", "back 500", "up 100", "down 20", "cw 1", "ccw 360", "speed 10", "speed 100",
                          "sdk?", "sn?", "hardware?", "wifi?", "time?", "speed?"};
    for (const char* s : good) assert(sdkcmd::parse(s, c));
    const char* bad[] = {"forward 19", "forward 501", "forward 050", "cw 0", "cw 361", "speed 9", "speed 101",
                         "rc 0 0 0 0", "flip f", "go 50 0 0 30", "reboot", "forward 30 ", " forward 30", "forward -30",
                         "", "forward 9999", "wifi a b", "EXT led 0 0 0", "takeoff\n"};
    for (const char* s : bad) assert(!sdkcmd::parse(s, c));
    c = C("left 70"); assert(c.kind == Kind::Move && c.axis == LEFT && c.value == 70);
    c = C("ccw 45"); assert(c.kind == Kind::Turn && c.axis == 1 && c.value == 45);
    assert(C("stop").urgent() && C("land").urgent() && C("emergency").urgent() && C("motoroff").urgent());
    assert(!C("forward 50").urgent());
    assert(sdkcmd::replyOk(C("battery?"), "87") && !sdkcmd::replyOk(C("battery?"), "ok"));
    assert(sdkcmd::replyOk(C("takeoff"), "ok") && !sdkcmd::replyOk(C("takeoff"), "error"));
    assert(sdkcmd::timeoutMs(C("forward 100"), 20) == 8000 + 7500);

    // ---- admission
    Avoidance a;
    char why[80];
    uint32_t t = 1000;
    for (int i = 0; i < 4; ++i) a.update(i, true, false, 1500, t);
    assert(a.admit(C("forward 100"), t, 10, why, sizeof why));      // need 1000+400+60 = 1460 < 1500
    assert(!a.admit(C("forward 110"), t, 10, why, sizeof why));     // need 1560
    assert(!a.admit(C("forward 100"), t, 30, why, sizeof why));     // faster -> longer braking: 1580
    assert(strstr(why, "obstacle-front"));
    assert(!a.admit(C("forward 170"), t, 10, why, sizeof why));          // 2100 > sensor range
    a.update(1, false, true, 0, t);                                   // back: out of range -> 2000
    assert(a.admit(C("back 150"), t, 10, why, sizeof why));              // need 1960 < 2000
    assert(!a.admit(C("back 160"), t, 10, why, sizeof why) && strstr(why, "beyond-sensor-range-max-153cm"));
    assert(a.admit(C("up 200"), t, 10, why, sizeof why));                // no vertical sensors
    assert(a.admit(C("cw 90"), t, 10, why, sizeof why) && a.admit(C("takeoff"), t, 10, why, sizeof why));
    a.update(2, true, false, 280, t);
    assert(!a.admit(C("cw 90"), t, 10, why, sizeof why) && strstr(why, "too-close-left"));
    assert(a.admit(C("right 50"), t, 10, why, sizeof why));              // moving away is fine
    assert(!a.admit(C("forward 20"), t + 400, 10, why, sizeof why) && strstr(why, "invalid"));   // stale
    a.update(3, false, false, 0, t);
    assert(!a.admit(C("forward 20"), t, 10, why, sizeof why) && strstr(why, "sensor-right"));
    a.enabled = false;
    assert(a.admit(C("forward 500"), t, 10, why, sizeof why));           // OFF: relay only
    a.enabled = true;

    // ---- monitoring
    Avoidance m;
    uint32_t lastRetreat = 0;
    int axis = -1;
    for (int i = 0; i < 4; ++i) m.update(i, true, false, 1500, 0);
    Command hover;                                                     // Invalid = nothing running
    assert(m.monitor(C("forward 50"), 10, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);
    // approach at ~1 m/s: braking distance grows beyond the 400 mm base
    uint16_t d = 1500;
    uint32_t now = 0;
    Avoidance::Action act = Avoidance::Action::None;
    while (d > 300 && act == Avoidance::Action::None) {
        now += 50; d -= 50;
        for (int i = 0; i < 4; ++i) m.update(i, true, false, i == 0 ? d : 1500, now);
        act = m.monitor(C("forward 100"), now, lastRetreat, axis, why, sizeof why);
    }
    assert(act == Avoidance::Action::Stop && d > 400);                // stopped earlier than the static 400 mm
    printf("closing-speed stop at %u mm (limit %u)\n", d, m.stopDistance(0));
    // the same obstacle does not stop a move in another direction ...
    for (int k = 0; k < 10; ++k) { now += 50; for (int i = 0; i < 4; ++i) m.update(i, true, false, i == 0 ? 600 : 1500, now); }
    assert(m.monitor(C("back 50"), now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);
    // ... unless it is critical
    now += 50;
    for (int i = 0; i < 4; ++i) m.update(i, true, false, i == 0 ? 200 : 1500, now);
    assert(m.monitor(C("left 50"), now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::Stop);
    assert(m.monitor(C("back 20"), now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);  // moving away
    // hovering + critical -> retreat to the opposite side, then cooldown
    lastRetreat = now - 10000;
    assert(m.monitor(hover, now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::Retreat && axis == BACK);
    lastRetreat = now;
    assert(m.monitor(hover, now + 100, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);
    // trapped: both sides close -> no retreat
    now += 3000;
    for (int i = 0; i < 4; ++i) m.update(i, true, false, i < 2 ? 200 : 1500, now);
    lastRetreat = 0;
    why[0] = 0;
    assert(m.monitor(hover, now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None && strstr(why, "trapped"));
    // sensor lost while moving -> stop; while hovering -> nothing
    m.update(3, false, false, 0, now);
    assert(m.monitor(C("up 50"), now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::Stop);
    assert(m.monitor(hover, now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);
    m.enabled = false;
    assert(m.monitor(C("forward 50"), now, lastRetreat, axis, why, sizeof why) == Avoidance::Action::None);
    puts("policy tests passed");
    return 0;
}
