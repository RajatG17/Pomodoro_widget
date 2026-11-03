# Changelog

All notable changes to the Pomodoro Timer Widget are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-03

First public release, seeded from the accumulated feature and fix history.

### Added

- Initial Windows always-on-top Pomodoro timer widget with borderless,
  draggable window and taskbar/Alt+Tab presence.
- Translucent pinned window with 20% opacity when unfocused and fully opaque
  when focused/clicked; opacity updates from the Windows foreground check.
- Background Ctrl+Alt hotkeys for start/pause, reset, add/remove 10 minutes,
  and show widget, without intercepting normal text entry.
- Persistent state with atomic temporary-file writes, debounced slider saves,
  backup-first reset, settings validation, and hidden/invalid geometry recovery.
- Volume slider and mute for the completion sound.
- Configurable work, short-break, and long-break sessions with persistent
  completed-session counts and plans.
- Compact dark interface with segmented Timer/Music/Sessions navigation,
  accessible controls, and scaled layout that fits current displays.
- Windows media integration: background session discovery for browsers, Spotify,
  and other Windows media apps; track/artist display; targeted play/pause,
  next, previous, and stop controls; player selection; and Open player window
  reveal/launch. Legacy apps fall back to Windows media keys.
- Optional read-only media diagnostics and `--no-media` support.
- Windows MSI installer (WiX Toolset v7) that installs to
  `C:\Program Files\Pomodoro`, adds a Start Menu shortcut, offers an optional
  run-at-logon entry, and generates a standard uninstaller.

### Changed

- Replaced the Spotify playlist-link/URI entry and launch buttons with generic
  Windows now-playing media controls.
- Switched the Windows installer source from the Inno Setup script
  (`installer\PomodoroTimer.iss`) to a WiX Toolset MSI project
  (`installer\PomodoroTimer.wxs`) and relocated the installer source accordingly.
  The installer now builds `Output\PomodoroTimer-Setup-0.1.0.msi`.
- Rewrote the WiX installer source for WiX Toolset v7 and fixed its schema errors:
  `Package` now uses `Name`/`Manufacturer`/`Version`/`UpgradeCode`/`Scope` with an
  auto-generated product code, standard folders use `StandardDirectory`, components
  live under their destination directories, the Start Menu shortcut has its own
  component with an HKCU keypath and `RemoveFolder`, and the run-at-logon entry
  uses a correctly rooted `RegistryValue` with a quoted command path.
- The MSI builds with the unified `wix build` command, passes `wix msi validate`
  (ICE), and installs `dist\PomodoroTimer.exe` to `C:\Program Files\Pomodoro`.
- Added a custom WiX UI to the MSI: the setup wizard opens on an **Installation
  Options** page whose **Start Pomodoro Timer automatically when I log on to
  Windows** checkbox controls the per-user `RUNATLOGON` entry (cleared by default).
  The WiX UI extension is now a build dependency
  (`wix build -ext WixToolset.UI.wixext`).
- The installer's EULA page now shows the project's MIT license from
  `installer\LICENSE.rtf` instead of the extension's placeholder text.

### Fixed

- Rebuilt `dist\PomodoroTimer.exe` from the current source so the packaged app
  includes `--version` and matches the MSI payload.

[0.1.0]: https://github.com/RajatG17/Pomodoro_widget/releases/tag/v0.1.0

