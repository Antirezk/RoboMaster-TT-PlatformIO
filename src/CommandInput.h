#ifndef COMMAND_INPUT_H
#define COMMAND_INPUT_H

#include <Arduino.h>
#include "RCCommand.h"

enum class HighLevelAction : uint8_t
{
    NONE,
    HELLO,
    STOP,
    TAKEOFF,
    START_PAD_MISSION,
    LAND,
    ABORT
};

struct ActionRequest
{
    HighLevelAction action = HighLevelAction::NONE;
    uint8_t padId = 0;
};

class CommandInput
{
private:
    static constexpr size_t BUFFER_SIZE = 64;
    char buffer[BUFFER_SIZE] = {};
    size_t length = 0;
    RCCommand desired;
    uint32_t lastCommandTime = 0;
    uint32_t timeoutMs = 500;
    bool receivedCommand = false;
    ActionRequest pendingAction;

    void parseLine();

public:
    explicit CommandInput(uint32_t commandTimeoutMs = 500);
    void poll(Stream& stream);
    bool submitLine(const char* line);
    void submit(const RCCommand& command, uint32_t now);
    RCCommand getDesired(uint32_t now) const;
    bool isFresh(uint32_t now) const;
    uint32_t age(uint32_t now) const;
    bool takeAction(ActionRequest& request);
};

#endif
