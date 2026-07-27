"""Gemeinsame Datenstruktur fuer alle 'unsicheren' Zuordnungsvorschlaege
(exakter Mehrfachtreffer, Toleranz-Match, Duplikat-Verdacht,
Teilzahlungs-Verdacht - siehe matcher.py). EIN Typ statt vier Ad-hoc-Formen,
damit Controller und eine kuenftige UI nur einen Konsum-Pfad kennen
muessen: tx['candidate_docs'] ist immer entweder None oder eine Liste von
MatchCandidate.to_dict()-Ergebnissen."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MatchReasonType(str, Enum):
    """Diskriminator, WARUM ein Kandidat vorgeschlagen wird - str-Enum wie
    TxStatus (siehe tx_status.py), damit Werte unveraendert in
    session_state.json landen."""

    EXACT_AMOUNT_MULTI = "exact_amount_multi"  # mehrere Dokumente mit exakt gleichem Betrag
    TOLERANT_AMOUNT = "tolerant_amount"  # kein exakter Treffer, aber innerhalb der Toleranz
    DUPLICATE_SUSPECT = "duplicate_suspect"
    SPLIT_PAYMENT_DOC_SUM = "split_payment_doc_sum"  # 1 Dokument = Summe von 2 Buchungen
    SPLIT_PAYMENT_TX_SUM = "split_payment_tx_sum"  # 1 Buchung = Summe von 2+ Dokumenten


@dataclass
class MatchCandidate:
    reason_type: MatchReasonType
    confidence: float  # 0.0-1.0
    reason_detail: str  # deutschsprachiger Klartext, UI-tauglich
    # 0..n vorbereitete Paperless-Dokumente (siehe matcher.fetch_and_prepare_paperless_docs).
    # Immer eine Liste, auch bei genau einem Dokument - vermeidet eine
    # entweder/oder-Fallunterscheidung beim Konsumieren (Teilzahlung Fall B
    # braucht mehrere, alle anderen Faelle genau eines oder keins).
    documents: list[dict] = field(default_factory=list)
    # tx['id'] einer ANDEREN Transaktion - Duplikat-Partner oder
    # Teilzahlungs-Partner (1 Dokument = Summe zweier Buchungen). None,
    # wenn der Kandidat sich nicht auf eine andere Buchung bezieht.
    related_transaction_id: str | None = None
    # Kandidat-Betrag(-Summe) minus Zielbetrag, gerundet 2 Nachkommastellen.
    # None bei DUPLICATE_SUSPECT (dort gibt es kein Betrags-Delta).
    amount_delta: float | None = None

    def to_dict(self) -> dict:
        """Reine dict-Repraesentation fuer tx['candidate_docs'] - nur
        dicts/Listen (keine Dataclass-Instanzen) werden auf Transaktionen
        abgelegt, damit session_store.py unveraendert bleiben kann.
        reason_type bleibt als Enum-Member erhalten (nicht .value) - selbes
        Muster wie tx['status'] = TxStatus.XXX, json.dumps serialisiert
        str-Enums bereits heute klaglos."""
        return {
            "reason_type": self.reason_type,
            "confidence": self.confidence,
            "reason_detail": self.reason_detail,
            "documents": self.documents,
            "related_transaction_id": self.related_transaction_id,
            "amount_delta": self.amount_delta,
        }
