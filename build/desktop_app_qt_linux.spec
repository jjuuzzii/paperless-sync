# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer die Linux-Variante der Qt-UI (Einstiegspunkt:
run_app.py, UI-Quellcode in src/paperless_sync/ui_qt/) - baut EINE
einzelne, selbststaendige ausfuehrbare Datei (onefile), kein separater
_internal-Ordner wie unter Windows/macOS. Pendant zu desktop_app_qt.spec
(Windows); Datenpfade/Imports bewusst identisch gehalten.

UNGETESTET: dieses Projekt wird bislang nur unter Windows entwickelt, hier
ist kein Linux-System zum echten Verifizieren vorhanden - vor dem ersten
Release unter Linux pruefen.

Build-Befehl (Aufrufverzeichnis egal - Pfade in dieser Datei sind relativ
zu SPECPATH aufgeloest, siehe build/build.py fuer Details zu --workpath):
    pyinstaller build/desktop_app_qt_linux.spec --clean --noconfirm --workpath .pyinstaller-work

Ergebnis:
    dist/PaperlessSyncQt   (einzelne ausfuehrbare Datei, kein Installer)

Kein eingebettetes Icon: PyInstaller bettet unter Linux kein Icon in die
ausfuehrbare Datei ein (anders als .ico unter Windows/.icns unter macOS) -
eine Desktop-Verknuepfung braucht zusaetzlich eine eigene .desktop-Datei +
separate Icon-Datei, hier bewusst nicht mitgebaut.

AppImage (von der Anforderung als Alternative zu onefile genannt) ist hier
NICHT umgesetzt: dafuer muesste zusaetzlich appimagetool auf ein
onedir-Ergebnis angewendet werden (eigenes externes Tool, hier nicht
verfuegbar/testbar) - onefile wurde als einfacherer, ausschliesslich auf
PyInstaller selbst basierender erster Weg gewaehlt.
"""

import os

block_cipher = None

REPO_ROOT = os.path.join(SPECPATH, "..")

datas = [
    (os.path.join(REPO_ROOT, "config.json"), "."),
    (os.path.join(REPO_ROOT, ".env"), "."),
    (os.path.join(REPO_ROOT, "input", "beispiel_kontoauszug.csv"), "input"),
]

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

# Onefile: anders als bei Windows/macOS (EXE mit exclude_binaries=True +
# separates COLLECT) bekommt EXE() hier binaries/zipfiles/datas direkt mit
# - PyInstaller packt alles in EINE Datei, die sich zur Laufzeit in einen
# temporaeren Ordner entpackt.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PaperlessSyncQt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
