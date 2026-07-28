# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec fuer die macOS-Variante der Qt-UI (Einstiegspunkt:
run_app.py, UI-Quellcode in src/paperless_sync/ui_qt/) - baut ein
.app-Bundle. Pendant zu desktop_app_qt.spec (Windows); Datenpfade/Imports
bewusst identisch gehalten, nur BUNDLE()/Icon-Format sind macOS-spezifisch.

UNGETESTET: dieses Projekt wird bislang nur unter Windows entwickelt, hier
ist kein Mac zum echten Verifizieren vorhanden - vor dem ersten Release
auf einem echten Mac pruefen (insbesondere Codesigning/Gatekeeper, siehe
codesign_identity unten).

Build-Befehl (Aufrufverzeichnis egal - Pfade in dieser Datei sind relativ
zu SPECPATH aufgeloest, siehe build/build.py fuer Details zu --workpath):
    pyinstaller build/desktop_app_qt_macos.spec --clean --noconfirm --workpath .pyinstaller-work

Ergebnis:
    dist/PaperlessSyncQt.app

Icon: erwartet build/icon.icns (macOS-Icon-Format - .ico aus dem
Windows-Build funktioniert hier nicht). Fehlt die Datei, baut PyInstaller
ohne eingebettetes Icon (gleicher Fallback wie in desktop_app_qt.spec).

CFBundleShortVersionString unten muss bei einem Release manuell zusammen
mit version.py/build/installer.iss (MyAppVersion) hochgezaehlt werden -
siehe version.py-Docstring.
"""

import os

block_cipher = None

REPO_ROOT = os.path.join(SPECPATH, "..")

has_icon = os.path.exists(os.path.join(SPECPATH, "icon.icns"))

datas = [
    (os.path.join(REPO_ROOT, "config.json"), "."),
    (os.path.join(REPO_ROOT, ".env"), "."),
    (os.path.join(REPO_ROOT, "input", "beispiel_kontoauszug.csv"), "input"),
]
if has_icon:
    datas.append((os.path.join(SPECPATH, "icon.icns"), "."))

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
    # Ungetestet ohne Apple-Entwicklerkonto: ohne Codesigning meldet
    # Gatekeeper das .app-Bundle vermutlich als "nicht verifizierter
    # Entwickler" (aehnlich der Windows-Smart-App-Control-Problematik
    # dieses Projekts) - fuer einen echten Release waere ein
    # codesign_identity + Notarization noetig.
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "icon.icns") if has_icon else None,
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

app = BUNDLE(
    coll,
    name="PaperlessSyncQt.app",
    icon=os.path.join(SPECPATH, "icon.icns") if has_icon else None,
    bundle_identifier="com.perweinhofgut.paperlesssync",
    info_plist={
        "NSHighResolutionCapable": "True",
        "CFBundleShortVersionString": "2.3.1",
    },
)
