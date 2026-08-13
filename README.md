# RoboMaster TT ESP32：从 Arduino IDE 迁移到 VSCode + PlatformIO

本文记录如何将 RoboMaster TT Open-Source Controller 的 ESP32 开发环境，从 Arduino IDE 的 TelloTalent 专用 Board Package 迁移到 VSCode + PlatformIO。

## 1. 为什么不能直接选择 `esp32dev`

RoboMaster TT Open-Source Controller 使用的 ESP32 板级配置与普通 ESP32 Dev Module 不完全相同。

直接在 PlatformIO 中使用：

```ini
board = esp32dev
```

可能导致启动失败，例如：

```text
Detected size(2048k) smaller than the size in the binary image header(4096k)
```

甚至不断：

```text
rst:0x3 (SW_RESET)
rst:0xc (SW_CPU_RESET)
```

原因是普通 `esp32dev` 默认按照 4MB Flash 等参数编译，而 RoboMaster TT 的专用 Arduino Board Package 中定义的是：

```text
CPU Frequency   = 160 MHz
Flash Size      = 2 MB
Flash Frequency = 40 MHz
Flash Mode      = DIO
Partition       = minimal
Variant         = telloesp32
```

这些参数来自 TelloTalent 的 `boards.txt`：

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

## 2. 先确认 Arduino IDE 环境正常

建议首先使用 TelloTalent 专用 Arduino Board Package 验证硬件。

如果使用 CP2102N USB-UART，但 Windows 设备管理器出现黄色感叹号，需要安装 Silicon Labs CP210x VCP Driver。

正常以后设备管理器应该显示类似：

```text
Silicon Labs CP210x USB to UART Bridge (COM8)
```

使用专用 Arduino Board Package 烧录最简单程序：

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

确认能够正常运行以后，再进行 PlatformIO 迁移。

---

## 3. 创建 PlatformIO 项目

例如项目目录：

```text
C:\Users\<USERNAME>\Documents\PlatformIO\Projects\Robomaster
```

项目结构：

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

## 4. 创建 RoboMaster TT 自定义 Board

在项目根目录创建：

```text
boards/telloesp32.json
```

示例：

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

最重要的是：

```json
"flash_size": "2MB"
```

以及：

```json
"variant": "telloesp32"
```

如果仍然使用普通：

```json
"variant": "esp32"
```

虽然 ESP32 可能可以启动，但是 RoboMaster TT 的默认 GPIO 映射不会生效。

---

## 5. 配置 `platformio.ini`

例如：

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

`COM8` 根据自己的设备管理器修改。

---

## 6. 复制 TT 的 Partition Table

Arduino TelloTalent Board Package 使用：

```text
build.partitions=minimal
```

因此找到 Arduino 专用 Package 中对应的：

```text
minimal.csv
```

复制到 PlatformIO 项目根目录：

```text
Robomaster/minimal.csv
```

然后：

```ini
board_build.partitions = minimal.csv
```

---

## 7. 迁移 `pins_arduino.h`

这是非常关键的一步。

TelloTalent Arduino Package 中存在：

```text
variants/telloesp32/pins_arduino.h
```

其中定义了 RoboMaster TT 的真实默认 GPIO：

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

特别是：

```text
I2C SDA = GPIO27
I2C SCL = GPIO26
```

普通 ESP32 的默认 I2C 引脚通常不同。

如果 PlatformIO 使用错误的 variant：

```cpp
Wire.begin();
```

可能一个 I2C 设备都扫描不到。

而手动：

```cpp
Wire.begin(27, 26);
```

却能够正常工作。

这就是 `variant` 没有迁移成功的典型表现。

---

## 8. 让 PlatformIO 找到 `telloesp32` Variant

将 Arduino Package 中：

```text
variants/telloesp32
```

这个文件夹复制到 PlatformIO ESP32 Arduino Framework 的：

```text
C:\Users\<USERNAME>\.platformio\packages\
framework-arduinoespressif32\variants\
```

最终结构：

```text
framework-arduinoespressif32
└── variants
    └── telloesp32
        └── pins_arduino.h
```

注意不要多套一层：

```text
错误：

variants
└── telloesp32
    └── telloesp32
        └── pins_arduino.h
```

否则编译时会出现：

```text
fatal error: pins_arduino.h: No such file or directory
```

自定义 Board 中同时需要：

```json
"variant": "telloesp32"
```

---

## 9. Clean 后重新编译

迁移 Board / Variant 后建议：

```text
PlatformIO
→ Clean
→ Build
→ Upload
```

如果之前使用过错误的 4MB 配置，建议还可以执行一次：

```text
Erase Flash
```

然后重新 Upload。

---

## 10. 验证 PlatformIO 是否真正识别 TT

先测试：

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

成功：

```text
TT PlatformIO OK
RUNNING
RUNNING
RUNNING
```

---

## 11. 验证 I2C Variant

接入一个 VL53L0X，然后注意这里直接使用：

```cpp
Wire.begin();
```

而不是：

```cpp
Wire.begin(27, 26);
```

测试：

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

在 RoboMaster TT + VL53L0X 环境中测试得到：

```text
I2C Scan Start...
Found: 0x29
Found: 0x50
Scan Finished.
```

其中：

```text
0x29 = VL53L0X
0x50 = RoboMaster TT 板载 LED Matrix 相关设备
```

此时说明：

```text
PlatformIO
    ↓
自定义 telloesp32 Board
    ↓
telloesp32 Variant
    ↓
pins_arduino.h
    ↓
SDA = GPIO27
SCL = GPIO26
    ↓
I2C 正常工作
```

迁移完成。

---

## 常见问题

### `Detected size(2048k) smaller than ... 4096k`

PlatformIO 仍然按照普通 ESP32 的 4MB Flash 配置编译。

确认自定义 Board：

```json
"flash_size": "2MB"
```

并执行：

```text
Clean
Erase Flash
重新 Upload
```

### `pins_arduino.h: No such file or directory`

PlatformIO 找不到：

```text
variants/telloesp32/pins_arduino.h
```

确认：

```json
"variant": "telloesp32"
```

并确认 variant 已复制到正确目录。

### `Wire.begin()` 扫不到任何设备

如果：

```cpp
Wire.begin(27, 26);
```

可以工作，而：

```cpp
Wire.begin();
```

不工作，基本说明 `telloesp32` variant 没有正确加载。

TT 的 I2C 定义为：

```text
SDA = GPIO27
SCL = GPIO26
```

---

## 已验证环境

最终成功验证：

```text
RoboMaster TT Open-Source Controller
        +
CP2102N USB-UART
        +
VSCode
        +
PlatformIO
        +
Arduino Framework
        +
Custom telloesp32 Board
        +
TelloTalent pins_arduino.h
        ↓
正常启动
        ↓
Wire.begin()
        ↓
VL53L0X @ 0x29
        ↓
工作正常
```

这套环境可以继续用于 PCA9548A、多路 VL53L0X、传感器滤波以及 RoboMaster TT 本地避障开发。
