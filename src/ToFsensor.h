#ifndef TOF_SENSOR_H
#define TOF_SENSOR_H

#include <Arduino.h>
#include <Wire.h>
#include <VL53L0X.h>

constexpr uint8_t PCA9548A_ADDR = 0x70;
constexpr uint8_t VL53L0X_ADDR  = 0x29;


// ==============================
// ToF 状态
// ==============================

enum class ToFStatus
{
    VALID,      // 正常测距
    SAFE,       // 超出量程，认为前方很远
    OVERTIME,   // 测距超时
    ERROR       // I2C / PCA 通信错误
};


// ==============================
// 单个 ToF 的数据
// ==============================

struct ToFData
{
    uint16_t raw = 0;
    uint16_t filtered = 0;

    ToFStatus status = ToFStatus::OVERTIME;

    uint32_t lastUpdate = 0;

    // 连续失败次数和自动恢复次数，便于串口诊断
    uint8_t consecutiveFailures = 0;
    uint16_t recoveryCount = 0;
    uint16_t recoveryAttempts = 0;
    uint8_t lastI2cStatus = 0;
    uint8_t lastModelId = 0;
};


// ==============================
// 简单低通 + 跳变限制滤波
// ==============================

class SensorFilter
{
private:

    float lastValue = 0.0f;

    float alpha = 0.65f;

    float maxDelta = 150.0f;

    bool firstValue = true;


public:

    SensorFilter() = default;

    uint16_t update(uint16_t value);

    void reset();

    void setParameters(
        float alphaValue,
        float maxDeltaValue
    );
};


// ==============================
// 单个 VL53L0X
// ==============================

class ToFSensor
{
private:

    VL53L0X sensor;

    SensorFilter filter;

    ToFData data;

    uint8_t channel = 0;

    bool initialized = false;

    uint32_t lastRecoveryAttempt = 0;

    bool recoveryPending = false;

    bool initializeSensor();

    void recordFailure(ToFStatus status);

    void recordSuccess();

    void tryRecover();


public:

    bool begin(uint8_t pcaChannel);

    bool update();

    const ToFData& getData() const;

    uint8_t getChannel() const;
};


// ==============================
// 四方向 ToF 管理器
// ==============================

class ToFManager
{
private:

    ToFSensor frontSensor;
    ToFSensor rearSensor;
    ToFSensor leftSensor;
    ToFSensor rightSensor;


public:

    bool begin();

    void update();

    const ToFData& front() const;

    const ToFData& rear() const;

    const ToFData& left() const;

    const ToFData& right() const;
};


bool selectToFChannel(uint8_t channel);

#endif
