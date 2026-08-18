#include "ToFSensor.h"
#include <math.h>


namespace
{
// 传感器在 back-to-back 模式下通常早已有结果可读。
// 40ms 足以覆盖默认约 33ms 的测量预算，同时不会让故障通道长时间堵塞主循环。
constexpr uint16_t SENSOR_TIMEOUT_MS = 40;

constexpr uint8_t FAILURES_BEFORE_RECOVERY = 2;

// 避免断线时每个控制周期都反复执行完整初始化。
constexpr uint32_t RECOVERY_INTERVAL_MS = 500;
}


// ======================================
// PCA9548A 通道选择
// ======================================

bool selectToFChannel(uint8_t channel)
{
    if (channel > 7)
    {
        return false;
    }

    Wire.beginTransmission(PCA9548A_ADDR);

    Wire.write(1 << channel);

    return Wire.endTransmission() == 0;
}


// ======================================
// SensorFilter
// ======================================

uint16_t SensorFilter::update(uint16_t value)
{
    if (firstValue)
    {
        firstValue = false;

        lastValue = static_cast<float>(value);

        return value;
    }

    float current =
        static_cast<float>(value);

    float delta =
        current - lastValue;


    // 限制单次突变
    if (fabsf(delta) > maxDelta)
    {
        if (delta > 0)
        {
            current =
                lastValue + maxDelta;
        }
        else
        {
            current =
                lastValue - maxDelta;
        }
    }


    // 一阶低通滤波
    lastValue =
        alpha * current +
        (1.0f - alpha) * lastValue;


    if (lastValue < 0)
    {
        lastValue = 0;
    }

    return static_cast<uint16_t>(lastValue);
}


void SensorFilter::reset()
{
    firstValue = true;
    lastValue = 0.0f;
}


void SensorFilter::setParameters(
    float alphaValue,
    float maxDeltaValue)
{
    alpha = alphaValue;

    maxDelta = maxDeltaValue;
}


// ======================================
// ToFSensor
// ======================================

bool ToFSensor::begin(uint8_t pcaChannel)
{
    channel = pcaChannel;

    initialized = initializeSensor();

    return initialized;
}


bool ToFSensor::initializeSensor()
{
    initialized = false;

    if (!selectToFChannel(channel))
    {
        return false;
    }

    delay(10);

    sensor.setBus(&Wire);

    sensor.setTimeout(SENSOR_TIMEOUT_MS);

    if (!sensor.init())
    {
        return false;
    }

    // 每个 PCA 通道隔离，因此四个都可以保持 0x29
    sensor.setAddress(VL53L0X_ADDR);

    // 多路复用时让各传感器自行连续测量，切回该通道时结果通常已经就绪，
    // 不必等待下一次 100ms 的定时测量窗口。
    sensor.startContinuous();

    initialized = true;

    return true;
}


void ToFSensor::recordFailure(ToFStatus status)
{
    data.status = status;

    if (data.consecutiveFailures < 255)
    {
        ++data.consecutiveFailures;
    }
}


void ToFSensor::recordSuccess()
{
    data.consecutiveFailures = 0;

    if (recoveryPending)
    {
        recoveryPending = false;
        filter.reset();
        ++data.recoveryCount;

        Serial.printf(
            "ToF CH%u recovered (count=%u, attempts=%u)\n",
            channel,
            data.recoveryCount,
            data.recoveryAttempts
        );
    }
}


