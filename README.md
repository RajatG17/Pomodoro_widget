# Pomodoro Timer Widget

A small, always-on-top Pomodoro timer for Windows.

## Run manually

Option 1 - Standalone executable (no Python needed):

- Double-click `dist\PomodoroTimer.exe`
- Or from a terminal: `.\dist\PomodoroTimer.exe`

Option 2 - From source (requires Python 3 with tkinter, included with standard Windows Python installs):

- `python pomodoro.py`
- Or double-click `run.bat`

## Start automatically with Windows

To launch the widget every time you log in to Windows:

### Method A - Startup folder (recommended, simplest)

1. Press `Win + R`, type `shell:startup`, press Enter. This opens your personal Startup folder.
2. Create a shortcut there:
   - Right-click in the folder -> New -> Shortcut.
   - Target: full path to the executable, e.g. `G:\Documents\Projects\Pomodoro_widget\dist\PomodoroTimer.exe`
     (or to the launcher: `G:\Documents\Projects\Pomodoro_widget\run.bat`)
   - Name it `Pomodoro Timer` and finish.
3. (Optional) Right-click the shortcut -> Properties -> set "Run" to "Minimized" is not needed; the widget is borderless. You can also set a hotkey in the Shortcut key field.
4. Log out and back in (or restart) to verify the widget appears.

### Method B - Task Scheduler (more control)

1. Open Task Scheduler (`taskschd.msc`).
2. Create Task -> General tab: name it `Pomodoro Timer`, choose "Run only when user is logged on".
3. Triggers tab -> New -> "At log on" (optionally delay by 10 seconds).
4. Actions tab -> New -> "Start a program" -> Program: `G:\Documents\Projects\Pomodoro_widget\dist\PomodoroTimer.exe`.
5. Conditions tab -> uncheck "Start the task only if the computer is on AC power" if on a laptop.
6. Save and test by running the task manually.

### Method C - Registry (advanced)

Add a string value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`:

- Name: `PomodoroTimer`
- Value: `"G:\Documents\Projects\Pomodoro_widget\dist\PomodoroTimer.exe"`

You can do this via PowerShell:

```powershell
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
  -Name "PomodoroTimer" `
  -Value '"G:\Documents\Projects\Pomodoro_widget\dist\PomodoroTimer.exe"'
```

To remove it later:

```powershell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "PomodoroTimer"
```

## Features

- Stays on top of all windows (toggle with the "Pin"/"Unpin" button).
- Borderless, draggable widget.
- Start / Pause / Reset.
- Extend the running timer: `+1m`, `+5m`, `+10m` (or `+`/`-` keys).
- Quick presets: 25m / 15m / 5m.
- Sound + flash when the timer completes (volume slider + mute in the widget).
- Pinned window is translucent (~80%) and becomes opaque when clicked.
- Remembers last state (duration, remaining time, volume, mute, pin, position) between runs.
- Music tab with media-key controls (play/pause, next, prev, stop) and Spotify open/launch.
- Keyboard `+`/`-` adjust by 10 minutes.

## Keyboard shortcuts

- `Space` - Start/Pause
- `R` - Reset
- `+` / `-` - Add/remove 10 minutes

## Testing

Run the unit tests:

```powershell
python -m unittest tests.test_pomodoro -v
```

## Notes

- The widget is borderless, so it has no taskbar button; use the `X` button in the header to close it.
- To stop autostart, remove the shortcut/task/registry entry you created above.
