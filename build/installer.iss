; Inno Setup Skript fuer Paperless Sync Desktop.
; Baut aus dem PyInstaller-Onedir-Output (dist\PaperlessSyncQt) einen
; richtigen Windows-Installer (Program Files, Startmenue-Eintrag,
; Deinstallation ueber "Apps & Features").
;
; Voraussetzung: PyInstaller-Build ist bereits aktuell
;   (pyinstaller build/desktop_app_qt.spec --clean --noconfirm --workpath .pyinstaller-work)
;
; Kompilieren:
;   "C:\Program Files\Inno Setup 7\ISCC.exe" build/installer.iss
; Ergebnis:
;   installer_output\PaperlessSync-Setup-<Version>.exe
;
; Hinweis: Inno Setup loest relative Pfade unten relativ zum Ordner DIESER
; Datei auf (build/), nicht relativ zum Aufrufverzeichnis - deshalb die
; "..\"-Praefixe fuer alles, was im Repo-Root liegt. icon.ico liegt mit im
; build/-Ordner und bleibt daher ohne Praefix.

#define MyAppName "Paperless Sync"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "Perwein Hofgut"
#define MyAppExeName "PaperlessSyncQt.exe"
#define MyBuildDir "..\dist\PaperlessSyncQt"

[Setup]
AppId={{8F2C9A1E-4B7D-4E3A-9C6F-2D1A5B8E7F30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer_output
OutputBaseFilename=PaperlessSync-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Nutzerdaten liegen in %APPDATA%\PaperlessSync (siehe config_manager.py) -
; das Programm selbst braucht daher Adminrechte nur fuer die Installation
; nach Program Files, nicht fuer den laufenden Betrieb.
PrivilegesRequired=admin

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Symbol erstellen"; GroupDescription: "Zusaetzliche Symbole:"

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} jetzt starten"; Flags: nowait postinstall skipifsilent
