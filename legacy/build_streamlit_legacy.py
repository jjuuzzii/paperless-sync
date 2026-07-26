"""Baut die ALTE Streamlit-Weboberflaeche als .exe (ARCHIVIERT/LEGACY,
siehe legacy/README.md und app.py in diesem Ordner).

Die aktuelle Oberflaeche (PySide6/Qt, siehe desktop_app_qt.py) wird ueber
`build/build.py` gebaut - dieses Skript bleibt nur als Fallback erhalten,
falls die Streamlit-Variante noch gebraucht wird.

Voraussetzung:  pip install -r requirements-build.txt -r legacy/requirements-legacy.txt
Ausfuehren:      python legacy/build_streamlit_legacy.py
Ergebnis:        dist/PaperlessSync/PaperlessSync.exe

Aequivalenter Rohbefehl (im Repo-Root ausfuehren):
    pyinstaller build/paperless_sync.spec --clean --noconfirm --workpath .pyinstaller-work
"""
import subprocess
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parent.parent
    spec_file = repo_root / "build" / "paperless_sync.spec"
    workpath = repo_root / ".pyinstaller-work"
    cmd = [
        sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm",
        "--workpath", str(workpath),
    ]
    print("Ausfuehrung:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=repo_root)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
