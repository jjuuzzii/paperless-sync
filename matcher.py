"""Transaktions-Aufbau und Abgleich gegen Paperless-Dokumente."""
from __future__ import annotations

import re

from dateutil import parser as dateparser

from csv_utils import parse_amount, parse_date


def build_transactions(df, mapping: dict) -> list[dict]:
    """Baut aus dem CSV-DataFrame die Transaktionsliste: chronologisch
    sortiert, mit fortlaufender dreistelliger ID. Einnahmen (positive
    Betraege) und Ausgaben (negative Betraege) werden beide beruecksichtigt;
    fuer den Betragsvergleich wird zusaetzlich der Absolutwert gefuehrt."""
    date_col = mapping.get("date_column")
    amount_col = mapping.get("amount_column")
    purpose_col = mapping.get("purpose_column")
    counterparty_col = mapping.get("counterparty_column")  # optional, z.B. "Name Zahlungsbeteiligter"

    records = []
    for row_index, row in df.iterrows():
        date_val = parse_date(row.get(date_col))
        amount_val = parse_amount(row.get(amount_col))
        purpose_val = str(row.get(purpose_col, "")).strip()
        counterparty_val = str(row.get(counterparty_col, "")).strip() if counterparty_col else ""
        if date_val is None or amount_val is None:
            continue
        records.append(
            {
                "date": date_val,
                "amount_raw": amount_val,
                "amount_abs": abs(amount_val),
                "purpose": purpose_val,
                "counterparty": counterparty_val,
                "row_index": row_index,  # Original-CSV-Zeile, fuer den gefilterten Export
            }
        )

    records.sort(key=lambda r: r["date"])

    transactions = []
    month_counters: dict[str, int] = {}
    for i, r in enumerate(records, start=1):
        r["id"] = f"{i:03d}"  # global eindeutig - interner Schluessel fuer alle Lookups/Klicks, NICHT anzeigen
        month_key = r["date"].strftime("%Y-%m")
        month_counters[month_key] = month_counters.get(month_key, 0) + 1
        r["display_number"] = f"{month_counters[month_key]:03d}"  # 1..X je Monat - das sieht der Nutzer (Karte/Export)
        r["status"] = None  # None | matched | missing | unclear | tagged | uploaded
        r["note"] = ""
        r["tag"] = None  # gesetzt, wenn status == "tagged" (PRIVAT/EINZAHLUNG/UMBUCHUNG/eigener Tag)
        r["suggested_tag"] = None  # Vorschlag aus gelerntem Verwendungszweck, noch nicht bestaetigt
        r["matched_docs"] = []  # ein oder mehrere verknuepfte Paperless-Dokumente (z.B. Sammelabbuchung)
        r["candidate_docs"] = None  # bei status == "unclear": Liste der mehrdeutigen Kandidaten
        r["uploaded_bytes"] = None
        r["uploaded_name"] = None
        transactions.append(r)
    return transactions


def parse_paperless_date(value):
    if not value:
        return None
    try:
        return dateparser.parse(value).date()
    except (ValueError, TypeError, OverflowError):
        return None


def extract_amount_from_filename(filename: str, pattern: str):
    if not filename:
        return None
    try:
        m = re.search(pattern, filename)
    except re.error:
        return None
    if not m:
        return None
    try:
        return round(float(m.group(1)), 2)
    except (ValueError, IndexError):
        return None


def extract_amount_from_custom_field(doc: dict, field_id):
    for cf in doc.get("custom_fields") or []:
        if cf.get("field") == field_id:
            return parse_amount(cf.get("value"))
    return None


