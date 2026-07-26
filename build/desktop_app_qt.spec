# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer den PySide6/Qt-UI-Prototyp (desktop_app_qt.py).
Getrennt von desktop_app.spec (CustomTkinter-Version, archiviert) - baut in
einen eigenen Ordner, ohne die bestehende CTk-.exe zu beruehren.

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
    [os.path.join(REPO_ROOT, "desktop_app_qt.py")],
    # REPO_ROOT fuer desktop_app_qt.py/desktop_state.py/desktop_controller.py,
    # zusaetzlich src/ fuer die Core-Backend-Module (paperless_sync.core.*),
    # die desktop_app_qt.py transitiv importiert.
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