void ToFSensor::tryRecover()
{
    if (data.consecutiveFailures < FAILURES_BEFORE_RECOVERY)
    {
        return;
    }

    uint32_t now = millis();

    if (now - lastRecoveryAttempt < RECOVERY_INTERVAL_MS)
    {
        return;
    }

    lastRecoveryAttempt = now;

    ++data.recoveryAttempts;

    if (!selectToFChannel(channel))
    {
        initialized = false;
        data.lastI2cStatus = 0xFE;

        Serial.printf(
            "ToF CH%u recovery #%u failed: PCA channel select\n",
            channel,
            data.recoveryAttempts
        );

        return;
    }

    // 先读取芯片 ID。0xEE 表示 CH 下游的 VL53L0X 确实有响应。
    data.lastModelId = sensor.readReg(VL53L0X::IDENTIFICATION_MODEL_ID);
    data.lastI2cStatus = sensor.last_status;

    if (data.lastI2cStatus != 0 || data.lastModelId != 0xEE)
    {
        initialized = false;

        Serial.printf(
            "ToF CH%u recovery #%u failed: no sensor response "
            "(i2c=%u, id=0x%02X)\n",
            channel,
            data.recoveryAttempts,
            data.lastI2cStatus,
            data.lastModelId
        );

        return;
    }

    if ((data.recoveryAttempts & 1U) != 0)
    {
        // 第一级：只清中断并重启测量状态机，避免不必要的完整校准。
        sensor.writeReg(VL53L0X::SYSTEM_INTERRUPT_CLEAR, 0x01);
        sensor.stopContinuous();
        delay(2);
        sensor.startContinuous();

        data.lastI2cStatus = sensor.last_status;
        initialized = data.lastI2cStatus == 0;
    }
    else
    {
        // 第二级：VL53L0X 内部软复位，再执行完整初始化与校准。
        sensor.writeReg(VL53L0X::SOFT_RESET_GO2_SOFT_RESET_N, 0x00);
        delay(2);
        sensor.writeReg(VL53L0X::SOFT_RESET_GO2_SOFT_RESET_N, 0x01);
        delay(10);

        initialized = initializeSensor();
        data.lastI2cStatus = sensor.last_status;
    }

    if (initialized)
    {
        // 必须等下一次真正读到数据后，才计为恢复成功。
        recoveryPending = true;
    }
    else
    {
        data.status = ToFStatus::ERROR;

        Serial.printf(
            "ToF CH%u recovery #%u failed during restart/init (i2c=%u)\n",
            channel,
            data.recoveryAttempts,
            data.lastI2cStatus
        );
    }
}

bool ToFSensor::update()
{
    if (!initialized)
    {
        recordFailure(ToFStatus::ERROR);
        tryRecover();

        return false;
    }

    // ==================================
    // 选择 PCA9548A 通道失败
    // ==================================

    if (!selectToFChannel(channel))
    {
        recordFailure(ToFStatus::ERROR);
        tryRecover();

        return false;
    }


    // ==================================
    // 读取距离
    // ==================================

    uint16_t value =
        sensor.readRangeContinuousMillimeters();


    // ==================================
    // 真正的测距超时
    // ==================================

    if (sensor.timeoutOccurred())
    {
        recordFailure(ToFStatus::OVERTIME);
        tryRecover();

        return false;
    }


    data.raw = value;


    // ==================================
    // VL53L0X 超出有效测距范围
    //
    // 8190 / 8191 等值通常表示：
    // 没有可靠目标 / 超量程
    //
    // 对避障而言：
    // 前方没有近距离障碍物
    //
    // 所以设为 SAFE
    // 不进入 blocked
    // ==================================

    if (value >= 8190)
    {
        data.lastUpdate = millis();
        recordSuccess();
        data.status = ToFStatus::SAFE;

        return true;
    }


    // ==================================
    // 0 明显异常
    // ==================================

    if (value == 0)
    {
        recordFailure(ToFStatus::ERROR);
        tryRecover();

        return false;
    }


    // ==================================
    // 正常距离
    // ==================================

    data.lastUpdate = millis();

    recordSuccess();

    data.filtered =
        filter.update(value);

    data.status = ToFStatus::VALID;

    return true;
}

const ToFData& ToFSensor::getData() const
{
    return data;
}


uint8_t ToFSensor::getChannel() const
{
    return channel;
}


// ======================================
// ToFManager
// ======================================

bool ToFManager::begin()
{
    bool allOK = true;


    // CH0 = 前
    if (!frontSensor.begin(0))
    {
        Serial.println("FRONT Init Error");

        allOK = false;
    }
    else
    {
        Serial.println("FRONT OK");
    }


    // CH1 = 后
    if (!rearSensor.begin(1))
    {
        Serial.println("REAR Init Error");

        allOK = false;
    }
    else
    {
        Serial.println("REAR OK");
    }


    // CH2 = 左
    if (!leftSensor.begin(4))
    {
        Serial.println("LEFT Init Error");

        allOK = false;
    }
    else
    {
        Serial.println("LEFT OK");
    }


    // CH3 = 右
    if (!rightSensor.begin(3))
    {
        Serial.println("RIGHT Init Error");

        allOK = false;
    }
    else
    {
        Serial.println("RIGHT OK");
    }


    return allOK;
}


void ToFManager::update()
{
    frontSensor.update();

    rearSensor.update();

    leftSensor.update();

    rightSensor.update();
}


const ToFData& ToFManager::front() const
{
    return frontSensor.getData();
}


const ToFData& ToFManager::rear() const
{
    return rearSensor.getData();
}


const ToFData& ToFManager::left() const
{
    return leftSensor.getData();
}


const ToFData& ToFManager::right() const
{
    return rightSensor.getData();
}
