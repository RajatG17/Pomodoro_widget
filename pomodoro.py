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

STATE_FILE = os.path.join(os.path.expanduser("~"), ".pomodoro_state.json")
DEFAULT_GEOMETRY = "290x300+120+120"
MIN_WINDOW_WIDTH = 290
MIN_WINDOW_HEIGHT = 300

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2


class PomodoroApp:
    def __init__(self, root):
        self.root = root
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
        if self.remaining <= 0:
            self.remaining = self.total
        self.running = False
        self.after_id = None
        self.drag_x = 0
        self.drag_y = 0
        self.completed = False
        self.volume = self._state.get("volume", 70)
        self.muted = self._state.get("muted", False)
        self._opaque_after = None

        self._build_ui()

        # apply persisted topmost / geometry
        topmost = self._state.get("topmost", True)
        self.root.attributes("-topmost", topmost)
        self._last_geometry = (self._validated_geometry(self._state.get("geometry"))
                               or self._default_geometry())
        self.root.geometry(self._last_geometry)
        self.pin_btn.config(text="Unpin" if topmost else "Pin")
        self.vol_scale.set(self.volume)
        self.mute_var.set(self.muted)
        self._apply_alpha()

        self._update_display()

        # keyboard shortcuts: +/- 10 minutes
        self.root.bind("<space>", lambda e: self.toggle())
        self.root.bind("<r>", lambda e: self.reset())
        self.root.bind("<R>", lambda e: self.reset())
        self.root.bind("<plus>", lambda e: self.add_time(600))
        self.root.bind("<KP_Add>", lambda e: self.add_time(600))
        self.root.bind("<minus>", lambda e: self.add_time(-600))
        self.root.bind("<KP_Subtract>", lambda e: self.add_time(-600))

        # translucency on click
        self.root.bind("<Button-1>", self._on_click)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- state persistence ----------
    def _screen_rects(self):
        """Enumerate monitors so disconnected displays cannot hide the widget."""
        rects = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )

        def collect(monitor, dc, rect, data):
            r = rect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return True

        if ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(collect), 0):
            return rects
        return [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]

    def _validated_geometry(self, value):
        if not isinstance(value, str):
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
        if any(left <= x and top <= y and x + width <= right and y + height <= bottom
               for left, top, right, bottom in self._screen_rects()):
            return value
        return None

    def _default_geometry(self):
        # Keep the whole window visible, even on smaller primary displays.
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        x = max(0, min(120, width - MIN_WINDOW_WIDTH))
        y = max(0, min(120, height - MIN_WINDOW_HEIGHT))
        return f"{MIN_WINDOW_WIDTH}x{MIN_WINDOW_HEIGHT}+{x}+{y}"

    @staticmethod
    def _load_state():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
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
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _on_close(self):
        self._save_state()
        self.root.destroy()

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
        for txt, cmd in (("⏮ Prev", self._media_prev),
                         ("⏯ Play/Pause", self._media_playpause),
                         ("⏭ Next", self._media_next),
                         ("⏹ Stop", self._media_stop)):
            tk.Button(mf, text=txt, command=cmd, bg="#45475a", fg="#cdd6f4",
                     bd=0, font=("Segoe UI", 9), width=12).pack(side="left", padx=2)

        openf = tk.Frame(music_tab, bg="#1e1e2e")
        openf.pack(pady=8, fill="x", padx=8)
        tk.Label(openf, text="Open Spotify URI/URL:", fg="#a6adc8",
                bg="#1e1e2e", font=("Segoe UI", 9)).pack(anchor="w")
        self.uri_var = tk.StringVar()
        tk.Entry(openf, textvariable=self.uri_var, bg="#45475a", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0, font=("Segoe UI", 9)).pack(fill="x", pady=2)
        tk.Button(openf, text="Open in Spotify", command=self._open_spotify,
                 bg="#1db954", fg="#ffffff", bd=0, font=("Segoe UI", 9, "bold")).pack(pady=2)

        tk.Button(music_tab, text="Launch Spotify app", command=self._launch_spotify,
                 bg="#45475a", fg="#cdd6f4", bd=0, font=("Segoe UI", 9)).pack(pady=4)

        tk.Label(music_tab, text="Media keys control the active player (Spotify).",
                fg="#6c7086", bg="#1e1e2e", font=("Segoe UI", 8)).pack(pady=(8, 4))

    # ---------- drag ----------
    def start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def do_drag(self, event):
        x = self.root.winfo_x() + event.x - self.drag_x
        y = self.root.winfo_y() + event.y - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def _on_click(self, event=None):
        self.root.attributes("-alpha", 1.0)
        if self._opaque_after:
            try:
                self.root.after_cancel(self._opaque_after)
            except Exception:
                pass
        if bool(self.root.attributes("-topmost")):
            self._opaque_after = self.root.after(2000, self._revert_alpha)

    def _revert_alpha(self):
        if bool(self.root.attributes("-topmost")):
            self.root.attributes("-alpha", 0.8)

    def _apply_alpha(self):
        if bool(self.root.attributes("-topmost")):
            self.root.attributes("-alpha", 0.8)
        else:
            self.root.attributes("-alpha", 1.0)

    def toggle_topmost(self):
        cur = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not cur)
        self.pin_btn.config(text="Unpin" if (not cur) else "Pin")
        self.status_label.config(text="On top" if (not cur) else "Not on top")
        self._apply_alpha()
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
        if not self.running:
            self.status_label.config(text="Paused" if self.remaining < self.total else "Ready")

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
        new_remaining = self.remaining + delta
        if new_remaining < 0:
            new_remaining = 0
        if delta > 0:
            self.total += delta
        else:
            if self.total + delta >= new_remaining and self.total + delta > 0:
                self.total += delta
        self.remaining = new_remaining
        if self.total < self.remaining:
            self.total = self.remaining
        if self.total <= 0:
            self.total = 1
        label = f"{delta//60:+d}m" if delta % 60 == 0 else f"{delta:+d}s"
        self.status_label.config(text=label)
        self._update_display()
        self._save_state()

    def set_duration(self, secs):
        if self.running:
            self.status_label.config(text="Stop first")
            return
        self.total = secs
        self.remaining = secs
        self.completed = False
        self.status_label.config(text=f"Set {secs//60}m")
        self._update_display()
        self._save_state()

    # ---------- volume ----------
    def _on_volume(self, val):
        self.volume = int(float(val))
        self.muted = self.volume <= 0
        self.mute_var.set(self.muted)
        self._save_state()

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


def main():
    root = tk.Tk()
    PomodoroApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
