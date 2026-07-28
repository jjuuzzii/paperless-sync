"""Erzeugt den steuerberater-fertigen Export-Ordner aus den abgeglichenen
Transaktionen eines Monats. Ueber alle Monate identische Struktur, durchweg
relative Pfade (siehe _relative_receipt_paths), damit der ganze Monatsordner
1:1 als ZIP verschickt werden kann, ohne dass Referenzen in
00_Uebersicht.csv brechen:

<Monat>/
    00_Uebersicht.csv              Alle Buchungen des Monats, Master-Index
    01_Belege_zugeordnet/          PDFs, umbenannt nach Datum_Betrag_Empfaenger
    02_Ohne_Beleg_getaggt/
        notizen_getaggte_buchungen.csv   Getaggte Buchungen ohne Beleg (PRIVAT/EINZAHLUNG/UMBUCHUNG/eigene Tags)
    03_Kontoauszug_gefiltert.csv   Gefilterte Kopie der Original-Bank-CSV
    04_Offene_Posten.csv           UNRESOLVED/MULTI_MATCH/DUPLICATE_SUSPECT/SPLIT_PAYMENT
    05_Einzahlungen_Deposit.csv    Nur EINZAHLUNG-getaggte Buchungen
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date as date_cls
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .tx_status import TxStatus, OPEN_STATUSES, LABELS_DE, label_de

_MONTH_NAMES_DE = {
    1: "Januar", 2: "Februar", 3: "Maerz", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}
# Deutsche Monatsnamen MIT Umlaut fuer PDF-/Textanzeige - anders als
# _MONTH_NAMES_DE (ASCII-transliteriert fuer Dateinamen, siehe dort).
_MONTH_NAMES_DISPLAY = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}
# Kehrt tx_status.LABELS_DE um (deutscher Anzeige-Text -> TxStatus) - fuer
# die Jahresauswertung (00_Offene_Posten_Jahr.csv/00_Jahresuebersicht.pdf),
# die bewusst die bereits geschriebene 00_Jahresuebersicht.csv zurueckliest
# (siehe dortigen Docstring) statt Status ein zweites Mal aus den
# Transaktionen zu berechnen - dort steht nur noch der Anzeige-Text, dieser
# Rueckweg macht ihn wieder maschinenlesbar (fuer Zaehlung/Einfaerbung).
_LABEL_TO_STATUS = {label: status for status, label in LABELS_DE.items()}

_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_IBAN_RE = re.compile(r"IBAN:?\s*[A-Z]{2}\d{2}[A-Z0-9]{10,30}", re.IGNORECASE)
_BIC_RE = re.compile(r"BIC:?\s*[A-Z0-9]{8,11}", re.IGNORECASE)

RECIPIENT_MAX_LENGTH = 40

# Helle Hintergrundfarben je offenem Status fuer die PDF-Zusammenfassung -
# selber Farbcode-Sinn wie die Qt-UI-Badges (STATUS_BADGES in
# desktop_app_qt.py: amber/lila/tuerkis), hier als helle Tint-Variante,
# die auf weissem PDF-Papier noch gut lesbar bleibt. UNRESOLVED hat dort
# kein eigenes Badge (nur roter Rahmen) - hier trotzdem ein helles Rot,
# damit es sich in der Zusammenfassungstabelle sichtbar von den anderen
# drei abhebt.
_STATUS_COLORS_PDF = {
    TxStatus.UNRESOLVED: colors.HexColor("#f8d7da"),
    TxStatus.MULTI_MATCH: colors.HexColor("#ffe8b3"),
    TxStatus.DUPLICATE_SUSPECT: colors.HexColor("#e2d4f7"),
    TxStatus.SPLIT_PAYMENT: colors.HexColor("#c9f2e8"),
}


def month_folder_name(month_str: str) -> str:
    """'2026-01' -> '2026-01_Januar' - ueber alle Monate identisches Schema
    (Anforderung 10), damit die zwoelf Jahresordner sortiert und
    vergleichbar nebeneinander liegen."""
    year, month = month_str.split("-")
    return f"{month_str}_{_MONTH_NAMES_DE[int(month)]}"


def count_open_items(transactions: list[dict], month_str: str) -> int:
    """Anzahl Buchungen des Monats mit Klaerungsbedarf (siehe
    tx_status.OPEN_STATUSES) - fuer die Warnung vor dem Export
    (Anforderung 9). Wird VOR generate_export von der UI aufgerufen, nicht
    von generate_export selbst - der Export soll trotz offener Posten
    moeglich bleiben."""
    return sum(
        1
        for t in transactions
        if t["date"].strftime("%Y-%m") == month_str and t["status"] in OPEN_STATUSES
    )


def current_fiscal_year_start(fiscal_config: dict, today: date_cls | None = None) -> int:
    """Kalenderjahr, in dem das AKTUELL LAUFENDE Geschaeftsjahr begonnen hat
    (siehe get_fiscal_year_months) - Vorbelegung fuer den Jahresexport-
    Dialog. Bei Kalenderjahr einfach das laufende Jahr. Bei einem
    abweichenden Wirtschaftsjahr: liegt der aktuelle Monat VOR dem
    Startmonat, hat das laufende Geschaeftsjahr bereits im Vorjahr begonnen
    (z.B. Start Juli, heute Maerz 2026 -> laufendes Geschaeftsjahr ist
    2025/2026, Rueckgabe 2025)."""
    today = today or date_cls.today()
    if fiscal_config.get("calendar_year", True):
        return today.year
    start_month = int(fiscal_config.get("start_month", 7))
    return today.year if today.month >= start_month else today.year - 1


def fiscal_year_open_items_summary(transactions: list[dict], month_strs: list[str]) -> tuple[int, list[str]]:
    """Gesamtzahl offener Posten ueber alle Monate eines Geschaeftsjahres
    (siehe get_fiscal_year_months) plus die Liste der betroffenen Monate
    als deutsche Anzeige-Namen (z.B. 'Januar 2026') - fuer die Vorab-
    Warnung vor dem Jahresexport, analog zu count_open_items() auf
    Monatsebene."""
    total = 0
    months_with_open_items = []
    for month_str in month_strs:
        count = count_open_items(transactions, month_str)
        if count:
            total += count
            months_with_open_items.append(f"{_MONTH_NAMES_DISPLAY[int(month_str[5:7])]} {month_str[:4]}")
    return total, months_with_open_items


def _sanitize_filename(name: str, fallback: str = "beleg", max_length: int | None = None) -> str:
    name = _ILLEGAL_FILENAME_CHARS.sub("_", (name or "").strip())
    name = name.strip(" .")
    if max_length is not None:
        name = name[:max_length].strip(" _-")
    return name or fallback


def _recipient_name(tx: dict) -> str:
    """Empfaenger fuer den Beleg-Dateinamen: bevorzugt die Gegenpartei-Spalte
    (falls gemappt), sonst der Verwendungszweck - IBAN/BIC-Rauschen entfernt
    (das ist im Verwendungszweck nie hilfreich, siehe UI), auf sinnvolle
    Laenge gekuerzt."""
    raw = (tx.get("counterparty") or "").strip() or tx["purpose"]
    raw = _IBAN_RE.sub("", raw)
    raw = _BIC_RE.sub("", raw)
    raw = re.sub(r"\s{2,}", " ", raw).strip(" -,")
    return _sanitize_filename(raw, fallback="Unbekannt", max_length=RECIPIENT_MAX_LENGTH)


def _dedupe_filename(base_name: str, ext: str, used_names: set[str]) -> str:
    """Haengt bei einer Namenskollision (z.B. zwei Buchungen mit gleichem
    Datum/Betrag/Empfaenger) einen zusaetzlichen Zaehler an, statt eine
    bereits geschriebene Datei stillschweigend zu ueberschreiben."""
    candidate = f"{base_name}{ext}"
    n = 2
    while candidate in used_names:
        candidate = f"{base_name}_{n}{ext}"
        n += 1
    used_names.add(candidate)
    return candidate


def _receipt_filename(tx: dict, multi_suffix: str, used_names: set[str]) -> str:
    """{Datum}_{Betrag}_{Empfaenger}[_N].pdf gemaess Anforderung 3 -
    multi_suffix ('_1'/'_2'/...) fuer mehrere Belege EINER Buchung,
    _dedupe_filename fuer Kollisionen ZWISCHEN verschiedenen Buchungen."""
    date_str = tx["date"].strftime("%Y-%m-%d")
    amount_str = f"EUR{tx['amount_abs']:.2f}"
    recipient = _recipient_name(tx)
    base = f"{date_str}_{amount_str}_{recipient}{multi_suffix}"
    return _dedupe_filename(base, ".pdf", used_names)


def _get_pdf_files(tx: dict, client, missing_ids: set | None = None) -> tuple[list[tuple[bytes, str]], list[str]]:
    """Liefert ([(pdf_bytes, original_dateiname), ...], [Fehlermeldungen])
    fuer eine erfolgreich zugeordnete Transaktion - entweder direkt vom
    Nutzer hochgeladen (immer genau eine Datei, siehe tx['uploaded_bytes'])
    oder ein oder mehrere aus Paperless heruntergeladene Dokumente (z.B. bei
    einer Sammelabbuchung mit mehreren Einzelrechnungen, siehe
    tx['matched_docs']). uploaded_bytes entscheidet bewusst statt
    tx['status']: TxStatus.MATCHED deckt beide Faelle ab (siehe
    tx_status.py).

    missing_ids (optional): bereits durch refresh_and_check_matched_
    documents als fehlend erkannte Dokument-IDs - werden hier still
    uebersprungen (kein zweiter Download-Versuch, keine doppelte
    Fehlermeldung fuer dasselbe Dokument). Ein DARUEBER HINAUS
    fehlgeschlagenes Dokument (z.B. erst hier beim Download entdeckt)
    bricht trotzdem NICHT den ganzen Export ab - es wird uebersprungen und
    als Fehlermeldung zurueckgegeben, die uebrigen Belege der Buchung (und
    alle anderen Buchungen) werden trotzdem exportiert."""
    if tx.get("uploaded_bytes"):
        return [(tx["uploaded_bytes"], tx["uploaded_name"] or "beleg.pdf")], []

    missing_ids = missing_ids or set()
    files = []
    errors = []
    for doc in tx.get("matched_docs") or []:
        if doc["id"] in missing_ids:
            continue
        try:
            pdf_bytes = client.download_document(doc["id"])
        except Exception as exc:
            name = doc.get("original_file_name") or doc.get("title") or f"Paperless-ID {doc['id']}"
            errors.append(
                f"Buchung #{tx.get('display_number') or tx['id']}: Beleg '{name}' (Paperless-ID {doc['id']}) "
                f"konnte nicht heruntergeladen werden - moeglicherweise in Paperless geloescht ({exc})."
            )
            continue
        original_name = doc.get("original_file_name") or f"{doc.get('title') or doc['id']}.pdf"
        files.append((pdf_bytes, original_name))
    return files, errors


def _export_receipts(
    month_transactions: list[dict], belege_dir: Path, client, missing_ids: set | None = None
) -> tuple[dict[str, list[str]], list[str]]:
    """Schreibt alle Beleg-PDFs nach 01_Belege_zugeordnet/ und gibt
    ({tx_id: [relative_pfad, ...]}, [Fehlermeldungen]) zurueck - Ersteres
    fuer die 'Zugeordneter Beleg'-Spalte in 00_Uebersicht.csv (Anforderung
    8: relative Pfade), Letzteres fuer Belege, die nicht heruntergeladen
    werden konnten (siehe _get_pdf_files) - der Export laeuft dafuer
    trotzdem vollstaendig durch, die Meldungen werden der aufrufenden
    UI-Schicht nur zur Information zurueckgegeben."""
    receipt_paths: dict[str, list[str]] = {}
    warnings: list[str] = []
    used_names: set[str] = set()
    for tx in month_transactions:
        if tx["status"] != TxStatus.MATCHED:
            continue
        pdf_files, errors = _get_pdf_files(tx, client, missing_ids)
        warnings.extend(errors)
        multiple = len(pdf_files) > 1
        paths = []
        for idx, (pdf_bytes, _original_name) in enumerate(pdf_files, start=1):
            suffix = f"_{idx}" if multiple else ""
            filename = _receipt_filename(tx, suffix, used_names)
            (belege_dir / filename).write_bytes(pdf_bytes)
            paths.append(f"01_Belege_zugeordnet/{filename}")
        receipt_paths[tx["id"]] = paths
    return receipt_paths, warnings


def _build_uebersicht_csv(
    month_transactions: list[dict], csv_delimiter: str, receipt_paths: dict[str, list[str]]
) -> bytes:
    """00_Uebersicht.csv - Master-Index MIT ALLEN Buchungen des Monats
    (Anforderung 2), Spalten exakt wie vorgegeben."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(["Datum", "Betrag", "Verwendungszweck", "Zugeordneter Beleg (relativer Pfad)", "Status", "Tag"])
    for t in sorted(month_transactions, key=lambda t: t["date"]):
        beleg = "; ".join(receipt_paths.get(t["id"]) or [])
        writer.writerow(
            [
                t["date"].strftime("%d.%m.%Y"),
                f"{t['amount_raw']:.2f}",
                t["purpose"],
                beleg,
                label_de(t["status"]),
                t.get("tag") or "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _build_getaggte_ohne_beleg_csv(month_transactions: list[dict], csv_delimiter: str) -> bytes:
    """02_Ohne_Beleg_getaggt/notizen_getaggte_buchungen.csv - alle getaggten
    Buchungen ohne Beleg (Anforderung 4: PRIVAT/EINZAHLUNG/UMBUCHUNG/eigene
    Tags brauchen nie einen Beleg-Upload)."""
    getaggt = sorted(
        (t for t in month_transactions if t["status"] == TxStatus.TAGGED),
        key=lambda t: t["date"],
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(["Datum", "Betrag", "Verwendungszweck", "Tag"])
    for t in getaggt:
        writer.writerow([t["date"].strftime("%d.%m.%Y"), f"{t['amount_raw']:.2f}", t["purpose"], t.get("tag") or ""])
    return buf.getvalue().encode("utf-8-sig")


def _build_offene_posten_csv(month_transactions: list[dict], csv_delimiter: str) -> bytes:
    """04_Offene_Posten.csv - alle Buchungen mit Klaerungsbedarf
    (Anforderung 6), mit konkretem Status statt nur 'offen'."""
    offen = sorted(
        (t for t in month_transactions if t["status"] in OPEN_STATUSES),
        key=lambda t: t["date"],
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(["Datum", "Betrag", "Verwendungszweck", "Status"])
    for t in offen:
        writer.writerow([t["date"].strftime("%d.%m.%Y"), f"{t['amount_raw']:.2f}", t["purpose"], label_de(t["status"])])
    return buf.getvalue().encode("utf-8-sig")


def _build_einzahlungen_csv(month_transactions: list[dict], csv_delimiter: str) -> bytes:
    """05_Einzahlungen_Deposit.csv - nur als EINZAHLUNG getaggte Buchungen
    (Anforderung 7)."""
    einzahlungen = sorted(
        (t for t in month_transactions if t["status"] == TxStatus.TAGGED and t.get("tag") == "EINZAHLUNG"),
        key=lambda t: t["date"],
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(["Datum", "Betrag", "Verwendungszweck"])
    for t in einzahlungen:
        writer.writerow([t["date"].strftime("%d.%m.%Y"), f"{t['amount_raw']:.2f}", t["purpose"]])
    return buf.getvalue().encode("utf-8-sig")


def _build_kontoauszug_csv(month_transactions: list[dict], csv_df, csv_delimiter: str) -> bytes:
    """03_Kontoauszug_gefiltert.csv - die Original-Bank-CSV, gefiltert auf
    die Buchungen dieses Exports (Anforderung 5), in der urspruenglichen
    Zeilenreihenfolge. row_index fehlt (None) bei Buchungen, die nicht aus
    einer hochgeladenen CSV stammen (z.B. ueber eine Bank-API ergaenzt,
    siehe desktop_controller.on_external_import) - die gehoeren per
    Definition nicht in eine "gefilterte Original-CSV" und werden hier
    uebersprungen."""
    row_indices = sorted(t["row_index"] for t in month_transactions if t.get("row_index") is not None)
    if not row_indices:
        return "".encode("utf-8-sig")
    filtered_df = csv_df.loc[row_indices].copy()
    csv_text = filtered_df.to_csv(sep=csv_delimiter, index=False, lineterminator="\n")
    return csv_text.encode("utf-8-sig")


def refresh_and_check_matched_documents(transactions: list[dict], client) -> tuple[list[str], set]:
    """Prueft fuer alle uebergebenen Buchungen mit matched_docs, ob die
    verknuepften Paperless-Dokumente noch existieren, und aktualisiert bei
    noch existierenden Dokumenten den zwischengespeicherten Titel/
    Dateinamen/Korrespondenten IN-PLACE. matched_docs ist ein Schnappschuss
    vom Zeitpunkt der Zuordnung (siehe match_transactions) - ein Umbenennen
    in Paperless wuerde ihn sonst nie aktualisieren, das Doc-Pill in der UI
    zeigte dauerhaft den alten Namen.

    Rueckgabe: (warnings, missing_ids) - warnings sind deutschsprachige
    Meldungen fuer jedes Dokument, das nicht mehr gefunden wurde (z.B. in
    Paperless geloescht, siehe generate_export-Docstring, der Export wird
    dadurch NICHT verweigert). missing_ids wird an _export_receipts
    weitergereicht, damit dort kein zweiter (redundanter) Download-
    Versuch fuer bereits hier als fehlend erkannte Dokumente unternommen
    wird - sonst gaebe es fuer dasselbe fehlende Dokument zwei Meldungen."""
    correspondents_by_id: dict | None = None
    warnings: list[str] = []
    missing_ids: set = set()
    for tx in transactions:
        for doc in tx.get("matched_docs") or []:
            try:
                fresh = client.get_document(doc["id"])
            except Exception as exc:
                missing_ids.add(doc["id"])
                name = doc.get("original_file_name") or doc.get("title") or f"Paperless-ID {doc['id']}"
                warnings.append(
                    f"Buchung #{tx.get('display_number') or tx['id']}: Dokument '{name}' (Paperless-ID {doc['id']}) "
                    f"wurde nicht gefunden - moeglicherweise in Paperless geloescht ({exc})."
                )
                continue
            if correspondents_by_id is None:
                try:
                    correspondents_by_id = {c["id"]: c.get("name") for c in client.get_correspondents()}
                except Exception:
                    correspondents_by_id = {}
            doc["title"] = fresh.get("title")
            doc["original_file_name"] = fresh.get("original_file_name")
            doc["correspondent_name"] = correspondents_by_id.get(fresh.get("correspondent"))
    return warnings, missing_ids


def generate_export(
    export_base_dir: Path,
    month_str: str,
    transactions: list[dict],
    csv_df,
    csv_delimiter: str,
    client,
) -> tuple[Path, list[str]]:
    """export_base_dir: der Ordner, in dem der Monatsordner angelegt wird -
    entweder der Standard-Exportordner oder ein vom Nutzer frei gewaehlter
    Zielordner (siehe desktop_state.AppState.get_export_base_dir). Prueft
    NICHT selbst auf offene Posten (siehe count_open_items) - der Export
    bleibt bewusst auch bei offenen Posten moeglich (Anforderung 9).

    Rueckgabe: (export_root, warnings) - warnings enthaelt Meldungen zu
    Belegen, die nicht mehr in Paperless gefunden wurden (siehe
    refresh_and_check_matched_documents/_export_receipts), im Normalfall
    leer. Aktualisiert nebenbei zwischengespeicherte Titel/Dateinamen noch
    vorhandener Dokumente in-place (transactions wird mutiert)."""
    export_root = Path(export_base_dir) / month_folder_name(month_str)
    export_root.mkdir(parents=True, exist_ok=True)

    belege_dir = export_root / "01_Belege_zugeordnet"
    belege_dir.mkdir(exist_ok=True)
    ohne_beleg_dir = export_root / "02_Ohne_Beleg_getaggt"
    ohne_beleg_dir.mkdir(exist_ok=True)

    month_transactions = [t for t in transactions if t["date"].strftime("%Y-%m") == month_str]

    warnings, missing_ids = refresh_and_check_matched_documents(month_transactions, client)
    receipt_paths, export_warnings = _export_receipts(month_transactions, belege_dir, client, missing_ids)
    warnings.extend(export_warnings)

    (export_root / "00_Uebersicht.csv").write_bytes(
        _build_uebersicht_csv(month_transactions, csv_delimiter, receipt_paths)
    )
    (ohne_beleg_dir / "notizen_getaggte_buchungen.csv").write_bytes(
        _build_getaggte_ohne_beleg_csv(month_transactions, csv_delimiter)
    )
    (export_root / "03_Kontoauszug_gefiltert.csv").write_bytes(
        _build_kontoauszug_csv(month_transactions, csv_df, csv_delimiter)
    )
    (export_root / "04_Offene_Posten.csv").write_bytes(
        _build_offene_posten_csv(month_transactions, csv_delimiter)
    )
    (export_root / "05_Einzahlungen_Deposit.csv").write_bytes(
        _build_einzahlungen_csv(month_transactions, csv_delimiter)
    )

    return export_root, warnings


def get_fiscal_year_months(start_year: int, fiscal_config: dict) -> list[str]:
    """Die 12 Monats-Strings ('YYYY-MM') eines Geschaeftsjahres, bereits in
    Geschaeftsjahr-Reihenfolge (siehe config_manager.DEFAULT_CONFIG
    ['fiscal_year']). Bei Kalenderjahr (calendar_year=True, der Normalfall)
    ist das Januar..Dezember von start_year. Bei einem abweichenden
    Wirtschaftsjahr beginnt es im konfigurierten start_month von start_year
    und laeuft 12 Monate bis start_month-1 des Folgejahres (z.B.
    start_month=7, start_year=2025 -> Juli 2025 bis Juni 2026 - das
    Geschaeftsjahr '2025/2026' aus der Anforderung)."""
    calendar_year = fiscal_config.get("calendar_year", True)
    start_month = 1 if calendar_year else int(fiscal_config.get("start_month", 7))
    months = []
    for i in range(12):
        month_index = start_month - 1 + i
        year = start_year + month_index // 12
        month = month_index % 12 + 1
        months.append(f"{year:04d}-{month:02d}")
    return months


def fiscal_year_folder_name(start_year: int, fiscal_config: dict) -> str:
    """'Jahresexport_2026' bei Kalenderjahr, 'Jahresexport_2025-2026' bei
    einem abweichenden Wirtschaftsjahr - start_year ist dabei immer das
    Kalenderjahr, in dem das Geschaeftsjahr BEGINNT (siehe
    get_fiscal_year_months)."""
    if fiscal_config.get("calendar_year", True):
        return f"Jahresexport_{start_year}"
    return f"Jahresexport_{start_year}-{start_year + 1}"


def fiscal_year_label(start_year: int, fiscal_config: dict) -> str:
    """Anzeige-Text des Geschaeftsjahres fuer UI/PDF-Titel ('2026' bzw.
    '2025/2026') - siehe fiscal_year_folder_name fuer die
    Dateisystem-Variante mit Unterstrich/Bindestrich statt Schraegstrich."""
    if fiscal_config.get("calendar_year", True):
        return str(start_year)
    return f"{start_year}/{start_year + 1}"


def export_fiscal_year(
    export_base_dir: Path,
    start_year: int,
    fiscal_config: dict,
    transactions: list[dict],
    csv_df,
    csv_delimiter: str,
    client,
    company_name: str = "",
    logo_path: Path | None = None,
    on_progress=None,
) -> tuple[Path, list[str]]:
    """Baut den kompletten Jahresexport: ruft generate_export() fuer jeden
    der 12 Monate des Geschaeftsjahres frisch aus transactions auf (siehe
    get_fiscal_year_months) - unabhaengig davon, ob fuer einen dieser
    Monate schon einmal separat exportiert wurde, keine Wiederverwendung
    alter Ordnerinhalte. Sammelt die entstandenen Monatsordner unter einem
    gemeinsamen, nach dem Geschaeftsjahr benannten Wurzelordner (siehe
    fiscal_year_folder_name).

    Die Monatsordner (month_folder_name: 'YYYY-MM_Monatsname') sortieren
    durch ihr bestehendes YYYY-MM-Praefix bereits von selbst chronologisch
    in Geschaeftsjahr-Reihenfolge, auch ueber einen Jahreswechsel hinweg
    (z.B. '2025-07_Juli' < '2025-12_Dezember' < '2026-01_Januar' <
    '2026-06_Juni') - keine zusaetzliche Umbenennung/Nummerierung noetig.

    Erzeugt danach drei Jahres-Zusammenfassungen im Wurzelordner -
    00_Jahresuebersicht.csv (ALLE Buchungen), 00_Offene_Posten_Jahr.csv
    (NUR Klaerungsbedarf) und 00_Jahresuebersicht.pdf (Deckblatt +
    offene-Posten-Zusammenfassung zuerst + vollstaendige Monatsliste) -
    company_name/logo_path nur fuers PDF-Deckblatt, sonst ungenutzt.

    Prueft NICHT selbst auf offene Posten, verweigert den Export nie - das
    ist Aufgabe der aufrufenden UI-Schicht (siehe generate_export-Docstring
    fuer denselben Grundsatz auf Monatsebene, sowie die jahresweite
    Vorab-Warnung).

    on_progress (optional): Callback(step: int, total: int, label: str),
    einmal je Monat VOR dessen Verarbeitung plus einmal fuer die
    abschliessenden Jahres-Zusammenfassungen - fuer eine Fortschrittsanzeige
    in der UI (siehe desktop_app_qt.FiscalYearExportWorker), da der
    komplette Ablauf bei vielen Belegen mehrere Sekunden dauern kann.

    Rueckgabe: (year_root, warnings) - warnings sammelt die Meldungen aller
    12 Monate zu Belegen, die nicht mehr in Paperless gefunden wurden
    (siehe generate_export), im Normalfall leer."""
    year_root = Path(export_base_dir) / fiscal_year_folder_name(start_year, fiscal_config)
    year_root.mkdir(parents=True, exist_ok=True)

    month_strs = get_fiscal_year_months(start_year, fiscal_config)
    total_steps = len(month_strs) + 1
    warnings: list[str] = []
    for i, month_str in enumerate(month_strs, start=1):
        if on_progress:
            month_label = f"{_MONTH_NAMES_DISPLAY[int(month_str[5:7])]} {month_str[:4]}"
            on_progress(i, total_steps, f"Monat {i} von {len(month_strs)}: {month_label}")
        _month_root, month_warnings = generate_export(year_root, month_str, transactions, csv_df, csv_delimiter, client)
        warnings.extend(month_warnings)

    if on_progress:
        on_progress(total_steps, total_steps, "Jahres-Zusammenfassungen werden erstellt ...")

    (year_root / "00_Jahresuebersicht.csv").write_bytes(
        _build_jahresuebersicht_csv(year_root, month_strs, csv_delimiter, transactions)
    )
    (year_root / "00_Offene_Posten_Jahr.csv").write_bytes(
        _build_offene_posten_jahr_csv(year_root, month_strs, csv_delimiter)
    )
    (year_root / "00_Jahresuebersicht.pdf").write_bytes(
        _build_jahresuebersicht_pdf(
            year_root, month_strs, fiscal_year_label(start_year, fiscal_config), csv_delimiter, company_name, logo_path
        )
    )

    return year_root, warnings


def _build_jahresuebersicht_csv(
    year_root: Path, month_strs: list[str], csv_delimiter: str, transactions: list[dict]
) -> bytes:
    """00_Jahresuebersicht.csv - fasst die bereits geschriebenen
    00_Uebersicht.csv ALLER 12 Monate (siehe export_fiscal_year) in einer
    Tabelle zusammen, ergaenzt um 'Monat' und 'Relativer Pfad zum
    Monatsordner' vorne dran, damit jede Zeile von der Jahresuebersicht aus
    zum passenden Beleg im jeweiligen Unterordner zurueckverfolgbar bleibt.
    Liest bewusst die bereits erzeugten Monatsdateien zurueck, statt Status/
    Belegpfade ein zweites Mal aus den Transaktionen zu berechnen - so
    bleiben Jahres- und Monatsuebersicht garantiert konsistent (gleiche
    zentrale Status-Werte aus tx_status.py, einmal pro Monat berechnet).

    'Empfänger/Absender' ist in der monatlichen 00_Uebersicht.csv NICHT
    enthalten (deren Spalten sind fest vorgegeben, siehe dortiger
    Docstring) - hier stattdessen direkt aus transactions nachgezogen und
    per Position zugeordnet: _build_uebersicht_csv sortiert dieselbe
    Monats-Teilmenge von transactions ebenfalls nur nach Datum (stabiler
    Sort), die Reihenfolge ist also garantiert identisch."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(
        ["Monat", "Relativer Pfad zum Monatsordner", "Datum", "Betrag", "Verwendungszweck",
         "Empfänger/Absender", "Zugeordneter Beleg (relativer Pfad)", "Status", "Tag"]
    )
    for month_str in month_strs:
        folder_name = month_folder_name(month_str)
        month_csv_path = year_root / folder_name / "00_Uebersicht.csv"
        if not month_csv_path.exists():
            continue
        text = month_csv_path.read_bytes().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text), delimiter=csv_delimiter))[1:]  # Kopfzeile ueberspringen
        month_transactions = sorted(
            (t for t in transactions if t["date"].strftime("%Y-%m") == month_str), key=lambda t: t["date"]
        )
        for row, t in zip(rows, month_transactions):
            writer.writerow([month_str, folder_name, row[0], row[1], row[2], t.get("counterparty") or "", row[3], row[4], row[5]])
    return buf.getvalue().encode("utf-8-sig")


def _read_jahresuebersicht_rows(year_root: Path, csv_delimiter: str) -> list[list[str]]:
    """Liest die bereits geschriebene 00_Jahresuebersicht.csv zurueck (ohne
    Kopfzeile) - gemeinsame Grundlage fuer _build_offene_posten_jahr_csv
    und _build_jahresuebersicht_pdf, damit beide garantiert dieselben Daten
    zeigen wie die CSV selbst. Spalten (siehe _build_jahresuebersicht_csv):
    [0] Monat, [1] Relativer Pfad zum Monatsordner, [2] Datum, [3] Betrag,
    [4] Verwendungszweck, [5] Empfänger/Absender,
    [6] Zugeordneter Beleg (relativer Pfad), [7] Status, [8] Tag."""
    text = (year_root / "00_Jahresuebersicht.csv").read_bytes().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=csv_delimiter))[1:]


def _build_offene_posten_jahr_csv(year_root: Path, month_strs: list[str], csv_delimiter: str) -> bytes:
    """00_Offene_Posten_Jahr.csv - NUR die Buchungen des gesamten
    Geschaeftsjahres mit Klaerungsbedarf (siehe tx_status.OPEN_STATUSES),
    mit einem Kopfblock (Anzahl offener Posten je Monat/Status-Typ ganz
    oben, Format 'Monat JJJJ: N status_a, N status_b') - NUR fuer Monate,
    die tatsaechlich offene Posten haben (kein Rauschen aus einer Zeile pro
    Monat mit '0 offene Posten' fuer jeden erledigten Monat, siehe Chat),
    damit auf einen Blick erkennbar ist, wo noch Klaerungsbedarf besteht,
    ohne die Detailzeilen lesen zu muessen. MUSS NACH 00_Jahresuebersicht.csv
    aufgerufen werden (siehe export_fiscal_year) - liest diese zurueck
    statt Status ein zweites Mal zu berechnen."""
    rows = _read_jahresuebersicht_rows(year_root, csv_delimiter)
    open_rows_by_month: dict[str, list[list[str]]] = {m: [] for m in month_strs}
    for row in rows:
        status = _LABEL_TO_STATUS.get(row[7])
        if status in OPEN_STATUSES:
            open_rows_by_month.setdefault(row[0], []).append(row)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")

    writer.writerow(["Zusammenfassung offene Posten pro Monat"])
    months_with_open_items = [m for m in month_strs if open_rows_by_month.get(m)]
    if not months_with_open_items:
        writer.writerow(["Keine offenen Posten im gesamten Geschäftsjahr."])
    for month_str in months_with_open_items:
        month_rows = open_rows_by_month[month_str]
        month_label = f"{_MONTH_NAMES_DISPLAY[int(month_str[5:7])]} {month_str[:4]}"
        counts: dict[str, int] = {}
        for row in month_rows:
            status = _LABEL_TO_STATUS.get(row[7])
            key = status.value if status else row[7]
            counts[key] = counts.get(key, 0) + 1
        parts = ", ".join(f"{n} {key}" for key, n in counts.items())
        writer.writerow([f"{month_label}: {parts}"])
    writer.writerow([])

    writer.writerow(["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    for month_str in month_strs:
        for row in open_rows_by_month.get(month_str, []):
            writer.writerow([row[0], row[2], row[3], row[4], row[5], row[7]])
    return buf.getvalue().encode("utf-8-sig")


def _pdf_cell(text, style) -> Paragraph:
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(text)), style)


def _build_jahresuebersicht_pdf(
    year_root: Path,
    month_strs: list[str],
    year_label: str,
    csv_delimiter: str,
    company_name: str,
    logo_path: Path | None,
) -> bytes:
    """00_Jahresuebersicht.pdf: Deckblatt (Firmenname/Logo, Geschaeftsjahr,
    Erstellungsdatum), danach eine Zusammenfassungsseite mit
    AUSSCHLIESSLICH den offenen Posten des Jahres (Anforderung 5 - die
    muessen zuerst kommen, nicht in der grossen Gesamtliste versteckt
    sein), farblich nach Status markiert, mit einer Anzahl-pro-Monat/
    Status-Zusammenfassung davor. Erst danach die vollstaendige
    Jahresliste, gruppiert nach Monat mit Seitenumbruch dazwischen. Liest
    wie _build_offene_posten_jahr_csv die bereits geschriebene
    00_Jahresuebersicht.csv zurueck, damit alle drei Jahres-
    Zusammenfassungen (CSV/Offene-Posten-CSV/PDF) garantiert dieselben
    Daten zeigen. MUSS NACH 00_Jahresuebersicht.csv aufgerufen werden."""
    rows = _read_jahresuebersicht_rows(year_root, csv_delimiter)
    rows_by_month: dict[str, list[list[str]]] = {m: [] for m in month_strs}
    for row in rows:
        rows_by_month.setdefault(row[0], []).append(row)

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=9)
    header_style = ParagraphStyle("header", parent=cell_style, textColor=colors.white, fontName="Helvetica-Bold")

    def _table(header: list[str], data_rows: list[list[str]], row_colors: list | None = None) -> Table:
        table_data = [[_pdf_cell(h, header_style) for h in header]]
        for r in data_rows:
            table_data.append([_pdf_cell(c, cell_style) for c in r])
        tbl = Table(table_data, repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if row_colors:
            for i, color in enumerate(row_colors, start=1):
                if color is not None:
                    style_cmds.append(("BACKGROUND", (0, i), (-1, i), color))
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm
    )
    story = []

    # --- Deckblatt -----------------------------------------------------
    if logo_path is not None:
        logo_path = Path(logo_path)
        if logo_path.exists():
            try:
                story.append(Image(str(logo_path), width=2.5 * cm, height=2.5 * cm))
                story.append(Spacer(1, 1 * cm))
            except Exception:
                pass  # kaputte/nicht lesbare Logo-Datei soll den Export nicht verhindern
    if company_name:
        story.append(Paragraph(company_name, styles["Title"]))
    story.append(Paragraph(f"Jahresübersicht {year_label}", styles["Heading1"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"Erstellt am {date_cls.today().strftime('%d.%m.%Y')}", styles["Normal"]))
    story.append(PageBreak())

    # --- Zusammenfassungsseite: NUR offene Posten, zuerst -----------------
    story.append(Paragraph("Offene Posten", styles["Heading1"]))
    story.append(Spacer(1, 0.2 * cm))

    open_rows_by_month: dict[str, list[list[str]]] = {
        month_str: [r for r in rows_by_month.get(month_str, []) if _LABEL_TO_STATUS.get(r[7]) in OPEN_STATUSES]
        for month_str in month_strs
    }

    # NUR Monate mit tatsaechlich offenen Posten auflisten (kein Rauschen
    # aus einer Zeile pro erledigtem Monat mit "0 offene Posten", siehe Chat).
    for month_str in month_strs:
        open_rows = open_rows_by_month[month_str]
        if not open_rows:
            continue
        month_label = f"{_MONTH_NAMES_DISPLAY[int(month_str[5:7])]} {month_str[:4]}"
        counts: dict[str, int] = {}
        for row in open_rows:
            status = _LABEL_TO_STATUS.get(row[7])
            key = status.value if status else row[7]
            counts[key] = counts.get(key, 0) + 1
        parts = ", ".join(f"{n} {key}" for key, n in counts.items())
        story.append(Paragraph(f"{month_label}: {parts}", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    all_open_rows = [row for month_str in month_strs for row in open_rows_by_month[month_str]]
    if all_open_rows:
        row_colors = [_STATUS_COLORS_PDF.get(_LABEL_TO_STATUS.get(row[7])) for row in all_open_rows]
        detail_rows = [[row[0], row[2], row[3], row[4], row[5], row[7]] for row in all_open_rows]
        story.append(_table(
            ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"], detail_rows, row_colors
        ))
    else:
        story.append(Paragraph("Keine offenen Posten im gesamten Geschäftsjahr.", styles["Normal"]))
    story.append(PageBreak())

    # --- Vollstaendige Jahresliste, gruppiert nach Monat -------------------
    for idx, month_str in enumerate(month_strs):
        month_label = f"{_MONTH_NAMES_DISPLAY[int(month_str[5:7])]} {month_str[:4]}"
        story.append(Paragraph(month_label, styles["Heading2"]))
        story.append(Spacer(1, 0.2 * cm))
        month_rows = rows_by_month.get(month_str, [])
        if not month_rows:
            story.append(Paragraph("Keine Buchungen in diesem Monat.", styles["Normal"]))
        else:
            detail_rows = [[row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[0]] for row in month_rows]
            story.append(_table(
                ["Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Beleg", "Status", "Tag", "Monat"],
                detail_rows,
            ))
        if idx < len(month_strs) - 1:
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


def zip_export_folder(folder: Path) -> bytes:
    """Packt einen kompletten Export-Ordner (Monats- oder Jahresexport) als
    ZIP-Bytes - mit Pfaden relativ zum UEBERGEORDNETEN Ordner, damit der
    Ordnername selbst (z.B. 'Jahresexport_2026/') als oberste Ebene im ZIP
    erhalten bleibt und alle relativen Pfade in den *.csv-Dateien (z.B.
    '2026-01_Januar/01_Belege_zugeordnet/...') nach dem Entpacken an
    anderer Stelle weiterhin aufloesbar sind. Reine Bytes statt direktem
    Schreiben, analog zu backup.create_backup() - die aufrufende UI-Schicht
    entscheidet ueber den Zielpfad (siehe SettingsDialog._create_backup)."""
    folder = Path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(folder.parent))
    return buf.getvalue()
