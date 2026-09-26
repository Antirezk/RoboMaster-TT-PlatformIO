# RoboMaster TT Mission Pad 自主控制 MVP

新增 **PC 直连官方 UDP SDK 的英文语音 Demo**，详见 [pc_demo/README.md](pc_demo/README.md)。从此目录运行 `python -m pc_demo --simulate --demo` 可先验证完整通信流程；`python -m pc_demo --voice` 使用本地英文识别控制真机。该入口独立于下述 ESP32 主链路。

这是一个运行在 Windows Laptop（Python 3.10+）上的第一阶段实现。当前唯一自主导航目标是 Mission Pad；不包含颜色识别、红色毯子、YOLO、SLAM、多机或复杂路径规划。语音只作为可选的本地输入层。

LLM 只可能出现在自然语言解析层。任务一旦成为受限的 `Task`，之后的状态机、20 Hz 定位和 RC 控制均为本地确定性代码；断网或没有 API Key 时，所有已支持的机械命令仍可运行。

## 架构

```text
Keyboard / microphone
    -> local Faster-Whisper (voice mode only, complete utterance)
    -> TaskParser (local aliases/regex first, optional local LLM fallback)
    -> validated Task
    -> MissionPlanner
    -> GotoMissionPadMission / deterministic StateMachine
    -> MissionPadTracker (mid, x, y, z)
    -> PID + MotionController
    -> bounded desired RCCommand
    -> ESP32 UDP transport
    -> four-ToF safety arbitration
    -> TTController (sole RC writer) -> RoboMaster TT
```

关键目录：

```text
llm_drone/
├── main.py                    # 参数、REPL、退出时安全 shutdown
├── config.yaml                # 唯一飞行参数来源
├── core/                      # 系统编排、领域异常
├── models/                    # Task、DroneState、MissionPadObservation
├── llm/                       # 机械解析和可选 LLM 客户端
├── voice/                     # 麦克风 VAD、Faster-Whisper、语音安全确认
├── planner/                   # Planner、状态机、任务与可替换搜索策略
├── control/                   # ESP32 UDP/串口适配、可选直连、PID、RC、运动接口
├── perception/                # Mission Pad 遥测归一化
├── telemetry/                 # 控制台和 logs/drone.log
├── simulation/                # 确定性 MockDrone
└── tests/                     # 不依赖真机的 pytest
```

`main.py` 不含 PID、SDK、Mission Pad 或状态机实现。默认实机链路不使用 DJITelloPy发送 RC；ESP32 是 TT 的唯一控制写入者。详细仲裁和协议见仓库根目录 `HIGH_LEVEL_PROTOCOL.md`。

## 安装

在 PowerShell 中：

```powershell
cd C:\Users\Antirez\Documents\PlatformIO\Projects\Robomaster\llm_drone
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
```

若使用本地语音输入，再安装：

```powershell
python -m pip install -r requirements-voice.txt
```

迁移到 Jetson Orin Nano/Ubuntu 时，使用 Python 3.10+ 创建虚拟环境并安装同一份依赖即可；文件路径和控制代码不依赖 Windows API。

## 支持命令

- `起飞` / `takeoff`
- `降落` / `land`
- `停止` / `stop`（发送零 RC，并中止活动任务；若任务正在飞行，任务安全降落）
- `电量` / `battery`
- `状态` / `status`
- `pad`（输出当前 `mid/x/y/z`）
- `搜索1号挑战垫`
- `飞到1号挑战垫`
- `飞到1号挑战垫并降落`

Pad ID 只允许 1–8。任务在后台的 20 Hz 控制线程执行，所以 REPL 仍可接受 `status`、`stop` 或 `land`。`搜索…` 找到目标后悬停并完成搜索任务；必须随后显式输入 `land`。`飞到…` 对准后悬停；只有带“并降落”的命令才下降并着陆。

## Mock Mode

```powershell
python main.py --mock
```

输入：

```text
飞到1号挑战垫并降落
```

