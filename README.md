# RoboMaster TT ESP32: Migrating from Arduino IDE to VS Code + PlatformIO

This guide explains how to migrate the ESP32 development environment for the RoboMaster TT Open-Source Controller from the dedicated TelloTalent board package for Arduino IDE to VS Code and PlatformIO.

## Original Project

This project is based on the original Arduino implementation:

Original Arduino library: `RoboMaster/RMTT_Libs`

Thanks to the original author for providing the Arduino implementation.

## 1. Why You Cannot Simply Use `esp32dev`

The ESP32 board configuration used by the RoboMaster TT Open-Source Controller differs from that of a standard ESP32 Dev Module.

Using the following configuration directly in PlatformIO:

```ini
board = esp32dev
```

may prevent the board from booting and produce an error such as:

```text
Detected size(2048k) smaller than the size in the binary image header(4096k)
```

The board may also continuously reset with messages such as:

```text
rst:0x3 (SW_RESET)
rst:0xc (SW_CPU_RESET)
```

This happens because the standard `esp32dev` configuration assumes a 4 MB flash chip and other default parameters. The dedicated Arduino board package for the RoboMaster TT instead defines the following:

```text
CPU Frequency   = 160 MHz
Flash Size      = 2 MB
Flash Frequency = 40 MHz
Flash Mode      = DIO
Partition       = minimal
Variant         = telloesp32
```

These settings come from the TelloTalent `boards.txt` file:

```text
telloesp32.build.mcu=esp32
telloesp32.build.core=esp32
telloesp32.build.variant=telloesp32

telloesp32.build.f_cpu=160000000L
telloesp32.build.flash_size=2MB
telloesp32.build.flash_freq=40m
telloesp32.build.flash_mode=dio
telloesp32.build.boot=dio
telloesp32.build.partitions=minimal
```

---

## 2. Verify the Arduino IDE Environment First

Before migrating, use the dedicated TelloTalent board package for Arduino IDE to verify that the hardware works correctly.

If you are using a CP2102N USB-to-UART adapter and Windows Device Manager displays a yellow warning icon, install the Silicon Labs CP210x VCP driver.

Once the driver is working, Device Manager should display an entry similar to:

```text
Silicon Labs CP210x USB to UART Bridge (COM8)
```

Use the dedicated Arduino board package to upload this minimal test program:

```cpp
void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println("TT ESP32 START");
}

void loop()
{
    Serial.println("RUNNING");
    delay(1000);
}
```

Proceed with the PlatformIO migration only after confirming that this program runs correctly.

---

## 3. Create a PlatformIO Project

For example, the project directory can be:

```text
C:\Users\<USERNAME>\Documents\PlatformIO\Projects\Robomaster
```

Use the following project structure:

```text
Robomaster
│
├── boards
│   └── telloesp32.json
│
├── src
│   └── main.cpp
│
├── minimal.csv
│
└── platformio.ini
```

---

## 4. Create a Custom RoboMaster TT Board Definition

Create the following file in the project root:

```text
boards/telloesp32.json
```

Example configuration:

```json
{
  "build": {
    "arduino": {
      "ldscript": "esp32_out.ld"
    },
    "core": "esp32",
    "extra_flags": "-DARDUINO_TELLOESP32_DEV",
    "f_cpu": "160000000L",
    "f_flash": "40000000L",
    "flash_mode": "dio",
    "mcu": "esp32",
    "variant": "telloesp32"
  },

  "connectivity": [
    "wifi",
    "bluetooth"
  ],

  "debug": {
    "openocd_target": "esp32.cfg"
  },

  "frameworks": [
    "arduino"
  ],

  "name": "RoboMaster TT ESP32",

  "upload": {
    "flash_size": "2MB",
    "maximum_ram_size": 327680,
    "maximum_size": 1310720,
    "protocol": "esptool",
    "require_upload_port": true,
    "speed": 115200
  },

  "vendor": "DJI"
}
```

The most important setting is:

```json
"flash_size": "2MB"
```

The custom variant is equally important:

```json
"variant": "telloesp32"
```

If you continue using the standard variant:

```json
"variant": "esp32"
```

the ESP32 may boot, but the default RoboMaster TT GPIO mapping will not be applied.

---

## 5. Configure `platformio.ini`

Example configuration:

```ini
[env:telloesp32]

platform = espressif32
board = telloesp32
framework = arduino

upload_port = COM8
monitor_port = COM8

upload_speed = 115200
monitor_speed = 115200

board_build.f_cpu = 160000000L
board_build.f_flash = 40000000L
board_build.flash_mode = dio

board_build.partitions = minimal.csv
```

Replace `COM8` with the port shown for your device in Windows Device Manager.

---

## 6. Copy the TT Partition Table

The Arduino TelloTalent board package uses:

```text
build.partitions=minimal
```

Locate the corresponding file in the dedicated Arduino package:

```text
minimal.csv
```

Copy it to the root of the PlatformIO project:

```text
Robomaster/minimal.csv
```

Then configure PlatformIO to use it:

```ini
board_build.partitions = minimal.csv
```

---

## 7. Migrate `pins_arduino.h`

This is a critical step.

The TelloTalent Arduino package contains:

