#include "TelloUart.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

TelloUart::Submit TelloUart::submit(uint32_t id, const Command& c, uint32_t now, const char* reason) {
    if (busy()) {
        if (!c.urgent()) {
            // Never let our own keep-alive block the operator: run the command right after it.
            const bool keepAlive = pendingId_ == 0 && pending_.kind == Kind::Enter && !resyncing_;
            if (keepAlive && queued_.kind == Kind::Invalid) {
                queued_ = c;
                queuedId_ = id;
                return Submit::Started;
            }
            return Submit::Busy;
        }
        if (queued_.kind != Kind::Invalid) {
            Command q = queued_;
            queued_ = Command();
            if (done_) done_(ctx_, queuedId_, q.text, "INTERRUPTED", reason ? reason : c.text);
        }
        // Urgent command pre-empts whatever is waiting. Report the old one first.
        char detail[64];
        snprintf(detail, sizeof(detail), "%s", reason ? reason : c.text);
        if (resyncing_) {
            resyncing_ = false;
            pending_ = Command();
            if (deferred_.kind != Kind::Invalid && done_) {
                done_(ctx_, deferredId_, deferred_.text, "INTERRUPTED", detail);
            }
            deferred_ = Command();
        } else {
            const uint32_t oldId = pendingId_;
            Command old = pending_;
            pending_ = Command();
            if (old.kind == Kind::Takeoff) airborne = true;   // may already be flying
            if (done_) done_(ctx_, oldId, old.text, "INTERRUPTED", detail);
        }
        resyncNeeded = true;
        start(id, c, now);
        ambiguous_ = true;
        return Submit::Started;
    }
    if (resyncNeeded && !c.urgent() && c.kind != Kind::Emergency) {
        deferred_ = c;
        deferredId_ = id;
        Command probe;
        sdkcmd::parse("battery?", probe);
        resyncing_ = true;
        start(0, probe, now);
        return Submit::Started;
    }
    start(id, c, now);
    return Submit::Started;
}

void TelloUart::start(uint32_t id, const Command& c, uint32_t now) {
    pending_ = c;
    pendingId_ = id;
    ambiguous_ = false;
    deadline_ = now + sdkcmd::timeoutMs(c, speed);
    if (live_) {
        port_.print("[TELLO] ");
        port_.print(c.text);
    } else {
        benchSchedule(c, now);
    }
    if (!c.isRead()) lastControlTx = now;
    if (c.kind == Kind::Emergency) {
        airborne = false;
        motorsOn = false;
        resyncNeeded = true;
        finish("OK", "sent-no-reply");
    }
}

void TelloUart::finish(const char* status, const char* detail) {
    const uint32_t id = pendingId_;
    char cmd[sizeof(pending_.text)];
    memcpy(cmd, pending_.text, sizeof(cmd));
    const bool ambiguous = ambiguous_;
    pending_ = Command();
    ambiguous_ = false;
    benchAt_ = 0;
    if (done_) done_(ctx_, id, cmd, status, ambiguous && strcmp(status, "OK") == 0 ? "ok-after-interrupt" : detail);
    if (queued_.kind != Kind::Invalid && !busy()) {
        Command next = queued_;
        queued_ = Command();
        submit(queuedId_, next, millis());
    }
}

void TelloUart::onReply(const char* reply, uint32_t now) {
    if (!busy()) return;   // late or unsolicited reply: ignore (resync handles alignment)
    if (resyncing_) {
        if (!sdkcmd::replyOk(pending_, reply)) return;   // late "ok": keep waiting for the number
        battery = atoi(reply);
        resyncing_ = false;
        resyncNeeded = false;
        pending_ = Command();
        if (deferred_.kind != Kind::Invalid) {
            Command next = deferred_;
            deferred_ = Command();
            start(deferredId_, next, now);
        }
        return;
    }
    const bool ok = sdkcmd::replyOk(pending_, reply);
    if (ok) {
        switch (pending_.kind) {
            case Kind::Enter: sdkReady = true; break;
            case Kind::Takeoff: airborne = true; motorsOn = false; break;   // motoron ends on takeoff
            case Kind::Land: airborne = false; break;
            case Kind::MotorOn: motorsOn = true; break;
            case Kind::MotorOff: motorsOn = false; break;
            case Kind::Speed: speed = pending_.value; break;
            case Kind::Read:
                if (strcmp(pending_.text, "battery?") == 0) battery = atoi(reply);
                break;
            default: break;
        }
    }
    finish(ok ? "OK" : "ERROR", reply);
}

void TelloUart::loop(uint32_t now) {
    if (live_) {
        while (port_.available()) {
            const char ch = static_cast<char>(port_.read());
            if (ch == '\n') {
                line_[len_] = '\0';
                if (!overflow_ && strncmp(line_, "ETT ", 4) == 0) onReply(line_ + 4, now);
                len_ = 0;
                overflow_ = false;
            } else if (ch != '\r') {
                if (len_ + 1 < sizeof(line_)) line_[len_++] = ch;
                else overflow_ = true;
            }
        }
    } else if (benchAt_ && static_cast<int32_t>(now - benchAt_) >= 0) {
        benchAt_ = 0;
        char reply[sizeof(benchReply_)];
        memcpy(reply, benchReply_, sizeof(reply));
        onReply(reply, now);
    }
    if (busy() && pending_.kind != Kind::Emergency && static_cast<int32_t>(now - deadline_) >= 0) {
        if (resyncing_) {
            resyncing_ = false;
            pending_ = Command();
            const uint32_t id = deferredId_;
            Command lost = deferred_;
            deferred_ = Command();
            if (done_ && lost.kind != Kind::Invalid) done_(ctx_, id, lost.text, "ERROR", "resync-timeout");
            return;
        }
        if (pending_.kind == Kind::Takeoff) airborne = true;   // unknown -> assume flying
        resyncNeeded = true;
        finish("TIMEOUT", "no-reply-not-resent");
    }
}

// Bench build: no flight controller attached. Replies arrive after a short, motion-like delay.
void TelloUart::benchSchedule(const Command& c, uint32_t now) {
    const char* reply = "ok";
    uint32_t delayMs = 20;
    switch (c.kind) {
        case Kind::Takeoff: delayMs = 1500; if (airborne) reply = "error"; break;
        case Kind::Land: delayMs = 1500; break;
        case Kind::Move:
            delayMs = 300 + 1000UL * static_cast<uint32_t>(c.value) / (speed > 0 ? speed : 10);
            if (delayMs > 4000) delayMs = 4000;
            if (!airborne) reply = "error Motor stop";
            break;
        case Kind::Turn:
            delayMs = 300 + static_cast<uint32_t>(c.value) * 10;
            if (!airborne) reply = "error Motor stop";
            break;
        case Kind::MotorOn: if (airborne) reply = "error"; break;
        case Kind::Emergency: return;
        case Kind::Read:
            if (strcmp(c.text, "battery?") == 0) reply = "85";
            else if (strcmp(c.text, "sdk?") == 0) reply = "30";
            else if (strcmp(c.text, "hardware?") == 0) reply = "RMTT";
            else if (strcmp(c.text, "speed?") == 0) reply = "10.0";
            else if (strcmp(c.text, "time?") == 0) reply = "0s";
            else if (strcmp(c.text, "wifi?") == 0) reply = "90";
            else reply = "0TQBENCH";
            break;
        default: break;
    }
    snprintf(benchReply_, sizeof(benchReply_), "%s", reply);
    benchAt_ = now + delayMs;
    if (benchAt_ == 0) benchAt_ = 1;
}
