# RoboMaster TT：PC 本地英文语音 Demo

此入口复用现有语音模块和 `llm/llm_client.py` 的 Ollama 接口，控制链路是 PC 直接连接 TT 官方 SDK。旧 `main.py`、ESP32 固件和 PID 任务栈保持各自入口。标准命令无需大模型；规则无法理解的口语可交给本机 Ollama。无需 DJITelloPy 或云端识别；文字/UDP 模式只依赖 Python 标准库。

```text
Enter → 麦克风/VAD → 本地 Faster-Whisper（English）→ 完整英文文本
      → 严格规则解析 → 未匹配时本机 Ollama 提出候选 → 白名单校验/确认
      → UDP command → ok/error/查询结果
TT → 独立 UDP socket + tt-telemetry 线程 → 状态快照 + 终端摘要
```

## 1. 不接无人机先运行

在 PowerShell 中，进入本项目 `llm_drone` 目录：

```powershell
cd C:\Users\Antirez\Documents\PlatformIO\Projects\Robomaster\llm_drone
python -m pc_demo --simulate --demo
```

程序创建本机 UDP 模拟器，运行 `battery → take off → status → land on mission pad one`，展示 TX、RX、遥测、Pad 对准和降落。模拟器使用 `127.0.0.1` 和动态端口，不接触真机。它只模拟协议流程，不证明飞行或视觉定位效果。

交互文字调试：

```powershell
python -m pc_demo --simulate
```

## 2. 安装和准备本地语音

推荐独立 Python 3.10+ 虚拟环境；如果当前已有可用环境，可以直接安装依赖。

```powershell
python -m venv .venv-pc
.\.venv-pc\Scripts\Activate.ps1
python -m pip install -r pc_demo/requirements.txt
python -m pc_demo --prepare-model
python -m pc_demo --list-audio-devices
python -m pc_demo --simulate --voice
# 指定麦克风（编号取自上一条设备列表）：
python -m pc_demo --simulate --voice --audio-device 24
```

`--prepare-model` 在有互联网时下载/缓存 `small.en` 到项目的 `llm_drone/.cache/whisper`（Git 忽略），不创建无人机连接。实际语音运行强制 `local_files_only=True`，缺模型立即报错，不在连接 TT 后偷偷下载。也可以把 `config.json` 的 `asr_model` 改为已经下载的 CTranslate2 模型文件夹路径。

按 Enter 开始录音，先安静等待噪声校准，终端出现 `SPEAK now` 后说话。静音约 0.8 秒自动结束，显示 ASR 英文原文。标准命令直接解析执行；大模型提出的纠错/口语解释必须输入精确的 `YES` 才执行。低置信度和静音仍被拒绝，不通过大模型绕过 ASR 门槛。语音模式仍支持直接输入文字。当前为逐句按 Enter 录音，不是常驻唤醒词监听。

### 本机大模型理解与 ASR 纠错建议

ASR 已加入 `asr_initial_prompt` 无人机英文词汇提示，以帮助识别 `take off` 等术语，但不保证消除误识别。原始转写始终保留并显示；大模型处理的是文本，不是重新听音频。

Ollama 地址固定为 `http://127.0.0.1:11434`。当前已配置使用本机已有的 `qwen3.5:9b`，没有重新下载模型。更换模型时，在 `pc_demo/config.json` 的 `llm_model` 填写 `ollama list` 中的准确名称。检查模型无需连接无人机：

```powershell
ollama list
python -m pc_demo --check-llm
python -m pc_demo --simulate --voice
```

同一窗口还可以直接输入 `tick off` 或 `to the left`，独立测试语言理解，排除麦克风影响。例如候选流程：

```text
[识别文字] tick off
[本地大模型] ...
[原始文字] tick off
[模型理解 / 待确认] take off
[指令预览 / 尚未发送] takeoff
模型可能纠错或补了距离。确认上面的指令请输入 YES；其他输入取消：
```

`to the left` 可建议 `move left 20 centimeters`，未说距离时使用配置中的 `default_move_cm=20`，明确显示后再确认。模型不保证每次给出该建议，也可能拒绝不确定的输入。`--dry-run` 中模型结果只预览，不确认、不执行；模拟模式仍要求 YES，便于演练与真机相同的交互。

所有模型输出必须是受限 JSON，由代码编译为 SDK；字段、类型、参数范围和实际 UDP 指令再次校验，模型不能直接发送 SDK。显式否定、条件、多动作等常见形式在调用模型前拦截，其余拒绝规则也写入模型提示；模型的判断并非正确性保证，确认时应核对原文和候选动作。错误输出、超时、模型缺失不会执行候选指令。标准 `land` / `stop` 不等待模型；主线程正在推理时仍需等推理返回，或用 Ctrl+C 进入退出流程。

### 严格 SDK 输出约定

模型只允许返回 `recognized/action/value/pad_id/reason` 五个字段，禁止自由文本指令、额外字段、数组动作和命令拼接。示例：

```json
{"recognized": true, "action": "pad_land", "value": null, "pad_id": 8, "reason": "Explicit request to land on mission pad eight"}
```

