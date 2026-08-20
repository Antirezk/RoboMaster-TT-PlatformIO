#include "CommandInput.h"
#include <stdio.h>
#include <string.h>

CommandInput::CommandInput(uint32_t commandTimeoutMs)
    : timeoutMs(commandTimeoutMs)
{
}

void CommandInput::poll(Stream& stream)
{
    while (stream.available() > 0)
    {
        char value = static_cast<char>(stream.read());

        if (value == '\r') continue;

        if (value == '\n')
        {
            if (length > 0)
            {
                buffer[length] = '\0';
                parseLine();
                length = 0;
            }
            continue;
        }

        if (length < BUFFER_SIZE - 1)
        {
            buffer[length++] = value;
        }
        else
        {
            length = 0;
            Serial.println("INPUT ERROR: line too long");
        }
    }
}

void CommandInput::parseLine()
{
    int lr = 0;
    int fb = 0;
    int ud = 0;
    int yaw = 0;
    char extra = '\0';
    int padId = 0;

    if (sscanf(buffer, "rc %d %d %d %d %c", &lr, &fb, &ud, &yaw, &extra) == 4)
    {
        submit({
            clampRCValue(lr), clampRCValue(fb),
            clampRCValue(ud), clampRCValue(yaw)
        }, millis());
        return;
    }

    if (strcmp(buffer, "stop") == 0)
    {
        submit({}, millis());
        pendingAction.action = HighLevelAction::STOP;
        return;
    }

    if (strcmp(buffer, "hello") == 0)
    {
        pendingAction.action = HighLevelAction::HELLO;
        return;
    }

    if (strcmp(buffer, "takeoff") == 0)
    {
        desired = {};
        pendingAction.action = HighLevelAction::TAKEOFF;
        return;
    }

    if (sscanf(buffer, "mission %d %c", &padId, &extra) == 1 && padId >= 1 && padId <= 8)
    {
        desired = {};
        pendingAction.action = HighLevelAction::START_PAD_MISSION;
        pendingAction.padId = static_cast<uint8_t>(padId);
        return;
    }

    if (strcmp(buffer, "land") == 0)
    {
        desired = {};
        pendingAction.action = HighLevelAction::LAND;
        return;
    }

    if (strcmp(buffer, "abort") == 0)
    {
        desired = {};
        pendingAction.action = HighLevelAction::ABORT;
        return;
    }

    if (strcmp(buffer, "help") == 0)
    {
        Serial.println("INPUT: hello | rc <lr> <fb> <ud> <yaw> | stop | takeoff | mission <1..8> | land | abort");
        return;
    }

    Serial.printf("INPUT ERROR: '%s'\n", buffer);
}

bool CommandInput::submitLine(const char* line)
{
    if (line == nullptr)
        return false;
    size_t inputLength = strlen(line);
    if (inputLength == 0 || inputLength >= BUFFER_SIZE)
        return false;
    memcpy(buffer, line, inputLength + 1);
    length = 0;
    parseLine();
    return true;
}

void CommandInput::submit(const RCCommand& command, uint32_t now)
{
    desired = clampRC(command);
    lastCommandTime = now;
    receivedCommand = true;
}

RCCommand CommandInput::getDesired(uint32_t now) const
{
    return isFresh(now) ? desired : RCCommand{};
}

bool CommandInput::isFresh(uint32_t now) const
{
    return receivedCommand && (now - lastCommandTime <= timeoutMs);
}

uint32_t CommandInput::age(uint32_t now) const
{
    return receivedCommand ? now - lastCommandTime : UINT32_MAX;
}

bool CommandInput::takeAction(ActionRequest& request)
{
    if (pendingAction.action == HighLevelAction::NONE)
        return false;

    request = pendingAction;
    pendingAction = {};
    return true;
}
