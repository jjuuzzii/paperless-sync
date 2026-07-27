"""Zentrale Transaktions-Status-Werte - siehe CLAUDE.md. Ein str-Enum, damit
bestehende Konsumenten (session_store.py -> JSON, CSV-Export) weiterhin
einfache Strings sehen/vergleichen koennen, ohne dass Matching, Controller,
UI und Export mehr lose String-Literale verstreut pflegen muessen.

DUPLICATE_SUSPECT und SPLIT_PAYMENT werden aktuell von keiner
Erkennungslogik vergeben (siehe CLAUDE.md) - hier aber schon als echte
Kategorien vorgesehen, u.a. fuer den Steuerberater-Export."""
from __future__ import annotations

from enum import Enum


class TxStatus(str, Enum):
    MATCHED = "matched"
    TAGGED = "tagged"
    UNRESOLVED = "unresolved"
    MULTI_MATCH = "multi_match"
    DUPLICATE_SUSPECT = "duplicate_suspect"
    SPLIT_PAYMENT = "split_payment"


# Buchungen, die noch Klaerung brauchen - zentrale Definition fuer
# UI-Filter (action_transactions) und den Steuerberater-Export
# (04_Offene_Posten.csv + Warnung vor dem Export).
OPEN_STATUSES = (
    TxStatus.UNRESOLVED,
    TxStatus.MULTI_MATCH,
    TxStatus.DUPLICATE_SUSPECT,
    TxStatus.SPLIT_PAYMENT,
)

# Erledigt - Beleg zugeordnet (inkl. hochgeladener PDFs) oder als nicht
# noetig getaggt.
DONE_STATUSES = (TxStatus.MATCHED, TxStatus.TAGGED)

LABELS_DE = {
    TxStatus.MATCHED: "Beleg zugeordnet",
    TxStatus.TAGGED: "Kein Beleg nötig",
    TxStatus.UNRESOLVED: "Offen",
    TxStatus.MULTI_MATCH: "Mehrere Kandidaten - Auswahl nötig",
    TxStatus.DUPLICATE_SUSPECT: "Möglicher Duplikat-Fall",
    TxStatus.SPLIT_PAYMENT: "Mögliche Teilzahlung",
}


def label_de(status: TxStatus) -> str:
    return LABELS_DE.get(status, str(status))