| 模型动作 | 参数校验 | 代码生成的 SDK |
|---|---|---|
| `takeoff` / `land` / `stop` | value、pad_id 必须为 null | `takeoff` / `land` / `stop` |
| `battery` | value、pad_id 必须为 null | `battery?` |
| `status` | value、pad_id 必须为 null | 本地显示，不发送 SDK |
| `left/right/forward/back/up/down` | value 为整数 20–100 cm，pad_id=null | 例如 `left 20` |
| `cw/ccw` | value 为整数 1–180 度，pad_id=null | 例如 `cw 90` |
| `pad_land` | value=null，pad_id 为整数 1–8，且与原文编号一致 | `go 0 0 60 20 mN` → 等待 ok 和新遥测对准 → `land` |

这里移动/旋转范围比 SDK 的 20–500 cm / 1–360 度更保守，是 Demo 限制。数字字符串、浮点数、布尔值、越界参数均拒绝，不截断、不夹到边界。`land m1` 不是本流程的合法 SDK，发送层会拒绝；Pad 坐标与速度仅由配置生成，模型不能修改。发送层还只允许初始化 `command/mon/mdirection 0` 及上述受限动作。

`landing on mission pad one/two/three/four/five/six/seven/eight` 和对应数字 1–8 都由规则直接识别，无需模型猜测；其他 Pad 口语交由模型时，代码再次检查原文编号，缺失或不一致就拒绝。只有模型参与理解的结果才需要 YES；精确匹配的标准 Pad 句式直接进入原有定位检查。

模拟器默认只显示 Pad 1。测试其他编号时可运行 `python -m pc_demo --simulate --voice --sim-pad 8`，先 `take off`，再说 `landing on mission pad eight`。`--sim-pad` 允许 1–8，只改变模拟遥测，不会改变真实无人机报告的编号。自动协议演示也支持 `python -m pc_demo --simulate --demo --sim-pad 8`。

配置 `llm_enabled=false` 或启动时加 `--no-llm` 可恢复纯规则。ASR 置信度、模型健康检查和确认分别独立；不以模型自己声称的置信度作为飞行依据。已用本机 Qwen3.5 9B 实测文本：`tick off` → `take off`、`To the left.` → `move left 20 centimeters`、`please touch down` → `land`；`don't take off` 被规则拦截。第一次模型加载和推理约 22 秒，后两条约 1 秒。这只是少量文本样例，不代表完整语音准确率或实机验证；请先使用模拟模式。

## 3. PC 连接真实 TT

连接无人机自己的 `RMTT-…` / `TELLO-…` Wi-Fi，确保 PC 能访问 `192.168.10.1`。这不是原项目自定义的 `TT-HighLevel` AP。

**直接 SDK 控制时，必须停止旧 Python 控制程序及任何持续写入飞控的自定义 ESP32 固件控制循环。** 本入口绕过原四向 ToF 仲裁，不包含其避障功能；无需为本 Demo 烧录仓库中的自定义固件。

通道在 `config.json` 中分别配置：

| 通道 | 默认地址 | 用途 |
|---|---|---|
| Command | PC `0.0.0.0:8889` ↔ TT `192.168.10.1:8889` | 同一 socket 发命令、接 ok/error/查询结果 |
| State | PC `0.0.0.0:8890` | 独立 socket、独立接收线程 |
| Video | PC `11111` | 仅配置保留，不绑定、不发送 streamon |

先只验证通信和遥测：

```powershell
python -m pc_demo --dry-run
```

初始化必须依次收到 `command → ok`、`mon → ok`、`mdirection 0 → ok`。可输入 `battery`、`status`。Dry-run 会拒绝全部飞行动作，包括 Pad 移动和降落。Windows 防火墙需允许所用 Python 的 UDP 入站；无遥测会显示 `STALE`，起飞被拒绝。若端口已被占用，关闭原控制程序，不使用端口复用。

实机文字测试与语音测试：

```powershell
python -m pc_demo
python -m pc_demo --voice
```

启动时无人机应在地面。本程序只记录本会话的起降状态，不支持接管已在空中的无人机。先做单独 `take off` 和 `land`，再验证 Pad 降落。起飞需要新鲜遥测及至少 25% 电量。实机飞行尚需现场验证。

## 4. 英文命令

| 说话/文字 | SDK 或行为 |
|---|---|
| `take off` / `takeoff` | `takeoff` |
| `land` / `land now` | `land` |
| `stop` / `hover` | `stop`（悬停，不是关闭电机） |
| `battery` / `battery level` | `battery?` |
| `status` / `telemetry` / `pad` | 本地遥测快照 |
| `move forward 30 centimeters` | `forward 30` |
| `left 20 cm` | `left 20` |
| `turn clockwise 90 degrees` | `cw 90` |
| `land on mission pad one` / `land on mission pad 1` | Pad 1 对准、验证、降落 |

