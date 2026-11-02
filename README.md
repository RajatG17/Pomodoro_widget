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
- The widget opens raised and fully opaque for 3 seconds (without stealing keyboard
  focus). A pinned, inactive window fades to ~80%; clicking or focusing it makes
  it opaque. It stays opaque while focused, then fades 2 seconds after focus leaves.
  Unpinned windows always stay opaque.
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

Tests require Windows, Python with tkinter, and an interactive desktop. Each test
uses a temporary settings file; tests never read or overwrite your saved timer
settings. Media playback is not triggered by the tests.

## Notes

- The widget is borderless, so it has no taskbar button; use the `X` button in the header to close it.
- To stop autostart, remove the shortcut/task/registry entry you created above.

## Troubleshooting: app runs but no window appears

Saved settings live in `%USERPROFILE%\.pomodoro_state.json`. Older versions
could save an invisible `1x1` window, especially after running the old tests.
The fixed version automatically rejects tiny, malformed, oversized, and off-screen
window geometry and opens at a visible default position. Valid timer and audio
settings are kept; hidden windows no longer overwrite the last valid geometry.
Invalid JSON, non-object settings, and invalid field types no longer prevent
startup. Invalid fields fall back to defaults; remaining time and volume are
bounded to valid ranges. Saved durations outside 1 second–1 year use the default
25 minutes. Timers reopen paused; completed timers remain at zero.

Both the Python source and rebuilt `dist\PomodoroTimer.exe` include this recovery.

### Reset saved settings safely

Close existing copies of the widget first. If you want to reset all preferences
(including a volume/mute setting left by the old tests), run either:

```powershell
python pomodoro.py --reset-state
# Or, without Python:
.\dist\PomodoroTimer.exe --reset-state
```

The original settings are moved to a timestamped
`%USERPROFILE%\.pomodoro_state.json.backup-*` file before opening defaults.
If the backup fails, resetting stops and the original file is left untouched.
To restore a backup, close the app and copy that backup to
`%USERPROFILE%\.pomodoro_state.json`.

### Packaged executable smoke test

This opt-in test launches a temporary copy of the executable with isolated
settings, checks that a visible, usable window appears from `1x1` saved geometry
or malformed settings, verifies reset backups, then closes it. It does not launch
Spotify or change your own settings.

```powershell
$env:POMODORO_TEST_EXE = (Resolve-Path .\dist\PomodoroTimer.exe).Path
python -m unittest tests.test_executable -v
Remove-Item Env:\POMODORO_TEST_EXE
```
