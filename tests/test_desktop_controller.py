"""Tests fuer state/desktop_controller.py - Re-Import/Duplikat-Schutz/
Konto-Mismatch-Warnung/CSV-Archivierung sowie die Tag-/Beleg-Zuordnungs-
Aktionen. Nutzt die tmp_app_dirs/app_state/controller-Fixtures aus
conftest.py (isoliertes %APPDATA%-Aequivalent, gefakter Keyring)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from paperless_sync.core.tx_status import TxStatus


class FakeClient:
    """Minimaler Paperless-Client-Stand-in fuer Controller-Tests - protokolliert
    Aufrufe, statt echtes Netzwerk zu brauchen."""

    def __init__(self, documents=None):
        self._documents = documents or []
        self.uploaded = []
        self.tag_calls = []
        self._tag_ids = {}
        self._next_tag_id = 100

    def get_all_documents(self):
        return self._documents

    def get_correspondents(self):
        return []

    def upload_document(self, file_bytes, filename):
        self.uploaded.append((file_bytes, filename))

    def get_or_create_tag(self, name):
        if name not in self._tag_ids:
            self._tag_ids[name] = self._next_tag_id
            self._next_tag_id += 1
        return self._tag_ids[name]

    def add_tag_to_document(self, doc_id, tag_id):
        self.tag_calls.append(("add", doc_id, tag_id))

    def remove_tag_from_document(self, doc_id, tag_id):
        self.tag_calls.append(("remove", doc_id, tag_id))

    def update_custom_field_value(self, doc_id, field_id, value):
        self.tag_calls.append(("custom_field", doc_id, field_id, value))


def _write_csv(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_bytes(content.encode("utf-8-sig"))
    return str(path)


CSV_HEADER = "Datum;Betrag;Verwendungszweck;IBAN Auftragskonto\n"


def _csv_row(datum, betrag, zweck, iban="DE1111"):
    return f"{datum};{betrag};{zweck};{iban}\n"


# --- CSV-Import: neues Mapping / bereits bekanntes Mapping -------------------

def test_on_csv_upload_unknown_columns_requires_mapping(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    result = controller.on_csv_upload(path)
    assert result["mapping_ready"] is False
    assert "column_profiles" in result


def test_on_mapping_confirm_builds_transactions_and_saves_mapping(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    added, duplicates = controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    assert added == 1
    assert duplicates == 0
    assert len(controller.state.transactions) == 1
    assert controller.state.transactions[0]["amount_raw"] == -50.0
    col_sig = "Betrag|Datum|IBAN Auftragskonto|Verwendungszweck"
    assert col_sig in controller.state.config["csv_mappings"]


def test_second_upload_same_columns_uses_saved_mapping_automatically(controller, tmp_path):
    path1 = _write_csv(tmp_path, "bank1.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path1)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)

    path2 = _write_csv(tmp_path, "bank2.csv", CSV_HEADER + _csv_row("15.01.2026", "-30,00", "Miete"))
    result = controller.on_csv_upload(path2)
    assert result["mapping_ready"] is True
    assert result["added"] == 1
    assert len(controller.state.transactions) == 2


def test_reuploading_identical_file_is_a_noop(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    assert len(controller.state.transactions) == 1

    result = controller.on_csv_upload(path)  # exakt dieselbe Datei nochmal
    assert result["mapping_ready"] is True
    assert len(controller.state.transactions) == 1  # unveraendert, keine erneute Verarbeitung


# --- Duplikat-Schutz bei ueberlappenden Importen ----------------------------

def test_reimport_with_overlapping_bookings_does_not_duplicate(controller, tmp_path):
    path1 = _write_csv(
        tmp_path, "bank1.csv",
        CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf") + _csv_row("11.01.2026", "-20,00", "Bäcker"),
    )
    controller.on_csv_upload(path1)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    assert len(controller.state.transactions) == 2

    # zweite CSV enthaelt eine der beiden Buchungen erneut (ueberlappender
    # Export-Zeitraum) + eine wirklich neue
    path2 = _write_csv(
        tmp_path, "bank2.csv",
        CSV_HEADER + _csv_row("11.01.2026", "-20,00", "Baecker (leicht anderer Text)") + _csv_row("12.01.2026", "-99,00", "Neu"),
    )
    result = controller.on_csv_upload(path2)
    assert result["added"] == 1
    assert result["duplicates"] == 1
    assert len(controller.state.transactions) == 3


def test_reimport_preserves_existing_tags_and_matches(controller, tmp_path):
    # Regressionsschutz fuer den in der Doku erwaehnten realen Bug: ein
    # weiterer Import darf bereits geleistete Tag-/Match-Arbeit nie verwerfen.
    path1 = _write_csv(tmp_path, "bank1.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path1)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]
    controller.on_mark_private(tx_id)
    assert controller.state.transactions[0]["status"] == TxStatus.TAGGED

    path2 = _write_csv(tmp_path, "bank2.csv", CSV_HEADER + _csv_row("15.01.2026", "-30,00", "Miete"))
    controller.on_csv_upload(path2)

    tx = next(t for t in controller.state.transactions if t["id"] == tx_id or t["purpose"] == "Einkauf")
    assert tx["status"] == TxStatus.TAGGED
    assert tx["tag"] == "PRIVAT"


# --- Konto-Mismatch-Warnung --------------------------------------------------

def test_account_mismatch_detected_on_different_iban(controller, tmp_path):
    path1 = _write_csv(tmp_path, "bank1.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf", iban="DE1111"))
    controller.on_csv_upload(path1)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)

    path2 = _write_csv(tmp_path, "bank2.csv", CSV_HEADER + _csv_row("15.01.2026", "-30,00", "Miete", iban="DE2222"))
    result = controller.on_csv_upload(path2)
    assert result["account_mismatch"] is not None
    assert "DE1111" in result["account_mismatch"]
    assert "DE2222" in result["account_mismatch"]


def test_no_account_mismatch_when_iban_unchanged(controller, tmp_path):
    path1 = _write_csv(tmp_path, "bank1.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf", iban="DE1111"))
    controller.on_csv_upload(path1)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)

    path2 = _write_csv(tmp_path, "bank2.csv", CSV_HEADER + _csv_row("15.01.2026", "-30,00", "Miete", iban="DE1111"))
    result = controller.on_csv_upload(path2)
    assert result["account_mismatch"] is None


# --- CSV-Archivierung nach input/ -------------------------------------------

def test_csv_upload_archives_raw_file_into_input_dir(controller, tmp_path):
    path = _write_csv(tmp_path, "meine_bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    archived = list(controller.state.input_dir.glob("*_meine_bank.csv"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == Path(path).read_bytes()


def test_reuploading_identical_file_does_not_create_second_archive_copy(controller, tmp_path):
    path = _write_csv(tmp_path, "meine_bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_csv_upload(path)  # identische Datei nochmal
    archived = list(controller.state.input_dir.glob("*_meine_bank.csv"))
    assert len(archived) == 1


def test_external_import_archives_as_csv(controller):
    df = pd.DataFrame({"Datum": ["10.01.2026"], "Betrag": ["-50,00"], "Verwendungszweck": ["Bank-API"]})
    mapping = {"date_column": "Datum", "amount_column": "Betrag", "purpose_column": "Verwendungszweck"}
    added, duplicates = controller.on_external_import(df, mapping)
    assert added == 1
    archived = list(controller.state.input_dir.glob("*_enable_banking_import.csv"))
    assert len(archived) == 1


def test_external_import_respects_duplicate_protection(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)

    df = pd.DataFrame({"Datum": ["10.01.2026"], "Betrag": ["-50,00"], "Verwendungszweck": ["Einkauf (Bank-API-Text)"]})
    mapping = {"date_column": "Datum", "amount_column": "Betrag", "purpose_column": "Verwendungszweck"}
    added, duplicates = controller.on_external_import(df, mapping)
    assert added == 0
    assert duplicates == 1
    assert len(controller.state.transactions) == 1


# --- Tags ---------------------------------------------------------------

def test_on_apply_tag_builtin(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "100,00", "Gehalt"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]

    controller.on_apply_tag(tx_id, "EINZAHLUNG")
    tx = controller.state.transactions[0]
    assert tx["status"] == TxStatus.TAGGED
    assert tx["tag"] == "EINZAHLUNG"


def test_on_apply_tag_custom_tracks_usage_count(controller, tmp_path):
    path = _write_csv(
        tmp_path, "bank.csv",
        CSV_HEADER + _csv_row("10.01.2026", "-10,00", "Spende A") + _csv_row("11.01.2026", "-20,00", "Spende B"),
    )
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx1, tx2 = controller.state.transactions

    controller.on_apply_tag(tx1["id"], "Spende")
    controller.on_apply_tag(tx2["id"], "Spende")
    assert controller.state.config["custom_tags"]["Spende"] == 2
    assert controller.top_custom_tags() == ["Spende"]


def test_suggest_learned_tags_applies_to_matching_purpose(controller, tmp_path):
    path = _write_csv(
        tmp_path, "bank.csv",
        CSV_HEADER + _csv_row("10.01.2026", "-10,00", "Miete Referenz 123") + _csv_row("11.02.2026", "-10,00", "Miete Referenz 456"),
    )
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx1, tx2 = controller.state.transactions

    controller.on_apply_tag(tx1["id"], "UMBUCHUNG")
    assert tx2["suggested_tag"] == "UMBUCHUNG"


# --- Beleg-Zuordnung ---------------------------------------------------------

def test_on_pdf_drop_uploads_and_marks_matched(controller, tmp_path):
    controller.state.client = FakeClient()
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]

    pdf_path = tmp_path / "beleg.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    controller.on_pdf_drop(tx_id, str(pdf_path))

    tx = controller.state.transactions[0]
    assert tx["status"] == TxStatus.MATCHED
    assert tx["uploaded_name"] == "beleg.pdf"
    assert controller.state.client.uploaded == [(b"%PDF-1.4 fake", "beleg.pdf")]


def test_on_documents_selected_attaches_docs_and_applies_success_tag(controller, tmp_path):
    client = FakeClient()
    controller.state.client = client
    controller.state.config["paperless_success_tag_enabled"] = True
    controller.state.config["paperless_success_tag_name"] = "Abgeglichen"
    controller.state.paperless_docs_raw = [{"id": 1, "title": "Rechnung", "original_file_name": "r.pdf"}]

    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]

    controller.on_documents_selected(tx_id, [1])
    tx = controller.state.transactions[0]
    assert tx["status"] == TxStatus.MATCHED
    assert tx["matched_docs"][0]["id"] == 1
    assert ("add", 1, 100) in client.tag_calls


def test_on_remove_doc_reverts_to_unresolved_when_last_doc_removed(controller, tmp_path):
    client = FakeClient()
    controller.state.client = client
    controller.state.config["paperless_success_tag_enabled"] = False
    controller.state.paperless_docs_raw = [{"id": 1, "title": "Rechnung", "original_file_name": "r.pdf"}]

    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]
    controller.on_documents_selected(tx_id, [1])

    controller.on_remove_doc(tx_id, 1)
    tx = controller.state.transactions[0]
    assert tx["matched_docs"] == []
    assert tx["status"] == TxStatus.UNRESOLVED


def test_on_undo_resolution_resets_transaction(controller, tmp_path):
    path = _write_csv(tmp_path, "bank.csv", CSV_HEADER + _csv_row("10.01.2026", "-50,00", "Einkauf"))
    controller.on_csv_upload(path)
    controller.on_mapping_confirm("Datum", "Betrag", "Verwendungszweck", None)
    tx_id = controller.state.transactions[0]["id"]
    controller.on_mark_private(tx_id)

    controller.on_undo_resolution(tx_id)
    tx = controller.state.transactions[0]
    assert tx["status"] == TxStatus.UNRESOLVED
    assert tx["tag"] is None
    # gelerntes Tag-Muster bleibt bestehen (bewusst, siehe Docstring)
    assert controller.state.config["purpose_tag_memory"]


def test_on_generate_export_click_raises_without_transactions(controller):
    with pytest.raises(RuntimeError):
        controller.on_generate_export_click("2026-01")
