# Tests

Automatisierte Tests mit `pytest`. Reine Backend-/Logik-Tests, keine Qt-UI-Tests
(dafür bräuchte es eine headless-Qt-Testinfrastruktur, siehe "Nicht getestet" unten).

## Ausführen

```
pip install -r requirements-dev.txt
pytest
```

`pytest.ini` setzt `pythonpath = src`, kein manueller `sys.path`-Eingriff nötig.
Alle Tests sind vollständig von echten Nutzerdaten/Netzwerk/Keyring isoliert
(siehe `conftest.py`: `tmp_app_dirs`/`app_state`/`controller`-Fixtures faken
`get_base_dir()`/den OS-Keyring; `test_backup.py` patcht zusätzlich
`get_enable_banking_key_path()`, das sonst immer den echten `%APPDATA%`-Pfad
auflöst, siehe dortige Kommentare).

## Abdeckung

| Datei | Modul | Schwerpunkt |
|---|---|---|
| `test_csv_utils.py` | `core/csv_utils.py` | Encoding-/Trennzeichen-Erkennung, Betrags-/Datums-Parsing (Einzelwert + spaltenweit) |
| `test_matcher.py` | `core/matcher.py` | Exakter/Toleranz-Abgleich, Duplikat-Erkennung, Teilzahlungs-Vorschläge, `build_transactions` |
| `test_exporter.py` | `core/exporter.py` | Alle CSV-Bausteine, Dateinamen-Schema, End-zu-Ende-Export inkl. Zip/Entpack-Pfadtest |
| `test_desktop_controller.py` | `state/desktop_controller.py` | CSV-Import/Mapping, Duplikat-Schutz bei Re-Import, Konto-Mismatch-Warnung, CSV-Archivierung, Tags, Beleg-Zuordnung |
| `test_backup.py` | `core/backup.py` | Backup mit/ohne Passwort, Restore-Roundtrip, falsches/fehlendes Passwort scheitert sauber |

## Nicht getestet (bewusst außerhalb dieser Suite)

- **Qt-UI** (`ui_qt/desktop_app_qt.py`, `ui_qt/dialogs_qt.py`): Rendering, Klick-Handler,
  Dialoge — bräuchte eine headless-Qt-Testinfrastruktur (`pytest-qt` o.ä.), aktuell nur
  manuell geprüft (siehe Verifikationsbericht).
- **Netzwerk-Clients** (`core/paperless_client.py`, `core/enable_banking_client.py`):
  echte HTTP-Aufrufe, in den übrigen Tests durchgehend über Fakes (`FakePaperlessClient`,
  `FakeClient`) ersetzt statt gegen einen echten Server getestet.
- **`core/secrets_manager.py` / `core/credential_store.py` / `core/encrypted_fallback.py`**:
  nur indirekt über den gefakten Keyring in `conftest.py`/`test_backup.py` exerciert
  (Keyring-Pfad). Der Passphrasen-Fallback-Pfad (`encrypted_fallback.py`, kein Keyring
  verfügbar) hat keine eigenen Tests.
- **`state/desktop_state.py`**: nur der Konstruktor (`AppState()`) läuft indirekt über die
  `app_state`/`controller`-Fixtures mit. Die Session-Persistenz/-Wiederherstellung
  (`persist_session`/`_restore_session`) sowie die Migrations-Helfer
  (`_backfill_status`, `_backfill_matched_docs`, `_backfill_candidate_docs`,
  `_backfill_display_numbers`) und die abgeleiteten Properties (`visible_transactions`,
  `success_transactions` usw.) haben keine dedizierten Tests.
- **`state/session_store.py`**: Fernet-Ver-/Entschlüsselung von `session_state.json`,
  ungetestet.
- **`core/config_manager.py`**: `load_config`/`_deep_merge_defaults` laufen indirekt über
  die `app_state`-Fixture mit; `load_env`/`save_env`/`seed_default_files`/`_resolve_secret`
  (Klartext-.env-Migration) haben keine eigenen Tests.
- **`core/i18n.py`**: ungetestet (reine Übersetzungstabelle).
