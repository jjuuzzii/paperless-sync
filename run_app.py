"""Haupteinstiegspunkt von Paperless Sync (PySide6/Qt-UI).

Setzt sys.path fuer die UI-Schicht (src/paperless_sync/ui_qt/) und die
darunterliegenden State-/Core-Module (src/paperless_sync/state/, .../core/)
und startet dann das Hauptfenster. Wird sowohl fuer den Start aus dem
Quellcode als auch als PyInstaller-Einstiegspunkt genutzt, siehe
build/desktop_app_qt.spec.

Start (Quellcode):  python run_app.py

Die alte Streamlit-Oberflaeche hat einen eigenen, aehnlich aufgebauten
Launcher: legacy/run_streamlit_legacy.py (siehe legacy/README.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "src"))

from paperless_sync.ui_qt.desktop_app_qt import main

if __name__ == "__main__":
    main()
