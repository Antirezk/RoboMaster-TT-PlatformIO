# Python 高层 ↔ ESP32 底层反射协议

## 控制权

实机默认链路：

```text
Python Mission Pad 状态机（20 Hz desired RC）
        ↓ Wi-Fi UDP
ESP32 CommandInput
        ↓
四向 ToF ObstacleAvoidance
        ├─ NORMAL：通过或按距离限速
        ├─ BLOCKED：禁止继续接近
        ├─ ESCAPE：覆盖为反方向逃离 RC
        └─ FAULT：禁止相应方向并拒绝起飞
        ↓
TTController（唯一 TT UART / RC 写入者）
        ↓
RoboMaster TT
```

Python 不再通过 DJITelloPy向同一架飞机发送 RC。`--direct-wifi` 只保留为不使用 ESP32 反射层时的诊断模式，不能与集成模式同时运行。

## 无线连接

ESP32 默认建立：

```text
SSID: TT-HighLevel
Password: RMTT1234
ESP32 IP: 192.168.4.1
UDP port: 8889
```

正式使用前应通过 PlatformIO `build_flags` 覆盖 `HIGH_LEVEL_AP_SSID` 和 `HIGH_LEVEL_AP_PASSWORD`。密码至少 8 个字符。USB 串口 115200 仍支持相同命令，供桨叶拆除后的台架调试使用；真实飞行使用 UDP，不能拖着 USB 线飞行。

## 文本协议 v1

Python → ESP32，每个 UDP 数据报一条 ASCII 命令：

```text
hello
rc <lr> <fb> <ud> <yaw>
stop
takeoff
land
abort
```

四个 RC 通道在 ESP32 再次限制到 `-100..100`。如果 500 ms 没收到新 RC，`CommandInput` 自动把期望值变为零；若手动/高层飞行中持续 2 秒没有心跳，ESP32 自主进入降落序列。ToF 的紧急 ESCAPE 仍可覆盖零期望值。Python 在等待起飞回复期间也持续发送零 RC 心跳。

ESP32 → Python：

```text
HL HELLO 1
HL ACK takeoff ACCEPTED
HL ACK land ACCEPTED
HL TEL ms=... mission=READY airborne=1 fresh=1 safety=NORMAL override=0 ...
```

`HL TEL` 以 10 Hz 发给最后一个发送命令的 UDP 客户端，字段包括：

- `mission`：ESP32 Mission Executive 状态。
- `airborne`：ESP32 对 TT 起降状态的保守记录。
- `fresh`：高层 RC 是否仍在 500 ms 心跳期限内。
- `safety`：四向聚合状态 `NORMAL/BLOCKED/ESCAPE/FAULT`。
- `override`：最终安全 RC 是否修改了高层期望 RC。
- `f/b/l/r`：每向的模式和毫米距离。
- `mid/x/y/z/bat/h/age`：从 TT 内部 UART状态流解析的 Mission Pad、电量、高度和数据年龄。

Python 在 `BLOCKED/ESCAPE/FAULT` 时暂停 PID、发送零期望并等待底层反射；恢复后回到 SEARCH 重新捕获 Pad。持续介入超过配置时间则安全降落。ESP32 遥测超过 600 ms 或 TT 遥测过期时，Python拒绝继续使用相关数据。

## 烧录前后检查

```powershell
cd C:\Users\Antirez\Documents\PlatformIO\Projects\Robomaster
C:\Users\Antirez\.platformio\penv\Scripts\platformio.exe run
C:\Users\Antirez\.platformio\penv\Scripts\platformio.exe run -t upload
```

第一次必须拆下桨叶并打开串口监视器，确认：

```text
HIGH-LEVEL AP: TT-HighLevel IP=192.168.4.1 UDP=8889
MISSION -> READY
HL TEL ... safety=NORMAL ... bat=<有效值>
```

还要确认 `mid/x/y/z` 会随 Mission Pad 位置变化。TT 内部 UART状态行解析已按 SDK 的 `mid/x/y/z/bat/h` 字段实现，但具体固件输出格式仍必须在你的真机上确认。
