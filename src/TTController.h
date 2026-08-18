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

class TTController
{
private:
    static constexpr size_t RX_BUFFER_SIZE = 128;

    char rxBuffer[RX_BUFFER_SIZE] = {};
    size_t rxLength = 0;
    TTResponse response;
    bool responseAvailable = false;
    bool pendingCommand = false;
    uint32_t commandStartedAt = 0;
    bool debugOutput = true;

    bool sendAction(const char* command, bool force = false);
    void handleLine(const char* line);

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
};

#endif
