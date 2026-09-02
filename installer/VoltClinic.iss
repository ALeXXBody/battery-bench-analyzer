; VoltClinic Inno Setup script
;
; Builds a Windows installer that bundles the onefile VoltClinic.exe plus the
; app icon, creates Start Menu + desktop shortcuts, and an uninstaller.
;
; To compile on a Windows host (or CI runner with Inno Setup preinstalled):
;   ISCC.exe installer\VoltClinic.iss
;
; Define /DVERSION=x.y.z to control the version (CI passes it from the tag).

#ifndef VERSION
  #define VERSION "0.3.0"
#endif

#define MyAppName "VoltClinic"
#define MyAppExe "VoltClinic.exe"
#define MyAppVersion VERSION
#define MyAppPublisher "VoltClinic"
#define MyAppURL "https://github.com/ALeXXBody/voltclinic"

[Setup]
AppId={{B3D0C1E7-8F7C-4B6A-9E3D-5A1C2C0E4D2A}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=VoltClinic-{#MyAppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\VoltClinic.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent