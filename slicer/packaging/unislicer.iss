; Inno Setup script for the Windows installer. Built by .github/workflows/unislicer-windows.yml
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8C4C5D2E-6B1A-4F0E-9B7A-1E2D3C4B5A69}
AppName=UniSlicer
AppVersion={#MyAppVersion}
AppVerName=UniSlicer {#MyAppVersion}
AppPublisher=UniSlicer
DefaultDirName={autopf}\UniSlicer
DefaultGroupName=UniSlicer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=UniSlicer-Setup
SetupIconFile=unislicer.ico
UninstallDisplayIcon={app}\UniSlicer.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\UniSlicer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\UniSlicer"; Filename: "{app}\UniSlicer.exe"
Name: "{autodesktop}\UniSlicer"; Filename: "{app}\UniSlicer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\UniSlicer.exe"; Description: "{cm:LaunchProgram,UniSlicer}"; Flags: nowait postinstall skipifsilent
