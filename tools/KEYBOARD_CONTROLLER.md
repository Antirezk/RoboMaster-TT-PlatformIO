# 键盘 supervised 台架控制器

这个工具通过同一个 USB 串口完成两件事：

- 以 10 Hz 向 ESP32 发送 `Desired RC`；
- 实时读取 ESP32 的 `SOURCE / DESIRED / SAFE / ToF` 日志。

当前版本仅用于**拆桨台架测试**，没有起飞和降落按钮。

## 准备

1. 拆掉四个螺旋桨。
2. 用 USB 连接 ESP32。
3. 关闭 PlatformIO Serial Monitor，因为 Windows 不能让两个程序同时独占 COM8。
4. 本项目直接使用 PlatformIO 自带的 Python 和 pyserial，不需要 Tkinter。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_keyboard_controller.ps1 -Port COM8
```

程序会自动打开本地页面 `http://127.0.0.1:8765/`。如果 COM 口不是 COM8，修改 `-Port` 参数。

## 操作

1. 确认页面显示 `Serial: COM8 @ 115200`，并出现 ESP32 日志。
2. 点击 `ARM` 并确认已经拆桨。
3. 必须按住 `Space`，方向键才会产生非零 RC。

键位：

```text
Space + W/S  前进/后退
Space + A/D  左移/右移
Space + R/F  上升/下降
Space + Q/E  Yaw 旋转
Esc           STOP 并 DISARM
```

松开 Space、窗口失去焦点、串口断开或点击红色 STOP，都会输出零指令。

## 验证

按住 `Space + W` 时，窗口 TX 应显示：

```text
rc 0 15 0 0
```

ESP32 日志应该显示：

```text
SOURCE: HIGH-LEVEL
DESIRED: 0 15 0 0
```

如果前方安全：

```text
SAFE: 0 15 0 0
SAFETY: PASS
```

如果前方被阻挡：

```text
SAFE: 0 0 0 0
SAFETY: OVERRIDE
```
