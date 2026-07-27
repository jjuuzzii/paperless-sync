"""Minimales i18n-Modul fuer die Qt-Oberflaeche (desktop_app_qt.py,
dialogs_qt.py): Uebersetzungs-Dict mit dem deutschen Original-String als
Schluessel - kein separates Schluessel-Vokabular noetig, der Aufrufort
bleibt lesbar deutscher Text. Deutsch ist die Referenzsprache (Rueckgabe
unveraendert), Englisch die einzige hinterlegte Uebersetzung.

Sprachwechsel (siehe SettingsDialog in dialogs_qt.py) greift erst nach
einem Neustart der App - Widget-Texte werden nur einmal beim Aufbau der
Sidebar/Karten/Dialoge gesetzt, ein Live-Umschalten aller bereits
existierenden Widgets ist bewusst nicht implementiert (deutlich groesserer
Aufwand fuer wenig Nutzen bei einer Einstellung, die man selten aendert)."""
from __future__ import annotations

_LANG = "de"


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang if lang in ("de", "en") else "de"


def get_language() -> str:
    return _LANG


def tr(text: str, **kwargs) -> str:
    """Gibt text (deutsches Original) oder dessen englische Uebersetzung
    zurueck, je nach aktueller Sprache. kwargs werden per str.format() IN
    das (ggf. uebersetzte) Template eingesetzt, nicht vorher - so
    funktionieren Platzhalter auch bei abweichender Wortstellung in der
    Uebersetzung."""
    template = _EN.get(text, text) if _LANG == "en" else text
    return template.format(**kwargs) if kwargs else template


