"""Double-click (pythonw) to open the RoboMaster TT voice console."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from tello_app.gui import main
    main()
except Exception:
    import traceback
    import tkinter.messagebox
    tkinter.messagebox.showerror("TT Voice Console failed to start", traceback.format_exc())
