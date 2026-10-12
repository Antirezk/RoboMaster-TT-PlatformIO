# PyInstaller spec - build with:  pyinstaller packaging/TelloVoiceConsole.spec --noconfirm   (run from V3/)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = collect_data_files("faster_whisper")            # silero VAD model used by vad_filter
datas += [(os.path.join(ROOT, "tello_app", "config.json"), "tello_app")]
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
hiddenimports = collect_submodules("tello_app")

a = Analysis(
    [os.path.join(SPECPATH, "app_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "tensorflow", "matplotlib", "IPython", "pytest", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TelloVoiceConsole",
    console=False,          # windowed: no terminal
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TelloVoiceConsole", upx=False)
