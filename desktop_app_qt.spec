# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer den PySide6/Qt-UI-Prototyp (desktop_app_qt.py).
Getrennt von desktop_app.spec (CustomTkinter-Version) - baut in einen
eigenen Ordner, ohne die bestehende CTk-.exe zu beruehren.

Build-Befehl:
    pyinstaller desktop_app_qt.spec --clean --noconfirm

Ergebnis:
    dist/PaperlessSyncQt/PaperlessSyncQt.exe
"""

import os

block_cipher = None

has_icon = os.path.exists("icon.ico")

datas = [
    ("config.json", "."),
    (".env", "."),
    ("input/beispiel_kontoauszug.csv", "input"),
]
if has_icon:
    datas.append(("icon.ico", "."))

a = Analysis(
    ["desktop_app_qt.py"],
    pathex=[],
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
    name="PaperlessSyncQt",
)
