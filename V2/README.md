# Four-ToF voice command gate

独立实验版。旧 `src/`、`llm_drone/pc_demo` 不移动、不覆盖。
必要的 ToF 驱动、录音/ASR 和异常类型复制到这里后独立修改；模型缓存复用
`../llm_drone/.cache/whisper`，运行时不下载模型、不调用 LLM。

## 通路与范围

英文语音 → 本地 small.en → 严格整句解析 → USB 串口 115200 / ESP32 UDP9000 → ESP32 四向检查
→ 内部 UART `[TELLO] forward 30` → `ETT ok/error` → PC 日志。
这里 PC 不向 TT UDP 8889 发控制包，因此不会绕过新增检查。
ESP32 创建 `TT-ToF-Demo` Wi-Fi（密码 `RMTT1234`），高层地址 `192.168.4.1:9000`。
USB 适合桌面调试；飞行联调使用无线，不拖着连接电脑的 USB 线起飞。
每次固件启动仅接纳首个发 PING 的 USB/UDP 客户端；之后不能抢占，重新连接须重启板子。
UDP 命令不重发，丢包时通过 status 判断；丢回复不能理解成执行失败并自动重试。
没有改动 TT 的 8889/8890/11111 定义；本入口显示四向 ToF，不声称已订阅 TT 全量飞行遥测。

支持 `take off`、`land`、`stop`、`forward 30`、`back 30`、`left 30`、`right 30`、
`battery`、`status`、`exit`。距离限 20–100 cm，整数。`stop` 是悬停，绝不是关电机。
否定句、组合句、上下移动、旋转、Mission Pad 降落、原始 rc、motoron 均拒绝。
四水平测距传感器无法验证上下空间或 Mission Pad 的整条路径；这些高层任务暂未接入本入口。

## 四传感器决策

PCA9548A 0x70，VL53L0X 0x29；SDA27/SCL26；前0、后1、左4、右3。
原代码左侧注释 CH2 与实际接线 CH4 不一致，本版保留实际 CH4。

* 四个读数均须 VALID、300ms 内更新，原始值 1–2000mm；超量程 SAFE 不视为空旷。
* 距离取 min(raw, filtered)，新近障碍不等滤波追上。
* 全部方向须 >300mm；运动方向须 >行程毫米数+500mm。前进30cm即 >800mm。
* 不自动缩短指令、不绕行、不自动反向逃逸；拒绝就是完全不发送该运动。
* 运行/悬停时四向持续监控；运动方向 <=500mm、任一方向 <=300mm、任一测距失效，
  或 PC 心跳超过1.5秒未到，发送一次 stop 请求并锁住移动。
* command 收到 ok 后设置 speed20，第二个 ok 后才 ready。一次仅一个 SDK 请求，不排队。
* 超时、error、执行中打断都保留 UNKNOWN；SDK 无请求ID，晚到 ok 不可解释为 stop 成功。
  锁定后仍可请求 land（结果不确认），核实实机状态后重启；不自动重发运动。
* 串口帧带递增请求ID防止同一会话重复执行。PING 不经过 ASR，后台每250ms发送。

这些是待实测标定的保守阈值，不是碰撞保证。ToF 视野有限，玻璃、暗面、斜面可能误测；
采样和恢复使用原驱动同步 I2C，最坏延迟、真实制动距离和执行中 stop 的效果尚未实机验证。
没有独立定位反馈验证实际走了30cm，DONE只代表飞控返回ok，不等于测量验证的位移。
起飞/降落的上下通道仍由操作者检查。异常锁定后不再发送悬停保活，飞控自身超时行为需实测。

## 先在桌面验证

在 `Robomaster` 目录打开 PowerShell：

```powershell
python -m pip install -r V2/requirements.txt
# 仅编译，不上传、不控制设备
pio run -d V2/firmware -e bench
# 确认要更换板上固件后上传 bench（源文件仍保留，板上旧固件会被替换）
pio run -d V2/firmware -e bench -t upload --upload-port COM8
python V2/pc/main.py --port COM8
```

COM8 换成实际端口；关闭占用它的串口监视器。启动加载已有离线模型；看到 SPEAK now
直接说话，不用回车。说 status 查看最新四向值；终端不持续刷 ToF。
bench 使用真实 ToF，但 SDK握手/电量为模拟（电量85），任何飞行请求只显示 PREVIEW。
将障碍放到前方80cm内说 forward30，应 REJECTED；移到大于80cm，其他方向大于30cm且
读数有效，应 PREVIEW。依次遮挡各方向、拔传感器，确认拒绝；超量程也拒绝。

`--text` 是可选键盘调试入口；正常模式全语音。
无线 bench：电脑连接 `TT-ToF-Demo` 后运行：

```powershell
python V2/pc/main.py --host 192.168.4.1
```

`pio run -d V2/firmware -e live` 可编译实际 UART 路径，但本次未上传或飞行。
完成板上 bench 验证后，可用 `-e live -t upload --upload-port COM8` 上传实际执行版本；
进入空中联调前仍需制动测试和四向传感器验证，不能把编译通过当成可安全飞行。

## 自动验证

```powershell
python -m pytest V2/tests -q
g++ -std=c++11 V2/tests/policy_test.cpp -o "$env:TEMP/tof_policy_test.exe"
& "$env:TEMP/tof_policy_test.exe"
pio run -d V2/firmware -e bench -e live
```

参考：DJI RoboMaster TT SDK3.0
https://dl.djicdn.com/downloads/RoboMaster%20TT/Tello_SDK_3.0_User_Guide_en.pdf
