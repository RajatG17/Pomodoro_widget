"""Compact, dependency-free presentation for the desktop widget."""
import tkinter as tk
from tkinter import ttk

from session_plan import SETTING_LIMITS
from media_controls import clean_text


COLORS = {
    "bg": "#10151c", "surface": "#1a222d", "hover": "#273343",
    "border": "#2b3747", "text": "#edf3fa", "muted": "#9cabbf",
    "accent": "#76e0bf", "accent_dim": "#203c36", "ink": "#10231e",
    "danger": "#ff949e", "break": "#96c3ff", "long": "#c4b1ff",
}


class ModernButton(tk.Button):
    """Keep native button semantics, keyboard focus, and explicit hover feedback."""
    def __init__(self, parent, text, command, *, width=None, size=9, padx=7, pady=4):
        self.base = COLORS["surface"]
        self.foreground = COLORS["text"]
        self.hover = COLORS["hover"]
        self.inside = False
        options = dict(text=text, command=command, font=("Segoe UI", size),
                       bg=self.base, fg=self.foreground, activebackground=self.hover,
                       activeforeground=self.foreground, disabledforeground=COLORS["muted"],
                       bd=0, relief="flat", padx=padx, pady=pady, cursor="hand2",
                       highlightthickness=1, highlightbackground=self.base,
                       highlightcolor=COLORS["accent"], takefocus=True)
        if width is not None:
            options["width"] = width
        super().__init__(parent, **options)
        self.bind("<Enter>", lambda event: self._hover(True))
        self.bind("<Leave>", lambda event: self._hover(False))
        self.bind("<Return>", self._enter)

    def _enter(self, event):
        self.invoke()
        return "break"

    def _hover(self, inside):
        self.inside = inside
        self.paint()

    def recolor(self, background, foreground, hover=None):
        self.base, self.foreground = background, foreground
        self.hover = hover or COLORS["hover"]
        self.paint()

    def paint(self):
        color = self.hover if self.inside and self.cget("state") != "disabled" else self.base
        self.configure(bg=color, fg=self.foreground, activebackground=self.hover,
                       activeforeground=self.foreground, highlightbackground=self.base)


