"""Launcher fuer die alte Streamlit-PyInstaller-.exe (siehe legacy/README.md):
startet Streamlit programmatisch mit legacy/app.py als Ziel-Skript. Wird
NICHT fuer `streamlit run` benoetigt - dort direkt legacy/app.py verwenden."""
from __future__ import annotations

import sys
from pathlib import Path


def resolve_app_path() -> str:
    if getattr(sys, "frozen", False):
        # Bleibt "app.py" (nicht "legacy/app.py"): paperless_sync.spec
        # buendelt die Datei per datas=[("legacy/app.py", "."), ...] flach
        # in den Bundle-Root, unabhaengig vom Quellcode-Pfad.
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        return str(base / "app.py")
    base = Path(__file__).resolve().parent
    return str(base / "legacy" / "app.py")


def main():
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        resolve_app_path(),
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
