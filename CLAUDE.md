# CLAUDE.md

Anweisungen für die Arbeit an diesem Projekt (Claude Code / andere Assistenten).

## Projektüberblick

Paperless Sync ist ein Windows-Desktop-Tool (PySide6/Qt), das Banktransaktionen aus einer CSV-Datei automatisch mit Belegen in einer Paperless-ngx-Instanz abgleicht, damit jede Buchung fürs Finanzamt/den Steuerberater einen zugeordneten Beleg hat. Ungelöste Fälle werden manuell nachgetragen (PDF-Upload, bestehendes Paperless-Dokument verknüpfen, oder als "kein Beleg nötig" getaggt). Am Monatsende wird ein sauberer Export-Ordner generiert.

### Module

| Datei | Zuständigkeit |
|---|---|
| `desktop_app_qt.py` | Aktuelle Qt-UI: Hauptfenster, Sidebar, Karten-Rendering, Handler |
| `dialogs_qt.py` | Qt-Dialoge: Einstellungen, Dokumentenauswahl, PDF-Viewer, CSV-Spalten-Mapping |
| `desktop_state.py` | Anwendungszustand (`AppState`), framework-unabhängig |
| `desktop_controller.py` | Nutzeraktionen → Zustandsänderungen (`Controller`) |
| `matcher.py` | Baut Transaktionen aus der CSV, gleicht sie gegen Paperless-Dokumente ab |
| `paperless_client.py` | Dünner Paperless-ngx-REST-API-Wrapper |
| `exporter.py` | Erzeugt den monatlichen Export-Ordner für den Steuerberater |
| `csv_utils.py` | Encoding-/Trennzeichen-/Betrag-/Datums-Parsing |
| `config_manager.py` | Lädt/speichert `.env` und `config.json` |
| `backup.py` | ZIP-Backup/-Restore aller Nutzerdaten |
| `i18n.py` | Minimale DE/EN-Übersetzung der Oberfläche |
| `tx_status.py` | Zentrale Transaktions-Status-Werte (siehe unten) |
| `version.py` | Zentrale Versionsnummer (`__version__`) |

Ältere Oberflächen (CustomTkinter, Streamlit) sind nach `legacy/` archiviert, siehe `legacy/README.md` — nicht mehr aktiv gepflegt, Änderungen daran nur auf ausdrücklichen Wunsch.

Build: `python build/build.py` (PyInstaller + Inno-Setup-Installer, siehe `build/build.py`-Docstring für Details und ältere Build-Varianten).

## Arbeitsweise

- Vor Änderungen: den relevanten bestehenden Code zeigen und kurz erklären, was geändert wird.
- In kleinen, nachvollziehbaren Schritten vorgehen statt großer Sprünge.
- Nach jedem Schritt: kurz zusammenfassen und prüfen, dass die App fehlerfrei startet.
- Nach jedem erfolgreichen Schritt einzeln committen — nicht alles in einem Commit sammeln.
- Bei Unklarheiten nachfragen statt raten.
- Vor dem Löschen von Dateien immer nachfragen.

## Zentrale Transaktions-Status-Werte

Siehe `tx_status.py` (`TxStatus`-Enum). UI, Matching und Export nutzen dieselbe Liste, nicht mehr lose Strings:

- `MATCHED` — Beleg eindeutig zugeordnet (inkl. frisch hochgeladener PDFs, die noch keine Paperless-Dokument-ID haben)
- `TAGGED` — kein Beleg nötig (Privat/Einzahlung/Umbuchung/eigener Tag)
- `UNRESOLVED` — offen, noch nichts zugeordnet
- `MULTI_MATCH` — mehrere mögliche Belege mit gleichem Betrag, manuelle Auswahl nötig
- `DUPLICATE_SUSPECT` — möglicher Duplikat-Fall (noch ungenutzt, für spätere Erweiterung)
- `SPLIT_PAYMENT` — möglicher Teilzahlungs-Fall (noch ungenutzt, für spätere Erweiterung)