class ThinProgress(tk.Canvas):
    def __init__(self, parent, width, height):
        super().__init__(parent, width=width, height=height, bg=COLORS["surface"],
                         bd=0, highlightthickness=0)
        self.value = 0.0
        self.bar = self.create_rectangle(0, 0, 0, height, fill=COLORS["accent"], outline="")
        self.bind("<Configure>", lambda event: self.draw())

    def __setitem__(self, key, value):
        if key == "value":
            self.value = max(0.0, min(100.0, float(value)))
            self.draw()
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        return self.value if key == "value" else super().__getitem__(key)

    def draw(self):
        self.coords(self.bar, 0, 0, self.winfo_width() * self.value / 100, self.winfo_height())

    def set_color(self, color):
        self.itemconfigure(self.bar, fill=color)


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.job = self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<Unmap>", "<Destroy>"):
            widget.bind(event, self.hide, add="+")

    def schedule(self, event=None):
        self.hide()
        self.job = self.widget.after(550, self.show)

    def show(self):
        self.job = None
        if not self.widget.winfo_viewable():
            return
        text = self.text() if callable(self.text) else self.text
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        label = tk.Label(self.window, text=text, font=("Segoe UI", 8),
                         bg=COLORS["surface"], fg=COLORS["text"], padx=8, pady=5,
                         wraplength=240, justify="left", bd=1, relief="solid")
        label.pack()
        self.window.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        # Stay within the work area of the widget's monitor, including negative coordinates.
        left, top, right, bottom = self.widget.winfo_toplevel()._pomodoro_work_area()
        x = max(left, min(x, right - self.window.winfo_reqwidth()))
        y = max(top, min(y, bottom - self.window.winfo_reqheight()))
        # Tk negative offsets are edge-relative, not physical monitor coordinates.
        if x < 0:
            x -= self.window.winfo_screenwidth() - self.window.winfo_reqwidth()
        if y < 0:
            y -= self.window.winfo_screenheight() - self.window.winfo_reqheight()
        self.window.geometry(f"{x:+d}{y:+d}")
        self.window.deiconify()

    def hide(self, event=None):
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class WidgetUI:
    def __init__(self, app):
        self.app, self.root = app, app.root
        self.scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self.tooltips = []
        self.root.configure(bg=COLORS["bg"], highlightthickness=1,
                            highlightbackground=COLORS["border"])
        self.root._pomodoro_work_area = self.work_area
        self.root.bind("<Destroy>", self._destroyed, add="+")
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.style.configure("Pomodoro.TNotebook", background=COLORS["bg"], borderwidth=0,
                             tabmargins=0, padding=0)
        self.style.layout("Pomodoro.TNotebook", [])
        self.style.layout("Pomodoro.TNotebook.Tab", [])
        self.build_header()
        self.build_navigation()
        app.notebook = ttk.Notebook(self.root, style="Pomodoro.TNotebook", takefocus=False)
        app.notebook.pack(fill="both", expand=True, padx=self.dp(12), pady=(self.dp(7), self.dp(9)))
        self.pages = {}
        for name in ("Timer", "Music", "Sessions"):
            page = self.frame(app.notebook)
            app.notebook.add(page, text=name)
            self.pages[name] = page
        self.build_timer(self.pages["Timer"])
        self.build_music(self.pages["Music"])
        self.build_sessions(self.pages["Sessions"])
        app.notebook.bind("<<NotebookTabChanged>>", lambda event: self.refresh_navigation())
        self.refresh_navigation()

    def dp(self, value):
        return round(value * self.scale)

    def frame(self, parent, surface=False):
        return tk.Frame(parent, bg=COLORS["surface"] if surface else COLORS["bg"], bd=0)

    def label(self, parent, text, size=9, color="muted", **kwargs):
        return tk.Label(parent, text=text, bg=COLORS["bg"], fg=COLORS[color],
                        font=("Segoe UI", size), bd=0, **kwargs)

    def tip(self, widget, text):
        self.tooltips.append(Tooltip(widget, text))

    def button(self, parent, text, command, *, tip=None, **kwargs):
        button = ModernButton(parent, text, command, padx=self.dp(kwargs.pop("padx", 7)),
                              pady=self.dp(kwargs.pop("pady", 4)), **kwargs)
        if tip:
            self.tip(button, tip)
        return button

    def build_header(self):
        header = self.frame(self.root)
        header.pack(fill="x", padx=self.dp(10), pady=(self.dp(7), self.dp(2)))
        logo = tk.Canvas(header, width=self.dp(14), height=self.dp(16),
                         bg=COLORS["bg"], bd=0, highlightthickness=0)
        logo.create_oval(self.dp(2), self.dp(3), self.dp(12), self.dp(13),
                         outline=COLORS["accent"], width=2)
        logo.create_line(self.dp(7), self.dp(5), self.dp(7), self.dp(8), self.dp(10), self.dp(8),
                         fill=COLORS["accent"], width=1)
        logo.pack(side="left", padx=(0, self.dp(5)))
        title = self.label(header, "pomodoro", size=10, color="text")
        title.configure(font=("Segoe UI", 10, "bold"))
        title.pack(side="left")
        close = self.button(header, "×", self.app._on_close, width=2, size=11,
                            padx=2, pady=0, tip="Close widget · settings are saved")
        close.recolor(COLORS["bg"], COLORS["muted"], "#492a36")
        close.pack(side="right")
        self.app.pin_btn = self.button(header, "Unpin", self.app.toggle_topmost,
                                       width=5, size=8, padx=3, pady=2,
                                       tip="Pin / unpin above other windows")
        self.app.pin_btn.pack(side="right", padx=(0, self.dp(3)))
        for widget in (header, logo, title):
            widget.configure(cursor="fleur")
            widget.bind("<Button-1>", self.app.start_drag)
            widget.bind("<B1-Motion>", self.app.do_drag)
            widget.bind("<Button-1>", self.app._on_click, add="+")

    def build_navigation(self):
        navigation = self.frame(self.root, surface=True)
        navigation.pack(fill="x", padx=self.dp(12), pady=(self.dp(5), 0))
        self.navigation = {}
        for column, name in enumerate(("Timer", "Music", "Sessions")):
            navigation.columnconfigure(column, weight=1, uniform="nav")
            button = self.button(navigation, name, lambda name=name: self.app.notebook.select(self.pages[name]),
                                  width=7, size=8, pady=3, padx=3)
            button.grid(row=0, column=column, sticky="ew", padx=self.dp(2), pady=self.dp(2))
            self.navigation[name] = button

    def build_timer(self, page):
        app = self.app
        modes = self.frame(page, surface=True)
        modes.pack(fill="x")
        app.phase_buttons = []
        for column, (text, phase) in enumerate((("Work", "work"), ("Short", "short_break"), ("Long", "long_break"))):
            modes.columnconfigure(column, weight=1, uniform="phase")
            button = self.button(modes, text, lambda phase=phase: app.select_phase(phase), width=6,
                                  size=8, pady=3, padx=3, tip=f"Load {text.lower()} phase · pause first")
            button.grid(row=0, column=column, sticky="ew", padx=self.dp(2), pady=self.dp(2))
            app.phase_buttons.append(button)
        app.phase_label = self.label(page, app._phase_text(), size=8)
        app.phase_label.pack(pady=(self.dp(6), 0))
        app.time_label = tk.Label(page, text=app.fmt(app.remaining), bg=COLORS["bg"], fg=COLORS["text"],
                                  font=("Consolas", 48), bd=0)
        app.time_label.pack()
        app.status_label = self.label(page, "Ready", size=8)
        app.status_label.pack(pady=(0, self.dp(3)))
        self.tip(app.status_label, lambda: app.status_label.cget("text"))
        app.progress = ThinProgress(page, width=self.dp(192), height=self.dp(3))
        app.progress.pack(fill="x", pady=(self.dp(3), self.dp(8)))
        controls = self.frame(page)
        controls.pack(fill="x")
        app.start_btn = self.button(controls, "Start", app.toggle, width=12, size=10,
                                    tip="Space · start / pause; start next phase when finished")
        app.start_btn.pack(side="left", expand=True, fill="x")
        self.reset_btn = self.button(controls, "↻", app.reset, width=2, size=12, padx=4, pady=2,
                                      tip="R · restart this timer, keep session history")
        self.reset_btn.pack(side="left", padx=self.dp(5))
        app.next_phase_btn = self.button(controls, "Next", app.next_phase, width=4, padx=4,
                                         tip="Next phase · skip without counting; stays paused")
        app.next_phase_btn.pack(side="left")
        quick = self.frame(page)
        quick.pack(fill="x", pady=self.dp(7))
        app.preset_menu = tk.Menu(self.root, tearoff=False, bg=COLORS["surface"], fg=COLORS["text"],
                                  activebackground=COLORS["hover"], activeforeground=COLORS["text"],
                                  bd=0, font=("Segoe UI", 9))
        for minutes in (25, 15, 5):
            app.preset_menu.add_command(label=f"Set {minutes} minutes",
                                        command=lambda minutes=minutes: app.set_duration(minutes * 60))
        app.preset_btn = self.button(quick, "Set ▾", self.show_presets, width=5, size=8, padx=3, pady=2,
                                     tip="Presets · set 25, 15, or 5 minutes while paused")
        app.preset_btn.grid(row=0, column=0, sticky="ew", padx=(0, self.dp(3)))
        for column, minutes in enumerate((1, 5, 10), 1):
            self.button(quick, f"+{minutes}m", lambda minutes=minutes: app.add_time(minutes * 60),
                        width=4, size=8, padx=3, pady=2, tip=f"Extend current timer by {minutes} minute(s)").grid(
                            row=0, column=column, sticky="ew", padx=self.dp(2))
        for column in range(4):
            quick.columnconfigure(column, weight=1, uniform="quick")
        footer = self.frame(page)
        footer.pack(fill="x", pady=(self.dp(1), 0))
        app.session_count_label = self.label(footer, f"{app.sessions.completed_sessions} done", size=8)
        app.session_count_label.pack(side="left")
        app.mute_var = tk.BooleanVar(value=app.muted)
        app.mute_button = tk.Checkbutton(footer, text="Mute", variable=app.mute_var, command=app._on_mute,
                                         indicatoron=False, bg=COLORS["bg"], fg=COLORS["muted"],
                                         selectcolor=COLORS["surface"], activebackground=COLORS["hover"],
                                         activeforeground=COLORS["text"], bd=0, highlightthickness=0,
                                         font=("Segoe UI", 8), padx=self.dp(4), cursor="hand2")
        app.mute_button.pack(side="right")
        app.vol_scale = tk.Scale(footer, from_=0, to=100, orient="horizontal", length=self.dp(66),
                                  width=self.dp(3), sliderlength=self.dp(10), showvalue=False,
                                  bg=COLORS["bg"], fg=COLORS["muted"], troughcolor=COLORS["border"],
                                  activebackground=COLORS["accent"], bd=0, highlightthickness=0,
                                  sliderrelief="flat", command=app._on_volume, takefocus=True)
        app.vol_scale.set(app.volume)
        app.vol_scale.pack(side="right", padx=self.dp(5))
        self.label(footer, "Alarm", size=8).pack(side="right")
        self.tip(app.vol_scale, "Completion alarm volume · does not change music volume")

    def build_music(self, page):
        self.label(page, "WINDOWS MEDIA", size=8).pack(anchor="w", pady=(self.dp(4), self.dp(6)))
        self.media_source = self.label(page, "System media keys", size=8, color="accent", width=1, anchor="w")
        self.media_source.pack(fill="x")
        self.media_title = self.label(page, "No media playing", size=12, color="text",
                                       width=1, height=2, wraplength=self.dp(240), anchor="w", justify="left")
        self.media_title.configure(font=("Segoe UI", 12, "bold"))
        self.media_title.pack(fill="x", pady=(self.dp(4), 0))
        self.media_artist = self.label(page, "Start playback in a browser or app.", size=8, width=1, anchor="w")
        self.media_artist.pack(fill="x", pady=(0, self.dp(3)))
        self.tip(self.media_title, lambda: self.app.current_media.title if self.app.current_media else "No Windows media session")
        self.media_title.bind("<Configure>", lambda event: self.refresh_media())
        player = self.frame(page, surface=True)
        player.pack(fill="x", pady=self.dp(8))
        self.media_buttons = {}
        for column, (action, text, command, tip) in enumerate((
                ("previous", "‹", self.app._media_prev, "Previous track in selected player"),
                ("toggle", "Play / pause", self.app._media_playpause, "Play / pause selected player"),
                ("next", "›", self.app._media_next, "Next track in selected player"),
                ("stop", "■", self.app._media_stop, "Stop selected player"))):
            player.columnconfigure(column, weight=3 if column == 1 else 1)
            button = self.button(player, text, command, size=9, padx=4, tip=tip)
            button.grid(row=0, column=column, sticky="ew", padx=self.dp(2), pady=self.dp(3))
            self.media_buttons[action] = button
        actions = self.frame(page)
        actions.pack(fill="x", pady=(self.dp(2), self.dp(6)))
        self.open_player_button = self.button(actions, "Open player", self.app.open_media_player, width=10,
                                               size=8, tip="Reveal the app or browser owning this media session")
        self.open_player_button.recolor(COLORS["accent"], COLORS["ink"], "#9bedcf")
        self.open_player_button.pack(side="left", fill="x", expand=True, padx=(0, self.dp(4)))
        self.player_picker_button = self.button(actions, "Players ▾", self.show_players, width=7, size=8, padx=3,
                                                 tip="Follow playing media automatically, or choose a player")
        self.player_picker_button.pack(side="left", padx=(0, self.dp(4)))
        self.button(actions, "↻", self.app.refresh_media, width=2, size=11, padx=3, pady=2,
                    tip="Refresh Windows media sessions").pack(side="left")
        self.player_menu = tk.Menu(self.root, tearoff=False, bg=COLORS["surface"], fg=COLORS["text"],
                                   activebackground=COLORS["hover"], activeforeground=COLORS["text"],
                                   bd=0, font=("Segoe UI", 9))
        self.media_message = self.label(page, self.app.media_snapshot.message, size=8,
                                         width=1, height=2, wraplength=self.dp(240), anchor="nw", justify="left")
        self.media_message.pack(fill="x", pady=self.dp(3))
        self.refresh_media()

    def show_players(self):
        self.player_menu.delete(0, "end")
        prefix = "✓ " if self.app.media_selection is None else ""
        self.player_menu.add_command(label=prefix + "Follow playing media", command=lambda: self.app.select_media())
        self.player_menu.add_separator()
        for item in self.app.media_snapshot.sessions:
            prefix = "✓ " if item.id == self.app.media_selection else ""
            label = f"{prefix}{item.player} · {item.status} · {item.title or 'Untitled'}"
            self.player_menu.add_command(label=clean_text(label, 85),
                                          command=lambda key=item.id: self.app.select_media(key))
        try:
            self.player_menu.tk_popup(self.player_picker_button.winfo_rootx(),
                                       self.player_picker_button.winfo_rooty() + self.player_picker_button.winfo_height())
        finally:
            self.player_menu.grab_release()

    def _media_text(self, label, text, lines=1):
        text = clean_text(text)
        width = label.winfo_width()
        if width < 20:
            width = self.dp(240)
        label.configure(wraplength=max(1, width - self.dp(3)))
        maximum = width * (1.8 if lines == 2 else 0.95)
        font = label.cget("font")
        if int(self.root.tk.call("font", "measure", font, text)) <= maximum:
            return text
        left, right = 0, len(text)
        while left < right:
            middle = (left + right + 1) // 2
            if int(self.root.tk.call("font", "measure", font, text[:middle] + "…")) <= maximum:
                left = middle
            else:
                right = middle - 1
        return text[:left].rstrip() + "…"

    def refresh_media(self):
        app = self.app
        if app is None:
            return
        item = app.current_media
        source = f"{item.player} · {item.status}" if item else "System media keys"
        self.media_source.configure(text=self._media_text(self.media_source, source))
        self.media_title.configure(text=self._media_text(self.media_title, item.title or "Untitled media" if item else "No media playing", 2))
        self.media_artist.configure(text=self._media_text(self.media_artist, item.artist or "Artist unavailable" if item else "Start playback in a browser or music app."))
        for action, button in self.media_buttons.items():
            button.configure(state="normal" if item is None or action in item.controls else "disabled")
            button.paint()
        self.media_buttons["toggle"].configure(text="Pause" if item and item.status == "Playing" else "Play" if item else "Play / pause")
        self.open_player_button.configure(state="normal" if item else "disabled")
        self.open_player_button.recolor(COLORS["accent"] if item else COLORS["surface"],
                                         COLORS["ink"] if item else COLORS["muted"], "#9bedcf" if item else COLORS["hover"])
        self.player_picker_button.configure(state="normal" if app.media_snapshot.sessions else "disabled")
        self.player_picker_button.paint()
        message = app.media_notice or app.media_snapshot.message
        if not message:
            message = ("Selected player" if app.media_selection else "Following playing media") if item else "No Windows session. Buttons send media keys."
        self.media_message.configure(text=self._media_text(self.media_message, message, 2))

    def build_sessions(self, page):
        app = self.app
        self.label(page, "SESSION PLAN", size=8).pack(anchor="w", pady=(self.dp(4), self.dp(6)))
        form = self.frame(page)
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        app.session_vars = {}
        for row, (field, label, unit) in enumerate((("work_minutes", "Work", "min"),
                ("short_break_minutes", "Short break", "min"), ("long_break_minutes", "Long break", "min"),
                ("long_break_every", "Long break every", "sessions"))):
            self.label(form, label).grid(row=row, column=0, sticky="w", pady=self.dp(3))
            variable = tk.StringVar(value=str(getattr(app.sessions, field)))
            app.session_vars[field] = variable
            minimum, maximum = SETTING_LIMITS[field]
            tk.Spinbox(form, from_=minimum, to=maximum, textvariable=variable, width=4,
                       justify="center", bg=COLORS["surface"], fg=COLORS["text"], insertbackground=COLORS["text"],
                       buttonbackground=COLORS["surface"], relief="flat", bd=0, highlightthickness=1,
                       highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
                       font=("Segoe UI", 9)).grid(row=row, column=1, padx=self.dp(7), pady=self.dp(3), ipady=self.dp(2))
            self.label(form, unit, size=8).grid(row=row, column=2, sticky="w")
        actions = self.frame(page)
        actions.pack(fill="x", pady=(self.dp(7), self.dp(2)))
        save = self.button(actions, "Save plan", app.apply_session_settings, width=10)
        save.recolor(COLORS["accent"], COLORS["ink"], "#9bedcf")
        save.pack(side="left", fill="x", expand=True, padx=(0, self.dp(5)))
        self.button(actions, "Reset count", app.reset_session_count, width=10,
                    tip="Clear completed session count · keeps the current timer").pack(side="left", fill="x", expand=True)
        app.session_settings_status = self.label(page, "Changes apply to the next phase.", size=8,
                                                 wraplength=self.dp(260), height=2, anchor="w", justify="left")
        app.session_settings_status.pack(fill="x", pady=self.dp(5))
        app.session_stats_label = self.label(page, app._session_stats(), size=8)
        app.session_stats_label.pack(anchor="w")

    def show_presets(self):
        button = self.app.preset_btn
        try:
            self.app.preset_menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            self.app.preset_menu.grab_release()

    def refresh_navigation(self):
        selected = self.app.notebook.select()
        for name, button in self.navigation.items():
            active = selected == str(self.pages[name])
            button.recolor(COLORS["hover"] if active else COLORS["surface"],
                           COLORS["text"] if active else COLORS["muted"])
        if selected == str(self.pages["Music"]):
            self.app.start_media_service()

    def refresh(self):
        app = self.app
        accent = {"work": COLORS["accent"], "short_break": COLORS["break"], "long_break": COLORS["long"]}[app.sessions.phase]
        for phase, button in zip(("work", "short_break", "long_break"), app.phase_buttons):
            active = phase == app.sessions.phase
            button.recolor(COLORS["hover"] if active else COLORS["surface"], accent if active else COLORS["muted"])
        hover = {"work": "#9bedcf", "short_break": "#bad8ff", "long_break": "#dbceff"}[app.sessions.phase]
        app.start_btn.recolor(accent, COLORS["ink"], hover)
        app.pin_btn.recolor(COLORS["accent_dim"] if app.root.attributes("-topmost") else COLORS["bg"],
                            COLORS["accent"] if app.root.attributes("-topmost") else COLORS["muted"])
        app.session_count_label.configure(text=f"{app.sessions.completed_sessions} done")
        app.preset_btn.configure(state="disabled" if app.running else "normal")
        app.progress.set_color(accent)

    def work_area(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        return min(self.app._screen_rects(), key=lambda r: max(r[0] - x, 0, x - r[2]) ** 2
                   + max(r[1] - y, 0, y - r[3]) ** 2)

    def close(self):
        for tooltip in self.tooltips:
            tooltip.hide()
        self.tooltips.clear()

    def _destroyed(self, event):
        if event.widget is self.root:
            if self.app is not None:
                self.app.stop_media_service()
                # Drop the last application-owned variable references on Tk's
                # thread, not during a later media/sound worker GC cycle.
                self.app.session_vars.clear()
                self.app.mute_var = None
            self.close()
            self.root._pomodoro_work_area = None
            # Release view/controller cycles on the Tk thread. Otherwise a sound
            # worker's garbage collection can finalize Tk variables off-thread.
            self.app = None
