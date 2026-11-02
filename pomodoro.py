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


class PomodoroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Pomodoro")
        self.root.overrideredirect(True)          # borderless widget
        self.root.attributes("-topmost", True)    # stay on top of all windows
        self.root.geometry("290x260+120+120")
        self.root.configure(bg="#1e1e2e")
        self.root.resizable(False, False)

        self.total = 25 * 60
        self.remaining = self.total
        self.running = False
        self.after_id = None
        self.drag_x = 0
        self.drag_y = 0
        self.completed = False
        self.volume = 70   # 0-100
        self.muted = False

        self._build_ui()
        self._update_display()

        # keyboard shortcuts
        self.root.bind("<space>", lambda e: self.toggle())
        self.root.bind("<r>", lambda e: self.reset())
        self.root.bind("<R>", lambda e: self.reset())
        self.root.bind("<plus>", lambda e: self.add_time(60))
        self.root.bind("<KP_Add>", lambda e: self.add_time(60))
        self.root.bind("<minus>", lambda e: self.add_time(-60))
        self.root.bind("<KP_Subtract>", lambda e: self.add_time(-60))

    # ---------- UI ----------
    def _build_ui(self):
        # header (draggable)
        header = tk.Frame(self.root, bg="#313244", cursor="fleur")
        header.pack(fill="x")
        title = tk.Label(header, text="Pomodoro", fg="#cdd6f4", bg="#313244",
                        font=("Segoe UI", 10, "bold"))
        title.pack(side="left", padx=8, pady=4)
        close_btn = tk.Button(header, text="X", command=self.root.destroy,
                             bg="#313244", fg="#cdd6f4", bd=0,
                             font=("Segoe UI", 10), activebackground="#f38ba8",
                             activeforeground="#1e1e2e", width=3)
        close_btn.pack(side="right")
        self.pin_btn = tk.Button(header, text="Pin", command=self.toggle_topmost,
                                bg="#313244", fg="#cdd6f4", bd=0,
                                font=("Segoe UI", 9), width=4)
        self.pin_btn.pack(side="right")
        for w in (header, title):
            w.bind("<Button-1>", self.start_drag)
            w.bind("<B1-Motion>", self.do_drag)

        # time display
        self.time_label = tk.Label(self.root, text="25:00", fg="#cdd6f4",
                                  bg="#1e1e2e", font=("Consolas", 42, "bold"))
        self.time_label.pack(pady=(10, 2))

        # status line
        self.status_label = tk.Label(self.root, text="Ready", fg="#a6adc8",
                                    bg="#1e1e2e", font=("Segoe UI", 9))
        self.status_label.pack()

        # progress bar
        self.progress = ttk.Progressbar(self.root, length=250, mode="determinate")
        self.progress.pack(pady=4)

        # main controls
        ctrl = tk.Frame(self.root, bg="#1e1e2e")
        ctrl.pack(pady=4)
        self.start_btn = tk.Button(ctrl, text="Start", command=self.toggle, width=9,
                                  bg="#a6e3a1", fg="#1e1e2e",
                                  font=("Segoe UI", 10, "bold"), bd=0)
        self.start_btn.pack(side="left", padx=4)
        tk.Button(ctrl, text="Reset", command=self.reset, width=9,
                 bg="#fab387", fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
                 bd=0).pack(side="left", padx=4)

        # add time row
        addf = tk.Frame(self.root, bg="#1e1e2e")
        addf.pack(pady=2)
        tk.Label(addf, text="Extend:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left", padx=2)
        for m in (1, 5, 10):
            tk.Button(addf, text=f"+{m}m",
                     command=lambda m=m: self.add_time(m * 60),
                     bg="#45475a", fg="#cdd6f4", bd=0,
                     font=("Segoe UI", 9), width=4).pack(side="left", padx=2)

        # presets row
        presf = tk.Frame(self.root, bg="#1e1e2e")
        presf.pack(pady=2)
        tk.Label(presf, text="Set:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left", padx=2)
        for m in (25, 15, 5):
            tk.Button(presf, text=f"{m}m",
                     command=lambda m=m: self.set_duration(m * 60),
                     bg="#45475a", fg="#cdd6f4", bd=0,
                     font=("Segoe UI", 9), width=4).pack(side="left", padx=2)

        # volume row
        volf = tk.Frame(self.root, bg="#1e1e2e")
        volf.pack(pady=2, fill="x", padx=8)
        tk.Label(volf, text="Vol:", fg="#a6adc8", bg="#1e1e2e",
                font=("Segoe UI", 9)).pack(side="left")
        self.vol_scale = tk.Scale(volf, from_=0, to=100, orient="horizontal",
                                 length=150, bg="#1e1e2e", fg="#cdd6f4",
                                 troughcolor="#45475a", highlightthickness=0,
                                 showvalue=False, command=self._on_volume)
        self.vol_scale.set(self.volume)
        self.vol_scale.pack(side="left", padx=4)
        self.mute_var = tk.BooleanVar(value=False)
        tk.Checkbutton(volf, text="Mute", variable=self.mute_var,
                      command=self._on_mute, bg="#1e1e2e", fg="#cdd6f4",
                      selectcolor="#45475a", activebackground="#1e1e2e",
                      font=("Segoe UI", 9)).pack(side="left")

    # ---------- drag ----------
    def start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def do_drag(self, event):
        x = self.root.winfo_x() + event.x - self.drag_x
        y = self.root.winfo_y() + event.y - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def toggle_topmost(self):
        cur = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not cur)
        self.pin_btn.config(text="Unpin" if (not cur) else "Pin")
        self.status_label.config(text="On top" if (not cur) else "Not on top")

    def _on_volume(self, val):
        self.volume = int(float(val))
        self.muted = self.volume <= 0
        self.mute_var.set(self.muted)

    def _on_mute(self):
        self.muted = bool(self.mute_var.get())
        if self.muted:
            self.volume = 0
            self.vol_scale.set(0)
        else:
            if self.volume <= 0:
                self.volume = 70
            self.vol_scale.set(self.volume)

    # ---------- sound ----------
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
        # WAV header
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

    def add_time(self, delta):
        new_remaining = self.remaining + delta
        if new_remaining < 0:
            new_remaining = 0
        if delta > 0:
            # extend both remaining and total so progress stays consistent
            self.total += delta
        else:
            # removing time: keep total >= remaining
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

    def set_duration(self, secs):
        if self.running:
            self.status_label.config(text="Stop first")
            return
        self.total = secs
        self.remaining = secs
        self.completed = False
        self.status_label.config(text=f"Set {secs//60}m")
        self._update_display()

    def complete(self):
        self.completed = True
        self.status_label.config(text="Done!")
        threading.Thread(target=self._play_sequence, daemon=True).start()
        self.flash(0)

    def flash(self, n):
        if n >= 8:
            self.root.configure(bg="#1e1e2e")
            self.time_label.configure(bg="#1e1e2e")
            return
        color = "#f38ba8" if n % 2 == 0 else "#1e1e2e"
        self.root.configure(bg=color)
        self.time_label.configure(bg=color)
        self.root.after(180, lambda: self.flash(n + 1))


def main():
    root = tk.Tk()
    PomodoroApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()