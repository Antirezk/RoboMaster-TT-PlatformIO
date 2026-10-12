#pragma once
// One-at-a-time relay of SDK commands to the TT flight controller over the internal UART
// ("[TELLO] <cmd>" out, "ETT <reply>" back, as documented in the SDK 3.0 guide).
//
// The SDK has no request IDs, so after a timeout or an urgent interrupt the next reply
// could belong to either command. The relay then marks itself "resyncNeeded" and, before
// the next normal command, sends battery? and waits for a numeric reply (a late "ok" can
// never look like a number).
#include <Arduino.h>

#include "Commands.h"

class TelloUart {
public:
    // status: OK | ERROR | TIMEOUT | INTERRUPTED
    typedef void (*DoneFn)(void* ctx, uint32_t id, const char* cmd, const char* status, const char* detail);
    enum class Submit : uint8_t { Started, Busy };

    TelloUart(Stream& port, bool live) : port_(port), live_(live) {}
    void setCallback(DoneFn fn, void* ctx) { done_ = fn; ctx_ = ctx; }

    // id 0 = internal (keep-alive, avoidance stop, retreat, heartbeat land).
    Submit submit(uint32_t id, const Command& c, uint32_t now, const char* reason = nullptr);
    void loop(uint32_t now);

    bool busy() const { return pending_.kind != Kind::Invalid; }
    const Command& active() const { return pending_; }
    bool resyncing() const { return resyncing_; }

    bool airborne = false;
    bool sdkReady = false;
    bool motorsOn = false;
    bool resyncNeeded = false;
    int speed = 10;           // SDK default is unknown; assume slow for timeouts
    int battery = -1;
    uint32_t lastControlTx = 0;

private:
    void start(uint32_t id, const Command& c, uint32_t now);
    void finish(const char* status, const char* detail);
    void onReply(const char* reply, uint32_t now);
    void benchSchedule(const Command& c, uint32_t now);

    Stream& port_;
    bool live_;
    DoneFn done_ = nullptr;
    void* ctx_ = nullptr;

    Command pending_;
    uint32_t pendingId_ = 0;
    uint32_t deadline_ = 0;
    bool ambiguous_ = false;

    bool resyncing_ = false;      // pending_ is an internal battery? for re-alignment
    Command deferred_;
    uint32_t deferredId_ = 0;

    // A PC command that arrived while an internal keep-alive was waiting; runs right after it.
    Command queued_;
    uint32_t queuedId_ = 0;

    char line_[128];
    size_t len_ = 0;
    bool overflow_ = false;

    uint32_t benchAt_ = 0;        // bench mode: when the simulated reply arrives
    char benchReply_[16] = {0};
};
