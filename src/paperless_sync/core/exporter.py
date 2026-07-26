"""Erzeugt den sauberen Steuer-Export-Ordner aus den abgeglichenen
Transaktionen. Fuer JEDE Transaktions-ID landet mindestens eine Datei im
Zielordner (PDF, _<TAG>.txt oder _FEHLT.txt) - bei mehreren verknuepften
Belegen (z.B. Sammelabbuchung) entsprechend mehrere PDFs mit _1/_2/...
Suffix im Dateinamen."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    name = (name or "beleg").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name or "beleg"


def _direction_label(tx: dict) -> str:
    return "Einnahme" if tx["amount_raw"] >= 0 else "Ausgabe"


def _build_missing_report(month_str: str, month_transactions: list[dict]) -> str:
    """Kurzer Ueberblick, welche Buchungen noch ohne Beleg/Zuordnung sind -
    landet als eigene Datei im Export-Ordner, damit der Nutzer nicht jede
    einzelne _FEHLT.txt oeffnen muss, um zu sehen, was noch fehlt."""
    open_txs = [t for t in month_transactions if t["status"] in (None, "missing", "unclear")]
    lines = [f"Fehlende Belege - {month_str}", "=" * 32, ""]
    if not open_txs:
        lines.append("Keine fehlenden Belege - alle Buchungen dieses Monats sind zugeordnet oder getaggt.")
    else:
        lines.append(f"{len(open_txs)} Buchung(en) noch ohne zugeordneten Beleg:")
        lines.append("")
        for tx in sorted(open_txs, key=lambda t: t["date"]):
            id_str = tx.get("display_number") or tx["id"]
            date_str = tx["date"].strftime("%d.%m.%Y")
            amount_str = f"{tx['amount_raw']:.2f} EUR"
            suffix = "  [MEHRFACH-MATCH - manuell klaeren]" if tx["status"] == "unclear" else ""
            lines.append(f"#{id_str}  {date_str}  {amount_str}  {tx['purpose']}{suffix}")
    return "\n".join(lines) + "\n"


def _build_einzahlungen_csv(month_transactions: list[dict], csv_delimiter: str) -> bytes:
    """CSV-Tabelle aller als EINZAHLUNG getaggten Buchungen des Exportmonats -
    Einzahlungen haben nie einen Beleg (siehe BUILTIN_TAGS in app.py), muessen
    dem Steuerberater aber trotzdem als eigene, uebersichtliche Liste
    vorliegen, statt einzeln aus den _EINZAHLUNG.txt-Dateien zusammengesucht
    zu werden."""
    einzahlungen = sorted(
        (t for t in month_transactions if t["status"] == "tagged" and t.get("tag") == "EINZAHLUNG"),
        key=lambda t: t["date"],
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=csv_delimiter, lineterminator="\n")
    writer.writerow(["Beleg-Nr.", "Datum", "Betrag", "Verwendungszweck"])
    for t in einzahlungen:
        writer.writerow(
            [
                t.get("display_number") or t["id"],
                t["date"].strftime("%d.%m.%Y"),
                f"{t['amount_raw']:.2f}",
                t["purpose"],
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _get_pdf_files(tx: dict, client) -> list[tuple[bytes, str]]:
    """Liefert [(pdf_bytes, original_dateiname), ...] fuer eine erfolgreich
    zugeordnete Transaktion - entweder direkt vom Nutzer hochgeladen (immer
    genau eine Datei) oder ein oder mehrere aus Paperless heruntergeladene
    Dokumente (z.B. bei einer Sammelabbuchung mit mehreren
    Einzelrechnungen, siehe tx['matched_docs'])."""
    if tx["status"] == "uploaded":
        return [(tx["uploaded_bytes"], tx["uploaded_name"] or "beleg.pdf")]

    files = []
    for doc in tx.get("matched_docs") or []:
        pdf_bytes = client.download_document(doc["id"])
        original_name = doc.get("original_file_name") or f"{doc.get('title') or doc['id']}.pdf"
        files.append((pdf_bytes, original_name))
    return files


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
    Zielordner (siehe desktop_state.AppState.get_export_base_dir)."""
    export_root = Path(export_base_dir) / f"Export_Steuer_{month_str}"
    export_root.mkdir(parents=True, exist_ok=True)

    month_transactions = [t for t in transactions if t["date"].strftime("%Y-%m") == month_str]

    # Kontoauszug-CSV auf die fuer diesen Export relevanten Buchungen filtern
    # (nicht die komplette Original-CSV, falls diese mehrere Monate umfasst),
    # in der urspruenglichen Zeilenreihenfolge der Bank-CSV.
    row_indices = sorted(t["row_index"] for t in month_transactions)
    filtered_df = csv_df.loc[row_indices].copy()
    # Fuegt die gleiche Nummer voran, die auch im Dateinamen der zugehoerigen
    # Beleg-Datei steht (z.B. "001_..._Rechnung.pdf") - damit sich CSV-Zeile
    # und Beleg-Datei im Export-Ordner eindeutig einander zuordnen lassen.
    row_to_number = {t["row_index"]: t.get("display_number") or t["id"] for t in month_transactions}
    filtered_df.insert(0, "Beleg-Nr.", filtered_df.index.map(row_to_number))
    csv_text = filtered_df.to_csv(sep=csv_delimiter, index=False, lineterminator="\n")
    (export_root / "000_Kontoauszug.csv").write_bytes(csv_text.encode("utf-8-sig"))

    (export_root / "000_Fehlende_Belege.txt").write_text(
        _build_missing_report(month_str, month_transactions), encoding="utf-8"
    )

    (export_root / "000_Einzahlungen.csv").write_bytes(
        _build_einzahlungen_csv(month_transactions, csv_delimiter)
    )

    for tx in month_transactions:
        id_str = tx.get("display_number") or tx["id"]
        date_str = tx["date"].strftime("%Y-%m-%d")
        amount_str = f"{tx['amount_abs']:.2f}"
        direction = _direction_label(tx)

        if tx["status"] == "tagged":
            tag_name = _sanitize_filename(tx["tag"] or "SONSTIGES").upper().replace(" ", "_")
            content = (
                f"Transaktion {id_str}\n"
                f"Datum: {date_str}\n"
                f"Betrag: {tx['amount_raw']:.2f} EUR ({direction})\n"
                f"Verwendungszweck: {tx['purpose']}\n"
                f"Status: {tag_name}\n"
            )
            (export_root / f"{id_str}_{tag_name}.txt").write_text(content, encoding="utf-8")

        elif tx["status"] in ("matched", "uploaded"):
            pdf_files = _get_pdf_files(tx, client)
            multiple = len(pdf_files) > 1
            for idx, (pdf_bytes, original_name) in enumerate(pdf_files, start=1):
                safe_name = _sanitize_filename(original_name)
                suffix = f"_{idx}" if multiple else ""
                filename = f"{id_str}{suffix}_{date_str}_{amount_str}_{safe_name}"
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"
                (export_root / filename).write_bytes(pdf_bytes)

        else:
            content = (
                f"Transaktion {id_str}\n"
                f"Datum: {date_str}\n"
                f"Betrag: {tx['amount_raw']:.2f} EUR ({direction})\n"
                f"Verwendungszweck: {tx['purpose']}\n"
                f"Status: FEHLT\n"
                f"{tx.get('note', '')}\n"
            )
            (export_root / f"{id_str}_FEHLT.txt").write_text(content, encoding="utf-8")

    return export_root
