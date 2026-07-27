"""Universeller CSV-Import: Encoding-/Trennzeichen-Erkennung und Wertparsing."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

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
    wie in deutschen Bankauszuegen ueblich). Einzelwert-Best-Effort ohne
    Spaltenkontext - siehe detect_date_column_format() fuer die
    zuverlaessige, spaltenweite Variante mit Mehrdeutigkeits-Erkennung."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return dateparser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


# --- Spaltenweite Format-Erkennung -----------------------------------------
#
# Die obigen parse_amount()/parse_date() raten pro ZELLE isoliert - das
# genuegt fuer den bisherigen Gebrauch (z.B. matcher.extract_amount_from_
# custom_field, wo kein Spaltenkontext existiert), ist aber bei mehrdeutigen
# Einzelwerten ("1.234" - Tausender oder Dezimalstellen? "03.05.2026" - Tag
# oder Monat zuerst?) unzuverlaessig. Eine Bank-CSV nutzt aber IMMER
# durchgehend dasselbe Format - die Funktionen unten werten deshalb eine
# STICHPROBE der ganzen Spalte aus: ein einziger eindeutiger Wert darin
# (z.B. "25.03.2026" - Tag 25 kann kein Monat sein) beweist das Format fuer
# die gesamte Spalte, auch wenn andere Zeilen fuer sich genommen mehrdeutig
# waeren. Bleibt die Spalte durchgehend mehrdeutig, wird das ueber
# is_ambiguous=True/confidence explizit gemacht, statt still zu raten - so
# kann eine kuenftige UI gezielt nachfragen (siehe CLAUDE.md-Punkt "CSV-
# Erkennung robuster").

_AMOUNT_CLEAN_RE = re.compile(r"[^\d,.\-]")


def _clean_amount_value(value) -> str:
    if value is None:
        return ""
    return _AMOUNT_CLEAN_RE.sub("", str(value).strip())


@dataclass
class AmountFormatInfo:
    decimal_separator: str | None  # "," oder "."
    thousands_separator: str | None  # "." / "," / None (kein erkennbarer Gruppentrenner)
    is_ambiguous: bool
    confidence: str  # "high" | "assumed" | "ambiguous"
    sample_preview: list = field(default_factory=list)  # [(Rohwert, formatiertes Ergebnis), ...]


def parse_amount_with_format(value, decimal_separator: str, thousands_separator: str | None = None) -> float | None:
    """Deterministisches Gegenstueck zu parse_amount() - nutzt ein zuvor per
    detect_amount_column_format() bestimmtes (oder von einer kuenftigen UI
    vom Nutzer bestaetigtes) Format, rät nicht mehr pro Zelle. Ein falsch
    uebergebenes Format soll sichtbar scheitern (None), nicht still falsch
    runden."""
    s = _clean_amount_value(value)
    if not s or s == "-":
        return None
    if thousands_separator:
        s = s.replace(thousands_separator, "")
    if decimal_separator != ".":
        s = s.replace(decimal_separator, ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _amount_preview(cleaned_samples: list, decimal_separator: str, thousands_separator: str | None) -> list:
    preview = []
    for c in cleaned_samples[:5]:
        parsed = parse_amount_with_format(c, decimal_separator, thousands_separator)
        preview.append((c, f"{parsed:.2f}" if parsed is not None else "?"))
    return preview


def detect_amount_column_format(values: list, sample_size: int = 200) -> AmountFormatInfo:
    """Bestimmt Dezimal-/Tausendertrenner fuer eine GANZE Spalte aus einer
    Stichprobe (siehe Modul-Kommentar oben). Reihenfolge der Beweiskraft:

    1. EIN Wert mit BEIDEN Trennern gemeinsam (z.B. '1.234,56' oder
       '1,234.56') beweist das Format eindeutig - der SPAETERE der beiden
       ist der Dezimaltrenner (europ. vs. angelsaechsische Konvention).
    2. Sonst: kommt in der Stichprobe ein Wert vor, der NUR einen
       Trenner-Typ nutzt UND danach NICHT genau 3 Ziffern hat (z.B. ',5'
       oder ',56' oder ',1234') - ein Tausendertrenner ist IMMER exakt
       3-stellig gruppiert, das beweist diesen Trenner als Dezimaltrenner.
    3. Sonst, falls ueberhaupt ein Trenner vorkommt (aber jede Vorkommen
       zufaellig 3-stellig endet, z.B. durchgehend nur '1.234'): nicht
       entscheidbar, ob Tausender- oder Dezimaltrenner gemeint ist -
       confidence='assumed' (angenommen Dezimaltrenner, das haeufigere
       Format bei Bank-Einzelbetraegen ohne Gruppierung).
    4. Keine Zahl mit Trenner in der Stichprobe (nur Ganzzahlen) ->
       confidence='ambiguous', Default ','."""
    cleaned = [_clean_amount_value(v) for v in values]
    cleaned = [c for c in cleaned if c and c != "-"][:sample_size]

    for c in cleaned:
        if "," in c and "." in c:
            decimal = "," if c.rfind(",") > c.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            return AmountFormatInfo(decimal, thousands, False, "high", _amount_preview(cleaned, decimal, thousands))

    for sep, other in ((",", "."), (".", ",")):
        for c in cleaned:
            if sep in c and other not in c and len(c.rsplit(sep, 1)[-1]) != 3:
                return AmountFormatInfo(sep, None, False, "high", _amount_preview(cleaned, sep, None))

    for sep, other in ((",", "."), (".", ",")):
        if any(sep in c and other not in c for c in cleaned):
            return AmountFormatInfo(sep, None, True, "assumed", _amount_preview(cleaned, sep, None))

    return AmountFormatInfo(",", None, True, "ambiguous", _amount_preview(cleaned, ",", None))


_CURRENCY_MARKERS = {"EUR": "EUR", "€": "EUR", "USD": "USD", "$": "USD", "GBP": "GBP", "£": "GBP", "CHF": "CHF"}
_CURRENCY_RE = re.compile(r"(EUR|USD|GBP|CHF|€|\$|£)", re.IGNORECASE)


def detect_currency_marker(raw_value) -> str | None:
    """Erkennt eine Waehrungsangabe im Roh-Text EINES Betragsfeldes (z.B.
    'USD 123.45'). None sowohl bei fehlender Markierung ALS AUCH bei
    erkanntem EUR/€ - die Basiswaehrung dieser App ist immer EUR (siehe
    exporter.py: 'EUR{betrag}' im Beleg-Dateinamen), ein gefundenes EUR-
    Zeichen ist also KEINE Fremdwaehrung im Sinne dieser Funktion."""
    if raw_value is None:
        return None
    m = _CURRENCY_RE.search(str(raw_value))
    if not m:
        return None
    marker = _CURRENCY_MARKERS.get(m.group(0).upper(), _CURRENCY_MARKERS.get(m.group(0)))
    return None if marker == "EUR" else marker


_DATE_PATTERN = re.compile(r"^(\d{1,4})([./\-])(\d{1,2})\2(\d{1,4})$")


@dataclass
class DateFormatInfo:
    order: str | None  # "DMY" | "MDY" | "YMD" | None
    separator: str | None  # ".", "-", "/"
    is_ambiguous: bool
    confidence: str  # "high" | "assumed" | "ambiguous"
    sample_preview: list = field(default_factory=list)


def parse_date_with_format(value, order: str, separator: str | None = None):
    """Deterministisches Gegenstueck zu parse_date() - baut aus order
    ('DMY'/'MDY'/'YMD') ein explizites Format, kein dateutil-Raten mehr.
    separator: wenn gesetzt, muessen Werte mit ANDEREM Trenner scheitern
    (None) statt geraten zu werden - z.B. wenn die Spalte durchgehend '.'
    nutzt, ist ein einzelner Wert mit '/' vermutlich aus einer anderen
    Spalte verrutscht, keine gueltige Buchung."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _DATE_PATTERN.match(s)
    if not m:
        return None
    a, sep, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
    if separator and sep != separator:
        return None
    try:
        if order == "YMD":
            year, month, day = a, b, c
        elif order == "MDY":
            month, day, year = a, b, c
        else:
            day, month, year = a, b, c
        if len(year) == 2:
            year = ("20" if int(year) < 70 else "19") + year
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def detect_date_column_format(values: list, sample_size: int = 200) -> DateFormatInfo:
    """Bestimmt Tag/Monat/Jahr-Reihenfolge fuer eine GANZE Spalte aus einer
    Stichprobe (siehe Modul-Kommentar oben). EIN eindeutiger Wert (z.B.
    '25.03.2026' - Tag 25 kann kein Monat sein) beweist das Format fuer die
    gesamte Spalte, auch wenn andere Zeilen fuer sich genommen mehrdeutig
    waeren (z.B. '03.05.2026'). 4-stelliger erster Block -> ISO YMD,
    strukturell eindeutig unabhaengig von den Werten. Sonst: dmy_confirmed,
    wenn ein Wert im ERSTEN Block >12 hat; mdy_confirmed, wenn ein Wert im
    MITTLEREN Block >12 hat. Beide gleichzeitig -> Widerspruch (z.B. falsche
    Spalte gewaehlt), is_ambiguous=True. Keins von beiden (jede Zeile hat
    beide Bloecke <=12) -> genuin mehrdeutig, Reihenfolge wird per
    Trenner-Konvention geraten ('/' -> MDY, sonst DMY), aber
    confidence='assumed', is_ambiguous=True, damit eine kuenftige UI gezielt
    nachfragen kann statt es zu verschlucken."""
    samples = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        m = _DATE_PATTERN.match(s)
        if m:
            samples.append((s, m))
        if len(samples) >= sample_size:
            break

    if not samples:
        return DateFormatInfo(None, None, True, "ambiguous", [])

    separator = samples[0][1].group(2)
    preview = [s for s, _ in samples[:5]]

    if all(len(m.group(1)) == 4 for _, m in samples):
        return DateFormatInfo("YMD", separator, False, "high", preview)

    dmy_confirmed = any(int(m.group(1)) > 12 for _, m in samples)
    mdy_confirmed = any(int(m.group(3)) > 12 for _, m in samples)

    if dmy_confirmed and mdy_confirmed:
        return DateFormatInfo(None, separator, True, "ambiguous", preview)
    if dmy_confirmed:
        return DateFormatInfo("DMY", separator, False, "high", preview)
    if mdy_confirmed:
        return DateFormatInfo("MDY", separator, False, "high", preview)

    guessed = "MDY" if separator == "/" else "DMY"
    return DateFormatInfo(guessed, separator, True, "assumed", preview)


