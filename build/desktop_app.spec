# -*- mode: python ; coding: utf-8 -*-
"""
ARCHIVIERT/LEGACY - siehe legacy/README.md. Nicht mehr aktiv gepflegt, der
normale Build-Prozess (build/build.py) referenziert nur noch
desktop_app_qt.spec.

PyInstaller-Spec fuer Paperless Sync Desktop (CustomTkinter, kein Streamlit).

Einfacher als die alte Streamlit-Variante: die Desktop-App ist ein
gewoehnlicher Tkinter-Prozess mit eigenem Fenster - kein eingebetteter
Webserver/CLI-Bootstrap noetig. desktop_state.py/desktop_controller.py/
legacy/dialogs.py/config_manager.py/csv_utils.py/matcher.py/exporter.py/
paperless_client.py/session_store.py werden von PyInstaller automatisch
per Import-Analyse mitgebuendelt (kein manuelles Auflisten wie bei der
alten Streamlit-Spec noetig).

Build-Befehl (Aufrufverzeichnis egal - Pfade in dieser Datei sind relativ
zu SPECPATH aufgeloest, siehe build/build.py fuer Details zu --workpath):
    pyinstaller build/desktop_app.spec --clean --noconfirm --workpath .pyinstaller-work

Ergebnis:
    dist/PaperlessSyncDesktop/PaperlessSyncDesktop.exe
    (config.json, .env, input/, export/ liegen im selben Ordner und werden
    dort zur Laufzeit gelesen/geschrieben - siehe config_manager.get_base_dir
    und desktop_state.AppState)
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# WICHTIG: Pfade sind relativ zum Ordner DIESER Spec-Datei aufzuloesen
# (PyInstaller nutzt dafuer nicht das Aufrufverzeichnis) - SPECPATH stellt
# PyInstaller automatisch im Exec-Namespace der Spec-Datei bereit.
REPO_ROOT = os.path.join(SPECPATH, "..")

# icon.ico ist optional: existiert die Datei (noch) nicht, baut PyInstaller
# einfach mit dem Standard-Icon weiter statt abzubrechen.
has_icon = os.path.exists(os.path.join(SPECPATH, "icon.ico"))

datas = []
binaries = []
hiddenimports = []

# CustomTkinter (Theme-/Font-Assets) und tkinterdnd2 (natives Drag&Drop,
# bringt eine plattformspezifische Tcl-Paket-Binärdatei mit) muessen als
# Datendateien mitgebuendelt werden - PyInstaller findet sie sonst nicht.
for pkg in ("customtkinter", "tkinterdnd2"):
    datas += collect_data_files(pkg)
    hiddenimports += collect_submodules(pkg)

# Default-Dateien, die seed_default_files() beim allerersten Start neben
# die .exe kopiert (siehe config_manager.py).
datas += [
    (os.path.join(REPO_ROOT, "config.json"), "."),
    (os.path.join(REPO_ROOT, ".env"), "."),
    (os.path.join(REPO_ROOT, "input", "beispiel_kontoauszug.csv"), "input"),
]
if has_icon:
    datas.append((os.path.join(SPECPATH, "icon.ico"), "."))

a = Analysis(
    [os.path.join(REPO_ROOT, "legacy", "desktop_app.py")],
    # REPO_ROOT muss explizit rein: das Einstiegsskript liegt in legacy/,
    # PyInstaller durchsucht standardmaessig aber nur dessen eigenen Ordner,
    # nicht automatisch das Elternverzeichnis - dort liegen
    # desktop_state.py/desktop_controller.py/icon_utils.py.
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaperlessSyncDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # echte Desktop-App - kein Konsolenfenster
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "icon.ico") if has_icon else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PaperlessSyncDesktop",
)
