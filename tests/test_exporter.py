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
    _build_jahresuebersicht_csv,
    _build_jahresuebersicht_pdf,
    _build_kontoauszug_csv,
    _build_offene_posten_csv,
    _build_offene_posten_jahr_csv,
    _build_uebersicht_csv,
    _dedupe_filename,
    _export_receipts,
    _get_pdf_files,
    _receipt_filename,
    _recipient_name,
    _sanitize_filename,
    count_open_items,
    export_fiscal_year,
    fiscal_year_folder_name,
    fiscal_year_label,
    fiscal_year_open_items_summary,
    generate_export,
    get_fiscal_year_months,
    month_folder_name,
    zip_export_folder,
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


# --- Jahresexport: get_fiscal_year_months / fiscal_year_folder_name --------

def test_get_fiscal_year_months_calendar_year():
    months = get_fiscal_year_months(2026, {"calendar_year": True, "start_month": 7})
    assert months == [f"2026-{m:02d}" for m in range(1, 13)]


def test_get_fiscal_year_months_deviating_fiscal_year_crosses_calendar_year():
    months = get_fiscal_year_months(2025, {"calendar_year": False, "start_month": 7})
    assert months == [
        "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    ]


def test_get_fiscal_year_months_folder_names_already_sort_chronologically():
    # Kein Umbenennen/Nummerieren noetig - siehe export_fiscal_year-Docstring:
    # das YYYY-MM-Praefix von month_folder_name() sortiert von selbst richtig,
    # auch ueber den Jahreswechsel hinweg.
    months = get_fiscal_year_months(2025, {"calendar_year": False, "start_month": 7})
    folder_names = [month_folder_name(m) for m in months]
    assert folder_names == sorted(folder_names)
    assert folder_names[0] == "2025-07_Juli"
    assert folder_names[-1] == "2026-06_Juni"


def test_fiscal_year_folder_name_calendar_year():
    assert fiscal_year_folder_name(2026, {"calendar_year": True}) == "Jahresexport_2026"


def test_fiscal_year_folder_name_deviating_fiscal_year():
    assert fiscal_year_folder_name(2025, {"calendar_year": False, "start_month": 7}) == "Jahresexport_2025-2026"


# --- export_fiscal_year / zip_export_folder ---------------------------------

def test_export_fiscal_year_calendar_year_creates_all_12_month_folders(tmp_path):
    transactions = [
        make_transaction(id_=f"{i:03d}", date_=date(2026, m, 10), amount=-10.0 * m, status=TxStatus.MATCHED, row_index=None)
        for i, m in enumerate((1, 6, 12), start=1)
    ]
    csv_df = pd.DataFrame({"Datum": [], "Betrag": []})
    client = FakePaperlessClient()

    year_root = export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True, "start_month": 7}, transactions, csv_df, ";", client
    )

    assert year_root == tmp_path / "Jahresexport_2026"
    month_dirs = sorted(p.name for p in year_root.iterdir() if p.is_dir())
    assert len(month_dirs) == 12
    assert month_dirs[0] == "2026-01_Januar"
    assert month_dirs[-1] == "2026-12_Dezember"
    # Transaktionen landen im richtigen Monatsordner
    jan_rows = _rows((year_root / "2026-01_Januar" / "00_Uebersicht.csv").read_bytes())
    assert len(jan_rows) - 1 == 1
    feb_rows = _rows((year_root / "2026-02_Februar" / "00_Uebersicht.csv").read_bytes())
    assert len(feb_rows) - 1 == 0


def test_export_fiscal_year_reports_progress_per_month_plus_summary_step(tmp_path):
    # Fuer die Fortschrittsanzeige in der UI (siehe
    # desktop_app_qt.FiscalYearExportWorker) - ein Aufruf pro Monat VOR
    # dessen Verarbeitung, plus ein abschliessender Aufruf fuer die
    # Jahres-Zusammenfassungen. 13 Aufrufe insgesamt bei 12 Monaten.
    calls = []
    export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True}, [], pd.DataFrame({"Datum": [], "Betrag": []}), ";",
        FakePaperlessClient(), on_progress=lambda step, total, label: calls.append((step, total, label)),
    )
    assert len(calls) == 13
    assert calls[0] == (1, 13, "Monat 1 von 12: Januar 2026")
    assert calls[11] == (12, 13, "Monat 12 von 12: Dezember 2026")
    assert calls[12][0] == 13
    assert calls[12][1] == 13
    assert "Zusammenfassungen" in calls[12][2]
    assert all(c[1] == 13 for c in calls)  # total bleibt ueber alle Aufrufe hinweg konstant


