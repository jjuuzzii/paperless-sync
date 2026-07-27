"""Controller: verbindet die Desktop-UI mit der bestehenden Backend-Logik
(matcher, paperless_client, exporter, csv_utils, config_manager).

Jede on_*-Methode fuehrt genau eine Aktion aus, aktualisiert den AppState
und persistiert bei Bedarf die Sitzung. Die UI ruft danach einfach ihre
render()-Methode erneut auf - der Controller kennt kein Tkinter-Widget."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from paperless_sync.core.csv_utils import read_csv_raw, profile_columns
from paperless_sync.core.matcher import (
    build_transactions,
    renumber_transactions,
    fetch_and_prepare_paperless_docs,
    match_transactions,
    flag_duplicate_suspects,
    find_split_payment_candidates,
    normalize_purpose,
)
from paperless_sync.core.exporter import generate_export
from paperless_sync.core.config_manager import csv_signature as compute_csv_signature
from paperless_sync.core.tx_status import TxStatus

BUILTIN_TAGS = ["PRIVAT", "EINZAHLUNG", "UMBUCHUNG"]
TAG_ICONS = {"PRIVAT": "🔒", "EINZAHLUNG": "💰", "UMBUCHUNG": "🔄"}

# Heuristik fuer eine Spalte mit der EIGENEN Kontonummer/IBAN (nicht die
# des Zahlungsbeteiligten) - fuer eine Warnung, falls beim Nachladen einer
# weiteren CSV ploetzlich ein anderes Konto drin zu stehen scheint (z.B.
# aus Versehen den falschen Kontoauszug gewaehlt). Best-effort: viele
# Bank-Exportformate haben so eine Spalte gar nicht, dann gibt es keine
# Warnung (kein Fehlalarm, aber auch kein Schutz).
_ACCOUNT_ID_COLUMN_HINTS = ("iban auftragskonto", "kontonummer auftragskonto", "iban", "kontonummer", "konto")


def _find_account_identifier(df, columns) -> str | None:
    for hint in _ACCOUNT_ID_COLUMN_HINTS:
        for col in columns:
            col_lower = str(col).lower()
            if hint in col_lower and "beteiligt" not in col_lower:
                values = df[col].dropna().astype(str).str.strip()
                values = values[values != ""]
                if not values.empty:
                    return values.iloc[0]
    return None


class _BytesUpload:
    """Adapter, damit csv_utils.read_csv_raw (erwartet ein Objekt mit
    .getvalue(), wie Streamlits UploadedFile) auch mit rohen Bytes von einer
    lokalen Datei funktioniert."""

    def __init__(self, data: bytes):
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


class Controller:
    def __init__(self, state):
        self.state = state
        self._success_tag_id_cache: int | None = None

    def _merge_new_transactions(self, new_transactions: list[dict]) -> int:
        """Gemeinsame Merge-Logik fuer alle drei Import-Wege (CSV-Upload
        mit bekanntem Mapping, CSV-Upload mit manuell bestaetigtem Mapping,
        externe Quelle z.B. Bank-API): FUEGT zu state.transactions hinzu,
        ERSETZT NICHT. Ein Ersetzen wuerde jede bereits geleistete
        Zuordnungs-/Tag-Arbeit verwerfen, sobald irgendeine neue Quelle
        eingelesen wird - genau das ist einmal real passiert (siehe Chat).

        Duplikat-Schutz: eine "neue" Buchung gilt als bereits vorhanden,
        wenn Datum+Betrag uebereinstimmen (z.B. bei ueberlappenden
        Zeitraeumen zwischen zwei Importen) - bewusst OHNE den
        Verwendungszweck im Schluessel: der ist bei zwei verschiedenen
        Datenquellen fuer dieselbe reale Buchung oft NICHT textgleich (z.B.
        CSV-Spaltentext vs. Enable-Banking-remittance_information fuer
        dieselbe Buchung) - ein Abgleich inkl. Verwendungszweck haette
        solche Buchungen faelschlich als neu erkannt und dupliziert
        (echter Bug, aufgetreten beim CSV+Bank-API-Mix, siehe Chat).
        Gleiches Datum+Betrag OHNE Uebereinstimmung im Text ist der
        gleiche Grenzfall wie eine mehrdeutige Paperless-Zuordnung (siehe
        matcher.match_transactions/TxStatus.MULTI_MATCH) - dort wird
        Mehrdeutigkeit bei gleichem Betrag ebenfalls bewusst in Kauf
        genommen statt zusaetzlich zu raten. Nur echte Neuzugaenge werden
        angehaengt. id/display_number werden fuer die komplette, gemergte
        Liste neu vergeben (siehe matcher.renumber_transactions), Status/
        Tags/Matches bestehender Eintraege bleiben dabei unangetastet.

        Gibt die Anzahl tatsaechlich neu hinzugefuegter (nicht
        doppelter) Transaktionen zurueck."""
        state = self.state
        existing_keys = {(t["date"], t["amount_raw"]) for t in state.transactions}
        added = [t for t in new_transactions if (t["date"], t["amount_raw"]) not in existing_keys]
        if added:
            state.transactions = state.transactions + added
            renumber_transactions(state.transactions)
            self.suggest_learned_tags()
            state.persist_session()
        return len(added)

    def _archive_import_file(self, filename: str, raw_bytes: bytes) -> None:
        """Legt eine Kopie jeder importierten Quelldatei in state.input_dir/
        ab - manueller CSV-Upload UND Bank-API-Import (dort als CSV
        serialisiert, siehe on_external_import), damit jederzeit
        nachvollziehbar bleibt, welche Rohdaten wann importiert wurden.
        Zeitstempel-Praefix verhindert, dass ein zweiter Import mit
        gleichem Dateinamen (z.B. eine Bank exportiert immer 'umsatz.csv')
        die vorherige Kopie stillschweigend ueberschreibt."""
        state = self.state
        state.input_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename).strip() or "import.csv"
        (state.input_dir / f"{timestamp}_{safe_name}").write_bytes(raw_bytes)

    # --- CSV-Import & Mapping ------------------------------------------------
    def on_csv_upload(self, filepath: str) -> dict:
        """Liest eine neue CSV ein und FUEGT ihre Buchungen zu den
        bestehenden hinzu (siehe _merge_new_transactions - eine weitere CSV
        ersetzt nicht mehr bereits geleistete Zuordnungs-/Tag-Arbeit).

        Gibt ein Dict zurueck:
        - mapping_ready: True, wenn ein bereits bekanntes Mapping automatisch
          angewendet werden konnte (dann sind transactions sofort ergaenzt),
          sonst False (Mapping muss bestaetigt werden, siehe on_mapping_confirm)
        - account_mismatch: bei bereits vorhandenen Buchungen und einer
          erkennbaren Kontonummer/IBAN-Spalte, die vom bisherigen Konto
          abweicht (siehe _find_account_identifier) - sonst None. Nur eine
          Warnung, kein Blocker - die UI muss selbst entscheiden, ob trotzdem
          fortgefahren wird.
        - added/duplicates: nur gesetzt, wenn mapping_ready True ist (beim
          manuellen Mapping-Weg liefert erst on_mapping_confirm() die Zahlen)
        - column_profiles: nur gesetzt, wenn mapping_ready False ist - je
          Spalte Rollen-Vorschlag/Konfidenz/Format-Info/Werte-Vorschau
          (siehe csv_utils.profile_columns), Datengrundlage fuer einen
          kuenftigen Mapping-Dialog mit Vorschau-Tabelle und gezielter
          Nachfrage bei mehrdeutigem Datumsformat."""
        state = self.state
        path = Path(filepath)
        raw_bytes = path.read_bytes()

        sig = f"{path.name}_{len(raw_bytes)}"
        if sig == state.csv_signature:
            return {"mapping_ready": state.mapping_confirmed, "account_mismatch": None}

        self._archive_import_file(path.name, raw_bytes)
        df, encoding, delimiter, _ = read_csv_raw(_BytesUpload(raw_bytes))

        account_mismatch = None
        if state.transactions and state.csv_df is not None:
            old_account = _find_account_identifier(state.csv_df, state.csv_df.columns)
            new_account = _find_account_identifier(df, df.columns)
            if old_account and new_account and old_account != new_account:
                account_mismatch = (
                    f"Die neue CSV scheint ein anderes Konto zu enthalten (bisher: {old_account}, "
                    f"neu: {new_account}) - falsche Datei ausgewaehlt?"
                )

        state.csv_df = df
        state.csv_columns = list(df.columns)
        state.csv_encoding = encoding
        state.csv_delimiter = delimiter
        state.csv_signature = sig
        state.matched_once = False

        col_sig = compute_csv_signature(df.columns)
        saved_mapping = state.config["csv_mappings"].get(col_sig)
        if saved_mapping:
            state.pending_mapping = saved_mapping
            state.mapping_confirmed = True
            new_transactions = build_transactions(df, saved_mapping)
            for t in new_transactions:
                t["row_index"] = None  # csv_df wurde oben ersetzt, alte row_index-Werte waeren falsch
            added = self._merge_new_transactions(new_transactions)
            return {
                "mapping_ready": True,
                "account_mismatch": account_mismatch,
                "added": added,
                "duplicates": len(new_transactions) - added,
            }

        state.pending_mapping = {"date_column": None, "amount_column": None, "purpose_column": None}
        state.mapping_confirmed = False
        return {"mapping_ready": False, "account_mismatch": account_mismatch, "column_profiles": profile_columns(df)}

    def on_mapping_confirm(
        self, date_column: str, amount_column: str, purpose_column: str, counterparty_column: str | None = None
    ) -> tuple[int, int]:
        """Gibt (added, duplicates) zurueck (siehe _merge_new_transactions)."""
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
        state.matched_once = False
        new_transactions = build_transactions(state.csv_df, mapping)
        for t in new_transactions:
            t["row_index"] = None  # csv_df wurde beim Upload ersetzt, alte row_index-Werte waeren falsch
        added = self._merge_new_transactions(new_transactions)
        return added, len(new_transactions) - added

    def on_external_import(self, df, mapping: dict) -> tuple[int, int]:
        """Importiert Transaktionen aus einem bereits fertigen DataFrame mit
        bekanntem Spalten-Mapping (z.B. ein Bank-API-Import) - siehe
        _merge_new_transactions. row_index bewusst auf None gesetzt: dieser
        df ist nicht state.csv_df (das bleibt unveraendert die zuletzt
        hochgeladene CSV) - row_index=None wird von
        reapply_counterparty_mapping() und beim gefilterten CSV-Export
        (exporter._build_kontoauszug_csv) korrekt als 'keine CSV-Quelle'
        behandelt. Gibt (added, duplicates) zurueck."""
        # Als CSV serialisiert archiviert (siehe _archive_import_file) - es
        # gibt bei einem API-Import keine rohe Quelldatei, aber derselbe
        # Nachvollziehbarkeits-Gedanke gilt genauso.
        self._archive_import_file("enable_banking_import.csv", df.to_csv(index=False).encode("utf-8-sig"))
        new_transactions = build_transactions(df, mapping)
        for t in new_transactions:
            t["row_index"] = None
        added = self._merge_new_transactions(new_transactions)
        return added, len(new_transactions) - added

    # --- Paperless-Abgleich ------------------------------------------------
    def on_match_click(self) -> int:
        state = self.state
        if state.client is None:
            raise RuntimeError("Paperless ist nicht konfiguriert (.env pruefen).")
        if not state.transactions:
            raise RuntimeError("Bitte zuerst eine CSV laden.")

        # MUSS vor match_transactions() laufen (siehe flag_duplicate_suspects-
        # Docstring): sonst koennte derselbe Beleg faelschlich beiden
        # Buchungen eines Duplikat-Paars zugeordnet werden.
        if state.config.get("duplicate_detection", {}).get("enabled", True):
            flag_duplicate_suspects(state.transactions)

        docs = fetch_and_prepare_paperless_docs(state.client, state.config["amount_detection"])
        match_transactions(state.transactions, docs, state.config["amount_matching"])

        if state.config.get("split_payment", {}).get("enabled", True):
            find_split_payment_candidates(state.transactions, docs, state.config["amount_matching"], state.config["split_payment"])

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
        """Manuelle Aufloesung eines Einzel-Dokument-Kandidaten (MULTI_MATCH -
        egal ob exakter Mehrfachtreffer oder Toleranz-Vorschlag, siehe
        matcher.MatchCandidate/MatchReasonType). Der Nutzer waehlt aus den
        bereits ermittelten Kandidaten (tx['candidate_docs'], NICHT aus der
        vollen Dokumentliste) das richtige EINZELNE Dokument aus - dafuer
        kommen nur Kandidaten mit genau einem Dokument in Frage (Duplikat-/
        Teilzahlungs-Kandidaten haben documents-Laenge 0 bzw. >1 und tragen
        stattdessen related_transaction_id, dafuer gibt es noch keine
        Aufloesungs-Aktion)."""
        tx = self._find_tx(transaction_id)
        candidates = tx.get("candidate_docs") or []
        match = next(
            (c for c in candidates if len(c.get("documents") or []) == 1 and c["documents"][0]["id"] == paperless_doc_id),
            None,
        )
        if match is None:
            raise ValueError(f"Dokument #{paperless_doc_id} ist kein Kandidat fuer diese Transaktion.")
        self._apply_matched_doc(transaction_id, match["documents"][0])

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
