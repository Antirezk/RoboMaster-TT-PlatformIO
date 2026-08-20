#ifndef TT_CONTROLLER_H
#define TT_CONTROLLER_H

#include <Arduino.h>
#include "RCCommand.h"

enum class TTResponseType : uint8_t
{
    NONE,
    OK,
    ERROR,
    OTHER
};

struct TTResponse
{
    TTResponseType type = TTResponseType::NONE;
    char text[96] = {};
    uint32_t receivedAt = 0;
};

struct TTTelemetry
{
    int8_t missionPadId = -1;
    int16_t missionPadX = 0;
    int16_t missionPadY = 0;
    int16_t missionPadZ = 0;
    int16_t heightCm = -1;
    int16_t batteryPercent = -1;
    uint32_t updatedAt = 0;
    bool valid = false;
};

class TTController
{
private:
    static constexpr size_t RX_BUFFER_SIZE = 256;

    char rxBuffer[RX_BUFFER_SIZE] = {};
    size_t rxLength = 0;
    TTResponse response;
    bool responseAvailable = false;
    bool pendingCommand = false;
    uint32_t commandStartedAt = 0;
    bool debugOutput = true;
    TTTelemetry telemetry;

    bool sendAction(const char* command, bool force = false);
    void handleLine(const char* line);
    bool parseTelemetry(const char* line);

public:
    void begin();
    void poll();

    bool requestSDKMode();
    bool enableMissionPads();
    bool setMissionPadDirection(uint8_t direction);
    bool takeOff();
    bool land();
    bool stopMotion();
    bool goToMissionPad(uint8_t padId, int16_t x, int16_t y, int16_t z, uint16_t speed);

    bool sendRC(const RCCommand& command);
    void forceZeroRC();
    void cancelPendingCommand();

    bool hasPendingCommand() const;
    bool commandTimedOut(uint32_t now, uint32_t timeoutMs) const;
    bool takeResponse(TTResponse& result);
    void clearResponse();
    void setDebug(bool enable);
    const TTTelemetry& getTelemetry() const;
};

#endif
