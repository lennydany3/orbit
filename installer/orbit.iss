; Orbit's installer (Inno Setup 6). Build it from the project folder, after the two programs are built:
;
;     iscc /DAppVersion=1.0.7 installer\orbit.iss          ->  installer\Output\Orbit-Setup-1.0.7.exe
;
; It installs for the current user only (no administrator rights, no UAC prompt), which is also what lets Orbit update itself
; silently later: the program replaces its own installer-made files in a folder it owns.
;
;   {app}            %LOCALAPPDATA%\Programs\Orbit     Orbit.exe (the window) and engine\ (the voice and the brain, a program of its own)
;   {userappdata}    %APPDATA%\Orbit                   what is yours: settings, notes, reminders, keys, models, logs. Never touched
;                                                      by an update; only removed on uninstall if you say so.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Orbit"
#define AppExe "Orbit.exe"

[Setup]
AppId={{8F3C2A71-5B9E-4D6A-A1C4-7E0B3D92F5C8}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Orbit
AppPublisherURL=https://github.com/lennydany3/orbit
AppSupportURL=https://github.com/lennydany3/orbit/issues
AppUpdatesURL=https://github.com/lennydany3/orbit/releases
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=..\ui\assets\logo\orbit.ico
OutputDir=Output
OutputBaseFilename=Orbit-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardImageFile=wizard\welcome-164x314.bmp,wizard\welcome-328x628.bmp
WizardSmallImageFile=wizard\small-55.bmp,wizard\small-110.bmp
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Meet Orbit
WelcomeLabel2=Orbit is a voice assistant for your Windows PC. Say "hey orbit" and it opens apps, plays your music, sets timers and reminders, takes notes, and chats.%n%nThis installs Orbit for your account only. It does not need administrator rights.%n%nClick Next to continue.
FinishedHeadingLabel=Orbit is ready
FinishedLabelNoIcons=Orbit has been installed.
FinishedLabel=Orbit has been installed. It lives in the tray (the small arrow by the clock), and its window opens when you start it.
SelectDirLabel3=Orbit will be installed in the following folder.
SelectTasksLabel2=Choose how Orbit should fit into your PC.

[CustomMessages]
KeysTitle=Connect Orbit's brain
KeysSubtitle=Optional. You can do this later from the tray menu (API keys).
KeysBody=Orbit answers questions and chats using Groq, a free service. Paste a key from console.groq.com/keys. Leave it empty to use only the simple commands for now.
KeysGroq=Groq key (language model):

[Tasks]
Name: "desktopicon"; Description: "Add a shortcut to the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start Orbit when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\ui\target\release\orbit-ui.exe"; DestDir: "{app}"; DestName: "{#AppExe}"; Flags: ignoreversion
Source: "..\build\dist\orbit-engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Start with Windows: one value in your own Run key (no administrator rights), taken out again on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --spawn"; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Installed by hand: offer to start it. Updated by Orbit itself (silent): start it again with no question, so it comes straight back.
Filename: "{app}\{#AppExe}"; Description: "Start Orbit"; Flags: nowait postinstall skipifsilent runasoriginaluser
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Orbit\updates"
Type: filesandordirs; Name: "{app}"

[Code]
const
  // The app's colours (ui/src/view.rs), as Inno wants them: $BBGGRR.
  Ground = $070606;     // #060607
  Panel  = $0E0C0C;     // #0C0C0E
  Ink    = $F5F3F2;     // #F2F3F5
  Muted  = $988F8A;     // #8A8F98
  Accent = $F5A6C7;     // #C7A6F5

var
  KeysPage: TInputQueryWizardPage;

// ---- the dark look --------------------------------------------------------------------------------------------------------

procedure Tint(Parent: TWinControl);
var
  I: Integer;
  C: TControl;
begin
  for I := 0 to Parent.ControlCount - 1 do
  begin
    C := Parent.Controls[I];
    if C is TNewStaticText then
      TNewStaticText(C).Font.Color := Ink
    else if C is TPanel then
      TPanel(C).Color := Ground
    else if C is TNewCheckListBox then
    begin
      TNewCheckListBox(C).Color := Panel;
      TNewCheckListBox(C).Font.Color := Ink;
    end
    else if C is TNewMemo then
    begin
      TNewMemo(C).Color := Panel;
      TNewMemo(C).Font.Color := Ink;
    end
    else if C is TNewEdit then
    begin
      TNewEdit(C).Color := Panel;
      TNewEdit(C).Font.Color := Ink;
    end
    else if C is TNewCheckBox then
      TNewCheckBox(C).Font.Color := Ink
    else if C is TNewRadioButton then
      TNewRadioButton(C).Font.Color := Ink;
    if C is TWinControl then
      Tint(TWinControl(C));
  end;
end;

procedure DressTheWizard;
begin
  WizardForm.Color := Ground;
  WizardForm.InnerPage.Color := Ground;
  WizardForm.MainPanel.Color := Ground;
  WizardForm.WelcomePage.Color := Ground;
  WizardForm.FinishedPage.Color := Ground;
  WizardForm.Bevel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  Tint(WizardForm);
  WizardForm.PageNameLabel.Font.Color := Ink;
  WizardForm.PageNameLabel.Font.Style := [fsBold];
  WizardForm.PageDescriptionLabel.Font.Color := Muted;
  WizardForm.WelcomeLabel1.Font.Color := Ink;
  WizardForm.WelcomeLabel1.Font.Size := 18;
  WizardForm.WelcomeLabel2.Font.Color := Muted;
  WizardForm.FinishedHeadingLabel.Font.Color := Ink;
  WizardForm.FinishedHeadingLabel.Font.Size := 18;
  WizardForm.FinishedLabel.Font.Color := Muted;
end;

// ---- the optional keys page ---------------------------------------------------------------------------------------------

function EnvFile: String;
begin
  Result := ExpandConstant('{userappdata}\Orbit\.env');
end;

procedure InitializeWizard;
begin
  KeysPage := CreateInputQueryPage(wpSelectTasks, CustomMessage('KeysTitle'), CustomMessage('KeysSubtitle'), CustomMessage('KeysBody'));
  KeysPage.Add(CustomMessage('KeysGroq'), False);
  DressTheWizard;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // An update, or someone who set their keys up already, is never asked again.
  Result := (PageID = KeysPage.ID) and FileExists(EnvFile);
end;

function CleanKey(const S: String): String;
begin
  Result := Trim(S);
  StringChangeEx(Result, #13, '', True);
  StringChangeEx(Result, #10, '', True);
  StringChangeEx(Result, '"', '', True);
end;

procedure SaveKeys;
var
  Groq, Text: String;
begin
  Groq := CleanKey(KeysPage.Values[0]);
  if (Groq = '') or FileExists(EnvFile) then
    Exit;
  Text := '# Orbit''s keys. Edit this file from the tray menu (API keys...), then restart Orbit.' + #13#10;
  Text := Text + 'GROQ_API_KEY=' + Groq + #13#10;
  Text := Text + 'SPOTIFY_CLIENT_ID=' + #13#10;
  ForceDirectories(ExtractFilePath(EnvFile));
  SaveStringToFile(EnvFile, Text, False);
end;

// ---- install and uninstall ----------------------------------------------------------------------------------------------

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  // The engine is a separate program that the window starts; make sure no old one still holds its files.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM orbit-engine.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveKeys;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM orbit-engine.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      if MsgBox('Also delete your Orbit data (settings, notes, reminders, keys and the downloaded voice models)?' + #13#10 + #13#10 +
                'Choose No to keep them for a later install.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{userappdata}\Orbit'), True, True, True);
end;
