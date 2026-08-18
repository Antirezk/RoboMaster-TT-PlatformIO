#include <Arduino.h>
#include <Wire.h>

#include "CommandInput.h"
#include "MissionPadMission.h"
#include "ObstacleAvoidance.h"
#include "TTController.h"
#include "ToFSensor.h"

namespace
{
constexpr uint32_t CONTROL_PERIOD_MS = 100;
constexpr uint32_t STATUS_PERIOD_MS = 500;
constexpr uint32_t COMMAND_TIMEOUT_MS = 500;
constexpr uint8_t MISSION_BUTTON_PIN = 34;
constexpr uint32_t MISSION_BUTTON_HOLD_MS = 1500;

ToFManager sensors;
ObstacleAvoidance safety;
TTController drone;
CommandInput commandInput(COMMAND_TIMEOUT_MS);
MissionPadMission mission;

uint32_t lastControlTime = 0;
uint32_t lastStatusTime = 0;
uint32_t buttonPressedAt = 0;
bool buttonPressed = false;
bool buttonHandled = false;
bool buttonReleasedSinceBoot = false;

void printRC(const char* name, const RCCommand& command)
{
    Serial.printf(
        "%s: %d %d %d %d\n",
        name, command.lr, command.fb, command.ud, command.yaw
    );
}

void printSensor(const char* name, const ToFData& data)
{
    Serial.printf("%s:", name);
    switch (data.status)
    {
        case ToFStatus::VALID:
            Serial.printf("%u/%umm", data.filtered, data.raw);
            break;
        case ToFStatus::SAFE:
            Serial.print("SAFE");
            break;
        case ToFStatus::OVERTIME:
            Serial.printf(
                "OVERTIME(fail=%u/ok=%u/try=%u/i2c=%u/id=%02X)",
                data.consecutiveFailures, data.recoveryCount,
                data.recoveryAttempts, data.lastI2cStatus, data.lastModelId
            );
            break;
        case ToFStatus::ERROR:
            Serial.printf(
                "ERROR(fail=%u/ok=%u/try=%u/i2c=%u/id=%02X)",
                data.consecutiveFailures, data.recoveryCount,
                data.recoveryAttempts, data.lastI2cStatus, data.lastModelId
            );
            break;
    }
}

void printDirection(const char* name, const DirectionState& state)
{
    Serial.printf("%s:%s(%u)", name, avoidanceModeName(state.mode), state.distance);
}

bool anySafetyStateActive()
{
    return safety.front().mode != AvoidanceMode::NORMAL ||
           safety.rear().mode != AvoidanceMode::NORMAL ||
           safety.left().mode != AvoidanceMode::NORMAL ||
           safety.right().mode != AvoidanceMode::NORMAL;
}

const char* safetyDecisionName(const RCCommand& desired, const RCCommand& safe)
{
    if (safety.isIntervening(desired, safe)) return "OVERRIDE";
    if (anySafetyStateActive()) return "HOLD/CONSTRAINED";
    return "PASS";
}

void printStatus(uint32_t now, const RCCommand& desired, const RCCommand& safe)
{
    Serial.println("--------------------------------");
    Serial.printf(
        "MISSION: %s  pad=m%u  airborne=%u\n",
        missionStateName(mission.getState()),
        mission.targetPad(),
        mission.isAirborne()
    );
    if (mission.getError()[0] != '\0')
        Serial.printf("MISSION ERROR: %s\n", mission.getError());

    if (!mission.manualControlAllowed())
        Serial.println("SOURCE: MISSION EXECUTIVE");
    else if (commandInput.isFresh(now))
        Serial.printf("SOURCE: HIGH-LEVEL  age=%ums\n", commandInput.age(now));
    else
        Serial.println("SOURCE: FAILSAFE ZERO (command missing/stale)");

    printRC("DESIRED", desired);
    printRC("SAFE", safe);
    Serial.printf("SAFETY: %s\n", safetyDecisionName(desired, safe));

    printSensor("F", sensors.front()); Serial.print("  ");
    printSensor("B", sensors.rear()); Serial.print("  ");
    printSensor("L", sensors.left()); Serial.print("  ");
    printSensor("R", sensors.right()); Serial.println();

    printDirection("F", safety.front()); Serial.print("  ");
    printDirection("B", safety.rear()); Serial.print("  ");
    printDirection("L", safety.left()); Serial.print("  ");
    printDirection("R", safety.right()); Serial.println();
}

void handleActionRequests()
{
    ActionRequest request;
    while (commandInput.takeAction(request))
    {
        if (request.action == HighLevelAction::START_PAD_MISSION)
        {
            if (!mission.requestStart(request.padId))
                Serial.println("MISSION request rejected: executive is not READY");
        }
        else if (request.action == HighLevelAction::LAND ||
                 request.action == HighLevelAction::ABORT)
        {
            mission.requestAbort();
        }
    }
}

void updateMissionButton(uint32_t now)
{
    bool down = digitalRead(MISSION_BUTTON_PIN) == LOW;

    if (!down)
    {
        buttonReleasedSinceBoot = true;
        buttonPressed = false;
        buttonHandled = false;
        return;
    }

    // Require one release after boot, preventing a stuck/held button from starting a mission.
    if (!buttonReleasedSinceBoot)
        return;

    if (!buttonPressed)
    {
        buttonPressed = true;
        buttonPressedAt = now;
        return;
    }

    if (!buttonHandled && now - buttonPressedAt >= MISSION_BUTTON_HOLD_MS)
    {
        buttonHandled = true;
        if (mission.isAirborne())
        {
            Serial.println("BUTTON: abort and land requested");
            mission.requestAbort();
        }
        else if (mission.requestStart(1))
        {
            Serial.println("BUTTON: Mission Pad m1 requested");
        }
        else
        {
            Serial.println("BUTTON: mission not READY");
        }
    }
}
}

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("================================");
    Serial.println("RoboMaster TT Mission Pad MVP");
    Serial.println("Mission Executive + ToF Safety Supervisor");
    Serial.println("================================");

    pinMode(MISSION_BUTTON_PIN, INPUT_PULLUP);

    Wire.begin(27, 26);
    Wire.setClock(100000);
    Wire.setTimeOut(20);

    if (!sensors.begin())
        Serial.println("WARNING: one or more ToF sensors failed initialization");

    drone.begin();
    drone.setDebug(true);
    mission.begin(millis());

    Serial.println("Commands: rc ... | mission <1..8> | abort | land");
    Serial.println("Physical button GPIO34: hold 1.5s for m1; hold while airborne to abort/land");
}

void loop()
{
    drone.poll();
    commandInput.poll(Serial);
    handleActionRequests();

    uint32_t now = millis();
    updateMissionButton(now);

    if (now - lastControlTime < CONTROL_PERIOD_MS)
        return;

    lastControlTime = now;
    sensors.update();
    now = millis();

    RCCommand desired = commandInput.getDesired(now);
    RCCommand safe = safety.apply(desired, sensors, now);

    mission.update(now, drone, safety);

    // RC is sent only in READY/manual mode. Action commands own the TT link during a mission.
    if (mission.manualControlAllowed())
        drone.sendRC(safe);

    if (now - lastStatusTime >= STATUS_PERIOD_MS)
    {
        lastStatusTime = now;
        printStatus(now, desired, safe);
    }
}
