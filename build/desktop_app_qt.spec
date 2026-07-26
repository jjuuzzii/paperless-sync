# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer die aktuelle PySide6/Qt-UI (Einstiegspunkt:
run_app.py, UI-Quellcode in src/paperless_sync/ui_qt/). Getrennt von
desktop_app.spec (CustomTkinter-Version, archiviert) - baut in einen
eigenen Ordner, ohne die alte CTk-.exe zu beruehren.

Build-Befehl (Aufrufverzeichnis egal - Pfade in dieser Datei sind relativ
zu SPECPATH aufgeloest, siehe build/build.py fuer Details zu --workpath):
    pyinstaller build/desktop_app_qt.spec --clean --noconfirm --workpath .pyinstaller-work

Ergebnis:
    dist/PaperlessSyncQt/PaperlessSyncQt.exe
"""

import os

block_cipher = None

# WICHTIG: Pfade sind relativ zum Ordner DIESER Spec-Datei aufzuloesen
# (PyInstaller nutzt dafuer nicht das Aufrufverzeichnis) - SPECPATH stellt
# PyInstaller automatisch im Exec-Namespace der Spec-Datei bereit.
REPO_ROOT = os.path.join(SPECPATH, "..")

has_icon = os.path.exists(os.path.join(SPECPATH, "icon.ico"))

datas = [
    (os.path.join(REPO_ROOT, "config.json"), "."),
    (os.path.join(REPO_ROOT, ".env"), "."),
    (os.path.join(REPO_ROOT, "input", "beispiel_kontoauszug.csv"), "input"),
]
if has_icon:
    datas.append((os.path.join(SPECPATH, "icon.ico"), "."))

a = Analysis(
    [os.path.join(REPO_ROOT, "run_app.py")],
    # REPO_ROOT fuer run_app.py selbst und version.py, zusaetzlich src/ fuer
    # das komplette paperless_sync-Package (ui_qt/, state/, core/), das
    # run_app.py transitiv importiert.
    pathex=[REPO_ROOT, os.path.join(REPO_ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name="PaperlessSyncQt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name="PaperlessSyncQt",
)