Pad 支持 1–8 及英文 one–eight；可说 `land on pad number one`。位移只接受 20–100 cm，旋转只接受 1–180 度。规则路径要求位移/角度使用阿拉伯数字；若 ASR 输出 `thirty` 等词，可由本地模型提出转换后的候选，再确认执行。否定句、条件句、多动作、多个 Pad 候选、原始 SDK `go` 和 `emergency` 均不直接匹配；不会截取一句话中的 `take off` 子串执行。

## 5. “降落在 1 号 Pad” 的实际范围

TT 并非广播所有 Pad 的全局位置；它发送当前检测的 `mid` 和相对该 Pad 的 `x/y/z`。本 Demo 不建立地图、不盲飞搜索、不把旧位置当作有效定位。目标暂时看不见时拒绝任务，保持现有飞行状态，用户可输入 `land` 在当前位置降落。

先在此会话中 `take off`，再说 `land on mission pad one`：

1. 检查最近 1 秒内的遥测，必须为目标 `mid=1` 且坐标有效。
2. 发送 `go 0 0 60 20 m1`：使用 SDK 自带的 Pad 坐标定位，在 Pad 中心上方 60 cm 对准，速度 20 cm/s。
3. 收到 `ok` 后，等待 **5 个不同的新遥测帧**；x、y 距中心及 z 距 60 cm 的偏差均不超过 10 cm。
4. 条件满足才发送 `land` 并等待 `ok`；目标丢失、遥测过期、对准超时或 SDK error 都不会继续任务中的降落步骤。此时仍可能悬停，终端可输入 `land`。

这是“可见目标 Pad 的局部对准降落”，尚不包含“从未知位置寻找远处 1 号 Pad”。不模拟假坐标，不通过 Pad 编号推算路线。参数见 `pad_*`；如果需要更远距离搜索，后续应增加场地坐标或经过验证的搜索策略。

## 6. 日志、超时和退出

终端突出显示 `识别文字 → 解析动作 → 指令预览 → 已发送 → 收到回复`。例如说 `take off`，会显示识别文字 `take off`、预览 `takeoff`，只有实际 UDP 发送成功后才出现 `[已发送] takeoff`。Dry-run 会显示 `[未发送] takeoff — dry-run 模式已拦截飞行指令`；低置信度识别也会先展示识别文字，再说明拒绝原因。Pad 的 `land` 只作为条件步骤预览，不表示已经发送。

默认只在遥测连接状态改变时提示，空闲保活不在终端刷屏，便于看清每次语音。输入 `status` 查看完整遥测；如需原来的每秒实时输出，启动时加 `--telemetry`，例如 `python -m pc_demo --dry-run --voice --telemetry`。遥测始终在独立线程持续接收，日志文件仍保存每秒状态、电量、姿态、速度、高度、ToF、mid/x/y/z/mpry、全部原始遥测包和保活收发。文件为 `logs/pc_demo.log`，5 MB 轮转、保留两个备份；`--log-file` 可改路径。

SDK 没有请求序号。这里使用一把锁，普通命令、查询和保活均串行；仅接受目标 IP/命令端口的回复，不自动重发飞行动作。若命令超时或被 Ctrl+C 打断，结果标记 UNKNOWN，锁死正常命令通道并退出，防止迟到的 ACK 串台。UDP 的重复包/极端网络延迟无法仅靠无序号协议完全消除。

空闲每 5 秒发送 `command` 保活（与当前命令互斥），避免用户录音/思考时长时间没有 SDK 输入。通信失效后不持续保活；不以自动保活取代飞行器自带的失联策略。

`q` / EOF / Ctrl+C 退出：若本会话已起飞或起飞结果未知，尝试降落；健康通道等待回复，超时污染的通道仅发一次 **未确认** 的 `land`，绝不宣称已着陆。Dry-run 不发退出降落。程序退出后的实机状态需要观察确认。

为了保持实现简单，ASR 和单次动作在主线程串行运行；等待识别、SDK 动作或 Pad 验证时不能并行处理另一条语音/文字命令。遥测和保活继续独立工作。Ctrl+C 可打断主线程并进入上述退出流程；它不等于保证立即抢占飞控动作。

## 7. 回归和后续 Jetson

```powershell
python -m pip install pytest PyYAML
python -m pytest tests/test_pc_demo.py tests/test_voice.py -q
python -m pc_demo --simulate --demo
```

测试使用真实本机 UDP 覆盖握手失败、串行收发、error、超时后迟到 ACK、外部来源回复、遥测并行、过期/缺字段、dry-run、低电量、Pad 丢失及完整降落流程；测试 ASR 门槛不替代真实麦克风识别测试。

PC 默认 CPU INT8；代码不依赖 Windows API。迁移 Jetson 时可沿用通信和解析模块，但必须按 JetPack、Python、CUDA/cuDNN 与 CTranslate2 的实际兼容性重新部署并验证，不能保证直接复制 PC 环境即可运行。当前阶段不安装 Jetson 软件。

官方依据：[DJI RoboMaster TT SDK 3.0](https://dl.djicdn.com/downloads/RoboMaster%20TT/Tello_SDK_3.0_User_Guide_en.pdf)，通道与握手见第 2 页，Pad `go` 见第 4 页，`mon/mdirection` 见第 5 页，遥测见第 8–9 页。
