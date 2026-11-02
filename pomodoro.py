"""Pomodoro timer widget for Windows.

Stays on top of all windows, supports extending the timer duration.
Run with:  python pomodoro.py
"""
import tkinter as tk
from tkinter import ttk
import winsound
import struct
import math
import threading
import json
import os
import ctypes
from ctypes import wintypes
import re
import webbrowser
import argparse
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from windows_integration import GlobalHotkeys, SingleInstance, enable_taskbar

STATE_FILE = os.path.join(os.path.expanduser("~"), ".pomodoro_state.json")
DEFAULT_GEOMETRY = "290x300+120+120"
MIN_WINDOW_WIDTH = 290
MIN_WINDOW_HEIGHT = 300
MAX_SAVED_DURATION = 365 * 24 * 60 * 60

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2


def backup_saved_state():
    """Move settings to a uniquely named sibling backup before a manual reset."""
    state_file = Path(STATE_FILE)
    if not state_file.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = state_file.with_name(f"{state_file.name}.backup-{stamp}")
    state_file.replace(backup)
    return backup


class PomodoroApp:
    def __init__(self, root):
        self.root = root
        was_withdrawn = self.root.state() == "withdrawn"
        self.root.withdraw()  # Measure/layout before showing a half-built window.
        self._initializing = True
        self._minimum_width = MIN_WINDOW_WIDTH
        self._minimum_height = MIN_WINDOW_HEIGHT
        self.root.title("Pomodoro")
        self.root.overrideredirect(True)          # borderless widget
        self.root.attributes("-topmost", True)    # stay on top of all windows
        self._last_geometry = DEFAULT_GEOMETRY
        self.root.geometry(self._last_geometry)
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.root.configure(bg="#1e1e2e")
        self.root.resizable(False, False)

        self._state = self._load_state()

        self.total = self._state.get("total", 25 * 60)
        self.remaining = self._state.get("remaining", self.total)
        self.running = False
        self.after_id = None
        self.drag_x = 0
        self.drag_y = 0
        self.completed = self.remaining == 0
        self.volume = self._state.get("volume", 70)
        self.muted = self._state.get("muted", False)
        self._opaque_after = None
        self._state_after = None
        self._hotkeys = None
        self._desktop_presence = False
        self._taskbar_after = None

        self._build_ui()
        self.root.update_idletasks()
        self._minimum_width = max(MIN_WINDOW_WIDTH, self.root.winfo_reqwidth())
        self._minimum_height = max(MIN_WINDOW_HEIGHT, self.root.winfo_reqheight())

        # apply persisted topmost / geometry
        topmost = self._state.get("topmost", True)
        self.root.attributes("-topmost", topmost)
        self._set_window_geometry(self._fit_geometry(self._state.get("geometry")))
        self.pin_btn.config(text="Unpin" if topmost else "Pin")
        self.vol_scale.set(self.volume)
        self.mute_var.set(self.muted)
        # Do not start faded: give users a chance to find the widget first.
        self.root.attributes("-alpha", 1.0)

        self._update_display()
        self.status_label.config(text=("Done!" if self.completed else
                                       "Paused" if self.remaining < self.total else "Ready"))

        self._bind_local_shortcuts()

        # translucency on click
        self.root.bind("<Button-1>", self._on_click)
        self.root.bind("<FocusIn>", self._on_click, add="+")
        self.root.bind("<FocusOut>", self._schedule_fade, add="+")
        self.root.bind("<Map>", self._on_map, add="+")
        self.root.bind("<Configure>", self._queue_taskbar_refresh, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._initializing = False
        if not was_withdrawn:
            self.root.deiconify()

    # ---------- state persistence ----------
    def _screen_rects(self):
        """Return monitor work areas, excluding the taskbar and docked toolbars."""
        rects = []

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        get_info = ctypes.windll.user32.GetMonitorInfoW
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )

        def collect(monitor, dc, rect, data):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            r = info.rcWork if get_info(monitor, ctypes.byref(info)) else rect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return True

        if (ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
                and rects):
            return rects
        return [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]

    def _geometry_parts(self, value):
        if not isinstance(value, str) or len(value) > 100:
            return None
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", value)
        if not match:
            return None
        width, height = map(int, match.group(1, 2))
        if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
            return None
        x_text, y_text = match.group(3, 4)
        x, y = int(x_text), int(y_text)
        # In Tk geometry, negative offsets are measured from the right/bottom.
        if x_text.startswith("-"):
            x += self.root.winfo_screenwidth() - width
        if y_text.startswith("-"):
            y += self.root.winfo_screenheight() - height
        return width, height, x, y

    def _format_geometry(self, width, height, x, y):
        # Encode physical negative-monitor coordinates using Tk's edge offsets.
        x_offset = x if x >= 0 else x - self.root.winfo_screenwidth() + width
        y_offset = y if y >= 0 else y - self.root.winfo_screenheight() + height
        return f"{width}x{height}{x_offset:+d}{y_offset:+d}"

    def _validated_geometry(self, value):
        parts = self._geometry_parts(value)
        if parts is None:
            return None
        width, height, x, y = parts
        if width < self._minimum_width or height < self._minimum_height:
            return None
        if any(left <= x and top <= y and x + width <= right and y + height <= bottom
               for left, top, right, bottom in self._screen_rects()):
            return value
        return None

    def _default_geometry(self):
        return self._fit_geometry(DEFAULT_GEOMETRY)

    def _fit_geometry(self, value):
        """Fit contents on the nearest usable monitor, preserving position if possible."""
        parts = self._geometry_parts(value) or self._geometry_parts(DEFAULT_GEOMETRY)
        width, height, x, y = parts
        areas = self._screen_rects()
        left, top, right, bottom = min(
            areas, key=lambda r: max(r[0] - x, 0, x - r[2]) ** 2
            + max(r[1] - y, 0, y - r[3]) ** 2,
        )
        width = min(max(width, self._minimum_width), right - left)
        height = min(max(height, self._minimum_height), bottom - top)
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        return self._format_geometry(width, height, x, y)

    def _set_window_geometry(self, geometry):
        # On very small displays, prioritize keeping the header/close button reachable.
        width, height = map(int, re.match(r"(\d+)x(\d+)", geometry).groups())
        self.root.minsize(min(width, self._minimum_width), min(height, self._minimum_height))
        self.root.geometry(geometry)
        self._last_geometry = geometry

    @staticmethod
    def _load_state():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError, UnicodeError):
            return {}
        if not isinstance(state, dict):
            return {}

        # JSON can be valid while still holding values that break Tk or timer
        # arithmetic. Repair individual fields without discarding valid ones.
        total = state.get("total", 1500)
        if type(total) is not int or not 1 <= total <= MAX_SAVED_DURATION:
            total = 1500
        remaining = state.get("remaining", total)
        if type(remaining) is not int:
            remaining = total
        volume = state.get("volume", 70)
        if type(volume) is not int:
            volume = 70
        return {
            "total": total,
            "remaining": max(0, min(total, remaining)),
            "volume": max(0, min(100, volume)),
            "muted": state.get("muted") if type(state.get("muted")) is bool else False,
            "topmost": state.get("topmost") if type(state.get("topmost")) is bool else True,
            "geometry": state.get("geometry"),
        }

    def _save_state(self):
        if self._initializing:
            return
        self._cancel_state_save()
        # Before mapping (or while withdrawn), Tk can report a 1x1 window.
        # Preserve the last known good size/position instead of saving that.
        if self.root.winfo_ismapped():
            geometry = self._validated_geometry(self.root.geometry())
            if geometry:
                self._last_geometry = geometry
        data = {
            "total": self.total,
            "remaining": self.remaining,
            "volume": self.volume,
            "muted": self.muted,
            "topmost": bool(self.root.attributes("-topmost")),
            "geometry": self._last_geometry,
        }
        temporary = None
        try:
            serialized = json.dumps(data)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                             dir=Path(STATE_FILE).parent,
                                             prefix=".pomodoro-", suffix=".tmp") as f:
                temporary = Path(f.name)
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, STATE_FILE)
            return True
        except (OSError, TypeError, ValueError):
            logging.getLogger(__name__).warning("Could not save Pomodoro settings", exc_info=True)
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning("Could not remove temporary settings file")

    def _cancel_state_save(self):
        if self._state_after:
            self.root.after_cancel(self._state_after)
            self._state_after = None

    def _queue_state_save(self):
        if not self._initializing:
            self._cancel_state_save()
            self._state_after = self.root.after(250, self._flush_state_save)

    def _flush_state_save(self):
        self._state_after = None
        self._save_state()

    def _on_close(self):
        self.stop_global_hotkeys()
        if self._taskbar_after is not None:
            self.root.after_cancel(self._taskbar_after)
            self._taskbar_after = None
        self._cancel_fade()
        self._save_state()
        self.root.destroy()

    def stop_global_hotkeys(self):
        if self._hotkeys is not None:
            self._hotkeys.close()
            self._hotkeys = None

    def enable_desktop_presence(self):
        self._desktop_presence = True
        was_visible = self.root.state() != "withdrawn"
        self.root.withdraw()
        self.root.update_idletasks()
        try:
            enable_taskbar(self.root)
        except OSError:
            logging.getLogger(__name__).warning("Taskbar integration unavailable", exc_info=True)
            self.status_label.config(text="Taskbar integration unavailable")
        if was_visible:
            self.root.deiconify()

    def _queue_taskbar_refresh(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self._desktop_presence and self._taskbar_after is None:
            self._taskbar_after = self.root.after_idle(self._refresh_taskbar)

    def _refresh_taskbar(self):
        self._taskbar_after = None
        try:
            enable_taskbar(self.root)
        except OSError:
            logging.getLogger(__name__).warning("Taskbar integration unavailable", exc_info=True)

    def _set_alpha(self, alpha):
        self.root.attributes("-alpha", alpha)
        # Tk rewrites extended styles when changing opacity. Restore shell
        # visibility after its pending map/configure/attribute updates finish.
        self._queue_taskbar_refresh()

    def show_window(self):
        self._set_window_geometry(self._fit_geometry(self.root.geometry()))
        self.root.deiconify()
        self.root.lift()
        # Explicit relaunch/show requests may take focus; startup still does not.
        self.root.focus_force()
        self._on_click()

    def _bind_local_shortcuts(self):
        tag = f"PomodoroShortcuts{id(self)}"
        bindings = {"<space>": self.toggle, "<r>": self.reset, "<R>": self.reset,
                    "<plus>": lambda: self.add_time(600),
                    "<KP_Add>": lambda: self.add_time(600),
                    "<minus>": lambda: self.add_time(-600),
                    "<KP_Subtract>": lambda: self.add_time(-600),
                    "<Escape>": self._recover_window}
        for sequence, action in bindings.items():
            self.root.bind_class(tag, sequence,
                                 lambda event, action=action: self._local_shortcut(event, action))
        # Suppress button Space-release activation after handling its keypress.
        self.root.bind_class(tag, "<KeyRelease-space>",
                             lambda event: self._local_shortcut(event, lambda: None))

        def attach(widget):
            widget.bindtags((tag, *widget.bindtags()))
            for child in widget.winfo_children():
                attach(child)
        attach(self.root)

    def _local_shortcut(self, event, action):
        if isinstance(event.widget, (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Combobox)):
            return None
        if event.state & (0x4 | 0x8 | 0x20000):  # Leave Ctrl/Alt combinations alone.
            return None
        action()
        return "break"

    def start_global_hotkeys(self):
        self._hotkeys = GlobalHotkeys(self.root, {
            "toggle": self.toggle, "reset": self.reset,
            "add": lambda: self.add_time(600), "subtract": lambda: self.add_time(-600),
            "show": self._recover_window,
        })
        try:
            self._hotkeys.start()
            if self._hotkeys.failed_keys:
                self.status_label.config(text="Some global shortcuts are in use")
                logging.getLogger(__name__).warning("Shortcut conflicts: %s", self._hotkeys.failed_keys)
        except RuntimeError:
            self.status_label.config(text="Global shortcuts unavailable")
            self._hotkeys = None

    # ---------- UI ----------
    def _build_ui(self):
        # header (draggable)
        header = tk.Frame(self.root, bg="#313244", cursor="fleur")
        header.pack(fill="x")
        title = tk.Label(header, text="Pomodoro", fg="#cdd6f4", bg="#313244",
                        font=("Segoe UI", 10, "bold"))
        title.pack(side="left", padx=8, pady=4)
        close_btn = tk.Button(header, text="X", command=self._on_close,
                             bg="#313244", fg="#cdd6f4", bd=0,
                             font=("Segoe UI", 10), activebackground="#f38ba8",
                             activeforeground="#1e1e2e", width=3)
        close_btn.pack(side="right")
        self.pin_btn = tk.Button(header, text="Pin", command=self.toggle_topmost,
                                bg="#313244", fg="#cdd6f4", bd=0,
                                font=("Segoe UI", 9), width=5)
        self.pin_btn.pack(side="right")
        for w in (header, title):
            w.bind("<Button-1>", self.start_drag)
            w.bind("<B1-Motion>", self.do_drag)
            w.bind("<Button-1>", self._on_click, add="+")

        # tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        timer_tab = tk.Frame(self.notebook, bg="#1e1e2e")
        music_tab = tk.Frame(self.notebook, bg="#1e1e2e")
        self.notebook.add(timer_tab, text="Timer")
        self.notebook.add(music_tab, text="Music")

        # ---- Timer tab ----
        self.time_label = tk.Label(timer_tab, text="25:00", fg="#cdd6f4",
                                  bg="#1e1e2e", font=("Consolas", 42, "bold"))
        self.time_label.pack(pady=(10, 2))

        self.status_label = tk.Label(timer_tab, text="Ready", fg="#a6adc8",
                                    bg="#1e1e2e", font=("Segoe UI", 9))
        self.status_label.pack()

        self.progress = ttk.Progressbar(timer_tab, length=250, mode="determinate")
        self.progress.pack(pady=4)

        ctrl = tk.Frame(timer_tab, bg="#1e1e2e")
        ctrl.pack(pady=4)
        self.start_btn = tk.Button(ctrl, text="Start", command=self.toggle, width=9,
                                  bg="#a6e3a1", fg="#1e1e2e",
                                  font=("Segoe UI", 10, "bold"), bd=0)
        self.start_btn.pack(side="left", padx=4)
        tk.Button(ctrl, text="Reset", command=self.reset, width=9,
                 bg="#fab387", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
                 bd=0).pack(side="left", padx=4)

        addf = tk.Frame(timer_tab, bg="#1e1e2e")
        addf.pack(pady=2)
        tk.Label(addf, text="Extend:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left", padx=2)
        for m in (1, 5, 10):
            tk.Button(addf, text=f"+{m}m",
                     command=lambda m=m: self.add_time(m * 60),
                     bg="#45475a", fg="#cdd6f4", bd=0,
                     font=("Segoe UI", 9), width=4).pack(side="left", padx=2)

        presf = tk.Frame(timer_tab, bg="#1e1e2e")
        presf.pack(pady=2)
        tk.Label(presf, text="Set:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left", padx=2)
        for m in (25, 15, 5):
            tk.Button(presf, text=f"{m}m",
                     command=lambda m=m: self.set_duration(m * 60),
                     bg="#45475a", fg="#cdd6f4", bd=0,
                     font=("Segoe UI", 9), width=4).pack(side="left", padx=2)

        volf = tk.Frame(timer_tab, bg="#1e1e2e")
        volf.pack(pady=2, fill="x", padx=8)
        tk.Label(volf, text="Vol:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left")
        self.vol_scale = tk.Scale(volf, from_=0, to=100, orient="horizontal",
                                 length=150, bg="#1e1e2e", fg="#cdd6f4",
                                 troughcolor="#45475a", highlightthickness=0,
                                 showvalue=False, command=self._on_volume)
        self.vol_scale.set(self.volume)
        self.vol_scale.pack(side="left", padx=4)
        self.mute_var = tk.BooleanVar(value=self.muted)
        tk.Checkbutton(volf, text="Mute", variable=self.mute_var,
                      command=self._on_mute, bg="#1e1e2e", fg="#cdd6f4",
                      selectcolor="#45475a", activebackground="#1e1e2e",
                      font=("Segoe UI", 9)).pack(side="left")

        hint = tk.Label(timer_tab, text="Space=play  R=reset  +/-=10m",
                        fg="#6c7086", bg="#1e1e2e", font=("Segoe UI", 8))
        hint.pack(pady=(4, 6))

        # ---- Music tab ----
        tk.Label(music_tab, text="Spotify / Media", fg="#cdd6f4", bg="#1e1e2e",
                font=("Segoe UI", 11, "bold")).pack(pady=(10, 6))

        mf = tk.Frame(music_tab, bg="#1e1e2e")
        mf.pack(pady=4)
        media_controls = (("⏮ Prev", self._media_prev),
                          ("⏯ Play/Pause", self._media_playpause),
                          ("⏭ Next", self._media_next),
                          ("⏹ Stop", self._media_stop))
        for index, (txt, cmd) in enumerate(media_controls):
            tk.Button(mf, text=txt, command=cmd, bg="#45475a", fg="#cdd6f4",
                     bd=0, font=("Segoe UI", 9), width=12).grid(
                         row=index // 2, column=index % 2, padx=2, pady=2, sticky="ew")

        openf = tk.Frame(music_tab, bg="#1e1e2e")
        openf.pack(pady=8, fill="x", padx=8)
        tk.Label(openf, text="Open Spotify URI/URL:", fg="#a6adc8",
                bg="#1e1e2e", font=("Segoe UI", 9)).pack(anchor="w")
        self.uri_var = tk.StringVar()
        self.uri_entry = tk.Entry(openf, textvariable=self.uri_var, bg="#45475a", fg="#cdd6f4",
                                  insertbackground="#cdd6f4", bd=0, font=("Segoe UI", 9))
        self.uri_entry.pack(fill="x", pady=2)
        tk.Button(openf, text="Open in Spotify", command=self._open_spotify,
                 bg="#1db954", fg="#ffffff", bd=0, font=("Segoe UI", 9, "bold")).pack(pady=2)

        tk.Button(music_tab, text="Launch Spotify app", command=self._launch_spotify,
                 bg="#45475a", fg="#cdd6f4", bd=0, font=("Segoe UI", 9)).pack(pady=4)

        tk.Label(music_tab, text="Media keys control the active player\n(Spotify or another media app).",
                fg="#6c7086", bg="#1e1e2e", font=("Segoe UI", 8)).pack(pady=(8, 4))

    # ---------- drag ----------
    def start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def do_drag(self, event):
        x = self.root.winfo_x() + event.x - self.drag_x
        y = self.root.winfo_y() + event.y - self.drag_y
        geometry = self._format_geometry(self.root.winfo_width(), self.root.winfo_height(), x, y)
        self._set_window_geometry(self._fit_geometry(geometry))

    def _recover_window(self, event=None):
        self._set_window_geometry(self._default_geometry())
        self.root.update_idletasks()
        self.root.lift()
        self._on_click()
        self._save_state()
        return "break"

    def _on_click(self, event=None):
        self._set_alpha(1.0)
        self._schedule_fade()

    def _on_map(self, event):
        if event.widget is self.root:
            # Raise without forcing keyboard focus away from another app.
            self.root.lift()
            self._set_alpha(1.0)
            self._schedule_fade(delay=3000)

    def _cancel_fade(self):
        if self._opaque_after:
            self.root.after_cancel(self._opaque_after)
            self._opaque_after = None

    def _schedule_fade(self, event=None, delay=2000):
        self._cancel_fade()
        if bool(self.root.attributes("-topmost")):
            self._opaque_after = self.root.after(delay, self._revert_alpha)

    def _has_focus(self):
        focused = self.root.focus_displayof()
        return focused is not None and focused.winfo_toplevel() is self.root

    def _revert_alpha(self):
        self._opaque_after = None
        self._apply_alpha()

    def _apply_alpha(self):
        if bool(self.root.attributes("-topmost")) and not self._has_focus():
            self._set_alpha(0.8)
        else:
            self._set_alpha(1.0)

    def toggle_topmost(self):
        cur = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not cur)
        self.pin_btn.config(text="Unpin" if (not cur) else "Pin")
        self.status_label.config(text="On top" if (not cur) else "Not on top")
        self._cancel_fade()
        self._on_click()
        self._save_state()

    # ---------- logic ----------
    def fmt(self, secs):
        secs = max(0, int(secs))
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        if h:
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _update_display(self):
        self.time_label.config(text=self.fmt(self.remaining))
        if self.total > 0:
            pct = (1 - self.remaining / self.total) * 100
            self.progress["value"] = max(0, min(100, pct))
        else:
            self.progress["value"] = 0

    def tick(self):
        if not self.running:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self.remaining = 0
            self.running = False
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.after_id = None
            self.start_btn.config(text="Start")
            self._update_display()
            self.complete()
            return
        self._update_display()
        self.after_id = self.root.after(1000, self.tick)

    def toggle(self):
        if self.running:
            self.running = False
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.after_id = None
            self.start_btn.config(text="Start")
            self.status_label.config(text="Paused")
        else:
            if self.remaining <= 0:
                self.remaining = self.total
            self.completed = False
            self.running = True
            self.start_btn.config(text="Pause")
            self.status_label.config(text="Running")
            self.after_id = self.root.after(1000, self.tick)
        self._update_display()
        self._save_state()

    def reset(self):
        self.running = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.remaining = self.total
        self.completed = False
        self.start_btn.config(text="Start")
        self.status_label.config(text="Ready")
        self._update_display()
        self.root.configure(bg="#1e1e2e")
        self.time_label.configure(bg="#1e1e2e")
        self._save_state()

    def add_time(self, delta):
        # Adjust only the time actually available to remove/add. This keeps
        # elapsed time (total - remaining) unchanged, except for the 1s floor.
        delta = max(-self.remaining, min(delta, MAX_SAVED_DURATION - self.total))
        if not delta:
            return
        self.remaining += delta
        self.total = max(1, self.total + delta)
        self.completed = self.remaining == 0
        if self.completed:
            self.running = False
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.after_id = None
            self.start_btn.config(text="Start")
            self._update_display()
            self.complete()
            return
        label = f"{delta//60:+d}m" if delta % 60 == 0 else f"{delta:+d}s"
        self.status_label.config(text=label)
        self._update_display()
        self._save_state()

    def set_duration(self, secs):
        if self.running:
            self.status_label.config(text="Stop first")
            return
        self.total = max(1, min(secs, MAX_SAVED_DURATION))
        self.remaining = self.total
        self.completed = False
        self.status_label.config(text=f"Set {secs//60}m")
        self._update_display()
        self._save_state()

    # ---------- volume ----------
    def _on_volume(self, val):
        self.volume = int(float(val))
        self.muted = self.volume <= 0
        self.mute_var.set(self.muted)
        self._queue_state_save()

    def _on_mute(self):
        self.muted = bool(self.mute_var.get())
        if self.muted:
            self.volume = 0
            self.vol_scale.set(0)
        else:
            if self.volume <= 0:
                self.volume = 70
            self.vol_scale.set(self.volume)
        self._save_state()

    def _tone_bytes(self, freq, ms):
        if self.muted or self.volume <= 0:
            return None
        sample_rate = 22050
        n = int(sample_rate * ms / 1000)
        amp = int(32767 * (self.volume / 100.0))
        buf = bytearray()
        for i in range(n):
            val = int(amp * math.sin(2 * math.pi * freq * i / sample_rate))
            buf += struct.pack("<h", val)
        data_size = len(buf)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1, 1,
            sample_rate, sample_rate * 2, 2, 16, b"data", data_size,
        )
        return header + bytes(buf)

    def _play_tone(self, freq, ms):
        data = self._tone_bytes(freq, ms)
        if data:
            try:
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_SYNC)
            except Exception:
                pass

    def _play_sequence(self):
        for freq, ms in ((880, 200), (660, 200), (880, 300)):
            self._play_tone(freq, ms)

    def complete(self):
        self.completed = True
        self.status_label.config(text="Done!")
        threading.Thread(target=self._play_sequence, daemon=True).start()
        self.flash(0)
        self._save_state()

    def flash(self, n):
        if n >= 8:
            self.root.configure(bg="#1e1e2e")
            self.time_label.configure(bg="#1e1e2e")
            return
        color = "#f38ba8" if n % 2 == 0 else "#1e1e2e"
        self.root.configure(bg=color)
        self.time_label.configure(bg=color)
        self.root.after(180, lambda: self.flash(n + 1))

    # ---------- media / spotify ----------
    def _media_key(self, vk):
        try:
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        except Exception:
            pass

    def _media_playpause(self):
        self._media_key(VK_MEDIA_PLAY_PAUSE)

    def _media_next(self):
        self._media_key(VK_MEDIA_NEXT_TRACK)

    def _media_prev(self):
        self._media_key(VK_MEDIA_PREV_TRACK)

    def _media_stop(self):
        self._media_key(VK_MEDIA_STOP)

    def _open_spotify(self):
        uri = self.uri_var.get().strip()
        if not uri:
            return
        try:
            if uri.startswith("spotify:") or uri.startswith("https://open.spotify.com"):
                os.startfile(uri)
            else:
                webbrowser.open(uri)
        except Exception:
            try:
                webbrowser.open(uri)
            except Exception:
                pass

    def _launch_spotify(self):
        try:
            os.startfile("spotify:")
        except Exception:
            try:
                os.startfile("spotify")
            except Exception:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Always-on-top Pomodoro timer for Windows")
    parser.add_argument("--reset-state", action="store_true",
                        help="Back up saved settings and open with defaults")
    parser.add_argument("--no-global-hotkeys", action="store_true",
                        help="Disable system-wide Ctrl+Alt shortcuts")
    args = parser.parse_args(argv)
    try:
        instance = SingleInstance(STATE_FILE)
    except OSError as error:
        parser.exit(1, f"Could not coordinate Pomodoro instances: {error}\n")
    app = None
    try:
        if not instance.primary:
            instance.notify()
            return
        if args.reset_state:
            try:
                backup_saved_state()
            except OSError as error:
                parser.exit(1, f"Could not back up settings; nothing was reset: {error}\n")
        root = tk.Tk()
        root.withdraw()
        app = PomodoroApp(root)
        app.enable_desktop_presence()
        instance.attach(root, app.show_window)
        if not args.no_global_hotkeys:
            app.start_global_hotkeys()
        root.deiconify()
        root.mainloop()
    finally:
        if app is not None:
            app.stop_global_hotkeys()
        instance.close()


if __name__ == "__main__":
    main()
