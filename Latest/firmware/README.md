# ESP32 ToF guard firmware · V3

运行在 RoboMaster TT 开源控制器（ESP32）上。电脑连 ESP32 的 Wi-Fi，ESP32 读四个 VL53L0X，
检查后通过机内 UART（SDK 文档规定的 `[TELLO] <cmd>` → `ETT <reply>`）把指令转发给飞控。

```
PC ──Wi-Fi "TT-ToF-Demo" / UDP 9000──▶ ESP32 guard ──UART 1 Mbps──▶ TT 飞控
         (或 USB 串口 115200)            │
                                         └─ PCA9548A ─ 4 × VL53L0X（前 CH0 · 后 CH1 · 左 CH4 · 右 CH3）
```

## 编译与烧录

```powershell
cd Latest/firmware
pio run -e bench                                     # 只编译
pio run -e bench -t upload --upload-port COM8         # 桌面测试：真实 ToF，飞控回复为模拟，不会飞
pio run -e live  -t upload --upload-port COM8         # 实际转发给飞控
```

* `bench`：传感器是真的，飞控回复由固件模拟（电量 85），适合先在桌上用手遮挡传感器验证避障判断。
* `live`：通过 UART 转发给 TT。烧录 live 前务必先完成 bench 验证。
* 烧录后 ESP32 会建立 Wi-Fi `TT-ToF-Demo`（密码 `RMTT1234`），地址 `192.168.4.1:9000`。
  这些参数和所有阈值都在 `src/GuardConfig.h` 里。

## 协议（每行一条 ASCII 消息）

| 方向 | 消息 | 说明 |
|---|---|---|
| PC → | `HELLO` | 占用会话，回复 `WELCOME TOFGUARD 3.0.0 <bench\|live> avoid=<0\|1>` |
| PC → | `PING` | 心跳，约每 200 ms 一次 |
| PC → | `<id> SDK <tello 指令>` | 转发一条白名单内的 SDK 3.0 指令 |
| PC → | `<id> AVOID ON` / `<id> AVOID OFF` | 开 / 关避障 |
| PC → | `<id> STATUS` | 查询状态 |
| → PC | `ACK <id> OK\|ERROR\|REJECT\|TIMEOUT\|INTERRUPTED <detail>` | 每个请求恰好一个回复（id 递增，重复 id 会被拒绝） |
| → PC | `TOF f= b= l= r= ok=1111 cf= cb= cl= cr= avoid= air= busy= rs= bat=` | 10 Hz：四向距离 mm、有效位、接近速度 mm/s |
| → PC | `EVENT AVOID_STOP / AVOID_RETREAT / AVOID_TRAPPED / HEARTBEAT_LOST / AVOID ON\|OFF / INTERNAL …` | 守护器主动动作 |

白名单与 SDK 3.0 范围一致：`command takeoff land stop emergency motoron motoroff`、
`forward/back/left/right/up/down 20–500`、`cw/ccw 1–360`、`speed 10–100`、`battery? speed? time? wifi? sdk? sn? hardware?`。
`rc`、`flip`、`go`、`EXT`、`wifi`、`reboot` 一律拒绝。

## 避障策略（避障 ON）

| 情况 | 规则（默认值） |
|---|---|
| 平移前检查 | 运动方向读数必须 > 距离 + 400 mm + 速度 × 0.6 s（速度 30 cm/s 时为 +580 mm）。四个传感器都必须有效且 300 ms 内更新过。超出 2 m 量程无法验证的距离直接拒绝 |
| 起飞 / 旋转前 | 四周都必须 > 300 mm |
| 升降 | 没有垂直传感器，不做检查（由操作者确认） |
| 运动中刹停 | 运动方向距离 ≤ 400 mm + 接近速度 × 0.6 s（上限 1.2 m）时，立即插队发送 `stop`，被打断的指令回复 `INTERRUPTED avoid:…` |
| 任何方向 < 250 mm | 运动中立即停（正在远离那一侧的移动除外）；悬停时向反方向后退 20 cm，冷却 2.5 s；两侧都堵住就保持悬停并报 `AVOID_TRAPPED` |
| 运动中传感器失效 | 立即停；悬停时不动，但新的平移会被拒绝 |

避障 OFF 时只转发，不做以上检查。SDK 白名单、心跳看门狗和保活仍然有效。

## 链路安全

* 同一时间只执行一条指令，不排队。`stop` / `land` / `emergency` / `motoroff` 可以插队。
* SDK 的回复没有 ID：插队或超时之后，下一条普通指令前会先发 `battery?`，等收到数字回复再继续，迟到的 `ok` 不会被算成新指令的结果。
* 超时不重发；起飞超时按"可能已起飞"处理。
* PC 心跳中断 1.5 s：悬停；中断 6 s：自动降落。
* 空中或电机运转时，5 s 没有控制指令就发 `command` 保活（SDK 15 s 无指令会自动降落）。
  保活指令执行期间到达的 PC 指令会排在它后面执行，不会被拒绝。
* 另一台电脑可以在原会话静默 3 s 后用 `HELLO` 接管，不需要重启板子（V2 需要重启）。

## 主机测试（不需要硬件）

```bash
cd Latest/firmware/host_test
g++ -std=c++11 -Wall -Wextra -I../src test_policy.cpp -o test_policy && ./test_policy
g++ -std=c++11 -Wall -Wextra -DTOF_LIVE=1 -DGUARD_HOST_TEST -Istubs -I../src \
    test_firmware.cpp ../src/main.cpp ../src/TelloUart.cpp ../src/PcLink.cpp ../src/ToFSensor.cpp -o test_firmware && ./test_firmware
```

`test_policy` 测试白名单和避障规则；`test_firmware` 把整个固件和 `stubs/` 里的 Arduino 替身一起编译，
模拟一次完整飞行：会话占用、起飞、事前拒绝、接近障碍时刹停、重新对齐、自动后退、避障开关、保活、
心跳丢失后悬停再降落、会话接管。Windows 上可以用 MSYS2 / MinGW 的 g++ 运行。

> 阈值是保守的初始值，不是防撞保证。ToF 视场角有限，玻璃、黑色或斜面可能测不准；
> 实际刹车距离需要拿真机标定。第一次 live 测试请在空旷场地、低速（`speed 20`）下进行。
