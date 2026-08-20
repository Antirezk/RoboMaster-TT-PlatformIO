#include "MissionPadMission.h"
#include <string.h>

namespace
{
constexpr uint8_t MAX_COMMAND_RETRIES = 2;
constexpr uint32_t TAKEOFF_SETTLE_MS = 1500;
constexpr uint32_t PAD_SETTLE_MS = 1000;
constexpr uint32_t ABORT_HOVER_MS = 300;
}

void MissionPadMission::begin(uint32_t now)
{
    state = MissionState::SDK_START;
    stateEnteredAt = now;
    commandIssued = false;
    retryCount = 0;
    requestedPadId = 0;
    abortRequested = false;
    manualTakeoffRequested = false;
    airborne = false;
    errorMessage[0] = '\0';
}

void MissionPadMission::transition(MissionState next, uint32_t now)
{
    state = next;
    stateEnteredAt = now;
    commandIssued = false;
    retryCount = 0;
    Serial.printf("MISSION -> %s\n", missionStateName(next));
}

bool MissionPadMission::allDirectionsClear(const ObstacleAvoidance& safety) const
{
    return safety.front().mode == AvoidanceMode::NORMAL &&
           safety.rear().mode == AvoidanceMode::NORMAL &&
           safety.left().mode == AvoidanceMode::NORMAL &&
           safety.right().mode == AvoidanceMode::NORMAL;
}

bool MissionPadMission::autonomousFlightState() const
{
    return state == MissionState::MANUAL_TAKEOFF ||
           state == MissionState::TAKEOFF ||
           state == MissionState::TAKEOFF_SETTLE ||
           state == MissionState::GO_TO_PAD ||
           state == MissionState::PAD_SETTLE;
}

void MissionPadMission::beginAbort(
    uint32_t now,
    TTController& drone,
    const char* reason)
{
    Serial.printf("MISSION ABORT: %s\n", reason);
    drone.forceZeroRC();
    drone.cancelPendingCommand();
    abortRequested = false;
    transition(MissionState::ABORT_HOVER, now);
}

void MissionPadMission::fail(
    uint32_t now,
    TTController& drone,
    const char* reason)
{
    strncpy(errorMessage, reason, sizeof(errorMessage) - 1);
    errorMessage[sizeof(errorMessage) - 1] = '\0';

    if (airborne)
        beginAbort(now, drone, errorMessage);
    else
        transition(MissionState::ERROR, now);
}

uint32_t MissionPadMission::commandTimeoutMs() const
{
    switch (state)
    {
        case MissionState::MANUAL_TAKEOFF:
        case MissionState::TAKEOFF: return 15000;
        case MissionState::GO_TO_PAD: return 20000;
        case MissionState::LANDING:
        case MissionState::ABORT_LANDING: return 15000;
        default: return 2500;
    }
}

void MissionPadMission::issueStateCommand(uint32_t now, TTController& drone)
{
    if (commandIssued)
        return;

    bool sent = false;
    switch (state)
    {
        case MissionState::SDK_START:
            sent = drone.requestSDKMode();
            break;
        case MissionState::ENABLE_PAD:
            sent = drone.enableMissionPads();
            break;
        case MissionState::SET_PAD_DIRECTION:
            sent = drone.setMissionPadDirection(0); // downward only, 20 Hz
            break;
        case MissionState::MANUAL_TAKEOFF:
        case MissionState::TAKEOFF:
            sent = drone.takeOff();
            break;
        case MissionState::GO_TO_PAD:
            sent = drone.goToMissionPad(targetPadId, 0, 0, 50, 20);
            break;
        case MissionState::LANDING:
        case MissionState::ABORT_LANDING:
            sent = drone.land();
            break;
        default:
            return;
    }

    if (sent)
    {
        // Treat the aircraft as potentially airborne as soon as TAKEOFF is
        // accepted by the UART. If its reply is lost, the timeout path must
        // still choose an abort/land sequence instead of assuming it is safe.
        if (state == MissionState::TAKEOFF || state == MissionState::MANUAL_TAKEOFF)
            airborne = true;

        commandIssued = true;
        stateEnteredAt = now;
    }
}

