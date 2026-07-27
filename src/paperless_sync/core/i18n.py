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
    "Suchen (Verwendungszweck, Absender/Empfänger)...": "Search (purpose, sender/recipient)...",
    "Betrag (z.B. 50 oder 50-100)": "Amount (e.g. 50 or 50-100)",
    "Von (TT.MM.JJJJ)": "From (DD.MM.YYYY)",
    "Bis (TT.MM.JJJJ)": "To (DD.MM.YYYY)",
    "Keine Treffer für die aktuellen Filter.": "No matches for the current filters.",
    "{shown} von {total} Buchungen sichtbar (Filter: {label})": "{shown} of {total} transactions visible (filter: {label})",
    "Tastatur: ↑/↓ zum Navigieren, Strg+↓ springt zum nächsten offenen Posten": "Keyboard: ↑/↓ to navigate, Ctrl+↓ jumps to the next open item",

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

    # --- EnableBankingSetupWizard -----------------------------------------
    "Bank-Import einrichten": "Set up bank import",
    "Schritt {current} von {total}": "Step {current} of {total}",
    "Zurück": "Back",
    "Weiter": "Next",
    "Fertig": "Done",
    "Kein Schlüssel gefunden": "No key found",
    "Bitte zuerst die .pem-Datei in den angezeigten Ordner legen und 'Prüfen' klicken.":
        "Please place the .pem file in the shown folder first and click 'Check'.",
    "Application-ID fehlt": "Application ID missing",
    "Bitte eine Application-ID eintragen.": "Please enter an Application ID.",
    "Bank-Import über Enable Banking einrichten": "Set up bank import via Enable Banking",
    "Enable Banking ist eine Open-Banking-Schnittstelle, über die Kontobewegungen direkt von deiner Bank "
    "abgerufen werden können - als Alternative zum manuellen CSV-Export. Du registrierst dafür eine eigene, "
    "kostenlose Anwendung mit eigenem Zugang. Die Einrichtung dauert ca. 5 Minuten und ist einmalig.":
        "Enable Banking is an open banking interface that lets you fetch transactions directly from your "
        "bank - as an alternative to manual CSV export. You register your own free application with your "
        "own access. Setup takes about 5 minutes and is a one-time step.",
    "enablebanking.com/sign-in öffnen": "Open enablebanking.com/sign-in",
    "Anwendung registrieren": "Register application",
    "Im Enable-Banking-Control-Panel unter 'Applications' auf 'Add a new application' klicken und folgende Werte eintragen:":
        "In the Enable Banking control panel, under 'Applications', click 'Add a new application' and enter the following values:",
    "Environment": "Environment",
    "Redirect URL": "Redirect URL",
    "Application Name": "Application Name",
    "frei wählbar, z.B. 'Paperless Sync'": "free choice, e.g. 'Paperless Sync'",
    "💡 Beim Anlegen wird automatisch ein privater Schlüssel als .pem-Datei heruntergeladen - Download-Fenster "
    "offen lassen, wird im nächsten Schritt gebraucht.":
        "💡 A private key is automatically downloaded as a .pem file when you create the application - leave "
        "the download window open, you'll need it in the next step.",
    "Kopieren": "Copy",
    "Schlüssel ablegen": "Place key file",
    "Verschiebe die heruntergeladene .pem-Datei in diesen Ordner.": "Move the downloaded .pem file into this folder.",
    "Ordner öffnen": "Open folder",
    "Prüfen": "Check",
    "Schlüssel gefunden.": "Key found.",
    "Noch keine .pem-Datei gefunden.": "No .pem file found yet.",
    "Application-ID eintragen": "Enter Application ID",
    "Zu finden im Enable-Banking-Control-Panel unter deiner Anwendung ('Application ID').":
        "Found in the Enable Banking control panel under your application ('Application ID').",
    "Eigenes Konto verknüpfen": "Link your own account",
    "Im Enable-Banking-Control-Panel bei deiner Anwendung das eigene Konto whitelisten (im "
    "'restricted production'-Modus ist dafür kein separater Vertrag nötig, solange die Anwendung nur von dir "
    "selbst genutzt wird).":
        "In the Enable Banking control panel, whitelist your own account for your application (in "
        "'restricted production' mode this needs no separate contract as long as only you use the application).",
    "Enable Banking Control Panel öffnen": "Open Enable Banking control panel",
    "Testet den kompletten Ablauf einmal: Bank-Login im Browser, danach Abruf der letzten Kontobewegungen als Vorschau.":
        "Tests the whole flow once: bank login in the browser, then fetches recent transactions as a preview.",
    "Land:": "Country:",
    "Warte auf Bank-Login...": "Waiting for bank login...",
    "Verbindung erfolgreich!": "Connection successful!",
    "(keine Buchungen im Standardzeitraum)": "(no transactions in the default period)",
    "Mögliche Ursache: Application-ID falsch oder Konto noch nicht verknüpft.":
        "Possible cause: wrong Application ID or account not linked yet.",

    # --- EnableBankingDateRangeDialog --------------------------------------
    "Zeitraum wählen": "Choose date range",
    "Zeitraum für den Bank-Import": "Date range for the bank import",
    "Aktueller Monat": "Current month",
    "Letzte 30 Tage": "Last 30 days",
    "Letzte 90 Tage": "Last 90 days",
    "Alle verfügbaren Buchungen": "All available transactions",
    "Von:": "From:",
    "Bis:": "To:",
    "Manche Banken begrenzen den abrufbaren Zeitraum, unabhängig von deiner Auswahl hier.":
        "Some banks limit the retrievable period, regardless of your selection here.",
    "Ungültiges Datum": "Invalid date",
    "Bitte gültige Datumswerte im Format TT.MM.JJJJ eingeben.": "Please enter valid dates in DD.MM.YYYY format.",
    "Ungültiger Zeitraum": "Invalid date range",
    "Das Von-Datum muss vor dem Bis-Datum liegen.": "The from-date must be before the to-date.",

    # --- Bank-Import-Button (Sidebar) --------------------------------------
    "Von Bank importieren": "Import from bank",
    "Öffnet den Bank-Login im Browser – bei jedem Import erneut nötig.":
        "Opens the bank login in your browser - required again for every import.",

    # --- SettingsDialog: Bank-Import-Sektion -------------------------------
    "Bank-Import (Enable Banking)": "Bank import (Enable Banking)",
    "Direkter Import von Kontobewegungen ueber deine eigene Enable-Banking-Anwendung, als Alternative zum manuellen CSV-Export.":
        "Direct import of transactions via your own Enable Banking application, as an alternative to manual CSV export.",
    "Application-ID hinterlegt": "Application ID on file",
    "Schlüssel gefunden": "Key found",
    "Letzter erfolgreicher Import": "Last successful import",
    "noch nie": "never",
    "Einrichtungsassistent starten": "Start setup wizard",
    "Verbindung zurücksetzen": "Reset connection",
    "Löscht Application-ID, Redirect-URL und Schlüssel-Pfad aus den Einstellungen. Die .pem-Datei selbst wird "
    "NICHT automatisch gelöscht. Wirklich zurücksetzen?":
        "Deletes the Application ID, redirect URL, and key path from the settings. The .pem file itself is "
        "NOT deleted automatically. Really reset?",
    "Application-ID ändern": "Change Application ID",
    "Neue Application-ID:": "New Application ID:",
    "Schlüssel-Pfad ändern": "Change key path",
    "Schlüssel-Datei wählen": "Choose key file",
}
