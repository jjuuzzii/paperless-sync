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
from pathlib import Path

from .tx_status import TxStatus, OPEN_STATUSES, label_de

_MONTH_NAMES_DE = {
    1: "Januar", 2: "Februar", 3: "Maerz", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}

_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_IBAN_RE = re.compile(r"IBAN:?\s*[A-Z]{2}\d{2}[A-Z0-9]{10,30}", re.IGNORECASE)
_BIC_RE = re.compile(r"BIC:?\s*[A-Z0-9]{8,11}", re.IGNORECASE)

RECIPIENT_MAX_LENGTH = 40


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


def _get_pdf_files(tx: dict, client) -> list[tuple[bytes, str]]:
    """Liefert [(pdf_bytes, original_dateiname), ...] fuer eine erfolgreich
    zugeordnete Transaktion - entweder direkt vom Nutzer hochgeladen (immer
    genau eine Datei, siehe tx['uploaded_bytes']) oder ein oder mehrere aus
    Paperless heruntergeladene Dokumente (z.B. bei einer Sammelabbuchung mit
    mehreren Einzelrechnungen, siehe tx['matched_docs']). uploaded_bytes
    entscheidet bewusst statt tx['status']: TxStatus.MATCHED deckt beide
    Faelle ab (siehe tx_status.py)."""
    if tx.get("uploaded_bytes"):
        return [(tx["uploaded_bytes"], tx["uploaded_name"] or "beleg.pdf")]

    files = []
    for doc in tx.get("matched_docs") or []:
        pdf_bytes = client.download_document(doc["id"])
        original_name = doc.get("original_file_name") or f"{doc.get('title') or doc['id']}.pdf"
        files.append((pdf_bytes, original_name))
    return files


def _export_receipts(
    month_transactions: list[dict], belege_dir: Path, client
) -> dict[str, list[str]]:
    """Schreibt alle Beleg-PDFs nach 01_Belege_zugeordnet/ und gibt
    {tx_id: [relative_pfad, ...]} zurueck - fuer die 'Zugeordneter Beleg'-
    Spalte in 00_Uebersicht.csv (Anforderung 8: relative Pfade)."""
    receipt_paths: dict[str, list[str]] = {}
    used_names: set[str] = set()
    for tx in month_transactions:
        if tx["status"] != TxStatus.MATCHED:
            continue
        pdf_files = _get_pdf_files(tx, client)
        multiple = len(pdf_files) > 1
        paths = []
        for idx, (pdf_bytes, _original_name) in enumerate(pdf_files, start=1):
            suffix = f"_{idx}" if multiple else ""
            filename = _receipt_filename(tx, suffix, used_names)
            (belege_dir / filename).write_bytes(pdf_bytes)
            paths.append(f"01_Belege_zugeordnet/{filename}")
        receipt_paths[tx["id"]] = paths
    return receipt_paths


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


def generate_export(
    export_base_dir: Path,
    month_str: str,
    transactions: list[dict],
    csv_df,
    csv_delimiter: str,
    client,
) -> Path:
    """export_base_dir: der Ordner, in dem der Monatsordner angelegt wird -
    entweder der Standard-Exportordner oder ein vom Nutzer frei gewaehlter
    Zielordner (siehe desktop_state.AppState.get_export_base_dir). Prueft
    NICHT selbst auf offene Posten (siehe count_open_items) - der Export
    bleibt bewusst auch bei offenen Posten moeglich (Anforderung 9)."""
    export_root = Path(export_base_dir) / month_folder_name(month_str)
    export_root.mkdir(parents=True, exist_ok=True)

    belege_dir = export_root / "01_Belege_zugeordnet"
    belege_dir.mkdir(exist_ok=True)
    ohne_beleg_dir = export_root / "02_Ohne_Beleg_getaggt"
    ohne_beleg_dir.mkdir(exist_ok=True)

    month_transactions = [t for t in transactions if t["date"].strftime("%Y-%m") == month_str]

    receipt_paths = _export_receipts(month_transactions, belege_dir, client)

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

    return export_root


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


def export_fiscal_year(
    export_base_dir: Path,
    start_year: int,
    fiscal_config: dict,
    transactions: list[dict],
    csv_df,
    csv_delimiter: str,
    client,
) -> Path:
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

    Prueft NICHT selbst auf offene Posten - das ist Aufgabe der aufrufenden
    UI-Schicht (siehe generate_export-Docstring fuer denselben Grundsatz
    auf Monatsebene, sowie die geplante jahresweite Vorab-Warnung)."""
    year_root = Path(export_base_dir) / fiscal_year_folder_name(start_year, fiscal_config)
    year_root.mkdir(parents=True, exist_ok=True)

    month_strs = get_fiscal_year_months(start_year, fiscal_config)
    for month_str in month_strs:
        generate_export(year_root, month_str, transactions, csv_df, csv_delimiter, client)

    (year_root / "00_Jahresuebersicht.csv").write_bytes(
        _build_jahresuebersicht_csv(year_root, month_strs, csv_delimiter)
    )

    return year_root


def _build_jahresuebersicht_csv(year_root: Path, month_strs: list[str], csv_delimiter: str) -> bytes:
    """00_Jahresuebersicht.csv - fasst die bereits geschriebenen
    00_Uebersicht.csv ALLER 12 Monate (siehe export_fiscal_year) in einer
    Tabelle zusammen, ergaenzt um 'Monat' und 'Relativer Pfad zum
    Monatsordner' vorne dran, damit jede Zeile von der Jahresuebersicht aus
    zum passenden Beleg im jeweiligen Unterordner zurueckverfolgbar bleibt.
    Liest bewusst die bereits erzeugten Monatsdateien zurueck, statt Status/
    Belegpfade ein zweites Mal aus den Transaktionen zu berechnen - so
    bleiben Jahres- und Monatsuebersicht garantiert konsistent (gleiche
    zentrale Status-Werte aus tx_status.py, einmal pro Monat berechnet)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(
        ["Monat", "Relativer Pfad zum Monatsordner", "Datum", "Betrag", "Verwendungszweck",
         "Zugeordneter Beleg (relativer Pfad)", "Status", "Tag"]
    )
    for month_str in month_strs:
        folder_name = month_folder_name(month_str)
        month_csv_path = year_root / folder_name / "00_Uebersicht.csv"
        if not month_csv_path.exists():
            continue
        text = month_csv_path.read_bytes().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text), delimiter=csv_delimiter))
        for row in rows[1:]:  # Kopfzeile ueberspringen
            writer.writerow([month_str, folder_name] + row)
    return buf.getvalue().encode("utf-8-sig")


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
