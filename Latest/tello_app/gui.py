"""RoboMaster TT voice console - Tk main window.

Threads: Tk main loop (this file) · tt-control (Controller, owns UDP) ·
tt-asr (VoiceWorker) · tt-state (telemetry). The UI never blocks on the drone.
"""
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from . import ROOT, __version__, sdk
from .config import AppConfig
from .controller import Controller
from .llm import QwenInterpreter
from .parser import NotUnderstood, parse
from .sdk import FLIGHT, TEST, SDKError, make
from .voice import VoiceWorker

C = {
    "bg": "#0b1220", "panel": "#131c2e", "panel2": "#1a2540", "line": "#26324d",
    "text": "#e6edf7", "muted": "#8a9ab5", "dim": "#5d6b85",
    "cyan": "#38bdf8", "green": "#22c55e", "amber": "#f59e0b", "red": "#ef4444",
    "violet": "#a78bfa", "blue": "#3b82f6",
}
LOG_COLORS = {"tx": C["cyan"], "rx": C["green"], "ok": C["green"], "warn": C["amber"],
              "error": C["red"], "info": C["muted"], "mode": C["violet"], "voice": C["violet"],
              "heard": C["text"]}
LOG_PATH = ROOT / "logs/tello_app.log"


def pick_font(root, *names):
    available = set(tkfont.families(root))
    return next((n for n in names if n in available), "TkDefaultFont")


class FlatButton(tk.Label):
    """Flat button with hover colour; works the same on Windows and Linux."""
    def __init__(self, master, text, command, color, *, fg="white", font=None, padx=14, pady=10, **kw):
        super().__init__(master, text=text, bg=color, fg=fg, font=font, padx=padx, pady=pady,
                         cursor="hand2", **kw)
        self.color, self.fg, self.command, self.enabled = color, fg, command, True
        self.hover = self._shade(color, 1.18)
        self.bind("<Enter>", lambda e: self.enabled and self.configure(bg=self.hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self.color if self.enabled else C["line"]))
        self.bind("<ButtonRelease-1>", self._click)

    @staticmethod
    def _shade(color, factor):
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(min(255, int(v * factor + 8)) for v in (r, g, b))

    def _click(self, event):
        if self.enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def set_enabled(self, on):
        if on != self.enabled:
            self.enabled = on
            self.configure(bg=self.color if on else C["line"], fg=self.fg if on else C["dim"],
                           cursor="hand2" if on else "arrow")

    def set_color(self, color):
        self.color, self.hover = color, self._shade(color, 1.18)
        if self.enabled:
            self.configure(bg=color)


