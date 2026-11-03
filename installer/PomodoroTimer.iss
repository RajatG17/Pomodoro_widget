; Inno Setup script for the Pomodoro Timer Widget.
; Build with:  iscc "installer\PomodoroTimer.iss"
; Output:      "Output\PomodoroTimer-Setup-0.1.0.exe"

#define MyAppName "Pomodoro Timer"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Rajat Gade"
#define MyAppURL "https://github.com/RajatG17/Pomodoro_widget"
#define MyAppExeName "PomodoroTimer.exe"

[Setup]
AppId={{9E7B6C5D-4A3B-2C1D-8E7F-6A5B4C3D2E1F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppComments=Always-on-top Pomodoro timer with Windows media playback controls.
LicenseFile=..\LICENSE
DefaultDirName={autopf}\Pomodoro
DefaultGroupName=Pomodoro Timer
UninstallDisplayIcon={autopf}\Pomodoro\{#MyAppExeName}
OutputBaseFilename=PomodoroTimer-Setup-{#MyAppVersion}
OutputDir=..\Output
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "runatlogin"; Description: "Run Pomodoro Timer when I log on"; GroupDescription: "Pomodoro Timer"; Flags: unchecked

[Files]
Source: "..\dist\PomodoroTimer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Pomodoro Timer"; Filename: "{app}\PomodoroTimer.exe"; WorkingDir: "{app}"; Comment: "Always-on-top Pomodoro timer"

[Run]
Filename: "{app}"; Description: "Launch Pomodoro Timer"; Flags: nowait postinstall skipifsilent; Tasks: runatlogin
