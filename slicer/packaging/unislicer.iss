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
; a running UniSlicer locks its files: detect it (the app holds this mutex) and offer to close it
AppMutex=UniSlicerAppMutex
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

[CustomMessages]
english.ClaudeKeyTitle=Claude assistant
english.ClaudeKeyDesc=Optional: your Anthropic API key for the "Ask Claude" window
english.ClaudeKeyInfo=Paste your API key from console.anthropic.com. It is saved on this computer only (not in the installer file) and sent only to Anthropic. Leave it empty to keep the current key or to add one later in UniSlicer.
english.ClaudeKeyPrompt=API key (sk-ant-...):
hebrew.ClaudeKeyTitle=העוזר Claude
hebrew.ClaudeKeyDesc=לא חובה: מפתח ה-API שלך מ-Anthropic לחלון "שאל את Claude"
hebrew.ClaudeKeyInfo=הדבק את מפתח ה-API מ-console.anthropic.com. המפתח נשמר רק במחשב הזה (לא בקובץ ההתקנה) ונשלח רק ל-Anthropic. השאר ריק כדי לשמור את המפתח הקיים או להוסיף אותו מאוחר יותר בתוכנה.
hebrew.ClaudeKeyPrompt=מפתח API (sk-ant-...):
russian.ClaudeKeyTitle=Помощник Claude
russian.ClaudeKeyDesc=Необязательно: ваш ключ API Anthropic для окна «Спросить Claude»
russian.ClaudeKeyInfo=Вставьте ключ API с console.anthropic.com. Он сохраняется только на этом компьютере (не в файле установщика) и отправляется только в Anthropic. Оставьте пустым, чтобы сохранить текущий ключ или добавить его позже в UniSlicer.
russian.ClaudeKeyPrompt=Ключ API (sk-ant-...):
#ifdef HaveArabic
arabic.ClaudeKeyTitle=المساعد Claude
arabic.ClaudeKeyDesc=اختياري: مفتاح Anthropic API الخاص بك لنافذة «اسأل Claude»
arabic.ClaudeKeyInfo=الصق مفتاح API من console.anthropic.com. يُحفظ على هذا الحاسوب فقط (ليس في ملف التثبيت) ويُرسل إلى Anthropic فقط. اتركه فارغًا للإبقاء على المفتاح الحالي أو لإضافته لاحقًا في UniSlicer.
arabic.ClaudeKeyPrompt=مفتاح API (sk-ant-...):
#endif

[Code]
var
  ClaudeKeyPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  { optional page: the user's own Anthropic API key, written to this computer's settings only }
  ClaudeKeyPage := CreateInputQueryPage(wpSelectTasks, CustomMessage('ClaudeKeyTitle'),
    CustomMessage('ClaudeKeyDesc'), CustomMessage('ClaudeKeyInfo'));
  ClaudeKeyPage.Add(CustomMessage('ClaudeKeyPrompt'), True);
end;

function ClaudeKey: String;
begin
  { /CLAUDEKEY=... on the command line (silent installs on lab computers), else the page }
  Result := Trim(ExpandConstant('{param:CLAUDEKEY|}'));
  if (Result = '') and (ClaudeKeyPage <> nil) then
    Result := Trim(ClaudeKeyPage.Values[0]);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Key: String;
begin
  if CurStep = ssPostInstall then
  begin
    Key := ClaudeKey;
    if Key <> '' then
      RegWriteStringValue(HKEY_CURRENT_USER, 'Software\UniSlicer\UniSlicer', 'claude_api_key', Key);
  end;
end;