class App:
    def __init__(self, root, controller=None, voice_factory=None, interpreter=None):
        self.root = root
        self.config = AppConfig.load()
        self.events = queue.SimpleQueue()
        self._setup_file_log()
        self.controller = controller or Controller(self.config, log=self.log)
        self.interpreter = interpreter or QwenInterpreter(self.config.llm_model, self.config.llm_timeout_sec)
        self.voice = VoiceWorker(self.config, lambda: self.controller.mode, self._on_speech,
                                 lambda state, text: self.events.put(("voice_status", state, text)),
                                 factory=voice_factory)
        self.voice_state = "off"
        self.closing = False
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.tick)
        self.log("info", "Window ready. Connect to the drone (join its RMTT-/TELLO- Wi-Fi first) or press Simulator.")

    # ----------------------------------------------------------- logging
    def _setup_file_log(self):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.file_log = logging.getLogger("tello_app")
        self.file_log.setLevel(logging.INFO)
        self.file_log.propagate = False
        if not self.file_log.handlers:
            handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.file_log.addHandler(handler)

    def log(self, kind, text):
        self.file_log.info("[%s] %s", kind, text)
        self.events.put(("log", kind, text))

    # ------------------------------------------------------------- build
    def _build(self):
        root = self.root
        root.title(f"RoboMaster TT · Voice Console V{__version__}")
        root.geometry("1320x840")
        root.minsize(1180, 760)
        root.configure(bg=C["bg"])
        self.f_ui = pick_font(root, "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", "wenquanyi zen hei")
        self.f_mono = pick_font(root, "Cascadia Mono", "Consolas", "Noto Sans Mono CJK SC", "DejaVu Sans Mono")
        F = lambda size, weight="normal": (self.f_ui, size, weight)
        self.F = F

        # ---- header
        head = tk.Frame(root, bg=C["bg"])
        head.pack(fill="x", padx=24, pady=(14, 8))
        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text="RoboMaster TT  Voice Console  V3", bg=C["bg"], fg=C["text"], font=F(22, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(left, text="", bg=C["bg"], fg=C["muted"], font=F(10))
        self.subtitle.pack(anchor="w")
        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right")
        self.conn_pill = tk.Label(right, text="●  Disconnected", bg=C["panel2"], fg=C["muted"], font=F(11, "bold"),
                                  padx=12, pady=8)
        self.conn_pill.pack(side="left", padx=(0, 12))
        seg = tk.Frame(right, bg=C["line"], padx=2, pady=2)
        seg.pack(side="left", padx=(0, 10))
        self.route_buttons = {}
        for route, text in (("direct", "Direct"), ("guard", "ESP32 guard")):
            b = tk.Label(seg, text=text, font=F(10), padx=10, pady=6, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, r=route: self.select_route(r))
            self.route_buttons[route] = b
        self.b_connect = FlatButton(right, "Connect", lambda: self.connect(False), C["blue"], font=F(11, "bold"))
        self.b_connect.pack(side="left", padx=4)
        self.b_sim = FlatButton(right, "Simulator", lambda: self.connect(True), C["panel2"], font=F(11))
        self.b_sim.pack(side="left", padx=4)
        self.b_disc = FlatButton(right, "Disconnect", self.disconnect, C["panel2"], font=F(11))
        self.b_disc.pack(side="left", padx=4)

        # ---- mode switch
        modes = tk.Frame(root, bg=C["bg"])
        modes.pack(fill="x", padx=24, pady=(0, 10))
        self.mode_cards = {}
        for mode, icon, title, desc in (
                (TEST, "●", "Low-speed test mode", "Props removed · motoron / motoroff and read-only queries · cannot take off"),
                (FLIGHT, "●", "Flight mode", "Props on · take off / land / hover / move / climb / rotate / speed")):
            card = tk.Frame(modes, bg=C["panel"], highlightthickness=2, highlightbackground=C["line"], cursor="hand2")
            card.pack(side="left", fill="x", expand=True, padx=(0, 12) if mode == TEST else 0)
            inner = tk.Frame(card, bg=C["panel"])
            inner.pack(fill="x", padx=16, pady=8)
            ic = tk.Label(inner, text=icon, bg=C["panel"], fg=C["muted"], font=F(16))
            ic.pack(side="left", padx=(0, 12))
            txt = tk.Frame(inner, bg=C["panel"])
            txt.pack(side="left", fill="x", expand=True)
            t = tk.Label(txt, text=title, bg=C["panel"], fg=C["text"], font=F(14, "bold"))
            t.pack(anchor="w")
            d = tk.Label(txt, text=desc, bg=C["panel"], fg=C["muted"], font=F(10))
            d.pack(anchor="w")
            badge = tk.Label(inner, text="", bg=C["panel"], fg=C["bg"], font=F(10, "bold"), padx=10, pady=3)
            badge.pack(side="right")
            widgets = (card, inner, ic, txt, t, d)
            for w in widgets:
                w.bind("<Button-1>", lambda e, m=mode: self.select_mode(m))
            self.mode_cards[mode] = (widgets, ic, badge)
        self.banner = tk.Label(root, text="", bg=C["bg"], fg=C["amber"], font=F(10), anchor="w")
        self.banner.pack(fill="x", padx=26, pady=(0, 8))

        # ---- log
        logp = tk.Frame(root, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        logp.pack(side="bottom", fill="x", padx=24, pady=(10, 14))
        bar = tk.Frame(logp, bg=C["panel"])
        bar.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(bar, text="Communication log", bg=C["panel"], fg=C["text"], font=F(11, "bold")).pack(side="left")
        tk.Label(bar, text="  ▲ sent  ▼ reply    file: logs/tello_app.log", bg=C["panel"], fg=C["dim"],
                 font=F(9)).pack(side="left")
        clear = tk.Label(bar, text="Clear", bg=C["panel"], fg=C["muted"], font=F(9), cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda e: self.logbox.configure(state="normal") or
                   self.logbox.delete("1.0", "end") or self.logbox.configure(state="disabled"))
        self.logbox = tk.Text(logp, height=6, bg=C["panel"], fg=C["text"], bd=0, highlightthickness=0,
                              font=(self.f_mono, 10), wrap="word", padx=14, pady=8, state="disabled",
                              insertbackground=C["text"])
        self.logbox.pack(fill="both", expand=True)
        for kind, color in LOG_COLORS.items():
            self.logbox.tag_configure(kind, foreground=color)
        self.logbox.tag_configure("time", foreground=C["dim"])

        # ---- main 3 columns
        main = tk.Frame(root, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=24)
        main.columnconfigure(0, weight=3, uniform="c")
        main.columnconfigure(1, weight=4, uniform="c")
        main.columnconfigure(2, weight=4, uniform="c")
        main.rowconfigure(0, weight=1)
        self._build_status(self._panel(main, 0, "DRONE STATUS"))
        self._build_voice(self._panel(main, 1, "VOICE COMMAND"))
        self.pad_host = self._panel(main, 2, "CONTROLS")
        self._build_pads(self.pad_host)

        root.bind_all("<KeyPress>", self._key)
        self._apply_mode()

    def _panel(self, parent, column, title):
        outer = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        outer.grid(row=0, column=column, sticky="nsew", padx=(0, 12) if column < 2 else 0)
        tk.Label(outer, text=title, bg=C["panel"], fg=C["muted"], font=self.F(10, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        body = tk.Frame(outer, bg=C["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        return body

    def _tile(self, parent, row, col, title):
        tile = tk.Frame(parent, bg=C["panel2"])
        tile.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        tk.Label(tile, text=title, bg=C["panel2"], fg=C["muted"], font=self.F(9)).pack(anchor="w", padx=12, pady=(6, 0))
        value = tk.Label(tile, text="—", bg=C["panel2"], fg=C["text"], font=self.F(16, "bold"))
        value.pack(anchor="w", padx=12)
        sub = tk.Label(tile, text="", bg=C["panel2"], fg=C["dim"], font=self.F(9))
        sub.pack(anchor="w", padx=12, pady=(0, 6))
        return value, sub

    def _build_status(self, body):
        grid = tk.Frame(body, bg=C["panel"])
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="t")
        grid.columnconfigure(1, weight=1, uniform="t")
        self.t_state = self._tile(grid, 0, 0, "State")
        self.t_batt = self._tile(grid, 0, 1, "Battery")
        self.t_height = self._tile(grid, 1, 0, "Height")
        self.t_yaw = self._tile(grid, 1, 1, "Heading (yaw)")
        self.batt_bar = tk.Canvas(body, height=8, bg=C["panel2"], highlightthickness=0)
        self.batt_bar.pack(fill="x", padx=4, pady=(6, 2))
        self.busy_label = tk.Label(body, text="Idle", bg=C["panel"], fg=C["muted"], font=self.F(10), anchor="w",
                                   wraplength=300, justify="left")
        self.busy_label.pack(fill="x", padx=4, pady=(8, 0))
        self.info_label = tk.Label(body, text="", bg=C["panel"], fg=C["dim"], font=self.F(9), anchor="w")
        self.info_label.pack(fill="x", padx=4, pady=(4, 0))
        card = tk.Frame(body, bg=C["panel2"])
        card.pack(fill="x", padx=4, pady=(8, 0))
        top = tk.Frame(card, bg=C["panel2"])
        top.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(top, text="Obstacle avoidance", bg=C["panel2"], fg=C["text"], font=self.F(11, "bold")).pack(side="left")
        self.b_avoid = FlatButton(top, "OFF", self.toggle_avoidance, C["line"], font=self.F(10, "bold"), padx=12, pady=3)
        self.b_avoid.pack(side="right")
        self.avoid_note = tk.Label(card, text="", bg=C["panel2"], fg=C["muted"], font=self.F(9), anchor="w",
                                   justify="left", wraplength=255)
        self.avoid_note.pack(fill="x", padx=12)
        self.radar = tk.Canvas(card, height=112, bg=C["panel2"], highlightthickness=0)
        self.radar.pack(fill="x", padx=8, pady=(2, 6))

    def _build_voice(self, body):
        top = tk.Frame(body, bg=C["panel"])
        top.pack(fill="x")
        self.mic = tk.Canvas(top, width=96, height=96, bg=C["panel"], highlightthickness=0, cursor="hand2")
        self.mic.pack(side="left")
        self.mic.bind("<Button-1>", lambda e: self.toggle_voice())
        self.pulse = 0
        side = tk.Frame(top, bg=C["panel"])
        side.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.mic_title = tk.Label(side, text="Microphone off", bg=C["panel"], fg=C["text"], font=self.F(14, "bold"), anchor="w")
        self.mic_title.pack(fill="x")
        self.mic_status = tk.Label(side, text="Click the microphone to start voice control", bg=C["panel"], fg=C["muted"],
                                   font=self.F(10), anchor="w", wraplength=330, justify="left")
        self.mic_status.pack(fill="x", pady=(2, 6))
        self.use_llm = tk.BooleanVar(value=False)
        tk.Checkbutton(side, text="Use local Qwen when the rules don't understand", variable=self.use_llm, bg=C["panel"],
                       fg=C["muted"], selectcolor=C["panel2"], activebackground=C["panel"],
                       activeforeground=C["text"], font=self.F(9), bd=0, highlightthickness=0).pack(anchor="w")

        def block(title):
            frame = tk.Frame(body, bg=C["panel2"])
            frame.pack(fill="x", pady=(10, 0))
            tk.Label(frame, text=title, bg=C["panel2"], fg=C["muted"], font=self.F(9)).pack(anchor="w", padx=12, pady=(8, 0))
            return frame
        f = block("You said")
        self.heard = tk.Label(f, text="—", bg=C["panel2"], fg=C["text"], font=self.F(15, "bold"), anchor="w",
                              wraplength=380, justify="left")
        self.heard.pack(fill="x", padx=12, pady=(0, 10))
        f = block("Understood as")
        row = tk.Frame(f, bg=C["panel2"])
        row.pack(fill="x", padx=12, pady=(0, 10))
        self.sdk_chip = tk.Label(row, text="", bg=C["panel2"], fg=C["bg"], font=(self.f_mono, 11, "bold"), padx=8, pady=2)
        self.sdk_chip.pack(side="right", anchor="n")
        self.intent_label = tk.Label(row, text="—", bg=C["panel2"], fg=C["text"], font=self.F(12), anchor="w",
                                     wraplength=330, justify="left")
        self.intent_label.pack(side="left", fill="x", expand=True)
        f = block("Result")
        self.result = tk.Label(f, text="Waiting for a command", bg=C["panel2"], fg=C["muted"], font=self.F(11), anchor="w",
                               wraplength=380, justify="left")
        self.result.pack(fill="x", padx=12, pady=(0, 10))

        f = block("Type a command (same parser as voice, e.g. forward 50)")
        row = tk.Frame(f, bg=C["panel2"])
        row.pack(fill="x", padx=12, pady=(2, 10))
        self.entry = tk.Entry(row, bg=C["bg"], fg=C["text"], insertbackground=C["text"], bd=0,
                              highlightthickness=1, highlightbackground=C["line"], highlightcolor=C["cyan"],
                              font=self.F(11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self.typed())
        FlatButton(row, "Send", self.typed, C["blue"], font=self.F(10, "bold"), pady=6).pack(side="left", padx=(8, 0))
        self.help = tk.Label(body, text="", bg=C["panel"], fg=C["dim"], font=self.F(9), anchor="w", justify="left",
                             wraplength=400)
        self.help.pack(fill="x", pady=(8, 0))

    def _build_pads(self, host):
        F = self.F
        # ---------------- flight pad
        fp = tk.Frame(host, bg=C["panel"])
        self.flight_pad = fp
        row = tk.Frame(fp, bg=C["panel"])
        row.pack(fill="x")
        self.b_takeoff = FlatButton(row, "▲  Take off", lambda: self.press("takeoff"), C["green"], font=F(13, "bold"))
        self.b_takeoff.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.b_land = FlatButton(row, "▼  Land", lambda: self.press("land"), C["blue"], font=F(13, "bold"))
        self.b_land.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.b_hover = FlatButton(fp, "■  Hover / STOP   (Space)", lambda: self.press("stop"), C["amber"],
                                  fg="#1b1300", font=F(13, "bold"))
        self.b_hover.pack(fill="x", pady=10)

        steps = tk.Frame(fp, bg=C["panel"])
        steps.pack(fill="x", pady=(0, 6))
        self.dist = tk.IntVar(value=50)
        self.angle = tk.IntVar(value=90)
        for label, var, low, high, inc in (("Distance cm", self.dist, 20, self.config.max_move_cm, 10),
                                           ("Angle °", self.angle, 1, 360, 15)):
            box = tk.Frame(steps, bg=C["panel"])
            box.pack(side="left", fill="x", expand=True)
            tk.Label(box, text=label, bg=C["panel"], fg=C["muted"], font=F(9)).pack(side="left")
            tk.Spinbox(box, from_=low, to=high, increment=inc, textvariable=var, width=5, bg=C["panel2"],
                       fg=C["text"], buttonbackground=C["line"], bd=0, insertbackground=C["text"],
                       highlightthickness=0, font=F(11), justify="center").pack(side="left", padx=6)

        grid = tk.Frame(fp, bg=C["panel"])
        grid.pack(fill="x")
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="g")
        self.move_buttons = []
        layout = [(0, 0, "Turn left  Q", ("turn", "ccw")), (0, 1, "↑ Forward  W", ("move", "forward")),
                  (0, 2, "Turn right  E", ("turn", "cw")), (1, 0, "← Left  A", ("move", "left")),
                  (1, 1, "↓ Back  S", ("move", "back")), (1, 2, "Right →  D", ("move", "right")),
                  (2, 0, "▲ Up  R", ("move", "up")), (2, 2, "▼ Down  F", ("move", "down"))]
        for r, c, text, (kind, direction) in layout:
            b = FlatButton(grid, text, lambda k=kind, d=direction: self.press_move(k, d), C["panel2"],
                           font=F(11), pady=12)
            b.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            self.move_buttons.append(b)
        tk.Label(grid, text="T take off · L land", bg=C["panel"], fg=C["dim"], font=F(9)).grid(row=2, column=1)
        self.b_emergency = FlatButton(fp, "EMERGENCY  (motors stop instantly - the drone will fall)", self.emergency, "#7f1d1d",
                                      font=F(11, "bold"))
        self.b_emergency.pack(fill="x", pady=(14, 0))

        # ---------------- test pad
        tp = tk.Frame(host, bg=C["panel"])
        self.test_pad = tp
        self.b_motor_on = FlatButton(tp, "▶  Motors ON, low speed  (motoron)", lambda: self.press("motoron"), C["green"],
                                     font=F(14, "bold"), pady=16)
        self.b_motor_on.pack(fill="x")
        self.b_motor_off = FlatButton(tp, "■  Motors OFF  (motoroff · Space)", lambda: self.press("motoroff"),
                                      C["red"], font=F(14, "bold"), pady=16)
        self.b_motor_off.pack(fill="x", pady=10)
        tk.Label(tp, text="Read-only queries (the drone will not move)", bg=C["panel"], fg=C["muted"], font=F(9)).pack(anchor="w", pady=(6, 2))
        q = tk.Frame(tp, bg=C["panel"])
        q.pack(fill="x")
        for i in range(3):
            q.columnconfigure(i, weight=1, uniform="q")
        self.read_buttons = []
        for idx, (text, query) in enumerate((("Battery", "battery?"), ("Wi-Fi SNR", "wifi?"), ("SDK version", "sdk?"),
                                             ("Serial no.", "sn?"), ("Hardware", "hardware?"), ("Motor time", "time?"))):
            b = FlatButton(q, text, lambda qq=query: self.press("read", qq), C["panel2"], font=F(10), pady=9)
            b.grid(row=idx // 3, column=idx % 3, sticky="nsew", padx=3, pady=3)
            self.read_buttons.append(b)
        tk.Label(tp, text="Remove all four propellers and place the drone flat before testing.\n"
                          "motoron works only on the ground. Battery is polled every 5 s;\nbelow the minimum the motors are stopped automatically.", bg=C["panel"], fg=C["dim"],
                 font=F(9), justify="left").pack(anchor="w", pady=(12, 0))

    # ----------------------------------------------------------- actions
    def connect(self, simulate):
        self.controller.connect(simulate)

    def select_route(self, route):
        try:
            self.controller.set_route(route)
        except SDKError as exc:
            return self.show_result(str(exc), C["red"])
        self.show_result("Route: " + ("PC → ESP32 ToF guard → TT (join the TT-ToF-Demo Wi-Fi)" if route == "guard"
                                      else "PC → TT directly (join the RMTT-/TELLO- Wi-Fi)"), C["violet"])

    def toggle_avoidance(self):
        snap = self.controller.snapshot()
        if snap["route"] != "guard" or not snap["connected"]:
            return self.show_result("Obstacle avoidance needs the ESP32 guard: choose 'Via ESP32 guard' and connect",
                                    C["amber"])
        turn_on = not snap["avoid"]
        if not turn_on and snap["airborne"] and not messagebox.askokcancel(
                "Turn obstacle avoidance OFF", "The drone is flying. With avoidance OFF the ESP32 relays every "
                "command without checking the ToF sensors.\n\nTurn it off?", icon="warning", parent=self.root):
            return
        try:
            self.controller.set_avoidance(turn_on)
        except SDKError as exc:
            self.show_result(str(exc), C["red"])

    def disconnect(self):
        self.voice.invalidate()
        self.controller.disconnect()

    def select_mode(self, mode):
        if mode == self.controller.mode:
            return
        if mode == FLIGHT and not messagebox.askokcancel(
                "Switch to Flight mode", "In Flight mode the drone really takes off.\n\nPlease confirm:\n· All four propellers are fitted correctly\n"
                "· The area is clear, people at least 2 m away\n· Battery is above 30%\n\nSwitch now?", icon="warning", parent=self.root):
            return
        try:
            message = self.controller.set_mode(mode)
        except SDKError as exc:
            self.show_result(str(exc), C["red"])
            return
        self.voice.invalidate()
        self.show_result(message, C["violet"])
        self._apply_mode()

    def _apply_mode(self):
        mode = self.controller.mode
        for m, (widgets, icon, badge) in self.mode_cards.items():
            active = m == mode
            color = (C["green"] if m == TEST else C["cyan"]) if active else C["line"]
            widgets[0].configure(highlightbackground=color)
            bg = C["panel2"] if active else C["panel"]
            for w in widgets:
                w.configure(bg=bg)
            icon.configure(fg=color if active else C["muted"])
            badge.configure(text="ACTIVE" if active else "", bg=color if active else bg)
        if mode == TEST:
            self.flight_pad.pack_forget()
            self.test_pad.pack(fill="both", expand=True)
            self.banner.configure(text="Low-speed test: remove the propellers. Any takeoff / movement command is refused.", fg=C["green"])
            self.help.configure(text="Say: power on · power off / stop · battery · status")
        else:
            self.test_pad.pack_forget()
            self.flight_pad.pack(fill="both", expand=True)
            self.banner.configure(text="Flight mode: saying take off REALLY takes off! Say stop / hover or press Space to hover at any time.",
                                  fg=C["amber"])
            self.help.configure(text="Say: take off · land · hover · forward 50 cm · up 1 meter · turn left 90 · battery")

    def toggle_voice(self):
        on = not self.voice.enabled.is_set()
        self.voice.set_enabled(on)
        self.voice_state = "starting" if on else "off"
        self.mic_status.configure(text="Starting…" if on else "Click the microphone to start voice control")

    def emergency(self):
        self.execute(make("emergency", source="button"))

    def press(self, action, query=None):
        try:
            intent = make(action, query=query, source="button") if action == "read" else make(action, source="button")
        except SDKError as exc:
            return self.show_result(str(exc), C["red"])
        self.execute(intent)

    def press_move(self, kind, direction):
        try:
            if kind == "move":
                value = int(self.dist.get())
                if value > self.config.max_move_cm:
                    raise SDKError(f"exceeds the safety cap of {self.config.max_move_cm} cm")
            else:
                value = int(self.angle.get())
            self.execute(make(kind, value, direction=direction, source="button"))
        except (SDKError, tk.TclError, ValueError) as exc:
            self.show_result(f"Invalid value: {exc}", C["red"])

    def typed(self):
        text = self.entry.get().strip()
        if text:
            self.entry.delete(0, "end")
            self.handle_text(text, "typed")

    def handle_text(self, text, source):
        self.heard.configure(text=f"“{text}”")
        self.log("heard", f"[{source}] {text}")
        try:
            intent = parse(text, self.controller.mode, self.config)
        except NotUnderstood as exc:
            if not self.use_llm.get():
                return self.show_intent(None, str(exc))
            self.show_intent(None, "Rules did not understand - asking local Qwen…", C["violet"])
            generation = self.voice.generation
            mode = self.controller.mode

            def ask():
                try:
                    found = self.interpreter.interpret(text, mode, self.config)
                    self.events.put(("llm", found, generation, mode))
                except SDKError as err:
                    self.events.put(("llm_fail", str(err), generation, mode))
            threading.Thread(target=ask, daemon=True, name="tt-qwen").start()
            return
        except SDKError as exc:
            return self.show_intent(None, str(exc))
        self.execute(intent)

    def execute(self, intent):
        self.show_intent(intent)
        try:
            message = self.controller.submit(intent)
            self.show_result(message, C["cyan"])
        except SDKError as exc:
            self.show_result(str(exc), C["red"])
            self.log("warn", f"Not sent: {exc}")

    def show_intent(self, intent, error=None, color=None):
        if intent is None:
            self.intent_label.configure(text=error, fg=color or C["red"])
            self.sdk_chip.configure(text="", bg=C["panel2"])
            if color is None:
                self.show_result("Not sent", C["muted"])
            return
        self.intent_label.configure(text=f"{intent.label}  ·  {intent.source}", fg=C["text"])
        self.sdk_chip.configure(text=intent.sdk or "local", bg=C["cyan"] if intent.sdk else C["muted"])

    def show_result(self, text, color):
        self.result.configure(text=text, fg=color)

    def _on_speech(self, text, generation):
        self.events.put(("speech", text, generation))

    def _key(self, event):
        if isinstance(event.widget, (tk.Entry, tk.Spinbox)) or self.closing:
            return
        key = event.keysym.lower()
        if key == "space":
            self.press("stop" if self.controller.mode == FLIGHT else "motoroff")
            return
        if self.controller.mode != FLIGHT:
            return
        bindings = {"w": ("move", "forward"), "s": ("move", "back"), "a": ("move", "left"), "d": ("move", "right"),
                    "r": ("move", "up"), "f": ("move", "down"), "q": ("turn", "ccw"), "e": ("turn", "cw")}
        if key in bindings:
            self.press_move(*bindings[key])
        elif key == "t":
            self.press("takeoff")
        elif key == "l":
            self.press("land")

    def close(self):
        if self.closing:
            return
        snap = self.controller.snapshot()
        if snap["airborne"] and not messagebox.askokcancel("Close window", "The drone is still airborne. Closing will land it first. Continue?",
                                                           parent=self.root):
            return
        self.closing = True
        self.voice.close()
        self.show_result("Shutting down safely (landing / stopping motors)…", C["amber"])
        self.controller.close()

    # --------------------------------------------------------------- tick
    def tick(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._event(event)
        self._refresh()
        if self.closing and self.controller.closed.is_set():
            self.root.destroy()
            return
        self.root.after(100, self.tick)

    def _event(self, event):
        kind = event[0]
        if kind == "log":
            _, level, text = event
            arrow = {"tx": "▲ ", "rx": "▼ "}.get(level, "")
            self.logbox.configure(state="normal")
            self.logbox.insert("end", datetime.now().strftime("%H:%M:%S  "), "time")
            self.logbox.insert("end", arrow + text + "\n", level)
            self.logbox.see("end")
            if int(self.logbox.index("end-1c").split(".")[0]) > 600:
                self.logbox.delete("1.0", "100.0")
            self.logbox.configure(state="disabled")
            if level in {"ok", "error"} and not text.startswith(("SDK mode entered", "Connecting", "Connection")):
                self.show_result(text, C["green"] if level == "ok" else C["red"])
            elif level == "warn" and ("interrupt" in text or "Cancelled" in text):
                self.show_result(text, C["amber"])
            if level == "warn" and text.startswith(("Guard avoid", "Guard heartbeat")):
                self.show_result(text, C["amber"])
        elif kind == "voice_status":
            _, state, text = event
            if self.voice.enabled.is_set() or state == "error":
                self.voice_state = state
                self.mic_status.configure(text=text, fg=C["red"] if state == "error" else C["muted"])
        elif kind == "speech":
            _, text, generation = event
            if generation == self.voice.generation and self.voice.enabled.is_set():
                self.handle_text(text, "voice")
        elif kind in {"llm", "llm_fail"}:
            _, payload, generation, mode = event
            if generation != self.voice.generation or mode != self.controller.mode:
                self.show_intent(None, "Qwen result expired (stop or mode switch in the meantime) - not executed")
            elif kind == "llm":
                self.execute(payload)
            else:
                self.show_intent(None, payload)

    def _refresh(self):
        s = self.controller.snapshot()
        connected, mode = s["connected"], s["mode"]
        if s["connecting"]:
            self.conn_pill.configure(text="●  Connecting…", fg=C["amber"])
        elif connected:
            self.conn_pill.configure(text="●  " + ("Simulator" if s["simulated"] else "Connected"),
                                     fg=C["green"] if not s["uncertain"] else C["amber"])
        else:
            self.conn_pill.configure(text="●  Disconnected", fg=C["muted"])
        idle_link = not connected and not s["connecting"]
        route = s["route"]
        for r, b in self.route_buttons.items():
            active = r == route
            b.configure(bg=C["cyan"] if active else C["panel2"], fg=C["bg"] if active else C["muted"],
                        cursor="hand2" if idle_link else "arrow")
        self.subtitle.configure(text=(f"PC → ESP32 guard {self.config.esp32_host}:{self.config.esp32_port} → TT (UART)"
                                      if route == "guard" else
                                      f"PC → TT {self.config.drone_ip}:{self.config.command_port}")
                                     + " · Tello SDK 3.0 · offline speech small.en")
        self.b_connect.set_enabled(idle_link)
        self.b_sim.set_enabled(idle_link)
        self.b_disc.set_enabled(connected)

        if s["airborne"]:
            state, color, sub = "Flying", C["cyan"], "say hover / land"
        elif s["motors_on"]:
            state, color, sub = "Motors on", C["green"], "props-off test"
        elif connected:
            state, color, sub = "On ground", C["text"], "SDK ready"
        else:
            state, color, sub = "Offline", C["muted"], ""
        self.t_state[0].configure(text=state, fg=color)
        self.t_state[1].configure(text=sub)
        bat = s["battery"]
        bat_color = C["muted"] if bat is None else C["red"] if bat <= 20 else C["amber"] if bat <= 40 else C["green"]
        self.t_batt[0].configure(text="—" if bat is None else f"{bat}%", fg=bat_color)
        self.t_batt[1].configure(text=f"fly ≥{self.config.takeoff_min_battery} · land ≤{self.config.auto_land_battery}")
        self.batt_bar.delete("all")
        width = self.batt_bar.winfo_width()
        if bat is not None and width > 1:
            self.batt_bar.create_rectangle(0, 0, width * bat / 100, 8, fill=bat_color, width=0)
        self.t_height[0].configure(text="—" if s["height"] is None else f"{s['height']} cm")
        no_tele = "n/a via guard" if route == "guard" else ("UDP 8890" if connected else "")
        self.t_height[1].configure(text="" if s["height"] is not None else no_tele)
        self.t_yaw[1].configure(text="" if s["yaw"] is not None else no_tele)
        self.t_yaw[0].configure(text="—" if s["yaw"] is None else f"{s['yaw']}°")
        age = s["telemetry_age"]
        tele = (f"telemetry live" if age <= self.config.state_max_age_sec else
                "telemetry n/a via guard" if route == "guard" else "no UDP 8890 telemetry")
        busy = s["busy"]
        self.busy_label.configure(text=f"Running: {busy}" if busy else ("Reply stream will be re-aligned before the next command" if s["uncertain"] else "Idle, waiting for a command"),
                                  fg=C["amber"] if busy or s["uncertain"] else C["muted"])
        info = s["info"]
        speed = f"speed {s['speed']} cm/s" if s["speed"] else "speed: set on takeoff"
        self.info_label.configure(text=f"{speed} · {tele}")
        self._draw_radar(s)

        # buttons
        fl, air = connected and mode == FLIGHT, s["airborne"]
        self.b_takeoff.set_enabled(fl and not air and not busy)
        self.b_land.set_enabled(fl)
        self.b_hover.set_enabled(fl)
        self.b_emergency.set_enabled(fl and (air or bool(busy)))
        self.b_emergency.set_color(C["red"] if fl and air else "#7f1d1d")
        for b in self.move_buttons:
            b.set_enabled(fl and air and not busy)
        ts = connected and mode == TEST
        self.b_motor_on.set_enabled(ts and not s["motors_on"] and not busy)
        self.b_motor_off.set_enabled(ts)
        for b in self.read_buttons:
            b.set_enabled(connected and not busy)
        lock = s["airborne"] or s["motors_on"]
        for m, (widgets, _i, _b) in self.mode_cards.items():
            widgets[0].configure(cursor="arrow" if lock else "hand2")

        # mic
        self._draw_mic()

    def _draw_radar(self, s):
        c = self.radar
        c.delete("all")
        w, h = max(c.winfo_width(), 200), 112
        cx, cy = w / 2, h / 2
        tof, fresh_link = s["tof"], s["tof_age"] < 1.0
        guard = s["route"] == "guard"
        avoid = s["avoid"]
        if not guard:
            self.b_avoid.set_enabled(False)
            self.b_avoid.configure(text="N/A")
            self.avoid_note.configure(text="Choose 'ESP32 guard' in the top bar. Direct flights have no ToF protection.")
        elif not s["connected"] or not fresh_link:
            self.b_avoid.set_enabled(False)
            self.b_avoid.configure(text="—")
            self.avoid_note.configure(text="Connect to the guard to see the four ToF sensors.")
        else:
            self.b_avoid.set_enabled(True)
            self.b_avoid.set_color(C["green"] if avoid else C["red"])
            self.b_avoid.configure(text="ON" if avoid else "OFF")
            self.avoid_note.configure(text="Moves are checked; the guard stops or backs off near obstacles."
                                      if avoid else
                                      "OFF: commands are relayed without ToF checks. Click to turn it back on.")
        # drone body
        c.create_rectangle(cx - 13, cy - 13, cx + 13, cy + 13, outline=C["muted"], width=2)
        c.create_polygon(cx, cy - 9, cx - 6, cy + 5, cx + 6, cy + 5, fill=C["cyan"] if guard else C["dim"], outline="")
        rays = (("f", 0, -1, h / 2 - 16), ("b", 0, 1, h / 2 - 16), ("l", -1, 0, w / 2 - 50), ("r", 1, 0, w / 2 - 50))
        for i, (key, dx, dy, length) in enumerate(rays):
            mm = tof.get(key) if guard and fresh_link else None
            ok = guard and fresh_link and tof.get("fresh", [False] * 4)[i]
            if mm is None or not ok:
                color, frac, label = C["dim"], 1.0, "—"
            else:
                color = (C["red"] if mm < 400 else C["amber"] if mm < 1000 else C["green"])
                frac = min(1.0, mm / 2000)
                label = "≥2 m" if mm >= 2000 else f"{mm / 1000:.2f} m"
            x0, y0 = cx + dx * 16, cy + dy * 16
            x1, y1 = cx + dx * (16 + (length - 16) * frac), cy + dy * (16 + (length - 16) * frac)
            c.create_line(x0, y0, x1, y1, fill=color, width=3, dash=() if label != "—" else (2, 3))
            if label != "—":
                c.create_line(x1 - dy * 10, y1 - dx * 10, x1 + dy * 10, y1 + dx * 10, fill=color, width=3)
            tx = x1 + dx * 26 if dx else cx + 34
            ty = y1 if dx else (y1 + dy * 2)
            c.create_text(tx, ty, text=label, fill=color, font=(self.f_ui, 9, "bold"))
        if guard and fresh_link and avoid is False:
            c.create_text(cx, h - 8, text="avoidance OFF", fill=C["red"], font=(self.f_ui, 9, "bold"))

    def _draw_mic(self):
        on = self.voice.enabled.is_set()
        state = self.voice_state if on else ("error" if self.voice_state == "error" else "off")
        color = {"listening": C["green"], "recognizing": C["violet"], "calibrating": C["amber"],
                 "loading": C["amber"], "starting": C["amber"], "error": C["red"]}.get(state, C["dim"])
        titles = {"listening": "Listening", "recognizing": "Recognising", "calibrating": "Calibrating", "loading": "Loading model",
                  "starting": "Starting", "error": "Voice error", "off": "Microphone off"}
        self.mic_title.configure(text=titles.get(state, "Microphone on"), fg=color if on else C["text"])
        self.pulse = (self.pulse + 1) % 20
        m = self.mic
        m.delete("all")
        if on and state == "listening":
            r = 40 + 6 * abs(10 - self.pulse) / 10
            m.create_oval(48 - r, 48 - r, 48 + r, 48 + r, outline=color, width=2)
        m.create_oval(10, 10, 86, 86, fill=color if on else C["panel2"], outline="")
        fg = C["bg"] if on else C["muted"]
        m.create_rectangle(41, 26, 55, 52, fill=fg, outline="")
        m.create_oval(41, 20, 55, 34, fill=fg, outline="")
        m.create_oval(41, 45, 55, 59, fill=fg, outline="")
        m.create_arc(33, 33, 63, 63, start=200, extent=140, style="arc", outline=fg, width=3)
        m.create_line(48, 63, 48, 72, fill=fg, width=3)
        m.create_line(40, 72, 56, 72, fill=fg, width=3)
        if not on:
            m.create_line(30, 30, 66, 66, fill=C["red"], width=3)


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
