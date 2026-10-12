"""Entry point of the packaged Windows program (TelloVoiceConsole.exe)."""
import sys


def main():
    if "--self-test" in sys.argv:
        from tello_app.selftest import run
        sys.exit(run())
    try:
        from tello_app.gui import main as gui_main
        gui_main()
    except Exception:
        import traceback
        import tkinter.messagebox
        tkinter.messagebox.showerror("TT Voice Console failed to start", traceback.format_exc())


if __name__ == "__main__":
    main()
