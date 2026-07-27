"""Tests fuer core/exporter.py - Prioritaet 3 (siehe CLAUDE.md/Prompt 8):
Der monatliche Steuerberater-Export. Deckt alle Bausteinfunktionen sowie
einen vollstaendigen End-zu-Ende-Durchlauf mit ALLEN sechs Status-Faellen ab,
inkl. eines echten Zip/Entpack-Durchlaufs, der bestaetigt, dass die relativen
Pfade in 00_Uebersicht.csv auch ausserhalb des Original-Ordners noch
aufloesbar sind."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from paperless_sync.core.exporter import (
    _build_einzahlungen_csv,
    _build_getaggte_ohne_beleg_csv,
    _build_kontoauszug_csv,
    _build_offene_posten_csv,
    _build_uebersicht_csv,
    _dedupe_filename,
    _export_receipts,
    _get_pdf_files,
    _receipt_filename,
    _recipient_name,
    _sanitize_filename,
    count_open_items,
    generate_export,
    month_folder_name,
)
from paperless_sync.core.match_candidate import MatchCandidate, MatchReasonType
from paperless_sync.core.tx_status import TxStatus

from conftest import FakePaperlessClient, make_transaction


# --- kleine Bausteine ---------------------------------------------------

def test_month_folder_name():
    assert month_folder_name("2026-01") == "2026-01_Januar"
    assert month_folder_name("2026-12") == "2026-12_Dezember"


def test_count_open_items_only_counts_open_statuses_in_month():
    txs = [
        make_transaction(id_="001", date_=date(2026, 1, 5), status=TxStatus.UNRESOLVED),
        make_transaction(id_="002", date_=date(2026, 1, 6), status=TxStatus.MATCHED),
        make_transaction(id_="003", date_=date(2026, 1, 7), status=TxStatus.MULTI_MATCH),
        make_transaction(id_="004", date_=date(2026, 2, 1), status=TxStatus.UNRESOLVED),  # anderer Monat
    ]
    assert count_open_items(txs, "2026-01") == 2


def test_sanitize_filename_replaces_illegal_chars_and_truncates():
    assert _sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert _sanitize_filename("x" * 100, max_length=10) == "x" * 10
    assert _sanitize_filename("   ", fallback="beleg") == "beleg"


def test_recipient_name_prefers_counterparty_over_purpose():
    tx = make_transaction(purpose="Rechnung", counterparty="Mueller GmbH")
    assert _recipient_name(tx) == "Mueller GmbH"


def test_recipient_name_falls_back_to_purpose():
    tx = make_transaction(purpose="Einkauf beim Baecker", counterparty="")
    assert _recipient_name(tx) == "Einkauf beim Baecker"


def test_recipient_name_strips_iban_and_bic():
    tx = make_transaction(purpose="Miete IBAN: DE89370400440532013000 BIC: COBADEFFXXX", counterparty="")
    result = _recipient_name(tx)
    assert "DE89370400440532013000" not in result
    assert "COBADEFFXXX" not in result


def test_recipient_name_truncated_and_unbekannt_fallback():
    tx = make_transaction(purpose="x" * 100, counterparty="")
    assert len(_recipient_name(tx)) <= 40
    tx_empty = make_transaction(purpose="", counterparty="")
    assert _recipient_name(tx_empty) == "Unbekannt"


def test_dedupe_filename_appends_counter_on_collision():
    used = set()
    assert _dedupe_filename("beleg", ".pdf", used) == "beleg.pdf"
    assert _dedupe_filename("beleg", ".pdf", used) == "beleg_2.pdf"
    assert _dedupe_filename("beleg", ".pdf", used) == "beleg_3.pdf"


def test_receipt_filename_scheme():
    tx = make_transaction(date_=date(2026, 3, 15), amount=-42.5, purpose="Einkauf", counterparty="Netto")
    used = set()
    filename = _receipt_filename(tx, multi_suffix="", used_names=used)
    assert filename == "2026-03-15_EUR42.50_Netto.pdf"


def test_receipt_filename_multi_suffix_for_multiple_docs_same_booking():
    tx = make_transaction(date_=date(2026, 3, 15), amount=-42.5, counterparty="Netto")
    used = set()
    f1 = _receipt_filename(tx, multi_suffix="_1", used_names=used)
    f2 = _receipt_filename(tx, multi_suffix="_2", used_names=used)
    assert f1 == "2026-03-15_EUR42.50_Netto_1.pdf"
    assert f2 == "2026-03-15_EUR42.50_Netto_2.pdf"


def test_get_pdf_files_uploaded_bytes_takes_precedence():
    tx = make_transaction(uploaded_bytes=b"%PDF upload", uploaded_name="upload.pdf", matched_docs=[{"id": 1, "original_file_name": "x.pdf"}])
    files = _get_pdf_files(tx, client=None)
    assert files == [(b"%PDF upload", "upload.pdf")]


def test_get_pdf_files_downloads_matched_docs_from_client():
    client = FakePaperlessClient({1: b"doc-1-bytes", 2: b"doc-2-bytes"})
    tx = make_transaction(matched_docs=[{"id": 1, "original_file_name": "a.pdf"}, {"id": 2, "original_file_name": "b.pdf"}])
    files = _get_pdf_files(tx, client)
    assert files == [(b"doc-1-bytes", "a.pdf"), (b"doc-2-bytes", "b.pdf")]


# --- CSV-Bausteine --------------------------------------------------------

def _rows(csv_bytes: bytes, delimiter=";") -> list[list[str]]:
    text = csv_bytes.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def test_build_uebersicht_csv_contains_all_transactions_regardless_of_status():
    txs = [
        make_transaction(id_="001", date_=date(2026, 1, 5), status=TxStatus.MATCHED),
        make_transaction(id_="002", date_=date(2026, 1, 6), status=TxStatus.TAGGED, tag="PRIVAT"),
        make_transaction(id_="003", date_=date(2026, 1, 7), status=TxStatus.UNRESOLVED),
        make_transaction(id_="004", date_=date(2026, 1, 8), status=TxStatus.MULTI_MATCH),
        make_transaction(id_="005", date_=date(2026, 1, 9), status=TxStatus.DUPLICATE_SUSPECT),
        make_transaction(id_="006", date_=date(2026, 1, 10), status=TxStatus.SPLIT_PAYMENT),
    ]
    csv_bytes = _build_uebersicht_csv(txs, ";", receipt_paths={})
    rows = _rows(csv_bytes)
    assert rows[0] == ["Datum", "Betrag", "Verwendungszweck", "Zugeordneter Beleg (relativer Pfad)", "Status", "Tag"]
    assert len(rows) - 1 == 6  # Header + alle 6 Buchungen, unabhaengig vom Status


def test_build_uebersicht_csv_includes_receipt_path():
    tx = make_transaction(id_="001", status=TxStatus.MATCHED)
    csv_bytes = _build_uebersicht_csv([tx], ";", receipt_paths={"001": ["01_Belege_zugeordnet/beleg.pdf"]})
    rows = _rows(csv_bytes)
    assert rows[1][3] == "01_Belege_zugeordnet/beleg.pdf"


def test_build_getaggte_ohne_beleg_csv_only_tagged():
    txs = [
        make_transaction(id_="001", status=TxStatus.TAGGED, tag="PRIVAT"),
        make_transaction(id_="002", status=TxStatus.MATCHED),
    ]
    rows = _rows(_build_getaggte_ohne_beleg_csv(txs, ";"))
    assert len(rows) - 1 == 1
    assert rows[1][3] == "PRIVAT"


def test_build_offene_posten_csv_only_open_statuses():
    txs = [
        make_transaction(id_="001", status=TxStatus.UNRESOLVED),
        make_transaction(id_="002", status=TxStatus.MULTI_MATCH),
        make_transaction(id_="003", status=TxStatus.DUPLICATE_SUSPECT),
        make_transaction(id_="004", status=TxStatus.SPLIT_PAYMENT),
        make_transaction(id_="005", status=TxStatus.MATCHED),
        make_transaction(id_="006", status=TxStatus.TAGGED, tag="PRIVAT"),
    ]
    rows = _rows(_build_offene_posten_csv(txs, ";"))
    assert len(rows) - 1 == 4


def test_build_einzahlungen_csv_only_tagged_einzahlung():
    txs = [
        make_transaction(id_="001", status=TxStatus.TAGGED, tag="EINZAHLUNG"),
        make_transaction(id_="002", status=TxStatus.TAGGED, tag="PRIVAT"),
        make_transaction(id_="003", status=TxStatus.MATCHED),
    ]
    rows = _rows(_build_einzahlungen_csv(txs, ";"))
    assert len(rows) - 1 == 1


def test_build_kontoauszug_csv_skips_none_row_index():
    csv_df = pd.DataFrame({"Datum": ["05.01.2026", "06.01.2026"], "Betrag": ["-10,00", "-20,00"]})
    txs = [
        make_transaction(id_="001", row_index=0),
        make_transaction(id_="002", row_index=None),  # z.B. per Bank-API importiert
    ]
    result = _build_kontoauszug_csv(txs, csv_df, ";")
    text = result.decode("utf-8-sig")
    filtered = pd.read_csv(io.StringIO(text), sep=";")
    assert len(filtered) == 1


def test_build_kontoauszug_csv_empty_when_no_row_indices():
    csv_df = pd.DataFrame({"Datum": ["05.01.2026"], "Betrag": ["-10,00"]})
    txs = [make_transaction(id_="001", row_index=None)]
    result = _build_kontoauszug_csv(txs, csv_df, ";")
    assert result == "".encode("utf-8-sig")


# --- generate_export: End-zu-Ende mit allen 6 Status-Faellen --------------

@pytest.fixture
def full_month_transactions():
    doc = {"id": 1, "original_file_name": "rechnung.pdf", "title": "Rechnung"}
    return [
        make_transaction(id_="001", date_=date(2026, 1, 5), amount=-50.0, purpose="Einkauf", counterparty="Supermarkt",
                          status=TxStatus.MATCHED, matched_docs=[doc]),
        make_transaction(id_="002", date_=date(2026, 1, 6), amount=100.0, purpose="Gehalt", status=TxStatus.TAGGED, tag="EINZAHLUNG"),
        make_transaction(id_="003", date_=date(2026, 1, 7), amount=-30.0, purpose="Bargeld", status=TxStatus.TAGGED, tag="PRIVAT"),
        make_transaction(id_="004", date_=date(2026, 1, 8), amount=-20.0, purpose="Unklar", status=TxStatus.UNRESOLVED),
        make_transaction(id_="005", date_=date(2026, 1, 9), amount=-75.0, purpose="Mehrere Kandidaten", status=TxStatus.MULTI_MATCH,
                          candidate_docs=[MatchCandidate(MatchReasonType.EXACT_AMOUNT_MULTI, 1.0, "x", documents=[doc]).to_dict()]),
        make_transaction(id_="006", date_=date(2026, 1, 10), amount=-15.0, purpose="Doppelt", status=TxStatus.DUPLICATE_SUSPECT),
        make_transaction(id_="007", date_=date(2026, 1, 11), amount=-40.0, purpose="Teilzahlung", status=TxStatus.SPLIT_PAYMENT),
    ]


def test_generate_export_full_month_all_statuses(tmp_path, full_month_transactions):
    csv_df = pd.DataFrame({"Datum": [f"{i:02d}.01.2026" for i in range(5, 12)], "Betrag": ["x"] * 7})
    for i, tx in enumerate(full_month_transactions):
        tx["row_index"] = i
    client = FakePaperlessClient({1: b"%PDF-1.4 fake receipt"})

    export_root = generate_export(tmp_path, "2026-01", full_month_transactions, csv_df, ";", client)

    assert export_root == tmp_path / "2026-01_Januar"
    assert (export_root / "00_Uebersicht.csv").exists()
    assert (export_root / "01_Belege_zugeordnet").is_dir()
    assert (export_root / "02_Ohne_Beleg_getaggt" / "notizen_getaggte_buchungen.csv").exists()
    assert (export_root / "03_Kontoauszug_gefiltert.csv").exists()
    assert (export_root / "04_Offene_Posten.csv").exists()
    assert (export_root / "05_Einzahlungen_Deposit.csv").exists()

    uebersicht_rows = _rows((export_root / "00_Uebersicht.csv").read_bytes())
    assert len(uebersicht_rows) - 1 == 7  # ALLE Buchungen, keine fehlt

    receipt_files = list((export_root / "01_Belege_zugeordnet").iterdir())
    assert len(receipt_files) == 1
    assert receipt_files[0].name == "2026-01-05_EUR50.00_Supermarkt.pdf"

    offene_posten_rows = _rows((export_root / "04_Offene_Posten.csv").read_bytes())
    assert len(offene_posten_rows) - 1 == 4  # UNRESOLVED, MULTI_MATCH, DUPLICATE_SUSPECT, SPLIT_PAYMENT

    einzahlungen_rows = _rows((export_root / "05_Einzahlungen_Deposit.csv").read_bytes())
    assert len(einzahlungen_rows) - 1 == 1

    getaggt_rows = _rows((export_root / "02_Ohne_Beleg_getaggt" / "notizen_getaggte_buchungen.csv").read_bytes())
    assert len(getaggt_rows) - 1 == 2  # EINZAHLUNG + PRIVAT


def test_generate_export_zip_and_reopen_relative_paths_resolve(tmp_path, full_month_transactions):
    csv_df = pd.DataFrame({"Datum": [f"{i:02d}.01.2026" for i in range(5, 12)], "Betrag": ["x"] * 7})
    for i, tx in enumerate(full_month_transactions):
        tx["row_index"] = i
    client = FakePaperlessClient({1: b"%PDF-1.4 fake receipt"})

    export_root = generate_export(tmp_path, "2026-01", full_month_transactions, csv_df, ";", client)

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file_path in export_root.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(export_root.parent))

    unzip_dir = tmp_path / "unzipped_elsewhere"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(unzip_dir)

    reopened_root = unzip_dir / "2026-01_Januar"
    uebersicht_rows = _rows((reopened_root / "00_Uebersicht.csv").read_bytes())
    relative_paths = [row[3] for row in uebersicht_rows[1:] if row[3]]
    assert relative_paths  # mindestens der eine MATCHED-Beleg hat einen Pfad
    for rel_path in relative_paths:
        assert (reopened_root / rel_path).exists()


def test_generate_export_only_includes_transactions_from_requested_month(tmp_path):
    jan_tx = make_transaction(id_="001", date_=date(2026, 1, 5), status=TxStatus.MATCHED, row_index=0)
    feb_tx = make_transaction(id_="002", date_=date(2026, 2, 5), status=TxStatus.UNRESOLVED, row_index=1)
    csv_df = pd.DataFrame({"Datum": ["05.01.2026", "05.02.2026"], "Betrag": ["x", "x"]})
    client = FakePaperlessClient()
    export_root = generate_export(tmp_path, "2026-01", [jan_tx, feb_tx], csv_df, ";", client)
    rows = _rows((export_root / "00_Uebersicht.csv").read_bytes())
    assert len(rows) - 1 == 1
