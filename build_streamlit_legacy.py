"""Baut die ALTE Streamlit-Weboberflaeche als .exe (Legacy, siehe app.py).

Die aktuelle Desktop-UI (CustomTkinter, siehe desktop_app.py) wird ueber
`build.py` gebaut - dieses Skript bleibt nur als Fallback erhalten, falls
die Streamlit-Variante noch gebraucht wird.

Voraussetzung:  pip install -r requirements-build.txt
Ausfuehren:      python build_streamlit_legacy.py
Ergebnis:        dist/PaperlessSync/PaperlessSync.exe

Aequivalenter Rohbefehl:
    pyinstaller paperless_sync.spec --clean --noconfirm
"""
import subprocess
import sys
from pathlib import Path


def main():
    project_dir = Path(__file__).resolve().parent
    spec_file = project_dir / "paperless_sync.spec"
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm"]
    print("Ausfuehrung:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=project_dir)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