def fetch_and_prepare_paperless_docs(client, amount_detection: dict) -> list[dict]:
    """Holt alle Paperless-Dokumente und ermittelt je Dokument Datum
    (Rechnungsdatum = 'created'), Korrespondent-Name und Vergleichsbetrag
    gemaess der gewaehlten Erkennungsmethode (Dateiname-Regex oder Custom
    Field)."""
    raw_docs = client.get_all_documents()
    method = amount_detection.get("method", "filename_regex")
    pattern = amount_detection.get("regex_pattern") or r"_EUR(\d+\.\d+)"
    field_id = amount_detection.get("custom_field_id")

    try:
        correspondents_by_id = {c["id"]: c.get("name") for c in client.get_correspondents()}
    except Exception:
        correspondents_by_id = {}

    prepared = []
    for doc in raw_docs:
        date_val = parse_paperless_date(doc.get("created"))
        if method == "custom_field" and field_id is not None:
            amount_val = extract_amount_from_custom_field(doc, field_id)
        else:
            filename = doc.get("original_file_name") or doc.get("title") or ""
            amount_val = extract_amount_from_filename(filename, pattern)
        prepared.append(
            {
                "id": doc.get("id"),
                "title": doc.get("title"),
                "original_file_name": doc.get("original_file_name"),
                "date": date_val,
                "amount": amount_val,
                "correspondent_name": correspondents_by_id.get(doc.get("correspondent")),
            }
        )
    return prepared


def match_transactions(transactions: list[dict], paperless_docs: list[dict]) -> None:
    """Gleicht alle Transaktionen ab, die noch nicht manuell entschieden
    wurden (tagged/uploaded/matched bleiben unangetastet). Aendert die
    uebergebenen Transaktions-Dicts in-place.

    Match-Regel: NUR der Betrag muss exakt uebereinstimmen (auf 2
    Nachkommastellen gerundet) - kein Zeitfenster/Datumsabgleich, Belege
    koennen beliebig lange vor oder nach der Buchung in Paperless liegen.
    Tritt der Betrag mehrfach auf, wird NICHT geraten - die Transaktion
    wandert als "unclear" in Fehlt/Unklar. Jedes Paperless-Dokument wird
    hoechstens einer Transaktion zugeordnet.
    """
    candidates_pool = [d for d in paperless_docs if d["amount"] is not None]
    used_doc_ids = set()

    # Bereits zugeordnete Dokumente (automatischer Match ODER manuell
    # aufgeloester Mehrfach-Match aus einem frueheren Abgleich) gelten als
    # "vergeben", noch BEVOR die eigentliche Zuordnungsschleife laeuft -
    # sonst koennten sie bei einem erneuten Abgleich (z.B. nach neu
    # hochgeladenen Belegen) einer ANDEREN, noch offenen Transaktion mit
    # demselben Betrag angeboten werden.
    for tx in transactions:
        if tx["status"] in ("tagged", "uploaded", "matched"):
            for doc in tx.get("matched_docs") or []:
                used_doc_ids.add(doc["id"])

    for tx in transactions:
        # "matched" bewusst mit ausgenommen: sonst wuerde ein erneuter
        # Abgleich eine manuell aus mehreren Kandidaten aufgeloeste
        # Zuordnung (siehe Controller.on_ambiguous_doc_selected) wieder
        # verwerfen, sobald der Betrag weiterhin mehrfach vorkommt - was er
        # bei einem echten Mehrfach-Match fast immer tut.
        if tx["status"] in ("tagged", "uploaded", "matched"):
            continue

        candidates = [
            d
            for d in candidates_pool
            if d["id"] not in used_doc_ids and round(d["amount"], 2) == round(tx["amount_abs"], 2)
        ]

        if len(candidates) == 1:
            doc = candidates[0]
            used_doc_ids.add(doc["id"])
            tx["status"] = "matched"
            tx["matched_docs"] = [doc]
            tx["candidate_docs"] = None
            tx["note"] = ""
        elif len(candidates) == 0:
            tx["status"] = "missing"
            tx["matched_docs"] = []
            tx["candidate_docs"] = None
            tx["note"] = ""
        else:
            tx["status"] = "unclear"
            tx["matched_docs"] = []
            tx["candidate_docs"] = candidates  # fuer die manuelle Auswahl in der UI
            tx["note"] = "Achtung: Betrag tritt mehrfach auf - Bitte manuell zuordnen"
