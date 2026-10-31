; BatteryBenchAnalyzer Inno Setup script
;
; Builds a Windows installer that bundles the onefile BatteryBenchAnalyzer.exe plus the
; app icon, creates Start Menu + desktop shortcuts, and an uninstaller.
;
; To compile on a Windows host (or CI runner with Inno Setup preinstalled):
;   ISCC.exe installer\BatteryBenchAnalyzer.iss
;
; Define /DVERSION=x.y.z to control the version (CI passes it from the tag).

#ifndef VERSION
  #define VERSION "0.3.1"
#endif

#define MyAppName "Battery Bench Analyzer"
#define MyAppExe "BatteryBenchAnalyzer.exe"
#define MyAppVersion VERSION
#define MyAppPublisher "Battery Bench Analyzer"
#define MyAppURL "https://github.com/ALeXXBody/benchanalyzer"

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
OutputBaseFilename=BatteryBenchAnalyzer-{#MyAppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\BatteryBenchAnalyzer\{#MyAppExe}
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
Source: "..\dist\BatteryBenchAnalyzer\*"; DestDir: "{app}\BatteryBenchAnalyzer"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\BatteryBenchAnalyzer\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\BatteryBenchAnalyzer\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\BatteryBenchAnalyzer\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent