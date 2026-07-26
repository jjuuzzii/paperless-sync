# -*- mode: python ; coding: utf-8 -*-
"""
ARCHIVIERT/LEGACY - siehe legacy/README.md. Nicht mehr aktiv gepflegt, der
normale Build-Prozess (build/build.py) referenziert nur noch
desktop_app_qt.spec.

PyInstaller-Spec-Datei fuer Paperless Sync (Streamlit-App als eigenstaendige
Windows-.exe, onedir-Modus - empfohlen fuer Streamlit-Apps).

Build-Befehl (Aufrufverzeichnis egal - Pfade in dieser Datei sind relativ
zu SPECPATH aufgeloest, siehe build/build.py fuer Details zu --workpath):
    pyinstaller build/paperless_sync.spec --clean --noconfirm --workpath .pyinstaller-work

Ergebnis:
    dist/PaperlessSync/PaperlessSync.exe
    (config.json, .env, input/, export/ liegen im selben Ordner und werden
    zur Laufzeit dort gelesen/geschrieben - siehe config_manager.get_base_dir)
"""

import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# WICHTIG: Pfade sind relativ zum Ordner DIESER Spec-Datei aufzuloesen
# (PyInstaller nutzt dafuer nicht das Aufrufverzeichnis) - SPECPATH stellt
# PyInstaller automatisch im Exec-Namespace der Spec-Datei bereit.
REPO_ROOT = os.path.join(SPECPATH, "..")

datas = []
binaries = []
hiddenimports = [
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "importlib_metadata",
    "requests_pkcs12",
]

# collect_all buendelt Datendateien, Metadaten (importlib.metadata) und
# Submodule - Streamlit liest zur Laufzeit eigene Paketmetadaten aus, ohne
# das schlaegt der Start mit "PackageNotFoundError" fehl. cryptography wird
# fuer das optionale PKCS#12-Client-Zertifikat (mTLS) gebraucht und bringt
# eigene Binaerbindings mit, die PyInstaller sonst leicht uebersieht.
for pkg in ("streamlit", "cryptography"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# App-Quellcode + Laufzeit-Konfiguration, die Streamlit/die App selbst aus
# dem gebuendelten Verzeichnis lesen muss. app.py liegt im Quellcode jetzt
# in legacy/, landet im Bundle aber weiterhin flach im Root ("."), damit
# run_app.py.resolve_app_path() (frozen-Zweig) unveraendert funktioniert.
datas += [
    (os.path.join(REPO_ROOT, "legacy", "app.py"), "."),
    (os.path.join(REPO_ROOT, "config_manager.py"), "."),
    (os.path.join(REPO_ROOT, "csv_utils.py"), "."),
    (os.path.join(REPO_ROOT, "session_store.py"), "."),
    (os.path.join(REPO_ROOT, "paperless_client.py"), "."),
    (os.path.join(REPO_ROOT, "matcher.py"), "."),
    (os.path.join(REPO_ROOT, "exporter.py"), "."),
    (os.path.join(REPO_ROOT, "config.json"), "."),
    (os.path.join(REPO_ROOT, ".env"), "."),
    (os.path.join(REPO_ROOT, "input", "beispiel_kontoauszug.csv"), "input"),
]

a = Analysis(
    [os.path.join(REPO_ROOT, "run_app.py")],
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
    name="PaperlessSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # zeigt Streamlit-Serverlog/Fehler - fuer ein lokales Tool hilfreich
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PaperlessSync",
)