void MissionPadMission::handleCommandResult(uint32_t now, TTController& drone)
{
    if (!commandIssued)
        return;

    TTResponse response;
    if (drone.takeResponse(response))
    {
        commandIssued = false;

        if (response.type != TTResponseType::OK)
        {
            char message[96] = {};
            snprintf(message, sizeof(message), "%s: %s", missionStateName(state), response.text);
            fail(now, drone, message);
            return;
        }

        switch (state)
        {
            case MissionState::SDK_START:
                transition(MissionState::ENABLE_PAD, now);
                break;
            case MissionState::ENABLE_PAD:
                transition(MissionState::SET_PAD_DIRECTION, now);
                break;
            case MissionState::SET_PAD_DIRECTION:
                transition(MissionState::READY, now);
                break;
            case MissionState::MANUAL_TAKEOFF:
                airborne = true;
                transition(MissionState::READY, now);
                break;
            case MissionState::TAKEOFF:
                airborne = true;
                transition(MissionState::TAKEOFF_SETTLE, now);
                break;
            case MissionState::GO_TO_PAD:
                transition(MissionState::PAD_SETTLE, now);
                break;
            case MissionState::LANDING:
            case MissionState::ABORT_LANDING:
                airborne = false;
                transition(MissionState::COMPLETE, now);
                break;
            default:
                break;
        }
        return;
    }

    if (drone.commandTimedOut(now, commandTimeoutMs()))
    {
        drone.cancelPendingCommand();
        commandIssued = false;

        if (retryCount < MAX_COMMAND_RETRIES &&
            state != MissionState::GO_TO_PAD &&
            state != MissionState::TAKEOFF)
        {
            ++retryCount;
            Serial.printf(
                "MISSION retry %s (%u/%u)\n",
                missionStateName(state), retryCount, MAX_COMMAND_RETRIES
            );
        }
        else
        {
            char message[96] = {};
            snprintf(message, sizeof(message), "%s response timeout", missionStateName(state));
            fail(now, drone, message);
        }
    }
}

void MissionPadMission::update(
    uint32_t now,
    TTController& drone,
    const ObstacleAvoidance& safety)
{
    if (abortRequested && state != MissionState::ABORT_HOVER &&
        state != MissionState::ABORT_LANDING)
    {
        beginAbort(now, drone, "operator request");
    }

    if (autonomousFlightState() && !allDirectionsClear(safety))
    {
        beginAbort(now, drone, "ToF blocked, escape, or sensor fault");
    }

    switch (state)
    {
        case MissionState::READY:
            if (manualTakeoffRequested)
            {
                manualTakeoffRequested = false;
                if (!allDirectionsClear(safety))
                    Serial.println("MANUAL TAKEOFF rejected: ToF safety is not NORMAL");
                else
                    transition(MissionState::MANUAL_TAKEOFF, now);
            }
            else if (requestedPadId != 0)
            {
                if (!allDirectionsClear(safety))
                {
                    Serial.println("MISSION rejected: all four directions must be NORMAL");
                    requestedPadId = 0;
                }
                else
                {
                    targetPadId = requestedPadId;
                    requestedPadId = 0;
                    errorMessage[0] = '\0';
                    transition(MissionState::TAKEOFF, now);
                }
            }
            break;

        case MissionState::TAKEOFF_SETTLE:
            if (now - stateEnteredAt >= TAKEOFF_SETTLE_MS)
                transition(MissionState::GO_TO_PAD, now);
            break;

        case MissionState::PAD_SETTLE:
            if (now - stateEnteredAt >= PAD_SETTLE_MS)
                transition(MissionState::LANDING, now);
            break;

        case MissionState::ABORT_HOVER:
            if (now - stateEnteredAt >= ABORT_HOVER_MS)
                transition(MissionState::ABORT_LANDING, now);
            break;

        case MissionState::COMPLETE:
            if (now - stateEnteredAt >= 2000)
                transition(MissionState::READY, now);
            break;

        default:
            break;
    }

    issueStateCommand(now, drone);
    handleCommandResult(now, drone);
}

bool MissionPadMission::requestStart(uint8_t padId)
{
    if (state != MissionState::READY || airborne || padId < 1 || padId > 8)
        return false;
    requestedPadId = padId;
    return true;
}

bool MissionPadMission::requestTakeoff()
{
    if (state != MissionState::READY || airborne || manualTakeoffRequested)
        return false;
    manualTakeoffRequested = true;
    return true;
}

void MissionPadMission::requestAbort()
{
    abortRequested = true;
}

bool MissionPadMission::manualControlAllowed() const
{
    return state == MissionState::READY;
}

bool MissionPadMission::isReady() const { return state == MissionState::READY; }
bool MissionPadMission::isAirborne() const { return airborne; }
uint8_t MissionPadMission::targetPad() const { return targetPadId; }
MissionState MissionPadMission::getState() const { return state; }
const char* MissionPadMission::getError() const { return errorMessage; }

const char* missionStateName(MissionState state)
{
    switch (state)
    {
        case MissionState::SDK_START: return "SDK_START";
        case MissionState::ENABLE_PAD: return "ENABLE_PAD";
        case MissionState::SET_PAD_DIRECTION: return "SET_PAD_DIRECTION";
        case MissionState::READY: return "READY";
        case MissionState::MANUAL_TAKEOFF: return "MANUAL_TAKEOFF";
        case MissionState::TAKEOFF: return "TAKEOFF";
        case MissionState::TAKEOFF_SETTLE: return "TAKEOFF_SETTLE";
        case MissionState::GO_TO_PAD: return "GO_TO_PAD";
        case MissionState::PAD_SETTLE: return "PAD_SETTLE";
        case MissionState::LANDING: return "LANDING";
        case MissionState::ABORT_HOVER: return "ABORT_HOVER";
        case MissionState::ABORT_LANDING: return "ABORT_LANDING";
        case MissionState::COMPLETE: return "COMPLETE";
        case MissionState::ERROR: return "ERROR";
    }
    return "UNKNOWN";
}