def test_export_fiscal_year_deviating_fiscal_year_spans_calendar_years(tmp_path):
    transactions = [
        make_transaction(id_="001", date_=date(2025, 7, 5), amount=-10.0, status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="002", date_=date(2026, 6, 20), amount=-20.0, status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="003", date_=date(2025, 12, 31), amount=-30.0, status=TxStatus.UNRESOLVED, row_index=None),
    ]
    csv_df = pd.DataFrame({"Datum": [], "Betrag": []})
    client = FakePaperlessClient()

    year_root = export_fiscal_year(
        tmp_path, 2025, {"calendar_year": False, "start_month": 7}, transactions, csv_df, ";", client
    )

    assert year_root == tmp_path / "Jahresexport_2025-2026"
    assert (year_root / "2025-07_Juli").is_dir()
    assert (year_root / "2025-12_Dezember").is_dir()
    assert (year_root / "2026-06_Juni").is_dir()
    assert not (year_root / "2025-01_Januar").exists()  # ausserhalb des Geschaeftsjahres


def test_export_fiscal_year_regenerates_months_from_scratch(tmp_path):
    # Anforderung 2: immer frisch aus den aktuellen Transaktionsdaten, egal
    # ob fuer diesen Monat schon einmal separat exportiert wurde.
    stale_tx = make_transaction(id_="001", date_=date(2026, 3, 5), status=TxStatus.MATCHED, row_index=None)
    generate_export(tmp_path, "2026-03", [stale_tx], pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient())
    old_uebersicht = (tmp_path / "2026-03_Maerz" / "00_Uebersicht.csv").read_bytes()
    assert len(_rows(old_uebersicht)) - 1 == 1

    fresh_transactions = [
        make_transaction(id_="001", date_=date(2026, 3, 5), status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="002", date_=date(2026, 3, 15), status=TxStatus.UNRESOLVED, row_index=None),
    ]
    year_root = export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True}, fresh_transactions, pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient()
    )
    new_uebersicht = _rows((year_root / "2026-03_Maerz" / "00_Uebersicht.csv").read_bytes())
    assert len(new_uebersicht) - 1 == 2  # neu erzeugt, nicht der alte Stand mit nur 1 Buchung


def test_zip_export_folder_preserves_relative_paths_after_extraction(tmp_path):
    tx = make_transaction(id_="001", date_=date(2026, 1, 5), amount=-50.0, status=TxStatus.MATCHED,
                           matched_docs=[{"id": 1, "original_file_name": "r.pdf"}], row_index=None)
    client = FakePaperlessClient({1: b"%PDF-1.4 fake"})
    export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True}, [tx], pd.DataFrame({"Datum": [], "Betrag": []}), ";", client
    )
    year_root = tmp_path / "Jahresexport_2026"

    zip_bytes = zip_export_folder(year_root)

    unzip_dir = tmp_path / "unzipped_elsewhere"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(unzip_dir)

    reopened_root = unzip_dir / "Jahresexport_2026"
    uebersicht_rows = _rows((reopened_root / "2026-01_Januar" / "00_Uebersicht.csv").read_bytes())
    rel_path = uebersicht_rows[1][3]
    assert rel_path
    assert (reopened_root / "2026-01_Januar" / rel_path).exists()


# --- 00_Jahresuebersicht.csv -------------------------------------------------