```text
variants/telloesp32/pins_arduino.h
```

This file defines the actual default GPIO mapping for the RoboMaster TT:

```cpp
static const uint8_t TX = 1;
static const uint8_t RX = 3;

static const uint8_t SDA = 27;
static const uint8_t SCL = 26;

static const uint8_t SS   = 5;
static const uint8_t MOSI = 23;
static const uint8_t MISO = 19;
static const uint8_t SCK  = 18;
```

In particular:

```text
I2C SDA = GPIO27
I2C SCL = GPIO26
```

The standard ESP32 default I2C pins are usually different.

If PlatformIO uses the wrong variant, the following call:

```cpp
Wire.begin();
```

may fail to detect any I2C devices, while explicitly specifying the pins:

```cpp
Wire.begin(27, 26);
```

may work correctly. This is a typical sign that the custom variant was not migrated successfully.

---

## 8. Make the `telloesp32` Variant Available to PlatformIO

Copy the following directory from the Arduino package:

```text
variants/telloesp32
```

Place it in the PlatformIO ESP32 Arduino framework's variant directory:

```text
C:\Users\<USERNAME>\.platformio\packages\
framework-arduinoespressif32\variants\
```

The final directory structure should be:

```text
framework-arduinoespressif32
└── variants
    └── telloesp32
        └── pins_arduino.h
```

Do not accidentally add an extra nested directory:

```text
Incorrect:

variants
└── telloesp32
    └── telloesp32
        └── pins_arduino.h
```

Otherwise, compilation will fail with:

```text
fatal error: pins_arduino.h: No such file or directory
```

The custom board definition must also contain:

```json
"variant": "telloesp32"
```

---

## 9. Clean and Rebuild the Project

After migrating the board definition and variant, run:

```text
PlatformIO
→ Clean
→ Build
→ Upload
```

If the board was previously flashed using the incorrect 4 MB configuration, it is also a good idea to run:

```text
Erase Flash
```

Then upload the firmware again.

---

## 10. Verify That PlatformIO Recognizes the TT Board

Start with this test program:

```cpp
#include <Arduino.h>

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println("TT PlatformIO OK");
}

void loop()
{
    Serial.println("RUNNING");
    delay(1000);
}
```

Expected output:

```text
TT PlatformIO OK
RUNNING
RUNNING
RUNNING
```

---

## 11. Verify the I2C Variant

Connect a VL53L0X sensor. Notice that the test uses:

```cpp
Wire.begin();
```

rather than:

```cpp
Wire.begin(27, 26);
```

Use the following test program:

```cpp
#include <Arduino.h>
#include <Wire.h>

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Wire.begin();

    Serial.println("I2C Scan Start...");

    for(uint8_t address = 1; address < 127; address++)
    {
        Wire.beginTransmission(address);

        uint8_t error = Wire.endTransmission();

        if(error == 0)
        {
            Serial.print("Found: 0x");

            if(address < 16)
                Serial.print("0");

            Serial.println(address, HEX);
        }
    }

    Serial.println("Scan Finished.");
}

void loop()
{
}
```

Testing with a RoboMaster TT and VL53L0X produced:

```text
I2C Scan Start...
Found: 0x29
Found: 0x50
Scan Finished.
```

The detected addresses correspond to:

```text
0x29 = VL53L0X
0x50 = Device associated with the RoboMaster TT onboard LED matrix
```

At this point, the complete configuration path is working:

```text
PlatformIO
    ↓
Custom telloesp32 board
    ↓
telloesp32 variant
    ↓
pins_arduino.h
    ↓
SDA = GPIO27
SCL = GPIO26
    ↓
I2C working correctly
```

The migration is now complete.

---

## Troubleshooting

### `Detected size(2048k) smaller than ... 4096k`

PlatformIO is still compiling the project using the standard ESP32 4 MB flash configuration.

Confirm that the custom board definition contains:

```json
"flash_size": "2MB"
```

Then run:

```text
Clean
Erase Flash
Upload again
```

### `pins_arduino.h: No such file or directory`

PlatformIO cannot find:

```text
variants/telloesp32/pins_arduino.h
```

Confirm that the custom board definition contains:

```json
"variant": "telloesp32"
```

Also confirm that the variant was copied to the correct directory.

### `Wire.begin()` Does Not Detect Any Devices

If explicitly specifying the pins works:

```cpp
Wire.begin(27, 26);
```

but the default call does not:

```cpp
Wire.begin();
```

the `telloesp32` variant was most likely not loaded correctly.

The RoboMaster TT I2C pins are defined as:

```text
SDA = GPIO27
SCL = GPIO26
```

---

## Verified Environment

The final configuration was successfully verified with:

```text
RoboMaster TT Open-Source Controller
        +
CP2102N USB-to-UART
        +
VS Code
        +
PlatformIO
        +
Arduino Framework
        +
Custom telloesp32 board
        +
TelloTalent pins_arduino.h
        ↓
Boots correctly
        ↓
Wire.begin()
        ↓
VL53L0X @ 0x29
        ↓
Working correctly
```

This environment can be used as a foundation for PCA9548A integration, multiple VL53L0X sensors, sensor filtering, and onboard obstacle-avoidance development for the RoboMaster TT.
