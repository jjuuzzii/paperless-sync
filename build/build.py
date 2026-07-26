"""Baut die eigenstaendige Windows-.exe fuer die Paperless Sync Desktop-App
(PySide6/Qt, siehe desktop_app_qt.py) via PyInstaller, und im Anschluss -
falls Inno Setup installiert ist - direkt den Windows-Installer daraus.

Voraussetzung:  pip install -r requirements-build.txt
Ausfuehren:      python build/build.py
Ergebnis:        dist/PaperlessSyncQt/PaperlessSyncQt.exe
                 installer_output/PaperlessSync-Setup-<Version>.exe (falls
                 Inno Setup gefunden wird, siehe build/installer.iss)

Aequivalente Rohbefehle (IM REPO-ROOT ausfuehren, nicht aus build/ heraus -
die Datenpfade in den .spec-Dateien sind relativ zum Aufrufverzeichnis, nicht
zum Spec-Datei-Pfad. --workpath ist Pflicht, sonst legt PyInstaller seinen
Zwischen-Cache standardmaessig selbst in einen Ordner "build/" - der waere
sonst identisch mit diesem Skript-Ordner):
    pyinstaller build/desktop_app_qt.spec --clean --noconfirm --workpath .pyinstaller-work
    "C:\\Program Files\\Inno Setup 7\\ISCC.exe" build/installer.iss

Die aeltere CustomTkinter-Variante (legacy/desktop_app.py, wegen eines
Scroll-Tearing-Bugs in CTkScrollableFrame durch die Qt-Version abgeloest,
siehe TomSchimansky/CustomTkinter#1510, archiviert - siehe legacy/README.md)
laesst sich bei Bedarf weiterhin mit
`pyinstaller build/desktop_app.spec --clean --noconfirm --workpath .pyinstaller-work`
bauen. Die alte Streamlit-Weboberflaeche (legacy/app.py) laesst sich
weiterhin mit `python legacy/build_streamlit_legacy.py` bauen.
"""
import subprocess
import sys
from pathlib import Path

INNO_SETUP_CANDIDATES = [
    r"C:\Program Files\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def main():
    build_dir = Path(__file__).resolve().parent
    repo_root = build_dir.parent
    spec_file = build_dir / "desktop_app_qt.spec"
    # Eigener Zwischen-Cache-Pfad, damit PyInstaller nicht seinen eigenen
    # Standard-Workpath "build/" verwendet - der ist jetzt durch diesen
    # Skript-/Spec-Ordner belegt (siehe .gitignore).
    workpath = repo_root / ".pyinstaller-work"
    cmd = [
        sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm",
        "--workpath", str(workpath),
    ]
    print("Ausfuehrung:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        sys.exit(result.returncode)

    iscc = next((p for p in INNO_SETUP_CANDIDATES if Path(p).exists()), None)
    if not iscc:
        print("Inno Setup (ISCC.exe) nicht gefunden - Installer-Schritt uebersprungen.")
        print("Portable .exe liegt unter dist/PaperlessSyncQt/.")
        return
    installer_cmd = [iscc, str(build_dir / "installer.iss")]
    print("Ausfuehrung:", " ".join(installer_cmd))
    result = subprocess.run(installer_cmd, cwd=repo_root)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
