# Legacy-Oberflächen

Die Dateien in diesem Ordner sind **archiviert** — sie werden nicht mehr aktiv gepflegt und dienen nur noch als Referenz. Die aktuelle, unterstützte Oberfläche ist die Qt-App im Repo-Root (`desktop_app_qt.py`, ggf. nach weiterer Umstrukturierung unter `src/paperless_sync/ui_qt/`).

## Enthaltene Varianten

- **`desktop_app.py`, `dialogs.py`, `theme.py`, `ctk_fixes.py`** — ältere Desktop-Oberfläche mit CustomTkinter. Wurde durch die Qt-Oberfläche abgelöst, u.a. wegen eines bekannten Scroll-Tearing-Bugs in `CTkScrollableFrame` (TomSchimansky/CustomTkinter#1510).
- **`app.py`, `build_streamlit_legacy.py`** — noch ältere Web-Oberfläche mit Streamlit.

## Warum noch im Repo?

Rein als Referenz für alte UI-Entscheidungen. Für neue Features oder Bugfixes ist ausschließlich die Qt-Oberfläche relevant — Änderungen an diesem Ordner nur auf ausdrücklichen Wunsch.

## Falls doch mal gebraucht

Diese Skripte importieren weiterhin die gemeinsamen Backend-Module (`config_manager`, `csv_utils`, `matcher`, `paperless_client`, `exporter`, `session_store`, `backup`, `desktop_state`, `desktop_controller`, `icon_utils`) aus dem übergeordneten Verzeichnis bzw. `src/paperless_sync/` (je nach Stand der Umstrukturierung) und fügen sich selbst per `sys.path`-Anpassung hinzu. Direkt ausführbar:

```
python legacy/desktop_app.py
streamlit run legacy/app.py
```

Es ist nicht garantiert, dass diese Varianten mit jedem zukünftigen Stand des Backends kompatibel bleiben.