Mock 会确定性地模拟：未检测到 Pad → 检测到错误的 Pad 3 → 找到 Pad 1 → x/y 收敛 → z 下降 → land。控制台和 `logs/drone.log` 会显示：

```text
IDLE -> PRECHECK -> TAKEOFF -> SEARCH -> ALIGN -> DESCEND -> LAND -> COMPLETED
```

Mock 是软件和流程仿真，不是空气动力学或 TT 传感器仿真，不能证明实机 PID 安全。

## 本地语音输入

语音链路为：

```text
麦克风 -> 环境噪声校准/VAD -> 完整语句 -> Faster-Whisper
       -> 机械解析优先 -> 必要时本地 Qwen3.5 -> 受限 Task
       -> 高风险任务键盘确认 -> 确定性任务状态机
```

先列出麦克风：

```powershell
python main.py --list-audio-devices
```

然后先在 Mock 中测试。这里的麦克风编号以设备列表输出为准：

```powershell
python main.py --mock --voice --audio-device 24
```

启动时会加载 `config.yaml` 中的 Faster-Whisper `medium` 模型；首次使用会下载模型，因此应在连接 TT/ESP32 Wi-Fi 之前、仍有互联网时完成。进入语音模式后，按 Enter 才开始一次录音；程序先校准环境噪声，检测到说话后录制，并在持续静音后停止。不保存 WAV 文件，也不会把音频发送到云端。输入 `q` 后 Enter 退出。

系统只在完整转写结束后解析命令，不执行流式中间文本。ASR 的语言概率、平均 log probability、静音概率都必须通过阈值。`起飞`、`搜索挑战垫`、`飞到挑战垫`还必须在键盘输入精确的 `YES`；`停止`和`降落`无需确认。语音不应作为唯一紧急手段，飞行时仍应保留键盘和官方人工安全手段。

默认 ASR 使用 CPU INT8，避免和已占用 GPU 的 Qwen3.5 争抢显存：

```yaml
voice:
  model: medium
  device: cpu
  compute_type: int8
  audio_device: null
```

Windows 实测稳定后可尝试 `device: cuda`；Jetson 上也可改为 CUDA，但必须先安装与 CTranslate2 匹配的 CUDA/cuDNN 运行库，并同时测量 ASR、Qwen 和 20 Hz 飞控线程的延迟。资源不足时优先把 ASR 改为 `small` 或继续 CPU 推理，绝不能因模型推理阻塞实时控制。语音和 Qwen 都位于任务开始前的顶层，飞行状态机启动后不调用它们。

## Dry Run 与 Mission Pad 遥测

先烧录仓库根目录的 ESP32 固件。TT 上电后，电脑连接 ESP32 建立的 Wi-Fi：

```text
SSID: TT-HighLevel
Password: RMTT1234
```

关闭 PlatformIO 串口监视器，确保没有程序占用 COM8，然后运行：

```powershell
python main.py --dry-run
```

Dry-run 通过 UDP 连接 ESP32，并允许读取 TT battery、状态、ToF 和 Mission Pad telemetry；它在 Python transport 层硬性拒绝 `takeoff` 和所有非零 RC。ESP32 自己仍持续执行 ToF 安全监测。输入：

```text
battery
status
pad
```

`pad` 示例：

```text
Mission Pad: detected=True mid=1 x=12.0 y=-4.0 z=76.0
```

若无 Pad，`mid=-1` 且 x/y/z 为 `None`。Mission Pad 检测方向当前为 `0`（下视）；必须用当前 TT 和开源控制器固件验证内部 UART 遥测格式。

## 坐标系：实机验证前禁止自动对准

所有符号归一化只在 `MissionPadTracker` 完成：

```yaml
mission_pad:
  invert_x: false  # VERIFY_ON_HARDWARE
  invert_y: false  # VERIFY_ON_HARDWARE
```

目前代码的待验证映射是：归一化 `x` 进入 RC `left_right`，归一化 `y` 进入 RC `forward_backward`，控制误差为 `0 - measurement`；`z` 只用于降落高度触发。不要根据文档或 Mock 猜方向。手持 TT 在 Pad 上方完成以下四个动作并记录数值变化：

