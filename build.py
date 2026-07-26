"""Baut die eigenstaendige Windows-.exe fuer die Paperless Sync Desktop-App
(PySide6/Qt, siehe desktop_app_qt.py) via PyInstaller, und im Anschluss -
falls Inno Setup installiert ist - direkt den Windows-Installer daraus.

Voraussetzung:  pip install -r requirements-build.txt
Ausfuehren:      python build.py
Ergebnis:        dist/PaperlessSyncQt/PaperlessSyncQt.exe
                 installer_output/PaperlessSync-Setup-<Version>.exe (falls
                 Inno Setup gefunden wird, siehe installer.iss)

Aequivalente Rohbefehle:
    pyinstaller desktop_app_qt.spec --clean --noconfirm
    "C:\\Program Files\\Inno Setup 7\\ISCC.exe" installer.iss

Die aeltere CustomTkinter-Variante (desktop_app.py, wegen eines
Scroll-Tearing-Bugs in CTkScrollableFrame durch die Qt-Version abgeloest,
siehe TomSchimansky/CustomTkinter#1510) laesst sich bei Bedarf weiterhin mit
`pyinstaller desktop_app.spec --clean --noconfirm` bauen. Die alte
Streamlit-Weboberflaeche (app.py) laesst sich weiterhin mit
`python build_streamlit_legacy.py` bauen.
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
    project_dir = Path(__file__).resolve().parent
    spec_file = project_dir / "desktop_app_qt.spec"
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm"]
    print("Ausfuehrung:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=project_dir)
    if result.returncode != 0:
        sys.exit(result.returncode)

    iscc = next((p for p in INNO_SETUP_CANDIDATES if Path(p).exists()), None)
    if not iscc:
        print("Inno Setup (ISCC.exe) nicht gefunden - Installer-Schritt uebersprungen.")
        print("Portable .exe liegt unter dist/PaperlessSyncQt/.")
        return
    installer_cmd = [iscc, str(project_dir / "installer.iss")]
    print("Ausfuehrung:", " ".join(installer_cmd))
    result = subprocess.run(installer_cmd, cwd=project_dir)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
