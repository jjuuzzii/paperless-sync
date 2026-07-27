"""Transaktions-Aufbau und Abgleich gegen Paperless-Dokumente."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from dateutil import parser as dateparser

from .csv_utils import parse_amount, parse_date
from .match_candidate import MatchCandidate, MatchReasonType
from .tx_status import TxStatus

# Buchungen mit diesem Status sind eine bewusste Nutzerentscheidung und
# werden von keiner automatischen Erkennung (Duplikat, Toleranz-Match,
# Teilzahlung) mehr angefasst.
_LOCKED_STATUSES = (TxStatus.TAGGED, TxStatus.MATCHED)


def normalize_purpose(purpose: str) -> str:
    """Entfernt Ziffern (Datum, Uhrzeit, Betrag, Referenznummern sind pro
    Buchung fast immer einzigartig eingebettet) und normalisiert
    Whitespace, damit wiederkehrende/doppelt importierte Buchungen trotz
    unterschiedlicher CSV-Quelle (leicht abweichender Referenztext) erkannt
    werden. Frueher in desktop_controller.py, jetzt hier, weil auch
    flag_duplicate_suspects() sie braucht und core/ nicht von state/
    importieren darf."""
    without_digits = re.sub(r"\d+", "", purpose)
    return re.sub(r"\s+", " ", without_digits).strip().upper()


def flag_duplicate_suspects(transactions: list[dict]) -> int:
    """Gruppiert Transaktionen nach (date, amount_raw, normalisierter
    Verwendungszweck) - amount_raw VORZEICHENBEHAFTET (nicht amount_abs):
    +50 und -50 sind kein Duplikat-Verdacht, sondern zwei unterschiedliche
    Buchungen. normalize_purpose() statt striktem Wortlaut-Vergleich, weil
    dieselbe real doppelt importierte Buchung ueber zwei CSV-Quellen (oder
    CSV+Bank-API) oft einen leicht abweichenden, teils rein numerischen
    Verwendungszweck traegt (siehe Controller._merge_new_transactions -
    dort ist der Verwendungszweck aus demselben Grund bewusst NICHT Teil
    des Dedup-Schluessels).

    MUSS vor match_transactions() aufgerufen werden (siehe
    desktop_controller.on_match_click), damit derselbe Beleg nicht
    faelschlich beiden Buchungen eines Duplikat-Paars zugeordnet wird -
    match_transactions() ueberspringt DUPLICATE_SUSPECT-Buchungen.

    Ruehrt TAGGED/MATCHED-Buchungen nie an (Nutzerentscheidung bleibt
    unangetastet), referenziert eine solche aber trotzdem als
    related_transaction_id bei ihrem noch offenen Duplikat-Partner.

    Wird bei jedem Aufruf komplett neu berechnet statt bereits gepruefte
    Paare zu ueberspringen - unproblematisch, weil Transaktionen in dieser
    App nie geloescht werden (keine on_delete_transaction-Aktion): eine
    einmal erkannte Duplikat-Gruppe kann nur noch wachsen, nie schrumpfen,
    ein Re-Run liefert fuer unveraenderte Gruppen dasselbe Ergebnis.

    Gibt die Anzahl neu markierter Transaktionen zurueck."""
    groups: dict[tuple, list[dict]] = {}
    for tx in transactions:
        key = (tx["date"], tx["amount_raw"], normalize_purpose(tx["purpose"]))
        groups.setdefault(key, []).append(tx)

    newly_flagged = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        for tx in group:
            if tx["status"] in _LOCKED_STATUSES:
                continue
            if tx["status"] != TxStatus.DUPLICATE_SUSPECT:
                newly_flagged += 1
            others = [t for t in group if t is not tx]
            tx["status"] = TxStatus.DUPLICATE_SUSPECT
            tx["candidate_docs"] = [
                MatchCandidate(
                    reason_type=MatchReasonType.DUPLICATE_SUSPECT,
                    confidence=1.0,
                    reason_detail=f"Gleicher Betrag/Datum/Verwendungszweck wie Buchung #{other.get('display_number') or other['id']}",
                    related_transaction_id=other["id"],
                ).to_dict()
                for other in others
            ]
            tx["note"] = "Moeglicher Duplikat-Fall - pruefen, ob dieselbe Buchung versehentlich zweimal importiert wurde"
    return newly_flagged


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

    for r in records:
        r["status"] = TxStatus.UNRESOLVED  # siehe tx_status.TxStatus
        r["note"] = ""
        r["tag"] = None  # gesetzt, wenn status == "tagged" (PRIVAT/EINZAHLUNG/UMBUCHUNG/eigener Tag)
        r["suggested_tag"] = None  # Vorschlag aus gelerntem Verwendungszweck, noch nicht bestaetigt
        r["matched_docs"] = []  # ein oder mehrere verknuepfte Paperless-Dokumente (z.B. Sammelabbuchung)
        r["candidate_docs"] = None  # bei status == MULTI_MATCH: Liste der mehrdeutigen Kandidaten
        r["uploaded_bytes"] = None
        r["uploaded_name"] = None

    renumber_transactions(records)
    return records


def renumber_transactions(transactions: list[dict]) -> None:
    """Vergibt id (global fortlaufend, dreistellig) und display_number
    (1..X je Kalendermonat) neu, in-place, chronologisch sortiert -
    unveraendert die gleiche Logik wie zuvor in build_transactions(), jetzt
    auch fuer desktop_controller.on_external_import() nutzbar (neu vom
    Bank-Import hinzugefuegte Transaktionen muessen sich nahtlos in die
    bestehende Nummerierung einreihen). Aendert NUR id/display_number -
    status/tags/matched_docs bestehender Eintraege bleiben unangetastet."""
    transactions.sort(key=lambda r: r["date"])
    month_counters: dict[str, int] = {}
    for i, r in enumerate(transactions, start=1):
        r["id"] = f"{i:03d}"  # global eindeutig - interner Schluessel fuer alle Lookups/Klicks, NICHT anzeigen
        month_key = r["date"].strftime("%Y-%m")
        month_counters[month_key] = month_counters.get(month_key, 0) + 1
        r["display_number"] = f"{month_counters[month_key]:03d}"  # 1..X je Monat - das sieht der Nutzer (Karte/Export)


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


_DATE_SCORE_WINDOW_DAYS = 30  # danach traegt die Datumsnaehe nichts mehr zur Konfidenz bei
_TOLERANT_SCORE_WEIGHTS = {"amount": 0.6, "date": 0.25, "text": 0.15}


def _amount_closeness_score(candidate_amount: float, target_amount: float, tolerance_abs: float, tolerance_pct: float) -> float:
    """1.0 = exakt gleich, 0.0 = genau an der (guenstigeren der beiden
    ODER-verknuepften) Toleranzgrenze. Ein Kandidat landet ueberhaupt nur
    im Pool, weil MINDESTENS eine der beiden Toleranzen erfuellt ist -
    bewertet wird dementsprechend nach der guenstigeren."""
    diff = abs(candidate_amount - target_amount)
    if diff == 0:
        return 1.0
    abs_ratio = diff / tolerance_abs if tolerance_abs > 0 else float("inf")
    pct_budget = target_amount * tolerance_pct
    pct_ratio = diff / pct_budget if pct_budget > 0 else float("inf")
    return max(0.0, 1.0 - min(abs_ratio, pct_ratio, 1.0))


def _date_proximity_score(doc_date, tx_date) -> float:
    """0.5 (neutral) wenn eines der beiden Daten fehlt - fehlende Info soll
    nicht bestrafen (Paperless-'created' ist z.B. nicht bei jedem Dokument
    aussagekraeftig). Sonst linear abfallend innerhalb von
    _DATE_SCORE_WINDOW_DAYS."""
    if doc_date is None or tx_date is None:
        return 0.5
    days = abs((doc_date - tx_date).days)
    if days >= _DATE_SCORE_WINDOW_DAYS:
        return 0.0
    return 1.0 - (days / _DATE_SCORE_WINDOW_DAYS)


def _text_similarity_score(a: str, b: str) -> float:
    """0.5 (neutral) wenn einer der beiden Texte leer ist. Sonst
    difflib.SequenceMatcher (Standardbibliothek, keine neue Abhaengigkeit)
    zwischen Korrespondent-Name und Verwendungszweck/Zahlungsbeteiligtem -
    niedrig gewichtet, weil beides oft nichts inhaltlich Vergleichbares
    gemein hat."""
    if not a or not b:
        return 0.5
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_tolerant_candidates(
    tx: dict, candidates_pool: list[dict], used_doc_ids: set, tolerance_abs: float, tolerance_pct: float, top_n: int
) -> list[dict]:
    """Nur aufgerufen, wenn der exakte Betragsabgleich 0 Treffer fand. Ein
    Dokument wird Kandidat, sobald es INNERHALB der absoluten ODER der
    prozentualen Toleranz liegt (siehe match_transactions-Docstring fuer die
    Begruendung der ODER-Verknuepfung). Konfidenz aus Betrag/Datum/Text
    gewichtet (siehe _TOLERANT_SCORE_WEIGHTS), absteigend sortiert, auf
    top_n gekappt. Gibt fertige MatchCandidate.to_dict()-Eintraege zurueck."""
    scored = []
    for doc in candidates_pool:
        if doc["id"] in used_doc_ids:
            continue
        diff = abs(doc["amount"] - tx["amount_abs"])
        pct_budget = tx["amount_abs"] * tolerance_pct
        within_abs = tolerance_abs > 0 and diff <= tolerance_abs
        within_pct = pct_budget > 0 and diff <= pct_budget
        if not (within_abs or within_pct):
            continue
        amount_score = _amount_closeness_score(doc["amount"], tx["amount_abs"], tolerance_abs, tolerance_pct)
        date_score = _date_proximity_score(doc.get("date"), tx["date"])
        text_score = _text_similarity_score(doc.get("correspondent_name") or "", tx.get("counterparty") or tx.get("purpose") or "")
        weights = _TOLERANT_SCORE_WEIGHTS
        confidence = weights["amount"] * amount_score + weights["date"] * date_score + weights["text"] * text_score
        amount_delta = round(doc["amount"] - tx["amount_abs"], 2)
        scored.append((confidence, doc, amount_delta))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        MatchCandidate(
            reason_type=MatchReasonType.TOLERANT_AMOUNT,
            confidence=round(confidence, 3),
            reason_detail=(
                f"Kein exakter Treffer - Dokumentbetrag {doc['amount']:.2f} EUR weicht "
                f"{abs(amount_delta):.2f} EUR vom Buchungsbetrag ({tx['amount_abs']:.2f} EUR) ab, "
                f"innerhalb der eingestellten Toleranz"
            ),
            documents=[doc],
            amount_delta=amount_delta,
        ).to_dict()
        for confidence, doc, amount_delta in scored[:top_n]
    ]


def match_transactions(transactions: list[dict], paperless_docs: list[dict], amount_matching: dict | None = None) -> None:
    """Gleicht alle Transaktionen ab, die noch nicht manuell entschieden
    wurden (TAGGED/MATCHED bleiben unangetastet - MATCHED deckt auch bereits
    hochgeladene PDFs ab, siehe TxStatus) und die nicht als DUPLICATE_SUSPECT
    markiert sind (siehe flag_duplicate_suspects - muss vor dieser Funktion
    laufen, sonst koennte derselbe Beleg beiden Buchungen eines
    Duplikat-Paars zugeordnet werden). Aendert die uebergebenen
    Transaktions-Dicts in-place.

    Ablauf je Transaktion:
    1. Exakter Betrag (auf 2 Nachkommastellen gerundet), GENAU 1 Treffer ->
       automatisch MATCHED, wie bisher. Kein Zeitfenster/Datumsabgleich -
       Belege koennen beliebig lange vor/nach der Buchung in Paperless
       liegen.
    2. Exakter Betrag, MEHRERE Treffer -> MULTI_MATCH, ein MatchCandidate
       (reason_type=EXACT_AMOUNT_MULTI, confidence=1.0) je Dokument -
       manuelle Auswahl noetig, es wird nicht geraten.
    3. Exakter Betrag, KEIN Treffer -> Toleranz-Fallback
       (find_tolerant_candidates, amount_matching-Config). Werden dabei
       Kandidaten gefunden -> ebenfalls MULTI_MATCH (bewusst NIE
       automatisch MATCHED, auch nicht bei genau einem Toleranz-Kandidaten:
       Toleranz ist per Definition unsicher, anders als der exakte Betrag).
       Kein Kandidat -> UNRESOLVED.

    tx['candidate_docs'] ist in allen drei Faellen eine einheitliche Liste
    von MatchCandidate.to_dict()-Eintraegen (siehe match_candidate.py) -
    auch der bisherige exakte Mehrfachtreffer wird jetzt so verpackt, damit
    Controller/UI nur einen Konsum-Pfad brauchen.

    Jedes Paperless-Dokument wird hoechstens einer Transaktion zugeordnet
    (matched_docs) bzw. als Kandidat vorgeschlagen, wenn es bereits einer
    anderen Transaktion fest zugeordnet ist."""
    amount_matching = amount_matching or {}
    tolerance_abs = amount_matching.get("tolerance_abs", 0.0)
    tolerance_pct = amount_matching.get("tolerance_pct", 0.0)
    top_n = amount_matching.get("top_n_candidates", 3)

    candidates_pool = [d for d in paperless_docs if d["amount"] is not None]
    used_doc_ids = set()

    # Bereits zugeordnete Dokumente (automatischer Match ODER manuell
    # aufgeloester Mehrfach-/Toleranz-Match aus einem frueheren Abgleich)
    # gelten als "vergeben", noch BEVOR die eigentliche Zuordnungsschleife
    # laeuft - sonst koennten sie bei einem erneuten Abgleich (z.B. nach neu
    # hochgeladenen Belegen) einer ANDEREN, noch offenen Transaktion mit
    # aehnlichem Betrag angeboten werden.
    for tx in transactions:
        if tx["status"] in _LOCKED_STATUSES:
            for doc in tx.get("matched_docs") or []:
                used_doc_ids.add(doc["id"])

    for tx in transactions:
        # MATCHED bewusst mit ausgenommen (deckt auch bereits hochgeladene
        # PDFs ab, siehe TxStatus): sonst wuerde ein erneuter Abgleich eine
        # manuell aus mehreren Kandidaten aufgeloeste Zuordnung (siehe
        # Controller.on_ambiguous_doc_selected) wieder verwerfen. DUPLICATE_
        # SUSPECT wird ebenfalls uebersprungen (siehe Docstring oben).
        if tx["status"] in _LOCKED_STATUSES or tx["status"] == TxStatus.DUPLICATE_SUSPECT:
            continue

        exact_candidates = [
            d
            for d in candidates_pool
            if d["id"] not in used_doc_ids and round(d["amount"], 2) == round(tx["amount_abs"], 2)
        ]

        if len(exact_candidates) == 1:
            doc = exact_candidates[0]
            used_doc_ids.add(doc["id"])
            tx["status"] = TxStatus.MATCHED
            tx["matched_docs"] = [doc]
            tx["candidate_docs"] = None
            tx["note"] = ""
        elif len(exact_candidates) > 1:
            tx["status"] = TxStatus.MULTI_MATCH
            tx["matched_docs"] = []
            tx["candidate_docs"] = [
                MatchCandidate(
                    reason_type=MatchReasonType.EXACT_AMOUNT_MULTI,
                    confidence=1.0,
                    reason_detail="Betrag tritt mehrfach auf - bitte manuell zuordnen",
                    documents=[doc],
                ).to_dict()
                for doc in exact_candidates
            ]
            tx["note"] = "Achtung: Betrag tritt mehrfach auf - Bitte manuell zuordnen"
        else:
            tolerant = find_tolerant_candidates(tx, candidates_pool, used_doc_ids, tolerance_abs, tolerance_pct, top_n)
            if tolerant:
                tx["status"] = TxStatus.MULTI_MATCH
                tx["matched_docs"] = []
                tx["candidate_docs"] = tolerant
                tx["note"] = "Kein exakter Treffer - moegliche Kandidaten innerhalb der Toleranz gefunden, bitte pruefen"
            else:
                tx["status"] = TxStatus.UNRESOLVED
                tx["matched_docs"] = []
                tx["candidate_docs"] = None
                tx["note"] = ""
