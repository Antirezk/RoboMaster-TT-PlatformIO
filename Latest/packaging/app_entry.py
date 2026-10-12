"""Entry point of the packaged Windows program (TelloVoiceConsole.exe)."""
import sys


def main():
    if "--self-test" in sys.argv:
        try:
            from tello_app.selftest import run
            code = run()
        except BaseException:
            import traceback
            from pathlib import Path
            Path(sys.executable).resolve().with_name("self_test.log").write_text(
                "[FAIL] self-test crashed before finishing\n" + traceback.format_exc(), encoding="utf-8")
            code = 1
        sys.exit(code)
    try:
        from tello_app.gui import main as gui_main
        gui_main()
    except Exception:
        import traceback
        import tkinter.messagebox
        tkinter.messagebox.showerror("TT Voice Console failed to start", traceback.format_exc())


if __name__ == "__main__":
    main()
