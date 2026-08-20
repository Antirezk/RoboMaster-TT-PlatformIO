#ifndef MISSION_PAD_MISSION_H
#define MISSION_PAD_MISSION_H

#include <Arduino.h>
#include "ObstacleAvoidance.h"
#include "TTController.h"

enum class MissionState : uint8_t
{
    SDK_START,
    ENABLE_PAD,
    SET_PAD_DIRECTION,
    READY,
    MANUAL_TAKEOFF,
    TAKEOFF,
    TAKEOFF_SETTLE,
    GO_TO_PAD,
    PAD_SETTLE,
    LANDING,
    ABORT_HOVER,
    ABORT_LANDING,
    COMPLETE,
    ERROR
};

class MissionPadMission
{
private:
    MissionState state = MissionState::SDK_START;
    uint32_t stateEnteredAt = 0;
    bool commandIssued = false;
    uint8_t retryCount = 0;
    uint8_t targetPadId = 1;
    uint8_t requestedPadId = 0;
    bool abortRequested = false;
    bool manualTakeoffRequested = false;
    bool airborne = false;
    char errorMessage[96] = {};

    void transition(MissionState next, uint32_t now);
    bool allDirectionsClear(const ObstacleAvoidance& safety) const;
    bool autonomousFlightState() const;
    void beginAbort(uint32_t now, TTController& drone, const char* reason);
    void fail(uint32_t now, TTController& drone, const char* reason);
    void issueStateCommand(uint32_t now, TTController& drone);
    void handleCommandResult(uint32_t now, TTController& drone);
    uint32_t commandTimeoutMs() const;

public:
    void begin(uint32_t now);
    void update(uint32_t now, TTController& drone, const ObstacleAvoidance& safety);
    bool requestStart(uint8_t padId);
    bool requestTakeoff();
    void requestAbort();

    bool manualControlAllowed() const;
    bool isReady() const;
    bool isAirborne() const;
    uint8_t targetPad() const;
    MissionState getState() const;
    const char* getError() const;
};

const char* missionStateName(MissionState state);

#endif
