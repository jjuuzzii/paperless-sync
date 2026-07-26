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
    "Datei auswaehlen": "Choose file",
    "Datei waehlen": "Choose file",
    "Mit Paperless abgleichen": "Match with Paperless",
    "Gleiche ab ...": "Matching ...",
    "2 · MONAT": "2 · MONTH",
    "Paperless: nicht konfiguriert": "Paperless: not configured",
    "Paperless: wird geprueft...": "Paperless: checking...",
    "Paperless: Verbunden": "Paperless: Connected",
    "Paperless: nicht erreichbar": "Paperless: unreachable",
    "Exportordner: Bereit": "Export folder: Ready",
    "Exportordner: noch keine Auswahl": "Export folder: not selected yet",
    "ORDNER JETZT GENERIEREN": "GENERATE FOLDER NOW",
    "Keine Datei gewaehlt": "No file selected",

    # --- Hauptbereich / KPIs / Tabs --------------------------------------
    "ZUGEORDNETE BELEGE": "MATCHED RECEIPTS",
    "AKTION ERFORDERLICH": "ACTION REQUIRED",
    "MEHRFACH-MATCH": "MULTIPLE MATCHES",
    "Erfolgreich": "Successful",
    "Unklar / Fehlt": "Unclear / Missing",

    # --- Karten (Erfolg/Aktion) -------------------------------------------
    "Noch keine zugeordneten Belege.": "No matched receipts yet.",
    "Alles zugeordnet! 🎉": "Everything matched! 🎉",
    "{remaining} weitere anzeigen": "show {remaining} more",
    "Automatisch zugeordnet": "Automatically matched",
    "{doc_count} Belege verknuepft": "{doc_count} receipts linked",
    "Hochgeladen": "Uploaded",
    "Rueckgaengig": "Undo",
    "Beleg": "Receipt",
    "Betrag tritt mehrfach auf - bitte manuell zuordnen.": "Amount occurs more than once - please assign manually.",
    "Vorschlag: {icon} {label} - aehnliche Buchung, bereits so getaggt": "Suggestion: {icon} {label} - similar transaction, already tagged this way",
    "Uebernehmen": "Apply",
    "Aus Paperless waehlen": "Choose from Paperless",
    "PDF ablegen": "Drop PDF here",
    "Keine Kandidaten geladen": "No candidates loaded",
    "Zuordnen": "Assign",
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
    "PDF waehlen": "Choose PDF",
    "Upload fehlgeschlagen": "Upload failed",
    "Hinweis": "Note",
    "Bitte zuerst '🔍 Mit Paperless abgleichen' klicken, um die Dokumentliste zu laden.":
        "Please click '🔍 Match with Paperless' first to load the document list.",
    "CSV-Import fehlgeschlagen": "CSV import failed",
    "Bank-Kontoauszug waehlen": "Choose bank statement",
    "Mapping fehlgeschlagen": "Mapping failed",
    "Abgleich fehlgeschlagen": "Matching failed",
    "Abgleich abgeschlossen": "Matching complete",
    "{count} Paperless-Dokumente geladen.": "{count} Paperless documents loaded.",
    "Kein Monat": "No month",
    "Bitte zuerst einen Monat waehlen.": "Please select a month first.",
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
    "Ordner waehlen": "Choose folder",
    "Beleg-Erkennung": "Receipt amount detection",
    "Dateiname (Regex)": "Filename (regex)",
    "Paperless Custom Field": "Paperless custom field",
    "Regex-Muster (1. Gruppe = Betrag)": "Regex pattern (1st group = amount)",
    "Custom Field mit Rechnungsbetrag": "Custom field with invoice amount",
    "CSV-Spalten-Zuordnung": "CSV column mapping",
    "Gilt fuer das aktuell geladene CSV-Format. Absender/Empfaenger wirkt sofort auf bereits "
    "geladene Buchungen, Datum/Betrag/Verwendungszweck erst beim naechsten Import dieser Datei.":
        "Applies to the currently loaded CSV format. Sender/recipient takes effect immediately on "
        "already loaded transactions; date/amount/purpose only on the next import of this file.",
    "Spalte fuer Datum": "Column for date",
    "Spalte fuer Betrag": "Column for amount",
    "Spalte fuer Verwendungszweck": "Column for purpose",
    "Spalte fuer Absender/Empfaenger (optional)": "Column for sender/recipient (optional)",
    "Verwendungszweck: Rauschbegriffe ausblenden": "Purpose: hide noise terms",
    "Nur in der Kartenanzeige entfernt (Export/Zuordnung unveraendert). IBAN/BIC werden immer automatisch entfernt.":
        "Only removed in the card display (export/matching unaffected). IBAN/BIC are always removed automatically.",
    "(keine)": "(none)",
    "z.B. MC Hauptkarte": "e.g. MC main card",
    "Eigene Tags verwalten": "Manage custom tags",
    "Loescht nur die Tag-Definition aus der Schnellauswahl/Sonstiges-Liste. Bereits getaggte Buchungen behalten ihren Tag.":
        "Only deletes the tag definition from the quick-select/other list. Already tagged transactions keep their tag.",
    "(keine eigenen Tags)": "(no custom tags)",
    "{name}  ({count}x verwendet)": "{name}  (used {count}x)",
    "Paperless-Erfolgs-Tag": "Paperless success tag",
    "Setzt in Paperless selbst einen Tag auf Dokumente, die erfolgreich einer Buchung zugeordnet "
    "wurden (automatischer Match, manuelle Verknuepfung, aufgeloester Mehrfach-Match). Gilt nicht "
    "fuer frisch hochgeladene PDFs (Paperless verarbeitet die erst asynchron).":
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
    "Sprache der Oberflaeche. Wirkt erst nach einem Neustart der App.":
        "Interface language. Takes effect only after restarting the app.",
    "Neustart erforderlich, damit die Sprache wechselt.": "Restart required for the language change to take effect.",
    "Firmenlogo": "Company logo",
    "Eigenes Logo statt des Standard-Symbols oben in der Seitenleiste und als Fenstersymbol. "
    "Empfohlen: quadratisches PNG.":
        "Custom logo instead of the default icon at the top of the sidebar and as the window icon. "
        "Recommended: square PNG.",
    "kein Logo": "no logo",
    "Logo waehlen": "Choose logo",
    "Zuruecksetzen": "Reset",
    "Verbindung testen": "Test connection",
    "Speichern": "Save",
    "Custom Fields nicht ladbar: {exc}": "Could not load custom fields: {exc}",
    "Backup speichern": "Save backup",
    "Backup-ZIP waehlen": "Choose backup ZIP",
    "Backup fehlgeschlagen: {exc}": "Backup failed: {exc}",
    "Backup gespeichert: {path}": "Backup saved: {path}",
    "Ueberschreibt Einstellungen, Zugangsdaten und den aktuellen Arbeitsstand unwiderruflich.\n\n"
    "Die App wird danach beendet und muss manuell neu gestartet werden, damit der wiederhergestellte "
    "Stand geladen wird. Fortfahren?":
        "Irreversibly overwrites settings, credentials, and the current work state.\n\n"
        "The app will then close and must be restarted manually so the restored state is loaded. Continue?",
    "Wiederherstellung fehlgeschlagen: {exc}": "Restore failed: {exc}",
    "Das ZIP enthaelt keine bekannten Backup-Dateien (config.json / .env / session_state.json).":
        "The ZIP contains none of the known backup files (config.json / .env / session_state.json).",
    "Backup wiederhergestellt": "Backup restored",
    "Wiederhergestellt: {files}.\n\nDie App wird jetzt beendet - bitte manuell neu starten.":
        "Restored: {files}.\n\nThe app will now close - please restart it manually.",
    "Verbindung erfolgreich.": "Connection successful.",
    "Verbindung fehlgeschlagen - URL/Token pruefen.": "Connection failed - check URL/token.",
    "URL und ein echter Token sind Pflicht.": "URL and a real token are required.",

    # --- MappingDialog -------------------------------------------------
    "— keine —": "— none —",
    "CSV-Spalten zuordnen": "Map CSV columns",
    "Bestaetigen": "Confirm",

    # --- DocumentSearchDialog -------------------------------------------
    "Beleg aus Paperless waehlen": "Choose receipt from Paperless",
    "Mehrfachauswahl mit Strg/Umschalt-Klick moeglich - z.B. bei einer Sammelabbuchung mit mehreren Einzelrechnungen.":
        "Multi-select with Ctrl/Shift-click possible - e.g. for a combined charge with several individual invoices.",
    "Suchen (Titel, Absender, Dateiname)...": "Search (title, sender, filename)...",
    "Wert fuer das Custom Field:": "Value for the custom field:",
    "Abbrechen": "Cancel",
    "Verknuepfen": "Link",
    "  (bereits verknuepft)": "  (already linked)",
}
