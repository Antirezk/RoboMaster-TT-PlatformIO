#include "TTController.h"
#include <stdio.h>
#include <string.h>

void TTController::begin()
{
    // ESP32 Open-Source Controller <-> TT internal UART.
    Serial1.begin(1000000, SERIAL_8N1, 23, 18);
}

void TTController::poll()
{
    while (Serial1.available() > 0)
    {
        char value = static_cast<char>(Serial1.read());

        if (value == '\r')
            continue;

        if (value == '\n')
        {
            if (rxLength > 0)
            {
                rxBuffer[rxLength] = '\0';
                handleLine(rxBuffer);
                rxLength = 0;
            }
            continue;
        }

        if (rxLength < RX_BUFFER_SIZE - 1)
        {
            rxBuffer[rxLength++] = value;
        }
        else
        {
            rxLength = 0;
            if (debugOutput) Serial.println("TT RX overflow");
        }
    }
}

void TTController::handleLine(const char* line)
{
    if (parseTelemetry(line))
        return;

    // Official Open-Source Controller replies are prefixed with "ETT".
    // Ignore unrelated UART lines so they cannot complete the wrong command.
    if (strncmp(line, "ETT", 3) != 0)
    {
        if (debugOutput) Serial.printf("TT INFO: %s\n", line);
        return;
    }

    strncpy(response.text, line, sizeof(response.text) - 1);
    response.text[sizeof(response.text) - 1] = '\0';
    response.receivedAt = millis();

    if (strstr(line, "ok") != nullptr)
        response.type = TTResponseType::OK;
    else if (strstr(line, "error") != nullptr)
        response.type = TTResponseType::ERROR;
    else
        response.type = TTResponseType::OTHER;

    responseAvailable = true;
    pendingCommand = false;

    if (debugOutput)
        Serial.printf("TT RX: %s\n", line);
}

bool TTController::parseTelemetry(const char* line)
{
    // VERIFY_ON_HARDWARE: expected TT state fields are mid/x/y/z/bat/h.
    if (strstr(line, "mid:") == nullptr && strstr(line, "bat:") == nullptr)
        return false;

    char copy[RX_BUFFER_SIZE] = {};
    strncpy(copy, line, sizeof(copy) - 1);
    bool parsed = false;
    char* save = nullptr;
    for (char* token = strtok_r(copy, ";", &save); token != nullptr;
         token = strtok_r(nullptr, ";", &save))
    {
        while (*token == ' ') ++token;
        int value = 0;
        const char* midField = strstr(token, "mid:");
        if (midField != nullptr && sscanf(midField, "mid:%d", &value) == 1)
        {
            telemetry.missionPadId = static_cast<int8_t>(value);
            parsed = true;
        }
        else if (sscanf(token, "x:%d", &value) == 1)
            telemetry.missionPadX = static_cast<int16_t>(value);
        else if (sscanf(token, "y:%d", &value) == 1)
            telemetry.missionPadY = static_cast<int16_t>(value);
        else if (sscanf(token, "z:%d", &value) == 1)
            telemetry.missionPadZ = static_cast<int16_t>(value);
        else if (sscanf(token, "bat:%d", &value) == 1)
        {
            telemetry.batteryPercent = static_cast<int16_t>(value);
            parsed = true;
        }
        else if (sscanf(token, "h:%d", &value) == 1)
            telemetry.heightCm = static_cast<int16_t>(value);
    }

    if (parsed)
    {
        telemetry.updatedAt = millis();
        telemetry.valid = true;
    }
    return parsed;
}

bool TTController::sendAction(const char* command, bool force)
{
    if (pendingCommand && !force)
        return false;

    clearResponse();
    Serial1.printf("[TELLO] %s", command);
    pendingCommand = true;
    commandStartedAt = millis();

    if (debugOutput)
        Serial.printf("TT TX: %s\n", command);

    return true;
}

bool TTController::requestSDKMode()
{
    return sendAction("command");
}

bool TTController::enableMissionPads()
{
    return sendAction("mon");
}

bool TTController::setMissionPadDirection(uint8_t direction)
{
    if (direction > 2) return false;
    char command[24] = {};
    snprintf(command, sizeof(command), "mdirection %u", direction);
    return sendAction(command);
}

bool TTController::takeOff()
{
    return sendAction("takeoff");
}

bool TTController::land()
{
    return sendAction("land", true);
}

bool TTController::stopMotion()
{
    return sendAction("stop", true);
}

bool TTController::goToMissionPad(
    uint8_t padId,
    int16_t x,
    int16_t y,
    int16_t z,
    uint16_t speed)
{
    if (padId < 1 || padId > 8 || speed < 10 || speed > 100)
        return false;
    if (x < -500 || x > 500 || y < -500 || y > 500 || z < 0 || z > 500)
        return false;
    if (abs(x) <= 20 && abs(y) <= 20 && abs(z) <= 20)
        return false;

    char command[64] = {};
    snprintf(
        command,
        sizeof(command),
        "go %d %d %d %u m%u",
        x, y, z, speed, padId
    );
    return sendAction(command);
}

bool TTController::sendRC(const RCCommand& command)
{
    if (pendingCommand)
        return false;

    RCCommand safe = clampRC(command);
    Serial1.printf(
        "[TELLO] rc %d %d %d %d",
        safe.lr, safe.fb, safe.ud, safe.yaw
    );
    return true;
}

void TTController::forceZeroRC()
{
    Serial1.print("[TELLO] rc 0 0 0 0");
}

void TTController::cancelPendingCommand()
{
    pendingCommand = false;
    clearResponse();
}

bool TTController::hasPendingCommand() const
{
    return pendingCommand;
}

bool TTController::commandTimedOut(uint32_t now, uint32_t timeoutMs) const
{
    return pendingCommand && now - commandStartedAt > timeoutMs;
}

bool TTController::takeResponse(TTResponse& result)
{
    if (!responseAvailable)
        return false;
    result = response;
    responseAvailable = false;
    response.type = TTResponseType::NONE;
    return true;
}

void TTController::clearResponse()
{
    responseAvailable = false;
    response.type = TTResponseType::NONE;
    response.text[0] = '\0';
}

void TTController::setDebug(bool enable)
{
    debugOutput = enable;
}

const TTTelemetry& TTController::getTelemetry() const
{
    return telemetry;
}
