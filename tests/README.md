# Tests

Noch keine automatisierten Tests vorhanden. Wenn welche entstehen, zuerst
für die beiden Module ohne Qt-/Netzwerk-Abhängigkeit, deren Logik am
fehleranfälligsten und am einfachsten isoliert zu testen ist:

- `src/paperless_sync/core/csv_utils.py` — Encoding-/Trennzeichen-Erkennung, Betrags-/Datums-Parsing (viele Bank-Exportformate, viele Edge Cases)
- `src/paperless_sync/core/matcher.py` — Transaktions-/Dokumenten-Abgleich (Kernlogik der App)

Testrunner: noch nicht festgelegt (z.B. `pytest`, dann in `requirements-build.txt` oder einer eigenen `requirements-test.txt` ergänzen).
