#!/usr/bin/env bash
# RoboMaster TT Voice Console - Jetson Orin Nano 一键安装（JetPack 6 / Ubuntu 22.04）
#
# 用法：把整个 Latest 文件夹拷到 Jetson，然后在 Latest 文件夹里运行：
#     bash tools/setup_jetson.sh
# 运行时要能上网（先连普通 Wi-Fi，不要连 TT 的热点），中途会要求输入一次 sudo 密码。

set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="$HOME/tt-venv"

step() { echo; echo "==================== $1 ===================="; }
fail() { echo; echo "!!! $1"; echo "!!! 安装停止。把上面的输出发给 Claude。"; exit 1; }

step "1/7 检查系统"
cat /etc/nv_tegra_release 2>/dev/null || echo "（没找到 /etc/nv_tegra_release，可能不是 Jetson，继续尝试）"
python3 --version
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python 版本低于 3.10。需要 JetPack 6（R36.x）。"
[ -f "$ROOT/requirements.txt" ] && [ -d "$ROOT/tello_app" ] \
  || fail "没找到 requirements.txt 或 tello_app。请在 Latest 文件夹里运行本脚本。"
python3 - <<'EOF' || fail "连不上网。先连能上网的 Wi-Fi（不是 TT 热点）再运行。"
import urllib.request
urllib.request.urlopen("https://pypi.org/simple/pip/", timeout=10)
EOF
echo "系统检查通过。项目位置：$ROOT"

step "2/7 安装系统依赖（需要 sudo 密码）"
sudo apt-get update || fail "apt-get update 失败"
sudo apt-get install -y python3-pip python3-venv python3-tk libportaudio2 fonts-noto-cjk \
  || fail "apt-get install 失败"

step "3/7 创建虚拟环境 $VENV"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV" || fail "创建虚拟环境失败"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -U pip || fail "升级 pip 失败"

step "4/7 安装 Python 依赖（faster-whisper 等，可能要几分钟）"
pip install -r requirements.txt || fail "pip install -r requirements.txt 失败"

step "5/7 下载 Whisper 模型并检查环境"
python tools/check_setup.py --download || echo "（检查里有项目没通过；没连 TT 时“无人机不可达”是正常的，看下面模型是否在）"
if [ -d "$ROOT/models/whisper" ] && [ -n "$(ls -A "$ROOT/models/whisper" 2>/dev/null)" ]; then
  echo "Whisper 模型已就绪：$ROOT/models/whisper"
else
  echo "!!! 模型没下载成功。也可以把 Windows 电脑上 exe 旁边的 models/whisper 文件夹拷到 $ROOT/models/whisper"
fi

step "6/7 运行测试"
python -m pytest tests -q || echo "!!! 有测试没通过，把输出发给 Claude（不影响先试用界面）"

step "7/7 创建启动方式"
cat > "$HOME/tt-start.sh" <<EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
cd "$ROOT"
exec python TelloControl.pyw
EOF
chmod +x "$HOME/tt-start.sh"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  cat > "$DESKTOP_DIR/TT-Voice-Console.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TT Voice Console
Exec=$HOME/tt-start.sh
Terminal=false
EOF
  chmod +x "$DESKTOP_DIR/TT-Voice-Console.desktop"
  gio set "$DESKTOP_DIR/TT-Voice-Console.desktop" metadata::trusted true 2>/dev/null || true
  echo "桌面已创建图标：TT Voice Console（第一次双击如果提示，选“允许启动”）"
fi

echo
echo "==================== 麦克风设备 ===================="
python -c "import sounddevice as sd; print(sd.query_devices())" \
  || echo "!!! 读不到音频设备。插好 USB 麦克风后再试。"

echo
echo "==================== 当前功率模式 ===================="
sudo nvpmodel -q 2>/dev/null || echo "（读不到 nvpmodel）"

cat <<EOF

==================== 安装完成 ====================
接下来：
  1. 提速（建议）：  sudo nvpmodel -q 查看模式编号，切到最高档后运行  sudo jetson_clocks
  2. 连 TT：        nmcli dev wifi list   找到 RMTT-xxxxxx，然后
                    sudo nmcli dev wifi connect "RMTT-xxxxxx"
  3. 启动：          双击桌面 "TT Voice Console"，或运行  ~/tt-start.sh
     先点 Simulator 试语音，再点 Connect 连真机。
  麦克风报采样率错误时：把 tello_app/config.json 里 "audio_device" 改成 "pulse"。
EOF
