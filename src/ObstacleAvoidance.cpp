#include "ObstacleAvoidance.h"

namespace
{
uint8_t incrementSaturated(uint8_t value)
{
    return value == 255 ? value : static_cast<uint8_t>(value + 1);
}
}

ObstacleAvoidance::ObstacleAvoidance(const AvoidanceConfig& avoidanceConfig)
    : config(avoidanceConfig)
{
}

bool ObstacleAvoidance::sensorHealthy(const ToFData& data, uint32_t now) const
{
    if (data.status != ToFStatus::VALID && data.status != ToFStatus::SAFE)
    {
        return false;
    }

    return now - data.lastUpdate <= config.sensorFreshnessMs;
}

uint16_t ObstacleAvoidance::safetyDistance(const ToFData& data) const
{
    if (data.status == ToFStatus::SAFE)
    {
        return UINT16_MAX;
    }

    // 接近时取 raw/filtered 中更小值，避免滤波延迟紧急反应；
    // 离开时保留较小的滤波值，解除避障会更保守。
    return data.raw < data.filtered ? data.raw : data.filtered;
}

void ObstacleAvoidance::updateDirection(
    DirectionState& state,
    const ToFData& data,
    uint32_t now)
{
    if (!sensorHealthy(data, now))
    {
        state.mode = AvoidanceMode::SENSOR_FAULT;
        state.distance = 0;
        state.healthySamples = 0;
        return;
    }

    state.healthySamples = incrementSaturated(state.healthySamples);
    state.distance = safetyDistance(data);

    if (state.mode == AvoidanceMode::SENSOR_FAULT &&
        state.healthySamples < config.healthySamplesToRecover)
    {
        return;
    }

    if (data.status == ToFStatus::SAFE)
    {
        state.mode = AvoidanceMode::NORMAL;
        return;
    }

    switch (state.mode)
    {
        case AvoidanceMode::NORMAL:
            if (state.distance < config.escapeEnterMm)
                state.mode = AvoidanceMode::ESCAPE;
            else if (state.distance < config.blockedEnterMm)
                state.mode = AvoidanceMode::BLOCKED;
            break;

        case AvoidanceMode::BLOCKED:
            if (state.distance < config.escapeEnterMm)
                state.mode = AvoidanceMode::ESCAPE;
            else if (state.distance > config.blockedExitMm)
                state.mode = AvoidanceMode::NORMAL;
            break;

        case AvoidanceMode::ESCAPE:
            if (state.distance > config.blockedExitMm)
                state.mode = AvoidanceMode::NORMAL;
            else if (state.distance > config.escapeExitMm)
                state.mode = AvoidanceMode::BLOCKED;
            break;

        case AvoidanceMode::SENSOR_FAULT:
            if (state.distance < config.escapeEnterMm)
                state.mode = AvoidanceMode::ESCAPE;
            else if (state.distance < config.blockedEnterMm)
                state.mode = AvoidanceMode::BLOCKED;
            else
                state.mode = AvoidanceMode::NORMAL;
            break;
    }
}

bool ObstacleAvoidance::blocksApproach(const DirectionState& state) const
{
    return state.mode != AvoidanceMode::NORMAL;
}

int16_t ObstacleAvoidance::limitApproachSpeed(
    int16_t requested,
    const DirectionState& state) const
{
    if (state.mode != AvoidanceMode::NORMAL)
        return 0;

    if (state.distance >= config.slowStartMm)
        return requested;

    if (state.distance <= config.blockedEnterMm)
        return 0;

    float ratio = static_cast<float>(state.distance - config.blockedEnterMm) /
                  static_cast<float>(config.slowStartMm - config.blockedEnterMm);
    int16_t allowed = static_cast<int16_t>(
        config.minimumApproachSpeed + ratio * (100 - config.minimumApproachSpeed)
    );

    int16_t magnitude = abs(requested);
    if (magnitude > allowed) magnitude = allowed;
    return requested < 0 ? -magnitude : magnitude;
}

RCCommand ObstacleAvoidance::apply(
    const RCCommand& desired,
    const ToFManager& sensors,
    uint32_t now)
{
    updateDirection(frontState, sensors.front(), now);
    updateDirection(rearState, sensors.rear(), now);
    updateDirection(leftState, sensors.left(), now);
    updateDirection(rightState, sensors.right(), now);

    RCCommand safe = clampRC(desired);

    if (safe.fb > 0)
        safe.fb = blocksApproach(frontState) ? 0 : limitApproachSpeed(safe.fb, frontState);
    else if (safe.fb < 0)
        safe.fb = blocksApproach(rearState) ? 0 : limitApproachSpeed(safe.fb, rearState);

    if (safe.lr > 0)
        safe.lr = blocksApproach(rightState) ? 0 : limitApproachSpeed(safe.lr, rightState);
    else if (safe.lr < 0)
        safe.lr = blocksApproach(leftState) ? 0 : limitApproachSpeed(safe.lr, leftState);

    const bool frontEscape = frontState.mode == AvoidanceMode::ESCAPE;
    const bool rearEscape = rearState.mode == AvoidanceMode::ESCAPE;
    const bool leftEscape = leftState.mode == AvoidanceMode::ESCAPE;
    const bool rightEscape = rightState.mode == AvoidanceMode::ESCAPE;

    // 对向同时危险，或逃生方向不健康/被阻挡时保持 0，避免覆盖顺序造成振荡。
    if (frontEscape || rearEscape)
    {
        if (frontEscape && !rearEscape && !blocksApproach(rearState))
            safe.fb = -config.escapeSpeed;
        else if (rearEscape && !frontEscape && !blocksApproach(frontState))
            safe.fb = config.escapeSpeed;
        else
            safe.fb = 0;
    }

    if (leftEscape || rightEscape)
    {
        if (leftEscape && !rightEscape && !blocksApproach(rightState))
            safe.lr = config.escapeSpeed;
        else if (rightEscape && !leftEscape && !blocksApproach(leftState))
            safe.lr = -config.escapeSpeed;
        else
            safe.lr = 0;
    }

    return clampRC(safe);
}

bool ObstacleAvoidance::isIntervening(
    const RCCommand& desired,
    const RCCommand& safe) const
{
    return clampRC(desired) != safe;
}

const DirectionState& ObstacleAvoidance::front() const { return frontState; }
const DirectionState& ObstacleAvoidance::rear() const { return rearState; }
const DirectionState& ObstacleAvoidance::left() const { return leftState; }
const DirectionState& ObstacleAvoidance::right() const { return rightState; }

const char* avoidanceModeName(AvoidanceMode mode)
{
    switch (mode)
    {
        case AvoidanceMode::NORMAL: return "NORMAL";
        case AvoidanceMode::BLOCKED: return "BLOCKED";
        case AvoidanceMode::ESCAPE: return "ESCAPE";
        case AvoidanceMode::SENSOR_FAULT: return "FAULT";
    }
    return "UNKNOWN";
}