def test_build_jahresuebersicht_csv_combines_all_months(tmp_path):
    year_root = tmp_path / "Jahresexport_2026"
    transactions = []
    for month_str, purpose in (("2026-01", "Januar-Buchung"), ("2026-02", "Februar-Buchung")):
        tx = make_transaction(id_="001", date_=date(2026, int(month_str[-2:]), 10), purpose=purpose, row_index=None, counterparty="Muster Empfaenger GmbH")
        transactions.append(tx)
        generate_export(year_root, month_str, [tx], pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient())

    csv_bytes = _build_jahresuebersicht_csv(year_root, ["2026-01", "2026-02"], ";", transactions)
    rows = _rows(csv_bytes)
    assert rows[0] == [
        "Monat", "Relativer Pfad zum Monatsordner", "Datum", "Betrag", "Verwendungszweck",
        "Empfänger/Absender", "Zugeordneter Beleg (relativer Pfad)", "Status", "Tag",
    ]
    assert len(rows) - 1 == 2
    assert rows[1][0] == "2026-01"
    assert rows[1][1] == "2026-01_Januar"
    assert rows[1][4] == "Januar-Buchung"
    assert rows[1][5] == "Muster Empfaenger GmbH"
    assert rows[2][0] == "2026-02"
    assert rows[2][4] == "Februar-Buchung"


def test_build_jahresuebersicht_csv_skips_missing_month_folder(tmp_path):
    year_root = tmp_path / "Jahresexport_2026"
    tx = make_transaction(id_="001", date_=date(2026, 1, 10), row_index=None)
    generate_export(year_root, "2026-01", [tx], pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient())

    # "2026-02" wurde nie generiert (z.B. teilweiser Export) - darf nicht crashen
    csv_bytes = _build_jahresuebersicht_csv(year_root, ["2026-01", "2026-02"], ";", [tx])
    rows = _rows(csv_bytes)
    assert len(rows) - 1 == 1


def test_export_fiscal_year_writes_jahresuebersicht_with_all_transactions(tmp_path):
    transactions = [
        make_transaction(id_="001", date_=date(2026, 1, 5), purpose="Jan", status=TxStatus.MATCHED, row_index=None,
                          matched_docs=[{"id": 1, "original_file_name": "r.pdf"}], counterparty="Buero Muster GmbH"),
        make_transaction(id_="002", date_=date(2026, 6, 15), purpose="Jun", status=TxStatus.UNRESOLVED, row_index=None),
        make_transaction(id_="003", date_=date(2026, 12, 20), purpose="Dez", status=TxStatus.TAGGED, tag="PRIVAT", row_index=None),
    ]
    client = FakePaperlessClient({1: b"%PDF-1.4 fake"})

    year_root = export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True}, transactions, pd.DataFrame({"Datum": [], "Betrag": []}), ";", client
    )

    assert (year_root / "00_Jahresuebersicht.csv").exists()
    rows = _rows((year_root / "00_Jahresuebersicht.csv").read_bytes())
    assert len(rows) - 1 == 3  # alle drei Buchungen aus allen 12 Monaten, keine fehlt
    assert rows[0][5] == "Empfänger/Absender"
    purposes = {row[4] for row in rows[1:]}
    assert purposes == {"Jan", "Jun", "Dez"}
    jan_counterparty = next(row[5] for row in rows[1:] if row[4] == "Jan")
    assert jan_counterparty == "Buero Muster GmbH"
    jun_counterparty = next(row[5] for row in rows[1:] if row[4] == "Jun")
    assert jun_counterparty == ""  # kein Absender hinterlegt -> leer, nicht "None"

    # Von der Jahresuebersicht aus zum Beleg der Januar-Buchung zurueckverfolgen
    jan_row = next(row for row in rows[1:] if row[4] == "Jan")
    month_folder, beleg_pfad = jan_row[1], jan_row[6]
    assert (year_root / month_folder / beleg_pfad).exists()


# --- fiscal_year_label -------------------------------------------------------

def test_fiscal_year_label_calendar_year():
    assert fiscal_year_label(2026, {"calendar_year": True}) == "2026"


def test_fiscal_year_label_deviating_fiscal_year():
    assert fiscal_year_label(2025, {"calendar_year": False, "start_month": 7}) == "2025/2026"


# --- 00_Offene_Posten_Jahr.csv -----------------------------------------------

