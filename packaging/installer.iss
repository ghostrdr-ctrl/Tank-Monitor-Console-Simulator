; Tank Monitor Console Simulator -- Inno Setup installer script.
; Copyright (C) 2026 Verbose Software. GNU GPL v3.
;
; Build (after PyInstaller has produced dist/TankMonitorConsoleSimulator):
;     iscc /DMyAppVersion=0.1.0 packaging\installer.iss
;
; The version is passed in on the command line so it stays in step with the
; package's __version__; packaging\build.py does that for you. Produces
; dist\installer\TankMonitorConsoleSimulator-<version>-Setup.exe.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Tank Monitor Console Simulator"
#define MyPublisher "Verbose Software"
#define MyExeName "TankMonitorConsoleSimulator.exe"
#define MyAppURL "https://github.com/ghostrdr-ctrl/Tank-Monitor-Console-Simulator"

[Setup]
; A fixed GUID identifies the product across versions so an upgrade replaces
; the previous install rather than standing up a second copy. Generated once
; for this product; never change it.
AppId={{6B9E2F4A-1C7D-4E58-9A3B-2F5C8D0E1A66}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyPublisher}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install where possible: no admin rights needed, and it drops the
; UAC prompt that a training tool does not warrant. Falls back to per-machine
; if the user runs it elevated.
PrivilegesRequiredOverridesAllowed=dialog commandline
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyExeName}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
; The app is a 64-bit Python build; install and run as 64-bit.
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller one-folder output. `recursesubdirs` and the wildcard
; take the .exe, the Python runtime, tkinter's Tcl/Tk, and the bundled
; LICENSE together.
Source: "..\dist\TankMonitorConsoleSimulator\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; A copy of the licence at the top of the install, not only inside
; _internal, so it is where a person looks for it. GPL section 6 wants the
; terms delivered with the program; this and the About box both do that.
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; \
    Flags: ignoreversion

[Icons]
; The Comment is what Windows shows in the shortcut's hover tooltip, so it
; carries the version: hovering the Start Menu or desktop shortcut reads
; "Tank Monitor Console Simulator 0.1.0". The .exe itself also carries the
; version in its Properties, from the PyInstaller version resource.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; \
    Comment: "{#MyAppName} {#MyAppVersion}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; \
    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; \
    Comment: "{#MyAppName} {#MyAppVersion}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The per-user data directory the app writes (console state, settings, the
; XPort config) lives under the user profile, not here, and is deliberately
; left in place so an uninstall-then-reinstall keeps a site's programming.
; Nothing extra to delete under {app}; the file list is removed automatically.
