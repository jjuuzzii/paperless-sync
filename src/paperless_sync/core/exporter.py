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
    Zeilenreihenfolge."""
    row_indices = sorted(t["row_index"] for t in month_transactions)
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