def _build_year_root_with_jahresuebersicht(tmp_path, month_transactions: dict[str, list[dict]]) -> Path:
    """Testhilfe: baut fuer jeden Monat generate_export() + danach die
    00_Jahresuebersicht.csv - Vorbedingung fuer _build_offene_posten_jahr_csv/
    _build_jahresuebersicht_pdf, die beide darauf aufbauen."""
    year_root = tmp_path / "Jahresexport_2026"
    year_root.mkdir(parents=True, exist_ok=True)
    all_transactions = []
    for month_str, txs in month_transactions.items():
        generate_export(year_root, month_str, txs, pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient())
        all_transactions.extend(txs)
    month_strs = get_fiscal_year_months(2026, {"calendar_year": True})
    (year_root / "00_Jahresuebersicht.csv").write_bytes(
        _build_jahresuebersicht_csv(year_root, month_strs, ";", all_transactions)
    )
    return year_root, month_strs


def test_build_offene_posten_jahr_csv_only_open_items_with_month_summary(tmp_path):
    month_transactions = {
        "2026-02": [make_transaction(id_="001", date_=date(2026, 2, 10), status=TxStatus.UNRESOLVED, row_index=None)],
        "2026-03": [
            make_transaction(id_="002", date_=date(2026, 3, 15), status=TxStatus.MULTI_MATCH, row_index=None),
            make_transaction(id_="003", date_=date(2026, 3, 20), status=TxStatus.DUPLICATE_SUSPECT, row_index=None),
            make_transaction(id_="004", date_=date(2026, 3, 25), status=TxStatus.MATCHED, row_index=None),  # nicht offen
        ],
    }
    year_root, month_strs = _build_year_root_with_jahresuebersicht(tmp_path, month_transactions)

    csv_bytes = _build_offene_posten_jahr_csv(year_root, month_strs, ";")
    rows = _rows(csv_bytes)

    assert rows[0] == ["Zusammenfassung offene Posten pro Monat"]
    # NUR Monate MIT offenen Posten werden aufgelistet, kein "0 offene
    # Posten" fuer jeden erledigten/leeren Monat (siehe Chat) - Kopfblock
    # ist daher genau 2 Zeilen lang (Februar + Maerz).
    detail_header_idx = next(i for i, r in enumerate(rows) if r == ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    month_summary_lines = [r[0] for r in rows[1:detail_header_idx] if r]
    assert month_summary_lines == ["Februar 2026: 1 unresolved", "März 2026: 1 multi_match, 1 duplicate_suspect"]

    detail_rows = rows[detail_header_idx + 1:]
    assert len(detail_rows) == 3  # nur die drei offenen, MATCHED fehlt
    assert all(row[0] in ("2026-02", "2026-03") for row in detail_rows)


def test_build_offene_posten_jahr_csv_empty_when_everything_resolved(tmp_path):
    month_transactions = {
        "2026-01": [make_transaction(id_="001", date_=date(2026, 1, 10), status=TxStatus.MATCHED, row_index=None)],
    }
    year_root, month_strs = _build_year_root_with_jahresuebersicht(tmp_path, month_transactions)

    csv_bytes = _build_offene_posten_jahr_csv(year_root, month_strs, ";")
    rows = _rows(csv_bytes)

    assert rows[0] == ["Zusammenfassung offene Posten pro Monat"]
    assert rows[1] == ["Keine offenen Posten im gesamten Geschäftsjahr."]
    detail_header_idx = next(i for i, r in enumerate(rows) if r == ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    assert rows[detail_header_idx + 1:] == []


# --- 00_Jahresuebersicht.pdf --------------------------------------------------

def test_build_jahresuebersicht_pdf_produces_valid_pdf_with_open_items(tmp_path):
    month_transactions = {
        "2026-02": [make_transaction(id_="001", date_=date(2026, 2, 10), status=TxStatus.UNRESOLVED, row_index=None)],
    }
    year_root, month_strs = _build_year_root_with_jahresuebersicht(tmp_path, month_transactions)

    pdf_bytes = _build_jahresuebersicht_pdf(year_root, month_strs, "2026", ";", "Musterfirma GmbH", None)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_build_jahresuebersicht_pdf_handles_no_open_items(tmp_path):
    month_transactions = {
        "2026-01": [make_transaction(id_="001", date_=date(2026, 1, 10), status=TxStatus.MATCHED, row_index=None)],
    }
    year_root, month_strs = _build_year_root_with_jahresuebersicht(tmp_path, month_transactions)

    pdf_bytes = _build_jahresuebersicht_pdf(year_root, month_strs, "2026", ";", "", None)
    assert pdf_bytes.startswith(b"%PDF")


def test_build_jahresuebersicht_pdf_handles_missing_logo_gracefully(tmp_path):
    year_root, month_strs = _build_year_root_with_jahresuebersicht(tmp_path, {})
    pdf_bytes = _build_jahresuebersicht_pdf(year_root, month_strs, "2026", ";", "Musterfirma GmbH", Path("does/not/exist.png"))
    assert pdf_bytes.startswith(b"%PDF")


# --- export_fiscal_year: alle drei Jahres-Zusammenfassungen -----------------

def test_export_fiscal_year_writes_all_three_summary_files(tmp_path):
    transactions = [
        make_transaction(id_="001", date_=date(2026, 2, 10), status=TxStatus.UNRESOLVED, row_index=None),
        make_transaction(id_="002", date_=date(2026, 5, 5), status=TxStatus.MATCHED, row_index=None),
    ]
    year_root = export_fiscal_year(
        tmp_path, 2026, {"calendar_year": True}, transactions, pd.DataFrame({"Datum": [], "Betrag": []}), ";",
        FakePaperlessClient(), company_name="Musterfirma GmbH", logo_path=None,
    )

    assert (year_root / "00_Jahresuebersicht.csv").exists()
    assert (year_root / "00_Offene_Posten_Jahr.csv").exists()
    pdf_bytes = (year_root / "00_Jahresuebersicht.pdf").read_bytes()
    assert pdf_bytes.startswith(b"%PDF")

    offene_rows = _rows((year_root / "00_Offene_Posten_Jahr.csv").read_bytes())
    detail_header_idx = next(i for i, r in enumerate(offene_rows) if r == ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    detail_rows = offene_rows[detail_header_idx + 1:]
    assert len(detail_rows) == 1  # nur die UNRESOLVED-Buchung, nicht die MATCHED


# --- Schritt 6: offene Posten in mehreren Monaten/Status-Typen, beide Geschaeftsjahr-Varianten --

def _mixed_status_transactions(base_year: int) -> list[dict]:
    """Offene Posten in DREI verschiedenen Monaten mit VIER verschiedenen
    Status-Typen, dazwischen erledigte Buchungen und ein Monat ganz ohne
    Buchungen - fuer die Schritt-6-Vorgabe 'mindestens drei verschiedene
    Monate und unterschiedliche Status-Typen'."""
    return [
        make_transaction(id_="001", date_=date(base_year, 1, 5), status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="002", date_=date(base_year, 2, 10), status=TxStatus.UNRESOLVED, row_index=None),
        make_transaction(id_="003", date_=date(base_year, 3, 12), status=TxStatus.MULTI_MATCH, row_index=None),
        make_transaction(id_="004", date_=date(base_year, 3, 20), status=TxStatus.DUPLICATE_SUSPECT, row_index=None),
        make_transaction(id_="005", date_=date(base_year, 5, 1), status=TxStatus.TAGGED, tag="PRIVAT", row_index=None),
        make_transaction(id_="006", date_=date(base_year, 6, 18), status=TxStatus.SPLIT_PAYMENT, row_index=None),
    ]


def test_fiscal_year_open_items_appear_correctly_across_months_calendar_year(tmp_path):
    transactions = _mixed_status_transactions(2026)
    fiscal_config = {"calendar_year": True}
    month_strs = get_fiscal_year_months(2026, fiscal_config)

    # Vorab-Warnung (siehe desktop_app_qt._on_export_fiscal_year_click) wuerde feuern -
    # ein Eintrag PRO MONAT mit offenen Posten, nicht pro offener Buchung (Maerz hat 2).
    total_open, months_with_open = fiscal_year_open_items_summary(transactions, month_strs)
    assert total_open == 4
    assert months_with_open == ["Februar 2026", "März 2026", "Juni 2026"]

    year_root = export_fiscal_year(
        tmp_path, 2026, fiscal_config, transactions, pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient(),
    )

    offene_rows = _rows((year_root / "00_Offene_Posten_Jahr.csv").read_bytes())
    detail_header_idx = next(i for i, r in enumerate(offene_rows) if r == ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    # NUR Monate MIT offenen Posten im Kopfblock - Januar (MATCHED), Mai
    # (TAGGED) und April (keine Buchungen) tauchen dort bewusst NICHT auf.
    month_summary_lines = [r[0] for r in offene_rows[1:detail_header_idx] if r]
    assert month_summary_lines == [
        "Februar 2026: 1 unresolved", "März 2026: 1 multi_match, 1 duplicate_suspect", "Juni 2026: 1 split_payment",
    ]

    detail_rows = offene_rows[detail_header_idx + 1:]
    assert len(detail_rows) == 4  # exakt die vier offenen, MATCHED/TAGGED fehlen komplett
    grounds = {row[5] for row in detail_rows}
    assert grounds == {"Offen", "Mehrere Kandidaten - Auswahl nötig", "Möglicher Duplikat-Fall", "Mögliche Teilzahlung"}

    pdf_bytes = (year_root / "00_Jahresuebersicht.pdf").read_bytes()
    assert pdf_bytes.startswith(b"%PDF")


def test_fiscal_year_open_items_across_calendar_year_boundary_deviating_fiscal_year(tmp_path):
    # Wirtschaftsjahr Juli 2025 - Juni 2026: dieselben relativen Monate wie
    # oben (Monat 2/3/3/6 des Geschaeftsjahres), aber ueber den
    # Kalenderjahreswechsel hinweg (Februar/Maerz/Juni 2026 statt 2025).
    fiscal_config = {"calendar_year": False, "start_month": 7}
    transactions = [
        make_transaction(id_="001", date_=date(2025, 7, 5), status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="002", date_=date(2026, 2, 10), status=TxStatus.UNRESOLVED, row_index=None),
        make_transaction(id_="003", date_=date(2026, 3, 12), status=TxStatus.MULTI_MATCH, row_index=None),
        make_transaction(id_="004", date_=date(2026, 3, 20), status=TxStatus.DUPLICATE_SUSPECT, row_index=None),
        make_transaction(id_="005", date_=date(2026, 6, 18), status=TxStatus.SPLIT_PAYMENT, row_index=None),
    ]
    month_strs = get_fiscal_year_months(2025, fiscal_config)
    total_open, months_with_open = fiscal_year_open_items_summary(transactions, month_strs)
    assert total_open == 4
    assert set(months_with_open) == {"Februar 2026", "März 2026", "Juni 2026"}

    year_root = export_fiscal_year(tmp_path, 2025, fiscal_config, transactions, pd.DataFrame({"Datum": [], "Betrag": []}), ";", FakePaperlessClient())
    assert year_root.name == "Jahresexport_2025-2026"
    assert (year_root / "2025-07_Juli").is_dir()
    assert (year_root / "2026-02_Februar").is_dir()
    assert (year_root / "2026-06_Juni").is_dir()

    offene_rows = _rows((year_root / "00_Offene_Posten_Jahr.csv").read_bytes())
    detail_header_idx = next(i for i, r in enumerate(offene_rows) if r == ["Monat", "Datum", "Betrag", "Verwendungszweck", "Empfänger/Absender", "Grund"])
    detail_rows = offene_rows[detail_header_idx + 1:]
    assert len(detail_rows) == 4
    assert {row[0] for row in detail_rows} == {"2026-02", "2026-03", "2026-06"}


def test_fiscal_year_no_warning_when_everything_resolved(tmp_path):
    # Vorab-Warnung darf NICHT feuern, wenn nichts offen ist.
    transactions = [
        make_transaction(id_="001", date_=date(2026, 1, 5), status=TxStatus.MATCHED, row_index=None),
        make_transaction(id_="002", date_=date(2026, 6, 1), status=TxStatus.TAGGED, tag="EINZAHLUNG", row_index=None),
    ]
    fiscal_config = {"calendar_year": True}
    month_strs = get_fiscal_year_months(2026, fiscal_config)
    total_open, months_with_open = fiscal_year_open_items_summary(transactions, month_strs)
    assert total_open == 0
    assert months_with_open == []
