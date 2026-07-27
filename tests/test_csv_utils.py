"""Tests fuer core/csv_utils.py - Prioritaet 1 (siehe CLAUDE.md/Prompt 8):
Encoding-/Trennzeichen-Erkennung, Betrags-/Datumsparsing (Einzelwert UND
spaltenweit), da fehlerhaftes Parsing hier direkt zu falschen Buchungsbetraegen
im Steuerberater-Export fuehren wuerde."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from paperless_sync.core.csv_utils import (
    detect_amount_column_format,
    detect_currency_marker,
    detect_date_column_format,
    detect_encoding_and_delimiter,
    parse_amount,
    parse_amount_with_format,
    parse_date,
    parse_date_with_format,
    preview_rows,
    profile_columns,
    read_csv_raw,
)
from conftest import FakeUpload


# --- detect_encoding_and_delimiter ------------------------------------------

def test_detect_encoding_utf8_sig_bom():
    raw = "Datum;Betrag\n01.01.2026;10,00\n".encode("utf-8-sig")
    text, encoding, delimiter = detect_encoding_and_delimiter(raw)
    assert encoding == "utf-8-sig"
    assert delimiter == ";"
    assert "Datum" in text


def test_detect_encoding_cp1252_umlaut():
    raw = "Empfaenger;Betrag\nMüller GmbH;10,00\n".encode("cp1252")
    text, encoding, delimiter = detect_encoding_and_delimiter(raw)
    assert encoding == "cp1252"
    assert "Müller" in text


def test_detect_delimiter_comma():
    raw = b"Date,Amount,Purpose\n2026-01-01,10.00,Test\n"
    _, _, delimiter = detect_encoding_and_delimiter(raw)
    assert delimiter == ","


def test_detect_delimiter_semicolon():
    raw = "Datum;Betrag;Verwendungszweck\n01.01.2026;10,00;Test\n".encode("utf-8")
    _, _, delimiter = detect_encoding_and_delimiter(raw)
    assert delimiter == ";"


# --- read_csv_raw ------------------------------------------------------------

def test_read_csv_raw_semicolon_de():
    raw = "Datum;Betrag;Verwendungszweck\n01.01.2026;-10,00;Testbuchung\n".encode("utf-8-sig")
    df, encoding, delimiter, raw_bytes = read_csv_raw(FakeUpload(raw))
    assert delimiter == ";"
    assert list(df.columns) == ["Datum", "Betrag", "Verwendungszweck"]
    assert df.iloc[0]["Betrag"] == "-10,00"
    assert raw_bytes == raw


def test_read_csv_raw_comma_en():
    raw = b"Date,Amount,Purpose\n2026-01-15,-10.00,Test\n"
    df, encoding, delimiter, _ = read_csv_raw(FakeUpload(raw))
    assert delimiter == ","
    assert df.iloc[0]["Amount"] == "-10.00"


def test_read_csv_raw_missing_values_become_empty_string():
    raw = b"Date,Amount,Purpose\n2026-01-15,,\n"
    df, _, _, _ = read_csv_raw(FakeUpload(raw))
    assert df.iloc[0]["Amount"] == ""
    assert df.iloc[0]["Purpose"] == ""


# --- parse_amount (Einzelwert, best effort) ---------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("-50,00", -50.0),
        ("123,45 €", 123.45),
        ("EUR123.45", 123.45),
        ("10", 10.0),
        ("0,5", 0.5),
    ],
)
def test_parse_amount_formats(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "-"])
def test_parse_amount_invalid(raw):
    assert parse_amount(raw) is None


# --- parse_date (Einzelwert, best effort, day-first) ------------------------

def test_parse_date_ddmmyyyy():
    assert parse_date("25.03.2026") == date(2026, 3, 25)


def test_parse_date_yyyymmdd():
    assert parse_date("2026-03-25") == date(2026, 3, 25)


def test_parse_date_yyyymmdd_ambiguous_day_not_swapped_with_month():
    # Regressionstest: dateutil's dayfirst=True vertauscht Tag/Monat
    # faelschlich, sobald beide <=12 sind, obwohl der erste Block (Jahr)
    # das Format strukturell eindeutig als ISO YYYY-MM-DD festlegt - z.B.
    # wurde '2026-01-10' vorher faelschlich als 10. Oktober gelesen.
    assert parse_date("2026-01-10") == date(2026, 1, 10)
    assert parse_date("2026-11-03") == date(2026, 11, 3)
    assert parse_date("2026-02-01") == date(2026, 2, 1)


def test_parse_date_invalid():
    assert parse_date("") is None
    assert parse_date(None) is None


# --- detect_amount_column_format (spaltenweit) ------------------------------

def test_detect_amount_column_both_separators_de():
    info = detect_amount_column_format(["1.234,56", "10,00", "-5,00"])
    assert info.decimal_separator == ","
    assert info.thousands_separator == "."
    assert info.confidence == "high"
    assert info.is_ambiguous is False


def test_detect_amount_column_both_separators_en():
    info = detect_amount_column_format(["1,234.56", "10.00", "-5.00"])
    assert info.decimal_separator == "."
    assert info.thousands_separator == ","
    assert info.confidence == "high"


def test_detect_amount_column_single_separator_non_3digit_is_decimal():
    # ',5' hat nur 1 Nachkommastelle -> kann kein Tausendertrenner sein
    info = detect_amount_column_format(["10,5", "20,00", "5,5"])
    assert info.decimal_separator == ","
    assert info.thousands_separator is None
    assert info.confidence == "high"


def test_detect_amount_column_ambiguous_all_3digit_groups():
    # Jeder Wert endet zufaellig 3-stellig -> nicht entscheidbar
    info = detect_amount_column_format(["1.234", "2.500", "10.000"])
    assert info.confidence == "assumed"
    assert info.is_ambiguous is True


def test_detect_amount_column_no_separator_at_all():
    info = detect_amount_column_format(["10", "20", "30"])
    assert info.confidence == "ambiguous"
    assert info.is_ambiguous is True


def test_parse_amount_with_format_wrong_format_fails_visibly():
    # Deterministisch: falsches Format -> None, nicht stillschweigend falsch runden
    assert parse_amount_with_format("abc", ",") is None
    assert parse_amount_with_format("", ",") is None


# --- detect_currency_marker --------------------------------------------------

def test_detect_currency_marker_foreign():
    assert detect_currency_marker("USD 123.45") == "USD"
    assert detect_currency_marker("$50") == "USD"
    assert detect_currency_marker("£20") == "GBP"


def test_detect_currency_marker_eur_is_none():
    assert detect_currency_marker("123,45 €") is None
    assert detect_currency_marker("EUR 10,00") is None
    assert detect_currency_marker("10,00") is None


# --- detect_date_column_format (spaltenweit) --------------------------------

def test_detect_date_column_iso_ymd():
    info = detect_date_column_format(["2026-01-15", "2026-03-05"])
    assert info.order == "YMD"
    assert info.confidence == "high"


def test_detect_date_column_dmy_confirmed_by_day_over_12():
    # 25 kann kein Monat sein -> beweist DMY fuer die ganze Spalte
    info = detect_date_column_format(["25.03.2026", "03.05.2026"])
    assert info.order == "DMY"
    assert info.confidence == "high"


def test_detect_date_column_mdy_confirmed_by_middle_over_12():
    info = detect_date_column_format(["03/25/2026", "05/03/2026"])
    assert info.order == "MDY"
    assert info.confidence == "high"


def test_detect_date_column_genuinely_ambiguous():
    # Jede Zeile hat beide Bloecke <=12 -> nicht entscheidbar
    info = detect_date_column_format(["03.05.2026", "01.02.2026"])
    assert info.is_ambiguous is True
    assert info.confidence == "assumed"
    assert info.order == "DMY"  # Punkt-Trenner -> DMY-Annahme


def test_detect_date_column_empty():
    info = detect_date_column_format([])
    assert info.order is None
    assert info.confidence == "ambiguous"


def test_parse_date_with_format_rejects_wrong_separator():
    # Spalte nutzt durchgehend '.', ein Wert mit '/' ist vermutlich verrutscht
    assert parse_date_with_format("03/05/2026", "DMY", separator=".") is None


def test_parse_date_with_format_two_digit_year():
    assert parse_date_with_format("15.03.26", "DMY", separator=".") == date(2026, 3, 15)
    assert parse_date_with_format("15.03.75", "DMY", separator=".") == date(1975, 3, 15)


# --- profile_columns / preview_rows (Smoke-Test) ----------------------------

def test_profile_columns_recognizes_roles():
    df = pd.DataFrame(
        {
            "Buchungstag": ["01.01.2026", "02.01.2026"],
            "Betrag": ["-10,00", "-25,50"],
            "Verwendungszweck": ["Einkauf", "Miete"],
        }
    )
    profiles = {p.name: p for p in profile_columns(df)}
    assert profiles["Buchungstag"].guessed_role == "date"
    assert profiles["Betrag"].guessed_role == "amount"
    assert profiles["Verwendungszweck"].guessed_role == "purpose"


def test_preview_rows_limits_and_converts():
    df = pd.DataFrame({"A": [1, 2, 3]})
    rows = preview_rows(df, n=2)
    assert len(rows) == 2
    assert rows[0] == {"A": 1}
