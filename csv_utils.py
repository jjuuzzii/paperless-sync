"""Universeller CSV-Import: Encoding-/Trennzeichen-Erkennung und Wertparsing."""
from __future__ import annotations

import csv
import io
import re

import pandas as pd
from dateutil import parser as dateparser

ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
DELIMITER_CANDIDATES = [";", ",", "\t", "|"]


def detect_encoding_and_delimiter(raw_bytes: bytes):
    """Probiert gaengige Encodings und ermittelt das Trennzeichen per Sniffer
    (mit Haeufigkeits-Fallback, falls der Sniffer scheitert)."""
    text = None
    used_encoding = None
    for enc in ENCODING_CANDIDATES:
        try:
            text = raw_bytes.decode(enc)
            used_encoding = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw_bytes.decode("latin-1", errors="replace")
        used_encoding = "latin-1 (fallback, verlustbehaftet)"

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(DELIMITER_CANDIDATES))
        delimiter = dialect.delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in DELIMITER_CANDIDATES}
        delimiter = max(counts, key=counts.get)

    return text, used_encoding, delimiter


def read_csv_raw(uploaded_file):
    """Liest eine hochgeladene Bank-CSV robust ein und gibt
    (DataFrame, encoding, delimiter, raw_bytes) zurueck. Alle Spalten werden
    als String gelesen, damit z.B. fuehrende Nullen oder Datums-/Betragsformate
    beim Parsen nicht verloren gehen."""
    raw_bytes = uploaded_file.getvalue()
    text, encoding, delimiter = detect_encoding_and_delimiter(raw_bytes)
    df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("")
    return df, encoding, delimiter, raw_bytes


def parse_amount(value) -> float | None:
    """Parst Betraege in beliebigem Format (DE '1.234,56', EN '1,234.56',
    mit Waehrungssymbolen/-codes wie 'EUR123.45' oder '123,45 €') zu float.
    Gibt None zurueck, wenn kein Zahlenwert erkennbar ist."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s == "-":
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_date(value):
    """Parst Datumswerte in beliebigem gaengigen Format (bevorzugt Tag-zuerst,
    wie in deutschen Bankauszuegen ueblich)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return dateparser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None
