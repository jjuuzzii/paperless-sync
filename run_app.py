"""Launcher fuer die PyInstaller-.exe: startet Streamlit programmatisch mit
app.py als Ziel-Skript. Wird NICHT fuer `streamlit run` benoetigt - dort
direkt app.py verwenden."""
from __future__ import annotations

import sys
from pathlib import Path


def resolve_app_path() -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return str(base / "app.py")


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
