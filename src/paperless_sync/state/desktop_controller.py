"""Controller: verbindet die Desktop-UI mit der bestehenden Backend-Logik
(matcher, paperless_client, exporter, csv_utils, config_manager).

Jede on_*-Methode fuehrt genau eine Aktion aus, aktualisiert den AppState
und persistiert bei Bedarf die Sitzung. Die UI ruft danach einfach ihre
render()-Methode erneut auf - der Controller kennt kein Tkinter-Widget."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from paperless_sync.core.csv_utils import read_csv_raw
from paperless_sync.core.matcher import build_transactions, fetch_and_prepare_paperless_docs, match_transactions
from paperless_sync.core.exporter import generate_export
from paperless_sync.core.config_manager import csv_signature as compute_csv_signature
from paperless_sync.core.tx_status import TxStatus

BUILTIN_TAGS = ["PRIVAT", "EINZAHLUNG", "UMBUCHUNG"]
TAG_ICONS = {"PRIVAT": "🔒", "EINZAHLUNG": "💰", "UMBUCHUNG": "🔄"}


class _BytesUpload:
    """Adapter, damit csv_utils.read_csv_raw (erwartet ein Objekt mit
    .getvalue(), wie Streamlits UploadedFile) auch mit rohen Bytes von einer
    lokalen Datei funktioniert."""

    def __init__(self, data: bytes):
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def normalize_purpose(purpose: str) -> str:
    """Siehe app.py/normalize_purpose: entfernt Ziffern (Datum, Uhrzeit,
    Betrag, Referenznummern sind pro Buchung fast immer einzigartig
    eingebettet) und normalisiert Whitespace, damit wiederkehrende Buchungen
    trotz unterschiedlichem Datum/Betrag erkannt werden."""
    without_digits = re.sub(r"\d+", "", purpose)
    return re.sub(r"\s+", " ", without_digits).strip().upper()


class Controller:
    def __init__(self, state):
        self.state = state
        self._success_tag_id_cache: int | None = None

    # --- CSV-Import & Mapping ------------------------------------------------
    def on_csv_upload(self, filepath: str) -> bool:
        """Liest eine neue CSV ein. Gibt True zurueck, wenn ein bereits
        bekanntes Mapping automatisch angewendet werden konnte (dann sind
        transactions sofort befuellt), sonst False (Mapping muss bestaetigt
        werden, siehe on_mapping_confirm)."""
        state = self.state
        path = Path(filepath)
        raw_bytes = path.read_bytes()

        sig = f"{path.name}_{len(raw_bytes)}"
        if sig == state.csv_signature:
            return state.mapping_confirmed

        df, encoding, delimiter, _ = read_csv_raw(_BytesUpload(raw_bytes))
        state.csv_df = df
        state.csv_columns = list(df.columns)
        state.csv_encoding = encoding
        state.csv_delimiter = delimiter
        state.csv_signature = sig
        state.transactions = []
        state.matched_once = False

        col_sig = compute_csv_signature(df.columns)
        saved_mapping = state.config["csv_mappings"].get(col_sig)
        if saved_mapping:
            state.pending_mapping = saved_mapping
            state.mapping_confirmed = True
            state.transactions = build_transactions(df, saved_mapping)
            self.suggest_learned_tags()
            state.persist_session()
            return True

        state.pending_mapping = {"date_column": None, "amount_column": None, "purpose_column": None}
        state.mapping_confirmed = False
        return False

    def on_mapping_confirm(
        self, date_column: str, amount_column: str, purpose_column: str, counterparty_column: str | None = None
    ) -> None:
        state = self.state
        mapping = {
            "date_column": date_column,
            "amount_column": amount_column,
            "purpose_column": purpose_column,
            "counterparty_column": counterparty_column,
        }
        state.pending_mapping = mapping
        state.mapping_confirmed = True
        col_sig = compute_csv_signature(state.csv_columns)
        state.config["csv_mappings"][col_sig] = mapping
        state.save_config()
        state.transactions = build_transactions(state.csv_df, mapping)
        self.suggest_learned_tags()
        state.matched_once = False
        state.persist_session()

    def on_external_import(self, df, mapping: dict) -> None:
        """Importiert Transaktionen aus einem bereits fertigen DataFrame mit
        bekanntem Spalten-Mapping - identisch zu on_mapping_confirm(), nur
        ohne den Umweg ueber eine hochgeladene CSV-Datei (kein
        csv_columns/Datei-Signatur-Konzept anwendbar, das Mapping wird auch
        NICHT in config['csv_mappings'] gemerkt, da es sich nicht auf ein
        wiederkehrendes CSV-Spaltenlayout bezieht). Fuer jede Datenquelle
        nutzbar, die dieselbe Tabellenform wie eine Bank-CSV liefert (Datum/
        Betrag/Verwendungszweck/[Gegenpartei])."""
        state = self.state
        state.csv_df = df
        state.csv_columns = list(df.columns)
        state.csv_signature = f"external_{len(df)}_{datetime.now().isoformat()}"
        state.csv_delimiter = ","
        state.csv_encoding = "utf-8"
        state.pending_mapping = mapping
        state.mapping_confirmed = True
        state.transactions = build_transactions(df, mapping)
        self.suggest_learned_tags()
        state.matched_once = False
        state.persist_session()

    # --- Paperless-Abgleich ------------------------------------------------
    def on_match_click(self) -> int:
        state = self.state
        if state.client is None:
            raise RuntimeError("Paperless ist nicht konfiguriert (.env pruefen).")
        if not state.transactions:
            raise RuntimeError("Bitte zuerst eine CSV laden.")
        docs = fetch_and_prepare_paperless_docs(state.client, state.config["amount_detection"])
        match_transactions(state.transactions, docs)
        state.matched_once = True
        state.paperless_docs_raw = docs
        state.persist_session()
        return len(docs)

    # --- Tags (Privat/Einzahlung/Umbuchung/Sonstiges + Lernen) ------------------------------------------------
    def top_custom_tags(self, limit: int = 3) -> list[str]:
        counts = self.state.config.get("custom_tags", {})
        return [name for name, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]]

    def _normalize_custom_tag_name(self, raw: str) -> str:
        """Bereinigt frei eingegebene Tag-Namen (Whitespace, ein versehentlich
        mitgetipptes fuehrendes '+' aus dem Sonstiges-Kombifeld) und
        verwendet die Schreibweise eines bereits vorhandenen Tags weiter,
        falls derselbe Name (Gross-/Kleinschreibung egal) schon existiert -
        verhindert Fragmentierung wie 'Darlehen' vs 'darlehen' vs '+ Darlehen'."""
        cleaned = raw.strip().lstrip("+").strip()
        existing = self.state.config.get("custom_tags", {})
        for name in existing:
            if name.lower() == cleaned.lower():
                return name
        return cleaned

    def suggest_learned_tags(self) -> int:
        state = self.state
        memory = state.config.get("purpose_tag_memory", {})
        if not memory:
            return 0
        count = 0
        for tx in state.transactions:
            if tx["status"] == TxStatus.UNRESOLVED:
                learned = memory.get(normalize_purpose(tx["purpose"]))
                if learned:
                    tx["suggested_tag"] = learned
                    count += 1
        return count

    def on_apply_tag(self, transaction_id: str, tag_name: str) -> None:
        """Wendet einen Tag an (PRIVAT/EINZAHLUNG/UMBUCHUNG oder ein eigener
        Tag) und merkt sich die Zuordnung Verwendungszweck -> Tag, damit
        gleichartige Buchungen (gleiches Muster, anderes Datum/Betrag)
        kuenftig als Vorschlag markiert werden (siehe suggest_learned_tags).
        Fuer eigene Tags wird zusaetzlich ein Nutzungszaehler gefuehrt, damit
        haeufig genutzte Tags oben in top_custom_tags landen."""
        state = self.state
        tx = self._find_tx(transaction_id)

        if tag_name not in BUILTIN_TAGS:
            tag_name = self._normalize_custom_tag_name(tag_name)

        tx["status"] = TxStatus.TAGGED
        tx["tag"] = tag_name
        tx["note"] = ""
        tx["suggested_tag"] = None

        if tag_name not in BUILTIN_TAGS:
            counts = state.config.setdefault("custom_tags", {})
            counts[tag_name] = counts.get(tag_name, 0) + 1
        memory = state.config.setdefault("purpose_tag_memory", {})
        memory[normalize_purpose(tx["purpose"])] = tag_name
        state.save_config()
        self.suggest_learned_tags()
        state.persist_session()

    def on_mark_private(self, transaction_id: str) -> None:
        self.on_apply_tag(transaction_id, "PRIVAT")

    # --- Beleg-Zuordnung: Upload / Verknuepfen vorhandener Dokumente ------------------------------------------------
    def on_pdf_drop(self, transaction_id: str, filepath: str) -> None:
        """Ein per Drag & Drop abgelegtes PDF wird an Paperless hochgeladen
        und die Transaktion als erledigt markiert."""
        state = self.state
        if state.client is None:
            raise RuntimeError("Paperless ist nicht konfiguriert (.env pruefen).")
        tx = self._find_tx(transaction_id)
        path = Path(filepath)
        file_bytes = path.read_bytes()
        state.client.upload_document(file_bytes, path.name)
        tx["status"] = TxStatus.MATCHED
        tx["uploaded_bytes"] = file_bytes
        tx["uploaded_name"] = path.name
        tx["note"] = ""
        state.persist_session()

    def on_documents_selected(
        self, transaction_id: str, paperless_doc_ids: list[int], custom_field_value: str | None = None
    ) -> None:
        """Verknuepft eines oder mehrere bereits in Paperless vorhandene
        Dokumente mit einer Transaktion (rote Karte: frei aus der ganzen
        Dokumentliste gewaehlt). Ergaenzt bestehende Verknuepfungen, ersetzt
        sie nicht - so lassen sich z.B. bei einer Amazon-Sammelabbuchung
        nach und nach mehrere Einzelrechnungen an dieselbe Buchung haengen.
        custom_field_value: optional, um den (bisher leeren) Betrag im
        Custom Field nachzutragen (nur im custom_field-Modus, nur sinnvoll
        bei genau einem neu ausgewaehlten Dokument)."""
        state = self.state
        tx = self._find_tx(transaction_id)
        docs = tx.setdefault("matched_docs", [])
        existing_ids = {d["id"] for d in docs}
        new_ids = [doc_id for doc_id in paperless_doc_ids if doc_id not in existing_ids]
        if not new_ids:
            return
        if custom_field_value is not None and len(new_ids) == 1:
            field_id = state.config["amount_detection"].get("custom_field_id")
            state.client.update_custom_field_value(new_ids[0], field_id, custom_field_value)
        for doc_id in new_ids:
            doc = next((d for d in state.paperless_docs_raw if d["id"] == doc_id), None)
            if doc is None:
                raise ValueError(f"Dokument #{doc_id} nicht in der geladenen Liste gefunden.")
            docs.append(dict(doc))
            self._apply_success_tag_in_paperless(doc_id)
        tx["status"] = TxStatus.MATCHED
        tx["candidate_docs"] = None
        tx["note"] = ""
        state.persist_session()

    def on_remove_doc(self, transaction_id: str, doc_id: int) -> None:
        """Entfernt EIN Dokument aus einer (moeglicherweise mehrteiligen)
        Zuordnung, ohne die ganze Zuordnung rueckgaengig zu machen (siehe
        on_undo_resolution fuer das komplette Zuruecksetzen). Faellt die
        Zuordnung dadurch auf 0 Dokumente, wandert die Buchung zurueck nach
        'Aktion erforderlich'."""
        tx = self._find_tx(transaction_id)
        docs = tx.get("matched_docs") or []
        tx["matched_docs"] = [d for d in docs if d["id"] != doc_id]
        self._remove_success_tag_in_paperless(doc_id)
        # uploaded_bytes-Check noetig, weil TxStatus.MATCHED sowohl
        # Paperless-verknuepfte als auch direkt hochgeladene Belege abdeckt
        # (siehe TxStatus) - letztere haben nie matched_docs, duerfen hier
        # also nicht faelschlich zurueckgesetzt werden.
        if not tx["matched_docs"] and not tx.get("uploaded_bytes") and tx["status"] == TxStatus.MATCHED:
            tx["status"] = TxStatus.UNRESOLVED
        self.state.persist_session()

    def on_ambiguous_doc_selected(self, transaction_id: str, paperless_doc_id: int) -> None:
        """Manuelle Aufloesung eines Mehrfach-Matches (status 'unclear') -
        der Nutzer waehlt aus den bereits ermittelten Kandidaten
        (tx['candidate_docs'], NICHT aus der vollen Dokumentliste) das
        richtige Dokument aus."""
        tx = self._find_tx(transaction_id)
        candidates = tx.get("candidate_docs") or []
        doc = next((d for d in candidates if d["id"] == paperless_doc_id), None)
        if doc is None:
            raise ValueError(f"Dokument #{paperless_doc_id} ist kein Kandidat fuer diese Transaktion.")
        self._apply_matched_doc(transaction_id, doc)

    def _apply_matched_doc(self, transaction_id: str, doc: dict) -> None:
        tx = self._find_tx(transaction_id)
        tx["status"] = TxStatus.MATCHED
        tx["matched_docs"] = [dict(doc)]
        tx["candidate_docs"] = None
        tx["note"] = ""
        self._apply_success_tag_in_paperless(doc["id"])
        self.state.persist_session()

    def _apply_success_tag_in_paperless(self, doc_id: int) -> None:
        """Setzt optional einen Tag (z.B. 'Abgeglichen') auf das
        Paperless-Dokument, wenn eine Buchung erfolgreich damit verknuepft
        wurde - siehe config_manager.DEFAULT_CONFIG. Best-effort: ein
        Fehler hier (z.B. kurzzeitig kein Netz) darf die eigentliche
        Zuordnung nicht scheitern lassen, die ist bereits erledigt."""
        state = self.state
        if not state.client or not state.config.get("paperless_success_tag_enabled"):
            return
        tag_name = (state.config.get("paperless_success_tag_name") or "").strip()
        if not tag_name:
            return
        try:
            if self._success_tag_id_cache is None:
                self._success_tag_id_cache = state.client.get_or_create_tag(tag_name)
            state.client.add_tag_to_document(doc_id, self._success_tag_id_cache)
        except Exception:
            pass

    def _remove_success_tag_in_paperless(self, doc_id: int) -> None:
        """Symmetrisch zu _apply_success_tag_in_paperless: entfernt den
        Erfolgs-Tag wieder, wenn eine Zuordnung rueckgaengig gemacht wird.
        Best-effort, siehe dort."""
        state = self.state
        if not state.client or not state.config.get("paperless_success_tag_enabled"):
            return
        tag_name = (state.config.get("paperless_success_tag_name") or "").strip()
        if not tag_name:
            return
        try:
            if self._success_tag_id_cache is None:
                self._success_tag_id_cache = state.client.get_or_create_tag(tag_name)
            state.client.remove_tag_from_document(doc_id, self._success_tag_id_cache)
        except Exception:
            pass

    def on_undo_resolution(self, transaction_id: str) -> None:
        """Macht eine Tag-Vergabe oder Beleg-Zuordnung rueckgaengig - die
        Transaktion wandert zurueck nach 'Aktion erforderlich'
        (status=UNRESOLVED) und kann neu getaggt/zugeordnet werden.
        custom_tags-Zaehler und purpose_tag_memory bleiben bewusst
        unangetastet (ein einzelnes Rueckgaengigmachen soll nicht das
        gelernte Muster fuer alle aehnlichen Buchungen loeschen). Ein
        bereits nach Paperless hochgeladenes Dokument bleibt dort liegen -
        nur die lokale Zuordnung wird zurueckgesetzt."""
        tx = self._find_tx(transaction_id)
        for doc in tx.get("matched_docs") or []:
            self._remove_success_tag_in_paperless(doc["id"])
        tx["status"] = TxStatus.UNRESOLVED
        tx["tag"] = None
        tx["matched_docs"] = []
        tx["candidate_docs"] = None
        tx["uploaded_bytes"] = None
        tx["uploaded_name"] = None
        tx["note"] = ""
        self.state.persist_session()

    # --- Export ------------------------------------------------
    def on_generate_export_click(self, month: str) -> Path:
        state = self.state
        if not state.transactions:
            raise RuntimeError("Keine Transaktionen zum Exportieren vorhanden.")
        return generate_export(
            state.get_export_base_dir(), month, state.transactions, state.csv_df, state.csv_delimiter, state.client
        )

    # --- Hilfsfunktionen ------------------------------------------------
    def _find_tx(self, transaction_id: str) -> dict:
        tx = next((t for t in self.state.transactions if t["id"] == transaction_id), None)
        if tx is None:
            raise KeyError(f"Transaktion {transaction_id} nicht gefunden.")
        return tx