1. 相对机头向右平移：确认 x 增大还是减小。
2. 相对机头向左平移：确认 x 相反变化。
3. 相对机头向前平移：确认 y 增大还是减小。
4. 相对机头向后平移：确认 y 相反变化。
5. 升高和降低：确认 z 的单位、有效范围和增减方向。

如果控制方向相反，只修改 `invert_x` / `invert_y`，不要在 PID、任务和 Mock 中分别改符号。还需验证原地 yaw 时下视传感器能否持续看见地面 Pad；若不能，后续通过 `SearchStrategy` 扩展小步前进或网格搜索。

## 实机测试顺序（必须逐级完成）

测试场地应室内无风、光照均匀、地面平整，安装保护罩，人员保持安全距离，始终准备输入 `land` 或 `stop`。不要跳级：

1. `python main.py --mock`，运行完整“飞到1号挑战垫并降落”。
2. 烧录 ESP32 固件、关闭串口监视器；电脑连接 `TT-HighLevel`，运行 `python main.py --dry-run`，输入 `battery` 和 `status`。
3. 不开电机，把无人机手持置于 Pad 上方，反复输入 `pad`，移动机体观察 `mid/x/y/z`。
4. 实测 `+x/-x/+y/-y/+z/-z` 与机头方向的关系，把结果写入 `invert_x/invert_y`；未确认前不得进入自动 ALIGN。
5. 退出 Dry-run，空旷场地只测试 `takeoff` → hover → `land`。
6. 测试 SEARCH；发现目标后只悬停，再人工 `land`，不要先做 ALIGN。建议临时使用 `搜索1号挑战垫` 并随时停止。
7. 校准符号后，以低高度、低 `max_xy_speed` 测试 ALIGN，不下降。
8. 确认横向闭环稳定后测试 DESCEND，保守设置下降速度和触发高度。
9. 最后才运行 `飞到1号挑战垫并降落`。

建议每一级至少重复三次且日志无异常后再升级。首次真实任务前，把 `search_timeout_sec` 调短，并让一名观察员专门负责紧急降落。

## 参数调试

所有参数集中在 `config.yaml`。示例 PID 是初值，不是经 TT 实机验证的安全值：

- `pid.x.kp`、`pid.y.kp`：横向纠偏力度；从小值增加。
- `pid.x.kd`、`pid.y.kd`：抑制振荡，但遥测噪声可能放大 RC 抖动。
- `ki`：第一阶段建议保持 0；确认存在稳定偏差后再小幅引入。
- `integral_limit`：启用积分时防止 wind-up。
- `max_xy_speed`：首次 ALIGN 应降低。
- `max_vertical_speed`：首次 DESCEND 应降低。
- `alignment_tolerance_cm` 和 `stable_frames`：共同决定何时算稳定对准。
- `landing_trigger_height_cm`：必须按真实 z 遥测和降落行为验证。
- `lost_timeout_sec`、`max_recoveries`：决定短时丢帧容忍与最终安全降落。
- `search.yaw_speed`、搜索/总超时：必须根据真实视野测试。

每次只改一类参数，并对照 `logs/drone.log` 中的时间戳、状态、Pad 数据和每周期 RC。日志文件保留完整 DEBUG 数据；控制台只在 Pad ID 变化时输出 INFO，避免 20 Hz 刷屏。

## 本地 GPU LLM：Qwen3.5

默认已经启用本机 Ollama 中实际安装的 `qwen3.5:9b`：

```yaml
llm:
  enabled: true
  provider: ollama
  model: qwen3.5:9b
  base_url: http://127.0.0.1:11434
  timeout_sec: 90
  keep_alive: 30m
  think: false
```

不需要互联网或 API Key。即使 Windows 连接到 `TT-HighLevel` 无互联网 AP，`127.0.0.1` 的 Ollama 仍可使用。解析顺序始终是：

