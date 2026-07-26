# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer Paperless Sync Desktop (CustomTkinter, kein Streamlit).

Einfacher als die alte Streamlit-Variante: die Desktop-App ist ein
gewoehnlicher Tkinter-Prozess mit eigenem Fenster - kein eingebetteter
Webserver/CLI-Bootstrap noetig. desktop_state.py/desktop_controller.py/
dialogs.py/config_manager.py/csv_utils.py/matcher.py/exporter.py/
paperless_client.py/session_store.py werden von PyInstaller automatisch
per Import-Analyse mitgebuendelt (kein manuelles Auflisten wie bei der
alten Streamlit-Spec noetig).

Build-Befehl:
    pyinstaller desktop_app.spec --clean --noconfirm

Ergebnis:
    dist/PaperlessSyncDesktop/PaperlessSyncDesktop.exe
    (config.json, .env, input/, export/ liegen im selben Ordner und werden
    dort zur Laufzeit gelesen/geschrieben - siehe config_manager.get_base_dir
    und desktop_state.AppState)
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# icon.ico ist optional: existiert die Datei (noch) nicht, baut PyInstaller
# einfach mit dem Standard-Icon weiter statt abzubrechen.
has_icon = os.path.exists("icon.ico")

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
    ("config.json", "."),
    (".env", "."),
    ("input/beispiel_kontoauszug.csv", "input"),
]
if has_icon:
    datas.append(("icon.ico", "."))

a = Analysis(
    ["desktop_app.py"],
    pathex=[],
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
    icon="icon.ico" if has_icon else None,
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
