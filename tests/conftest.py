"""Gemeinsame Fixtures/Hilfsfunktionen fuer die gesamte Testsuite - siehe
tests/README.md fuer Ausfuehrung. Alle Testdaten sind frei erfunden, keine
echten Kontodaten. pythonpath=src kommt aus pytest.ini, kein manueller
sys.path-Eingriff hier noetig."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FakeUpload:
    """Adapter fuer csv_utils.read_csv_raw (erwartet ein Objekt mit
    .getvalue(), wie Streamlits UploadedFile) - siehe
    desktop_controller._BytesUpload, hier unabhaengig nachgebaut, um keine
    private Klasse aus einem anderen Modul zu importieren."""

    def __init__(self, data: bytes):
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


class FakePaperlessClient:
    """Ersetzt paperless_client.PaperlessClient in exporter-Tests - liefert
    feste PDF-Bytes statt eines echten Downloads, kein Netzwerk noetig."""

    def __init__(self, documents: dict | None = None):
        self._documents = documents or {}

    def download_document(self, doc_id: int) -> bytes:
        return self._documents.get(doc_id, b"%PDF-1.4 fake receipt content")


def make_transaction(
    id_="001",
    display_number=None,
    date_=date(2026, 1, 15),
    amount=-50.0,
    purpose="Testbuchung",
    counterparty="",
    status=None,
    tag=None,
    matched_docs=None,
    candidate_docs=None,
    uploaded_bytes=None,
    uploaded_name=None,
    row_index=0,
    foreign_currency=None,
):
    """Baut ein vollstaendiges Transaktions-Dict mit sinnvollen Defaults -
    fuer matcher-/exporter-Tests, die nicht jedes Mal alle Felder von Hand
    auflisten wollen. status=None -> TxStatus.UNRESOLVED (haeufigster
    Ausgangszustand)."""
    from paperless_sync.core.tx_status import TxStatus

    return {
        "id": id_,
        "display_number": display_number or id_,
        "date": date_,
        "amount_raw": amount,
        "amount_abs": abs(amount),
        "purpose": purpose,
        "counterparty": counterparty,
        "row_index": row_index,
        "foreign_currency": foreign_currency,
        "status": status if status is not None else TxStatus.UNRESOLVED,
        "note": "",
        "tag": tag,
        "suggested_tag": None,
        "matched_docs": matched_docs or [],
        "candidate_docs": candidate_docs,
        "uploaded_bytes": uploaded_bytes,
        "uploaded_name": uploaded_name,
    }


def make_document(id_=1, amount=50.0, date_=None, correspondent=None, title=None, original_file_name=None):
    """Baut ein vorbereitetes Paperless-Dokument-Dict, wie es
    matcher.fetch_and_prepare_paperless_docs() liefern wuerde."""
    return {
        "id": id_,
        "title": title or f"Beleg{id_}",
        "original_file_name": original_file_name or f"beleg{id_}.pdf",
        "date": date_,
        "amount": amount,
        "correspondent_name": correspondent,
    }


@pytest.fixture
def tmp_app_dirs(tmp_path, monkeypatch):
    """Isoliert AppState/Controller-/Backup-Tests vollstaendig vom echten
    %APPDATA% - insbesondere get_enable_banking_key_path() nutzt IMMER den
    echten Plattform-Pfad, unabhaengig von get_base_dir() (siehe
    config_manager.py) - Enable-Banking-relevante Tests muessen deshalb
    IMMER den key_path-Config-Override verwenden, nie den Default-Pfad.
    Fakt ausserdem den Keyring, damit Tests nicht das echte OS-Credential-
    Backend anfassen."""
    import paperless_sync.core.credential_store as cs

    fake_store: dict = {}
    monkeypatch.setattr(cs, "is_keyring_available", lambda: True)
    monkeypatch.setattr(cs, "set_secret", lambda name, value: fake_store.__setitem__(name, value))
    monkeypatch.setattr(cs, "get_secret", lambda name: fake_store.get(name))
    monkeypatch.setattr(cs, "delete_secret", lambda name: fake_store.pop(name, None))

    import paperless_sync.state.desktop_state as ds

    monkeypatch.setattr(ds, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(ds, "get_resource_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app_state(tmp_app_dirs):
    from paperless_sync.state.desktop_state import AppState

    return AppState()


@pytest.fixture
def controller(app_state):
    from paperless_sync.state.desktop_controller import Controller

    return Controller(app_state)