_EN: dict[str, str] = {
    # --- Sidebar ---------------------------------------------------------
    "Einstellungen": "Settings",
    "1 · CSV-UPLOAD": "1 · CSV UPLOAD",
    "Datei auswählen": "Choose file",
    "Datei wählen": "Choose file",
    "Mit Paperless abgleichen": "Match with Paperless",
    "Gleiche ab ...": "Matching ...",
    "2 · MONAT": "2 · MONTH",
    "Paperless: nicht konfiguriert": "Paperless: not configured",
    "Paperless: wird geprüft...": "Paperless: checking...",
    "Paperless: Verbunden": "Paperless: Connected",
    "Paperless: nicht erreichbar": "Paperless: unreachable",
    "Exportordner: Bereit": "Export folder: Ready",
    "Exportordner: noch keine Auswahl": "Export folder: not selected yet",
    "ORDNER JETZT GENERIEREN": "GENERATE FOLDER NOW",
    "Keine Datei gewählt": "No file selected",

    # --- Hauptbereich / KPIs / Tabs --------------------------------------
    "ZUGEORDNETE BELEGE": "MATCHED RECEIPTS",
    "AKTION ERFORDERLICH": "ACTION REQUIRED",
    "MEHRFACH-MATCH": "MULTIPLE MATCHES",
    "ZU PRÜFEN": "TO REVIEW",
    "DUPLIKAT-VERDACHT": "SUSPECTED DUPLICATE",
    "TEILZAHLUNG?": "SPLIT PAYMENT?",
    "Erfolgreich": "Successful",
    "Unklar / Fehlt": "Unclear / Missing",

    # --- Karten (Erfolg/Aktion) -------------------------------------------
    "Noch keine zugeordneten Belege.": "No matched receipts yet.",
    "Alles zugeordnet! 🎉": "Everything matched! 🎉",
    "{remaining} weitere anzeigen": "show {remaining} more",
    "Automatisch zugeordnet": "Automatically matched",
    "{doc_count} Belege verknüpft": "{doc_count} receipts linked",
    "Hochgeladen": "Uploaded",
    "Rückgängig": "Undo",
    "Beleg": "Receipt",
    "Betrag tritt mehrfach auf - bitte manuell zuordnen.": "Amount occurs more than once - please assign manually.",
    "Vorschlag: {icon} {label} - ähnliche Buchung, bereits so getaggt": "Suggestion: {icon} {label} - similar transaction, already tagged this way",
    "Übernehmen": "Apply",
    "Aus Paperless wählen": "Choose from Paperless",
    "PDF ablegen": "Drop PDF here",
    "Keine Kandidaten geladen": "No candidates loaded",
    "Zuordnen": "Assign",
    "Kandidat": "candidate",
    "Kandidaten": "candidates",
    "anzeigen": "show",
    "ausblenden": "hide",
    "ohne Titel": "untitled",
    "kein Datum": "no date",
    "Sonstiges...": "Other...",
    "+ neuer Tag": "+ new tag",
    "Anwenden": "Apply",

    # --- Tag-Anzeigenamen (Wert/Schluessel selbst bleibt PRIVAT/EINZAHLUNG/
    # UMBUCHUNG - siehe BUILTIN_TAGS/TAG_ICONS in desktop_controller.py;
    # nur die angezeigte, kapitalisierte Form wird uebersetzt) -----------
    "Privat": "Private",
    "Einzahlung": "Deposit",
    "Umbuchung": "Transfer",

    # --- Dialoge / Meldungen ----------------------------------------------
    "Fehlgeschlagen": "Failed",
    "Sonstiges": "Other",
    "Neuer Tag-Name:": "New tag name:",
    "PDF wählen": "Choose PDF",
    "Upload fehlgeschlagen": "Upload failed",
    "Hinweis": "Note",
    "Bitte zuerst '🔍 Mit Paperless abgleichen' klicken, um die Dokumentliste zu laden.":
        "Please click '🔍 Match with Paperless' first to load the document list.",
    "CSV-Import fehlgeschlagen": "CSV import failed",
    "Bank-Kontoauszug wählen": "Choose bank statement",
    "Mapping fehlgeschlagen": "Mapping failed",
    "Abgleich fehlgeschlagen": "Matching failed",
    "Abgleich abgeschlossen": "Matching complete",
    "{count} Paperless-Dokumente geladen.": "{count} Paperless documents loaded.",
    "Kein Monat": "No month",
    "Bitte zuerst einen Monat wählen.": "Please select a month first.",
    "Export fehlgeschlagen": "Export failed",
    "Export fertig": "Export complete",
    "Ordner erstellt:\n{export_path}": "Folder created:\n{export_path}",

    # --- SettingsDialog -----------------------------------------------------
    "Zugangsdaten": "Credentials",
    "Firmenname": "Company name",
    "Paperless-URL": "Paperless URL",
    "Paperless-API-Token": "Paperless API token",
    "Optional: Client-Zertifikat (mTLS)": "Optional: client certificate (mTLS)",
    "kein Zertifikat": "no certificate",
    "Zertifikat-Passwort": "Certificate password",
    "Exportordner": "Export folder",
    "Wohin 'ORDNER JETZT GENERIEREN' die fertigen Monatsordner schreibt - z.B. ein geteilter "
    "OneDrive-/Steuerberater-Ordner. Standard = neben den App-Daten.":
        "Where 'GENERATE FOLDER NOW' writes the finished monthly folders - e.g. a shared "
        "OneDrive/accountant folder. Default = next to the app data.",
    "Standard": "Default",
    "Ordner wählen": "Choose folder",
    "Beleg-Erkennung": "Receipt amount detection",
    "Dateiname (Regex)": "Filename (regex)",
    "Paperless Custom Field": "Paperless custom field",
    "Regex-Muster (1. Gruppe = Betrag)": "Regex pattern (1st group = amount)",
    "Custom Field mit Rechnungsbetrag": "Custom field with invoice amount",
    "CSV-Spalten-Zuordnung": "CSV column mapping",
    "Gilt für das aktuell geladene CSV-Format. Absender/Empfänger wirkt sofort auf bereits "
    "geladene Buchungen, Datum/Betrag/Verwendungszweck erst beim nächsten Import dieser Datei.":
        "Applies to the currently loaded CSV format. Sender/recipient takes effect immediately on "
        "already loaded transactions; date/amount/purpose only on the next import of this file.",
    "Spalte für Datum": "Column for date",
    "Spalte für Betrag": "Column for amount",
    "Spalte für Verwendungszweck": "Column for purpose",
    "Spalte für Absender/Empfänger (optional)": "Column for sender/recipient (optional)",
    "Verwendungszweck: Rauschbegriffe ausblenden": "Purpose: hide noise terms",
    "Nur in der Kartenanzeige entfernt (Export/Zuordnung unverändert). IBAN/BIC werden immer automatisch entfernt.":
        "Only removed in the card display (export/matching unaffected). IBAN/BIC are always removed automatically.",
    "(keine)": "(none)",
    "z.B. MC Hauptkarte": "e.g. MC main card",
    "Eigene Tags verwalten": "Manage custom tags",
    "Löscht nur die Tag-Definition aus der Schnellauswahl/Sonstiges-Liste. Bereits getaggte Buchungen behalten ihren Tag.":
        "Only deletes the tag definition from the quick-select/other list. Already tagged transactions keep their tag.",
    "(keine eigenen Tags)": "(no custom tags)",
    "{name}  ({count}x verwendet)": "{name}  (used {count}x)",
    "Paperless-Erfolgs-Tag": "Paperless success tag",
    "Setzt in Paperless selbst einen Tag auf Dokumente, die erfolgreich einer Buchung zugeordnet "
    "wurden (automatischer Match, manuelle Verknüpfung, aufgelöster Mehrfach-Match). Gilt nicht "
    "für frisch hochgeladene PDFs (Paperless verarbeitet die erst asynchron).":
        "Sets a tag in Paperless itself on documents that were successfully matched to a transaction "
        "(automatic match, manual link, resolved multiple match). Does not apply to freshly uploaded "
        "PDFs (Paperless only processes those asynchronously).",
    "Aktiviert": "Enabled",
    "Tag-Name": "Tag name",
    "Datensicherung": "Backup",
    "Sichert Einstellungen, gelernte Tags, Paperless-Zugangsdaten und den aktuellen Arbeitsstand "
    "als ZIP - z.B. vor einem Rechnerwechsel.":
        "Backs up settings, learned tags, Paperless credentials, and the current work state as a "
        "ZIP - e.g. before switching computers.",
    "Backup erstellen": "Create backup",
    "Backup wiederherstellen": "Restore backup",
    "Sprache": "Language",
    "Sprache der Oberfläche. Wirkt erst nach einem Neustart der App.":
        "Interface language. Takes effect only after restarting the app.",
    "Neustart erforderlich, damit die Sprache wechselt.": "Restart required for the language change to take effect.",
    "Firmenlogo": "Company logo",
    "Eigenes Logo statt der Büroklammer oben links in der Seitenleiste. Nur PNG, quadratisch empfohlen.":
        "Custom logo instead of the paperclip icon at the top left of the sidebar. PNG only, square recommended.",
    "kein Logo": "no logo",
    "Logo wählen": "Choose logo",
    "Nur PNG-Dateien werden als Firmenlogo unterstützt.": "Only PNG files are supported as a company logo.",
    "Zurücksetzen": "Reset",
    "Verbindung testen": "Test connection",
    "Speichern": "Save",
    "Custom Fields nicht ladbar: {exc}": "Could not load custom fields: {exc}",
    "Backup speichern": "Save backup",
    "Backup-ZIP wählen": "Choose backup ZIP",
    "Backup fehlgeschlagen: {exc}": "Backup failed: {exc}",
    "Backup gespeichert: {path}": "Backup saved: {path}",
    "Überschreibt Einstellungen, Zugangsdaten und den aktuellen Arbeitsstand unwiderruflich.\n\n"
    "Die App wird danach beendet und muss manuell neu gestartet werden, damit der wiederhergestellte "
    "Stand geladen wird. Fortfahren?":
        "Irreversibly overwrites settings, credentials, and the current work state.\n\n"
        "The app will then close and must be restarted manually so the restored state is loaded. Continue?",
    "Wiederherstellung fehlgeschlagen: {exc}": "Restore failed: {exc}",
    "Das ZIP enthält keine bekannten Backup-Dateien (config.json / .env / session_state.json).":
        "The ZIP contains none of the known backup files (config.json / .env / session_state.json).",
    "Backup wiederhergestellt": "Backup restored",
    "Wiederhergestellt: {files}.\n\nDie App wird jetzt beendet - bitte manuell neu starten.":
        "Restored: {files}.\n\nThe app will now close - please restart it manually.",
    "Verbindung erfolgreich.": "Connection successful.",
    "Verbindung fehlgeschlagen - URL/Token prüfen.": "Connection failed - check URL/token.",
    "URL und ein echter Token sind Pflicht.": "URL and a real token are required.",

    # --- MappingDialog -------------------------------------------------
    "— keine —": "— none —",
    "CSV-Spalten zuordnen": "Map CSV columns",
    "Bestätigen": "Confirm",

    # --- PdfViewerDialog -------------------------------------------------
    "PDF konnte nicht geladen werden.": "Could not load PDF.",
    "Schliessen": "Close",
    "Vorschau": "Preview",
    "PDF wird geladen ...": "Loading PDF ...",
    "PDF konnte nicht geladen werden: {error}": "Could not load PDF: {error}",

    # --- DocumentSearchDialog -------------------------------------------
    "Beleg aus Paperless wählen": "Choose receipt from Paperless",
    "Mehrfachauswahl mit Strg/Umschalt-Klick möglich - z.B. bei einer Sammelabbuchung mit mehreren Einzelrechnungen.":
        "Multi-select with Ctrl/Shift-click possible - e.g. for a combined charge with several individual invoices.",
    "Suchen (Titel, Absender, Dateiname)...": "Search (title, sender, filename)...",
    "Wert für das Custom Field:": "Value for the custom field:",
    "Abbrechen": "Cancel",
    "Verknüpfen": "Link",
    "  (bereits verknüpft)": "  (already linked)",
}
