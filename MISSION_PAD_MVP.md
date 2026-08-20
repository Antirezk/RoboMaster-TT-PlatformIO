# RoboMaster TT Mission Pad MVP

> 本文保留机载 `mission <id>` 的独立回归测试流程。当前 Laptop 高层集成模式见
> [`llm_drone/README.md`](llm_drone/README.md) 和
> [`HIGH_LEVEL_PROTOCOL.md`](HIGH_LEVEL_PROTOCOL.md)。集成模式由 Python 计算期望 RC，
> ESP32 执行四向 ToF 反射和最终 RC 仲裁；不要同时启动本文的机载 `mission` 和 Python任务。

## 当前最小目标

先不接入 LLM。当前固件只验证一个确定性闭环：

```text
开机
  -> 进入 Tello SDK 模式
  -> 开启 Mission Pad 检测
  -> 只检测下方 Mission Pad
  -> 等待 READY
  -> 用户请求 m1 任务
  -> 四向 ToF 必须全部 NORMAL
  -> takeoff
  -> go 0 0 50 20 m1
  -> land
```

`go 0 0 50 20 m1` 表示飞到 m1 坐标系的 `(0, 0, 50 cm)`，速度为
`20 cm/s`。Mission Pad 识别和位置闭环由 TT 飞控执行，ESP32 负责任务顺序、
命令超时和四向 ToF 安全监督。

## 控制优先级

```text
人工 abort / land
        >
ToF 障碍或传感器故障
        >
Mission Pad 任务状态机
        >
supervised RC 手动指令
```

任务飞行中，只要任意方向不是 `NORMAL`（包括 `BLOCKED`、`ESCAPE`、
`SENSOR_FAULT`），固件就会：

1. 发送 `rc 0 0 0 0`；
2. 取消当前动作的等待；
3. 短暂停留 300 ms；
4. 强制发送 `land`。

这是 MVP 的保守策略。四向 ToF 目前不是用来重新规划绕行，而是用于中止任务。

## 可用入口

- 串口输入 `mission 1`：启动 m1 任务；也支持 m2 到 m8。
- 串口输入 `abort` 或 `land`：中止并降落。
- 官方扩展板按键（GPIO34）长按 1.5 秒：地面启动 m1；空中中止并降落。
- `rc lr fb ud yaw`：只在任务状态为 `READY` 时用于 supervised 手动控制。

按键在每次上电后必须先松开一次，避免卡住的按键在启动时误触发任务。

## 串口中应看到的正常初始化

```text
MISSION -> ENABLE_PAD
MISSION -> SET_PAD_DIRECTION
MISSION -> READY
```

状态区应显示：

```text
MISSION: READY
```

只有 `READY` 才接受任务。启动任务后，典型状态依次为：

```text
TAKEOFF
TAKEOFF_SETTLE
GO_TO_PAD
PAD_SETTLE
LANDING
COMPLETE
READY
```

## 分阶段测试

### 第 1 阶段：桨叶拆除，只测初始化

1. 拆下四个桨叶，不放飞。
2. 烧录固件并打开 115200 波特率串口监视器。
3. 给 TT 和扩展板正常供电。
4. 确认串口收到 `ETT ... ok`，任务最终进入 `READY`。
5. 连续观察至少两分钟，确认四路 ToF 没有重新出现持续 `OVERTIME`。
6. 不输入 `mission 1`，因为它会真实发送起飞命令。

### 第 2 阶段：地面拒绝测试

仍保持桨叶拆除。遮挡任意一个 ToF，使对应方向进入 `BLOCKED` 或 `ESCAPE`。
此时输入 `mission 1`，应看到：

```text
MISSION rejected: all four directions must be NORMAL
```

这个阶段只验证“有障碍时不能启动任务”，不要验证电机动作。

### 第 3 阶段：首次低风险飞行

1. 装好桨叶和保护罩，在室内无风、光照均匀的空旷场地测试。
2. 四周至少留出约 1.5 m，人员退到安全距离。
3. 将官方 m1 平整放在地面，把 TT 放在 m1 中央并朝固定方向。
4. 上电后等到状态进入 `READY`，同时确认四路 ToF 全是 `NORMAL`。
5. 关闭串口监视器并拔掉 USB 数据线，避免线缆牵扯飞机。
6. 长按官方扩展板按键 1.5 秒，启动 m1 任务。
7. 首次测试全程准备再次长按按键；空中长按会请求中止并降落。

不要第一次就加入障碍物来测试空中 abort。先重复完成至少三次无障碍的
`起飞 -> m1 上方 50 cm -> 降落`，再单独设计软质障碍物测试。

## 成功标准

- 上电初始化连续 10 次均进入 `READY`。
- 无障碍任务连续成功至少 3 次。
- 任意 ToF 非 `NORMAL` 时，地面任务请求 100% 被拒绝。
- 飞行任务遇到 ToF 安全状态后能够进入 `ABORT_HOVER -> ABORT_LANDING`。
- 命令回复丢失或超时时不会继续执行下一步；起飞命令发出后会保守地尝试降落。

完成这些标准后，再做下一层：把自然语言转换为受限的结构化任务，
例如 `{ "action": "go_to_pad", "pad_id": 1 }`。LLM 不直接生成电机或连续 RC，
也不能绕过 ESP32 的任务状态机和 ToF 安全层。
