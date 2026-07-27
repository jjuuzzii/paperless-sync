"""Tests fuer core/matcher.py - Prioritaet 2 (siehe CLAUDE.md/Prompt 8):
Kernlogik des automatischen Abgleichs. Deckt exakten Betragsabgleich,
Mehrfachtreffer, Duplikat-Erkennung, Toleranz-Matching (per Default
deaktiviert, siehe config_manager.DEFAULT_CONFIG) und Teilzahlungs-Vorschlaege
(ebenfalls per Default deaktiviert) ab - beide Heuristiken werden hier
SOWOHL aktiviert als auch im ausgelieferten Default-Zustand getestet."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from paperless_sync.core.matcher import (
    build_transactions,
    find_split_payment_candidates,
    find_tolerant_candidates,
    flag_duplicate_suspects,
    match_transactions,
    normalize_purpose,
    renumber_transactions,
)
from paperless_sync.core.match_candidate import MatchReasonType
from paperless_sync.core.tx_status import TxStatus

from conftest import make_document, make_transaction


# --- normalize_purpose -------------------------------------------------------

def test_normalize_purpose_strips_digits_and_normalizes_whitespace():
    assert normalize_purpose("Rechnung 12345   vom  01.02.2026") == "RECHNUNG VOM .."


def test_normalize_purpose_same_text_different_reference_numbers_match():
    a = normalize_purpose("Miete Referenz 998877")
    b = normalize_purpose("Miete Referenz 112233")
    assert a == b


# --- flag_duplicate_suspects --------------------------------------------------

def test_flag_duplicate_suspects_identical_bookings():
    tx1 = make_transaction(id_="001", amount=-50.0, purpose="Einkauf Supermarkt", date_=date(2026, 1, 10))
    tx2 = make_transaction(id_="002", amount=-50.0, purpose="Einkauf Supermarkt", date_=date(2026, 1, 10))
    count = flag_duplicate_suspects([tx1, tx2])
    assert count == 2
    assert tx1["status"] == TxStatus.DUPLICATE_SUSPECT
    assert tx2["status"] == TxStatus.DUPLICATE_SUSPECT
    assert tx1["candidate_docs"][0]["related_transaction_id"] == "002"
    assert tx2["candidate_docs"][0]["related_transaction_id"] == "001"


def test_flag_duplicate_suspects_different_amount_not_flagged():
    tx1 = make_transaction(id_="001", amount=-50.0, purpose="Einkauf", date_=date(2026, 1, 10))
    tx2 = make_transaction(id_="002", amount=-51.0, purpose="Einkauf", date_=date(2026, 1, 10))
    count = flag_duplicate_suspects([tx1, tx2])
    assert count == 0
    assert tx1["status"] == TxStatus.UNRESOLVED
    assert tx2["status"] == TxStatus.UNRESOLVED


def test_flag_duplicate_suspects_opposite_sign_not_flagged():
    # +50 und -50 sind KEIN Duplikat-Verdacht (z.B. Buchung + Rueckbuchung)
    tx1 = make_transaction(id_="001", amount=50.0, purpose="Einkauf", date_=date(2026, 1, 10))
    tx2 = make_transaction(id_="002", amount=-50.0, purpose="Einkauf", date_=date(2026, 1, 10))
    count = flag_duplicate_suspects([tx1, tx2])
    assert count == 0


def test_flag_duplicate_suspects_ignores_tagged_and_matched():
    tx1 = make_transaction(id_="001", amount=-50.0, purpose="Einkauf", date_=date(2026, 1, 10), status=TxStatus.TAGGED)
    tx2 = make_transaction(id_="002", amount=-50.0, purpose="Einkauf", date_=date(2026, 1, 10))
    count = flag_duplicate_suspects([tx1, tx2])
    assert count == 1
    assert tx1["status"] == TxStatus.TAGGED  # unangetastet
    assert tx2["status"] == TxStatus.DUPLICATE_SUSPECT


# --- match_transactions: exakter Betrag --------------------------------------

def test_match_transactions_single_exact_match():
    tx = make_transaction(amount=-50.0)
    doc = make_document(id_=1, amount=50.0)
    match_transactions([tx], [doc])
    assert tx["status"] == TxStatus.MATCHED
    assert tx["matched_docs"] == [doc]
    assert tx["candidate_docs"] is None


def test_match_transactions_multiple_exact_matches_needs_manual_pick():
    tx = make_transaction(amount=-50.0)
    doc1 = make_document(id_=1, amount=50.0)
    doc2 = make_document(id_=2, amount=50.0)
    match_transactions([tx], [doc1, doc2])
    assert tx["status"] == TxStatus.MULTI_MATCH
    assert tx["matched_docs"] == []
    assert len(tx["candidate_docs"]) == 2
    assert all(c["reason_type"] == MatchReasonType.EXACT_AMOUNT_MULTI.value for c in tx["candidate_docs"])


def test_match_transactions_no_match_stays_unresolved():
    tx = make_transaction(amount=-50.0)
    doc = make_document(id_=1, amount=999.0)
    match_transactions([tx], [doc])
    assert tx["status"] == TxStatus.UNRESOLVED
    assert tx["candidate_docs"] is None


def test_match_transactions_skips_tagged_and_matched_and_duplicate_suspect():
    tagged = make_transaction(id_="001", amount=-50.0, status=TxStatus.TAGGED)
    already_matched = make_transaction(id_="002", amount=-60.0, status=TxStatus.MATCHED, matched_docs=[make_document(id_=9, amount=60.0)])
    duplicate = make_transaction(id_="003", amount=-70.0, status=TxStatus.DUPLICATE_SUSPECT)
    doc = make_document(id_=1, amount=50.0)
    match_transactions([tagged, already_matched, duplicate], [doc])
    assert tagged["status"] == TxStatus.TAGGED
    assert tagged["matched_docs"] == []
    assert duplicate["status"] == TxStatus.DUPLICATE_SUSPECT


def test_match_transactions_document_used_only_once():
    tx1 = make_transaction(id_="001", amount=-50.0)
    tx2 = make_transaction(id_="002", amount=-50.0)
    doc = make_document(id_=1, amount=50.0)
    match_transactions([tx1, tx2], [doc])
    matched = [t for t in (tx1, tx2) if t["status"] == TxStatus.MATCHED]
    unresolved = [t for t in (tx1, tx2) if t["status"] == TxStatus.UNRESOLVED]
    assert len(matched) == 1
    assert len(unresolved) == 1


def test_match_transactions_already_matched_doc_not_reoffered():
    # Bereits fest zugeordnetes Dokument darf bei erneutem Abgleich keiner
    # ANDEREN offenen Buchung mit gleichem Betrag angeboten werden.
    locked = make_transaction(id_="001", amount=-50.0, status=TxStatus.MATCHED, matched_docs=[make_document(id_=1, amount=50.0)])
    open_tx = make_transaction(id_="002", amount=-50.0)
    match_transactions([locked, open_tx], [make_document(id_=1, amount=50.0)])
    assert open_tx["status"] == TxStatus.UNRESOLVED


# --- match_transactions: Toleranz-Matching (Default: deaktiviert) -----------

def test_match_transactions_tolerant_matching_disabled_by_default_stays_unresolved():
    # config_manager.DEFAULT_CONFIG: amount_matching.enabled = False (siehe
    # CLAUDE.md-Verlauf - fachlich zu oft unpassende Vorschlaege) - ohne
    # explizites enabled=True bleibt ein 2-EUR-Unterschied UNRESOLVED.
    tx = make_transaction(amount=-50.0, date_=date(2026, 1, 1))
    doc = make_document(id_=1, amount=52.0, date_=date(2026, 1, 11))
    match_transactions([tx], [doc], amount_matching=None)
    assert tx["status"] == TxStatus.UNRESOLVED


def test_match_transactions_tolerant_matching_enabled_suggests_not_auto_matches():
    # 2 EUR Abweichung, 10 Tage Datumsdifferenz (Datum spielt bewusst keine
    # Rolle, siehe find_tolerant_candidates-Docstring) - bei aktivierter
    # Toleranz erscheint das als VORSCHLAG (MULTI_MATCH), nie automatisch
    # MATCHED, auch wenn nur ein einziger Kandidat gefunden wird.
    tx = make_transaction(amount=-50.0, date_=date(2026, 1, 1))
    doc = make_document(id_=1, amount=52.0, date_=date(2026, 1, 11))
    amount_matching = {"enabled": True, "tolerance_abs": 5.0, "tolerance_pct": 0.03, "top_n_candidates": 3}
    match_transactions([tx], [doc], amount_matching=amount_matching)
    assert tx["status"] == TxStatus.MULTI_MATCH
    assert tx["matched_docs"] == []
    assert len(tx["candidate_docs"]) == 1
    assert tx["candidate_docs"][0]["reason_type"] == MatchReasonType.TOLERANT_AMOUNT.value


def test_match_transactions_tolerant_matching_outside_tolerance_unresolved():
    tx = make_transaction(amount=-50.0)
    doc = make_document(id_=1, amount=80.0)
    amount_matching = {"enabled": True, "tolerance_abs": 5.0, "tolerance_pct": 0.03, "top_n_candidates": 3}
    match_transactions([tx], [doc], amount_matching=amount_matching)
    assert tx["status"] == TxStatus.UNRESOLVED


def test_find_tolerant_candidates_sorted_by_confidence_desc():
    tx = make_transaction(amount=-50.0)
    close = make_document(id_=1, amount=51.0)
    far = make_document(id_=2, amount=54.0)
    result = find_tolerant_candidates(tx, [far, close], used_doc_ids=set(), tolerance_abs=5.0, tolerance_pct=0.03, top_n=3)
    assert [c["documents"][0]["id"] for c in result] == [1, 2]


# --- find_split_payment_candidates (Default: deaktiviert, hier aktiv getestet) --

def test_split_payment_case_a_one_doc_covers_two_bookings():
    # EIN Beleg = Summe zweier Buchungen (z.B. Anzahlung + Restzahlung)
    tx1 = make_transaction(id_="001", amount=-30.0, date_=date(2026, 1, 5))
    tx2 = make_transaction(id_="002", amount=-20.0, date_=date(2026, 1, 8))
    doc = make_document(id_=1, amount=50.0, date_=date(2026, 1, 6))
    amount_matching = {"tolerance_abs": 0.5, "tolerance_pct": 0.0, "top_n_candidates": 3}
    split_config = {"day_window": 14, "max_documents": 2, "max_pool_size": 8}
    count = find_split_payment_candidates([tx1, tx2], [doc], amount_matching, split_config)
    assert count == 2
    assert tx1["status"] == TxStatus.SPLIT_PAYMENT
    assert tx2["status"] == TxStatus.SPLIT_PAYMENT
    assert tx1["candidate_docs"][0]["related_transaction_id"] == "002"


def test_split_payment_case_b_one_booking_covered_by_two_docs():
    # EINE Buchung = Summe zweier Belege
    tx = make_transaction(id_="001", amount=-50.0, date_=date(2026, 1, 5))
    doc1 = make_document(id_=1, amount=30.0, date_=date(2026, 1, 4))
    doc2 = make_document(id_=2, amount=20.0, date_=date(2026, 1, 6))
    amount_matching = {"tolerance_abs": 0.5, "tolerance_pct": 0.0, "top_n_candidates": 3}
    split_config = {"day_window": 14, "max_documents": 2, "max_pool_size": 8}
    count = find_split_payment_candidates([tx], [doc1, doc2], amount_matching, split_config)
    assert count == 1
    assert tx["status"] == TxStatus.SPLIT_PAYMENT
    assert tx["candidate_docs"][0]["reason_type"] == MatchReasonType.SPLIT_PAYMENT_TX_SUM.value
    assert {d["id"] for d in tx["candidate_docs"][0]["documents"]} == {1, 2}


def test_split_payment_outside_day_window_not_flagged():
    tx1 = make_transaction(id_="001", amount=-30.0, date_=date(2026, 1, 1))
    tx2 = make_transaction(id_="002", amount=-20.0, date_=date(2026, 3, 1))  # weit ausserhalb
    doc = make_document(id_=1, amount=50.0, date_=date(2026, 1, 1))
    amount_matching = {"tolerance_abs": 0.5, "tolerance_pct": 0.0, "top_n_candidates": 3}
    split_config = {"day_window": 14, "max_documents": 2, "max_pool_size": 8}
    count = find_split_payment_candidates([tx1, tx2], [doc], amount_matching, split_config)
    assert count == 0
    assert tx1["status"] == TxStatus.UNRESOLVED
    assert tx2["status"] == TxStatus.UNRESOLVED


def test_split_payment_only_runs_on_unresolved_transactions():
    matched = make_transaction(id_="001", amount=-30.0, status=TxStatus.MATCHED, matched_docs=[make_document(id_=9, amount=30.0)])
    tx2 = make_transaction(id_="002", amount=-20.0, date_=date(2026, 1, 8))
    doc = make_document(id_=1, amount=50.0, date_=date(2026, 1, 6))
    amount_matching = {"tolerance_abs": 0.5, "tolerance_pct": 0.0, "top_n_candidates": 3}
    split_config = {"day_window": 14, "max_documents": 2, "max_pool_size": 8}
    count = find_split_payment_candidates([matched, tx2], [doc], amount_matching, split_config)
    assert count == 0  # matched-Partner faellt raus, tx2 allein ergibt keine Summe


# --- build_transactions / renumber_transactions ------------------------------

def test_build_transactions_comma_decimal_and_ddmmyyyy():
    df = pd.DataFrame(
        {
            "Datum": ["15.01.2026", "10.01.2026"],
            "Betrag": ["-50,00", "-20,00"],
            "Verwendungszweck": ["B", "A"],
        }
    )
    mapping = {"date_column": "Datum", "amount_column": "Betrag", "purpose_column": "Verwendungszweck"}
    txs = build_transactions(df, mapping)
    assert len(txs) == 2
    # chronologisch sortiert
    assert txs[0]["date"] == date(2026, 1, 10)
    assert txs[0]["amount_raw"] == -20.0
    assert txs[0]["status"] == TxStatus.UNRESOLVED


def test_build_transactions_dot_decimal_and_iso_date():
    df = pd.DataFrame(
        {
            "Date": ["2026-01-15", "2026-01-10"],
            "Amount": ["-50.00", "-20.00"],
            "Purpose": ["B", "A"],
        }
    )
    mapping = {"date_column": "Date", "amount_column": "Amount", "purpose_column": "Purpose"}
    txs = build_transactions(df, mapping)
    assert len(txs) == 2
    assert txs[0]["amount_raw"] == -20.0


def test_build_transactions_deterministic_format_when_provided():
    df = pd.DataFrame({"Datum": ["03.05.2026"], "Betrag": ["1.234,56"], "Zweck": ["X"]})
    mapping = {
        "date_column": "Datum", "amount_column": "Betrag", "purpose_column": "Zweck",
        "date_order": "DMY", "date_separator": ".",
        "amount_decimal_separator": ",", "amount_thousands_separator": ".",
    }
    txs = build_transactions(df, mapping)
    assert txs[0]["date"] == date(2026, 5, 3)
    assert txs[0]["amount_raw"] == 1234.56


def test_build_transactions_skips_unparseable_rows():
    df = pd.DataFrame({"Datum": ["nicht ein datum", "10.01.2026"], "Betrag": ["-10,00", "-20,00"], "Zweck": ["a", "b"]})
    mapping = {"date_column": "Datum", "amount_column": "Betrag", "purpose_column": "Zweck"}
    txs = build_transactions(df, mapping)
    assert len(txs) == 1
    assert txs[0]["purpose"] == "b"


def test_renumber_transactions_ids_and_display_numbers_per_month():
    tx_jan1 = make_transaction(date_=date(2026, 1, 5))
    tx_jan2 = make_transaction(date_=date(2026, 1, 20))
    tx_feb1 = make_transaction(date_=date(2026, 2, 1))
    txs = [tx_feb1, tx_jan2, tx_jan1]
    renumber_transactions(txs)
    assert [t["id"] for t in txs] == ["001", "002", "003"]  # chronologisch neu sortiert
    assert txs[0]["display_number"] == "001"  # Jan, 1.
    assert txs[1]["display_number"] == "002"  # Jan, 2.
    assert txs[2]["display_number"] == "001"  # Feb, 1.
