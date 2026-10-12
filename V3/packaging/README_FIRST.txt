RoboMaster TT Voice Console V3 - Windows 绿色版
=============================================

使用：解压后双击 TelloVoiceConsole.exe（整个文件夹可以拷到任何 Windows 10/11 64 位电脑，
无需安装 Python）。第一次运行时 Windows 防火墙会询问，请选择"允许"，否则收不到无人机回复。

  * 直连：电脑连无人机的 RMTT-xxxx / TELLO-xxxx Wi-Fi，顶栏选 Direct，点 Connect。
  * 避障：电脑连 ESP32 的 TT-ToF-Demo Wi-Fi（密码 RMTT1234），顶栏选 ESP32 guard，点 Connect。
  * 练习：点 Simulator，不连任何硬件。

文件夹里：
  TelloVoiceConsole.exe   程序
  config.json             可改的参数（电量门槛、移动上限、速度、ESP32 地址等），改完重启程序
  models\whisper          离线语音模型（不要删）
  logs\                   运行日志（自动生成）

自检：命令行运行  TelloVoiceConsole.exe --self-test  ，结果写在 self_test.log。
本地 Qwen 辅助理解是可选功能，需要另外安装 Ollama 和 qwen3.5:9b。
ESP32 固件与源码见 GitHub 仓库的 V3 文件夹。