```text
机械规则（紧急 stop/land、常用命令、数字 Pad 正则）
→ 无法匹配才调用本地 Qwen3.5
→ Ollama 原生 JSON Schema
→ Task 模型二次验证
→ Mission Planner
```

Ollama 请求使用 `temperature=0`、固定 seed、`think=false`、非流式短输出，并把完整 JSON Schema 同时交给推理服务。模型可以主动返回 `recognized=false`；含糊、否定、多动作、条件命令、多个候选 Pad、越界 ID 或越权速度/路径请求都会被安全拒绝，不会启动任务。日志中的 `TASK source=mechanical|local_llm` 可用于后续统计解析准确率。

本机真实回归命令：

```powershell
$env:RUN_LOCAL_LLM_TESTS = "1"
python -m pytest tests\test_llm_live.py -q
```

当前本地解析链路的口语、安全边界回归为 21/21 通过。本机 Ollama 实测将 `qwen3.5:9b` 约 5.49 GB 驻留在 VRAM；首次加载约需数十秒，保持加载后通常约一秒级。因此紧急停止和降落不依赖 LLM。若 Ollama 不可用、超时、JSON 无效或验证失败，系统只报错并保持原状态。

迁移 Jetson 时有两种配置：

- 继续使用 Ollama：保持 `provider: ollama`，根据 Jetson 可用统一内存选择 Qwen3.5 量化尺寸。
- 使用 vLLM/SGLang 等 OpenAI-compatible 服务：改为 `provider: openai_compatible`、相应模型名和本机 `/v1` 地址。

Jetson Orin Nano 上具体采用 2B、9B 或更小量化版本必须根据设备是 8GB/16GB、系统占用和实测延迟决定。更换模型后必须重新运行同一套 live accuracy regression，不能假定不同尺寸模型具有相同解析准确率。

## Safety 与真实/Mock 边界

真实实现：机械解析与严格验证、任务路由、20 Hz 状态机、搜索超时、低电量起飞拒绝、稳定帧、ALIGN 闭环、下降时持续横向控制、短时丢失悬停、超时回搜、多次失败安全降落、RC 限幅、Dry-run 底层拦截、日志、可中止后台任务。

仅 Mock 验证：Pad 3 后出现 Pad 1、x/y 根据 RC 理想收敛、z 根据下降 RC 理想降低、自动到达 30 cm。Mock 没有网络丢包、延迟、光照、下视视野、地效、姿态耦合或真实动力学。

必须由真实 TT 验证：TT 内部 UART 的 `mid/x/y/z/bat/h` 输出格式、Mission Pad 检测方向、x/y 符号及其相对机头轴、z 定义/单位/低高度可靠性、yaw 搜索有效性、20 Hz 控制周期、UDP 延迟/丢包、ToF 覆盖方向、PID 参数、稳定帧与丢失阈值，以及 30 cm 触发是否安全。

## 底层 ToF 反射集成

默认 `transport.mode: esp32_udp`。Python 每 50 ms 发送一个期望 RC；ESP32 每 50 ms 更新四向 ToF、执行 `ObstacleAvoidance.apply()` 并由 `TTController` 独占 TT UART。

当 ESP32 报告 `BLOCKED/ESCAPE/FAULT` 时，高层 PID立即暂停并清零，底层可以执行逃生 RC；安全状态恢复后，高层回到 SEARCH 重新捕获目标。介入持续超过 `safety.intervention_timeout_sec`，或传感器故障超过 `sensor_fault_timeout_sec`，任务进入 FAILED 并请求降落。

USB 串口模式只用于台架诊断，可在配置中改为 `esp32_serial`。真实飞行不得拖着 USB 线。`--direct-wifi` 会绕过 ToF 仲裁，仅保留为隔离诊断入口，禁止与 ESP32 控制同时使用。

紧急情况下优先使用 TT 官方可用的人工安全手段。软件 `stop` 表示零 RC/中止任务，不应被理解为任何环境下都能替代 `land` 或硬件级紧急措施。
