#pragma once
#include <stdint.h>

// ToF guard firmware V3 - all tunable thresholds in one place (millimetres / milliseconds).
// These are conservative starting values; calibrate them with real braking tests.
namespace guardcfg {

constexpr const char* FIRMWARE_VERSION = "3.0.0";

// ---- Wi-Fi access point the PC connects to
constexpr const char* AP_SSID = "TT-ToF-Demo";
constexpr const char* AP_PASSWORD = "RMTT1234";
constexpr uint16_t UDP_PORT = 9000;

// ---- TT internal UART (same wiring as V2)
constexpr uint32_t TT_BAUD = 1000000;
constexpr int TT_RX_PIN = 23;
constexpr int TT_TX_PIN = 18;

// ---- I2C / sensors (PCA9548A channels: front 0, back 1, left 4, right 3)
constexpr int SDA_PIN = 27;
constexpr int SCL_PIN = 26;
constexpr uint32_t SAMPLE_PERIOD_MS = 50;
constexpr uint16_t SENSOR_MAX_MM = 2000;     // out-of-range readings count as ">= 2000 mm"
constexpr uint32_t SENSOR_STALE_MS = 300;

// ---- avoidance policy
constexpr uint16_t MOVE_MARGIN_MM = 400;     // clearance left after a move (+ speed x STOP_REACTION_S)
constexpr uint16_t PERIMETER_MM = 300;       // every side, for takeoff and rotation
constexpr uint16_t STOP_BASE_MM = 400;       // in-motion stop distance at zero closing speed
constexpr float STOP_REACTION_S = 0.6f;      // + closing speed x this (reaction + braking)
constexpr uint16_t STOP_MAX_MM = 1200;
constexpr uint16_t CRITICAL_MM = 250;        // hovering this close -> back off
constexpr uint8_t RETREAT_CM = 20;
constexpr uint32_t RETREAT_COOLDOWN_MS = 2500;

// ---- link supervision
constexpr uint32_t HEARTBEAT_HOVER_MS = 1500;   // PC silent -> stop (hover)
constexpr uint32_t HEARTBEAT_LAND_MS = 6000;    // PC still silent -> land
constexpr uint32_t OWNER_TAKEOVER_MS = 3000;    // a new PC may take over a silent session
constexpr uint32_t KEEPALIVE_MS = 5000;         // SDK auto-lands after 15 s without a command
constexpr uint32_t STATUS_PERIOD_MS = 100;      // TOF line to the PC (10 Hz)

}  // namespace guardcfg