@dataclass
class ColumnProfile:
    name: str
    guessed_role: str | None  # "date" | "amount" | "purpose" | "counterparty" | None
    confidence: float  # 0.0-1.0
    sample_values: list
    date_format: DateFormatInfo | None = None
    amount_format: AmountFormatInfo | None = None


_COUNTERPARTY_NAME_HINTS = ("name zahlungsbeteiligter", "beguenstigter", "empfaenger", "zahlungspflichtiger")
_PURPOSE_NAME_HINTS = ("verwendungszweck", "buchungstext", "vorgang")
_BALANCE_NAME_HINTS = ("saldo", "kontostand")  # numerisch, aber kein Buchungsbetrag


def _hit_ratio(values: list, predicate) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if predicate(v)) / len(values)


def profile_columns(df: pd.DataFrame, preview_n: int = 5) -> list:
    """Liefert je Spalte Rollen-Vorschlag+Konfidenz+Format-Info+Werte-
    Vorschau, fuer eine kuenftige Mapping-UI (siehe CLAUDE.md-Punkt "CSV-
    Erkennung robuster") - Dropdowns vorbelegen, Vorschautabelle rendern,
    bei is_ambiguous gezielt nachfragen statt raten. Reine Analysefunktion,
    veraendert df nicht."""
    profiles = []
    for col in df.columns:
        values = [v for v in df[col].tolist() if str(v).strip()]
        name_lower = str(col).lower()

        date_info = detect_date_column_format(values)
        amount_info = detect_amount_column_format(values)
        date_ratio = _hit_ratio(values, lambda v: parse_date_with_format(v, date_info.order, date_info.separator) is not None) if date_info.order else 0.0
        amount_ratio = _hit_ratio(values, lambda v: parse_amount_with_format(v, amount_info.decimal_separator, amount_info.thousands_separator) is not None) if amount_info.decimal_separator else 0.0

        role, confidence = None, 0.0
        if date_ratio > 0.8:
            role, confidence = "date", date_ratio
        elif amount_ratio > 0.8 and not any(h in name_lower for h in _BALANCE_NAME_HINTS):
            role, confidence = "amount", amount_ratio
        elif any(h in name_lower for h in _PURPOSE_NAME_HINTS):
            role, confidence = "purpose", 0.7
        elif any(h in name_lower for h in _COUNTERPARTY_NAME_HINTS):
            role, confidence = "counterparty", 0.7

        profiles.append(
            ColumnProfile(
                name=col,
                guessed_role=role,
                confidence=round(confidence, 2),
                sample_values=values[:preview_n],
                date_format=date_info if role == "date" else None,
                amount_format=amount_info if role == "amount" else None,
            )
        )
    return profiles


def preview_rows(df: pd.DataFrame, n: int = 5) -> list:
    """df.head(n) zeilenorientiert (Spalte->Wert) fuer eine kuenftige
    Vorschau-Tabelle im Mapping-Dialog."""
    return df.head(n).fillna("").to_dict("records")
