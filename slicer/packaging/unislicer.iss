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
ShowLanguageDialog=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; a running UniSlicer locks its files: it holds the mutex UniSlicerAppMutex, checked in InitializeSetup below
; (not with AppMutex=, which checks at once: an update started from the program found it still closing and
; stopped with "Setup has detected that UniSlicer is currently running")
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#define HaveArabic
#endif

[Registry]
; the language picked in the installer becomes the app's language (QSettings "UniSlicer/UniSlicer")
Root: HKCU; Subkey: "Software\UniSlicer\UniSlicer"; ValueType: string; ValueName: "language"; ValueData: "he"; Languages: hebrew
Root: HKCU; Subkey: "Software\UniSlicer\UniSlicer"; ValueType: string; ValueName: "language"; ValueData: "en"; Languages: english
Root: HKCU; Subkey: "Software\UniSlicer\UniSlicer"; ValueType: string; ValueName: "language"; ValueData: "ru"; Languages: russian
#ifdef HaveArabic
Root: HKCU; Subkey: "Software\UniSlicer\UniSlicer"; ValueType: string; ValueName: "language"; ValueData: "ar"; Languages: arabic
#endif

; the "Ask Claude" window was removed: delete an API key an earlier version saved on this computer
Root: HKCU; Subkey: "Software\UniSlicer\UniSlicer"; ValueType: none; ValueName: "claude_api_key"; Flags: deletevalue

; "Open with UniSlicer" for model files (does not take over the default program)
Root: HKCU; Subkey: "Software\Classes\UniSlicer.Model"; ValueType: string; ValueName: ""; ValueData: "3D model (UniSlicer)"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\UniSlicer.Model\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\UniSlicer.exe,0"
Root: HKCU; Subkey: "Software\Classes\UniSlicer.Model\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\UniSlicer.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.stl\OpenWithProgids"; ValueType: string; ValueName: "UniSlicer.Model"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.3mf\OpenWithProgids"; ValueType: string; ValueName: "UniSlicer.Model"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.obj\OpenWithProgids"; ValueType: string; ValueName: "UniSlicer.Model"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.ply\OpenWithProgids"; ValueType: string; ValueName: "UniSlicer.Model"; ValueData: ""; Flags: uninsdeletevalue

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; exported print files: <install folder>\Prints\<file name>\<file name>.<ext>; writable for the user even
; in Program Files, and kept when UniSlicer is uninstalled or updated
Name: "{app}\Prints"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "..\dist\UniSlicer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\UniSlicer"; Filename: "{app}\UniSlicer.exe"
Name: "{autodesktop}\UniSlicer"; Filename: "{app}\UniSlicer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\UniSlicer.exe"; Description: "{cm:LaunchProgram,UniSlicer}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; an update from inside the program installs silently: open the program again when it is done
Filename: "{app}\UniSlicer.exe"; Flags: nowait runasoriginaluser skipifnotsilent

[Code]
function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Result := True;
  Waited := 0;
  { started by the program's own Update (/FROMAPP=1): it is closing - give it up to 30 seconds }
  if ExpandConstant('{param:FROMAPP|0}') = '1' then
  begin
    while CheckForMutexes('UniSlicerAppMutex') and (Waited < 30000) do
    begin
      Sleep(500);
      Waited := Waited + 500;
    end;
  end;
  { still running (or Setup run by hand while it is open): ask to close it, as before }
  while CheckForMutexes('UniSlicerAppMutex') do
  begin
    if MsgBox(FmtMessage(SetupMessage(msgSetupAppRunningError), ['UniSlicer']), mbError, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;
