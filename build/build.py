"""Baut die eigenstaendige Desktop-App (PySide6/Qt, Einstiegspunkt
run_app.py, UI-Code in src/paperless_sync/ui_qt/) via PyInstaller - erkennt
die aktuelle Plattform und waehlt automatisch den passenden Weg:

  Windows: PyInstaller (onedir) + Inno Setup (falls installiert)
  macOS:   PyInstaller (.app-Bundle via BUNDLE()) - UNGETESTET, siehe
           build/desktop_app_qt_macos.spec
  Linux:   PyInstaller (onefile, einzelne ausfuehrbare Datei) - UNGETESTET,
           siehe build/desktop_app_qt_linux.spec

Voraussetzung:  pip install -r requirements-build.txt
Ausfuehren:      python build/build.py
Ergebnis:
    Windows: dist/PaperlessSyncQt/PaperlessSyncQt.exe
             installer_output/PaperlessSync-Setup-<Version>.exe (falls
             Inno Setup gefunden wird, siehe build/installer.iss)
    macOS:   dist/PaperlessSyncQt.app
    Linux:   dist/PaperlessSyncQt

Aequivalente Rohbefehle (IM REPO-ROOT ausfuehren, nicht aus build/ heraus -
die Datenpfade in den .spec-Dateien sind relativ zum Aufrufverzeichnis, nicht
zum Spec-Datei-Pfad. --workpath ist Pflicht, sonst legt PyInstaller seinen
Zwischen-Cache standardmaessig selbst in einen Ordner "build/" - der waere
sonst identisch mit diesem Skript-Ordner):
    pyinstaller build/desktop_app_qt.spec --clean --noconfirm --workpath .pyinstaller-work
    "C:\\Program Files\\Inno Setup 7\\ISCC.exe" build/installer.iss
    pyinstaller build/desktop_app_qt_macos.spec --clean --noconfirm --workpath .pyinstaller-work
    pyinstaller build/desktop_app_qt_linux.spec --clean --noconfirm --workpath .pyinstaller-work

Die aeltere CustomTkinter-Variante (legacy/desktop_app.py, wegen eines
Scroll-Tearing-Bugs in CTkScrollableFrame durch die Qt-Version abgeloest,
siehe TomSchimansky/CustomTkinter#1510, archiviert - siehe legacy/README.md)
laesst sich bei Bedarf weiterhin mit
`pyinstaller build/desktop_app.spec --clean --noconfirm --workpath .pyinstaller-work`
bauen. Die alte Streamlit-Weboberflaeche (legacy/app.py) laesst sich
weiterhin mit `python legacy/build_streamlit_legacy.py` bauen.
"""
import platform
import subprocess
import sys
from pathlib import Path

INNO_SETUP_CANDIDATES = [
    r"C:\Program Files\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def _run_pyinstaller(spec_file: Path, repo_root: Path, workpath: Path) -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm",
        "--workpath", str(workpath),
    ]
    print("Ausfuehrung:", " ".join(cmd))
    return subprocess.run(cmd, cwd=repo_root).returncode


def build_windows(build_dir: Path, repo_root: Path, workpath: Path) -> int:
    returncode = _run_pyinstaller(build_dir / "desktop_app_qt.spec", repo_root, workpath)
    if returncode != 0:
        return returncode

    iscc = next((p for p in INNO_SETUP_CANDIDATES if Path(p).exists()), None)
    if not iscc:
        print("Inno Setup (ISCC.exe) nicht gefunden - Installer-Schritt uebersprungen.")
        print("Portable .exe liegt unter dist/PaperlessSyncQt/.")
        return 0
    installer_cmd = [iscc, str(build_dir / "installer.iss")]
    print("Ausfuehrung:", " ".join(installer_cmd))
    return subprocess.run(installer_cmd, cwd=repo_root).returncode


def build_macos(build_dir: Path, repo_root: Path, workpath: Path) -> int:
    returncode = _run_pyinstaller(build_dir / "desktop_app_qt_macos.spec", repo_root, workpath)
    if returncode == 0:
        print("Ergebnis: dist/PaperlessSyncQt.app")
    return returncode


def build_linux(build_dir: Path, repo_root: Path, workpath: Path) -> int:
    returncode = _run_pyinstaller(build_dir / "desktop_app_qt_linux.spec", repo_root, workpath)
    if returncode == 0:
        print("Ergebnis: dist/PaperlessSyncQt (einzelne ausfuehrbare Datei)")
    return returncode


def main():
    build_dir = Path(__file__).resolve().parent
    repo_root = build_dir.parent
    # Eigener Zwischen-Cache-Pfad, damit PyInstaller nicht seinen eigenen
    # Standard-Workpath "build/" verwendet - der ist jetzt durch diesen
    # Skript-/Spec-Ordner belegt (siehe .gitignore).
    workpath = repo_root / ".pyinstaller-work"

    system = platform.system()
    if system == "Windows":
        returncode = build_windows(build_dir, repo_root, workpath)
    elif system == "Darwin":
        print("macOS erkannt - dieser Build-Weg ist UNGETESTET (siehe desktop_app_qt_macos.spec).")
        returncode = build_macos(build_dir, repo_root, workpath)
    elif system == "Linux":
        print("Linux erkannt - dieser Build-Weg ist UNGETESTET (siehe desktop_app_qt_linux.spec).")
        returncode = build_linux(build_dir, repo_root, workpath)
    else:
        print(f"Unbekannte Plattform {system!r} - kein Build-Weg definiert.")
        sys.exit(1)

    sys.exit(returncode)


if __name__ == "__main__":
    main()
