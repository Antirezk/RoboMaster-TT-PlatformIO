#ifndef OBSTACLE_AVOIDANCE_H
#define OBSTACLE_AVOIDANCE_H

#include <Arduino.h>
#include "RCCommand.h"
#include "ToFSensor.h"

enum class AvoidanceMode : uint8_t
{
    NORMAL,
    BLOCKED,
    ESCAPE,
    SENSOR_FAULT
};

struct DirectionState
{
    AvoidanceMode mode = AvoidanceMode::SENSOR_FAULT;
    uint16_t distance = 0;
    uint8_t healthySamples = 0;
};

struct AvoidanceConfig
{
    uint16_t escapeEnterMm = 300;
    uint16_t escapeExitMm = 450;
    uint16_t blockedEnterMm = 500;
    uint16_t blockedExitMm = 700;
    uint16_t slowStartMm = 900;
    int16_t minimumApproachSpeed = 15;
    int16_t escapeSpeed = 25;
    uint32_t sensorFreshnessMs = 300;
    uint8_t healthySamplesToRecover = 3;
};

class ObstacleAvoidance
{
private:
    AvoidanceConfig config;
    DirectionState frontState;
    DirectionState rearState;
    DirectionState leftState;
    DirectionState rightState;

    bool sensorHealthy(const ToFData& data, uint32_t now) const;
    uint16_t safetyDistance(const ToFData& data) const;
    void updateDirection(DirectionState& state, const ToFData& data, uint32_t now);
    int16_t limitApproachSpeed(int16_t requested, const DirectionState& state) const;
    bool blocksApproach(const DirectionState& state) const;

public:
    explicit ObstacleAvoidance(const AvoidanceConfig& avoidanceConfig = {});
    RCCommand apply(const RCCommand& desired, const ToFManager& sensors, uint32_t now);
    bool isIntervening(const RCCommand& desired, const RCCommand& safe) const;

    const DirectionState& front() const;
    const DirectionState& rear() const;
    const DirectionState& left() const;
    const DirectionState& right() const;
};

const char* avoidanceModeName(AvoidanceMode mode);

#endif
