# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec-Datei fuer Paperless Sync (Streamlit-App als eigenstaendige
Windows-.exe, onedir-Modus - empfohlen fuer Streamlit-Apps).

Build-Befehl:
    pyinstaller paperless_sync.spec --clean --noconfirm

Ergebnis:
    dist/PaperlessSync/PaperlessSync.exe
    (config.json, .env, input/, export/ liegen im selben Ordner und werden
    zur Laufzeit dort gelesen/geschrieben - siehe config_manager.get_base_dir)
"""

from PyInstaller.utils.hooks import collect_all

block_cipher = None

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
# dem gebuendelten Verzeichnis lesen muss.
datas += [
    ("app.py", "."),
    ("config_manager.py", "."),
    ("csv_utils.py", "."),
    ("session_store.py", "."),
    ("paperless_client.py", "."),
    ("matcher.py", "."),
    ("exporter.py", "."),
    ("config.json", "."),
    (".env", "."),
    ("input/beispiel_kontoauszug.csv", "input"),
]

a = Analysis(
    ["run_app.py"],
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
