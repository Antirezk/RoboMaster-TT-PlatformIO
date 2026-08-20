# RoboMaster TT PlatformIO Controller

PlatformIO firmware for the RoboMaster TT Open-Source Controller, with four-direction VL53L0X obstacle sensing, Mission Pad support, and a high-level UDP control interface.

The ESP32 acts as the safety and communication layer between a laptop controller and the RoboMaster TT. It is the only component that writes RC and action commands to the TT internal UART.

## Architecture

```text
Laptop controller
    │  Wi-Fi UDP: desired RC and action commands
    ▼
ESP32 high-level protocol
    │
    ├── CommandInput          command parsing and heartbeat timeout
    ├── ToFManager           four VL53L0X sensors through PCA9548A
    ├── ObstacleAvoidance    limit, block, or override unsafe motion
    ├── MissionPadMission    deterministic onboard mission executive
    └── TTController         sole writer to the RoboMaster TT UART
            │
            ▼
       RoboMaster TT
```

TT telemetry and ESP32 safety state are returned to the most recent UDP client at 10 Hz.

## Features

- Custom 2 MB `telloesp32` PlatformIO board definition.
- Four-direction ToF obstacle monitoring with filtering and automatic recovery.
- Distance-based approach limiting, blocked-direction rejection, and escape behavior.
- Mission Pad initialization, takeoff, go-to-pad, landing, abort, timeout, and retry states.
- USB serial and Wi-Fi UDP high-level command inputs using the same text protocol.
- RC heartbeat failsafe: stale commands become zero RC after 500 ms.
- Automatic landing request when the high-level heartbeat is absent for 2 seconds while airborne.
- Machine-readable telemetry containing Mission Pad, battery, height, sensor, and safety state.

## Repository Layout

```text
.
├── boards/                  custom PlatformIO board definition
├── docs/                    protocol, migration, and test documentation
├── partitions/              ESP32 partition table
├── src/                     firmware and safety modules
├── tools/                   supervised keyboard bench controller
├── variants/telloesp32/     RoboMaster TT Arduino pin mapping
├── platformio.ini           PlatformIO environment
└── README.md                project overview
```

## Hardware

- RoboMaster TT Open-Source Controller
- ESP32 with the TelloTalent pin mapping
- PCA9548A I2C multiplexer
- Four VL53L0X ToF sensors
- CP2102N or compatible USB-to-UART interface for setup and bench testing

The default I2C mapping is:

```text
SDA = GPIO27
SCL = GPIO26
```

## Build

Open the project in VS Code with PlatformIO, or run:

```powershell
platformio run
```

Upload and monitor:

```powershell
platformio run --target upload
platformio device monitor --baud 115200
```

The default environment is `telloesp32`, configured for a 2 MB flash device at 160 MHz with the `minimal` partition table.

## High-Level Connection

By default, the ESP32 creates:

```text
SSID: TT-HighLevel
Password: RMTT1234
ESP32 IP: 192.168.4.1
UDP port: 8889
```

Override the SSID and password with the `HIGH_LEVEL_AP_SSID` and `HIGH_LEVEL_AP_PASSWORD` build flags before deployment.

Supported commands:

```text
hello
rc <left/right> <forward/back> <up/down> <yaw>
stop
takeoff
mission <1..8>
land
abort
```

All RC channels are clamped to `-100..100` on the ESP32.

## Safety Model

Each ToF direction is classified as one of:

```text
NORMAL
BLOCKED
ESCAPE
FAULT
```

The high-level controller sends desired motion only. The ESP32 applies the final safety decision before sending anything to the TT. Sensor faults fail closed, and action commands are rejected unless the onboard Mission Executive and safety state allow them.

Do not use a direct TT RC controller at the same time as the integrated ESP32 control path.

## Documentation

- [PlatformIO migration guide](docs/PLATFORMIO_MIGRATION.md)
- [High-level UDP protocol](docs/HIGH_LEVEL_PROTOCOL.md)
- [Mission Pad MVP and staged test procedure](docs/MISSION_PAD_MVP.md)
- [Supervised keyboard bench controller](tools/KEYBOARD_CONTROLLER.md)

## Test Carefully

Remove the propellers for initial UART, sensor, protocol, and command tests. Confirm that all four ToF directions report healthy data and that the Mission Executive reaches `READY` before attempting flight.

Real-flight testing should proceed incrementally: initialization, command rejection, takeoff/land, obstacle intervention, and only then Mission Pad flight.

## License

This project is released under the [MIT License](LICENSE).
