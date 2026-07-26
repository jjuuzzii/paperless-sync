"""Modale Dialoge fuer die Desktop-UI: Einrichtung, CSV-Spalten-Mapping und
Paperless-Dokumentsuche (fuer rote Karten: 'Dokument aus Paperless waehlen';
fuer gelbe Karten wird direkt eine einfache Auswahlliste ohne Suche genutzt,
siehe desktop_app.py)."""
from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from paperless_sync.core.config_manager import PLACEHOLDER_TOKEN, csv_signature as compute_csv_signature
from paperless_sync.core.paperless_client import PaperlessClient
from icon_utils import apply_window_icon
from ctk_fixes import LeakSafeScrollableFrame
from theme import COLORS, FONT_SUBTITLE


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, state, on_saved):
        super().__init__(master)
        self.title("Einstellungen")
        self.geometry("540x720")
        self.configure(fg_color=COLORS["bg_main"])
        self.state_ref = state
        self.on_saved = on_saved
        self.transient(master)
        self.grab_set()
        apply_window_icon(self)

        self._selected_cert_file = None
        self._custom_fields_cache = []

        # Der Inhalt ist mittlerweile zu lang fuer eine feste Fensterhoehe
        # (CSV-Mapping + Rauschbegriffe kamen spaeter dazu) - scrollbar statt
        # das Fenster immer weiter zu strecken (koennte kleinere Bildschirme
        # sprengen). Test/Speichern-Buttons bleiben ausserhalb des Scroll-
        # Bereichs, damit sie immer erreichbar sind.
        self.scroll = LeakSafeScrollableFrame(self, label_text="", fg_color=COLORS["bg_main"])
        self.scroll.pack(fill="both", expand=True, padx=0, pady=0)

        access_card = self._section("Zugangsdaten")
        self.company_entry = self._labeled_entry(access_card, "Firmenname", state.env.get("COMPANY_NAME") or "")
        self.url_entry = self._labeled_entry(access_card, "Paperless-URL", state.env.get("PAPERLESS_URL") or "http://localhost:8000")
        existing_token = "" if state.env.get("PAPERLESS_TOKEN") in ("", PLACEHOLDER_TOKEN) else state.env.get("PAPERLESS_TOKEN")
        self.token_entry = self._labeled_entry(access_card, "Paperless-API-Token", existing_token, show="*")

        cert_card = self._section("Optional: Client-Zertifikat (mTLS)")
        self._cert_path_var = ctk.StringVar(value=state.env.get("PAPERLESS_CLIENT_CERT_PATH") or "kein Zertifikat")
        cert_row = ctk.CTkFrame(cert_card, fg_color="transparent")
        cert_row.pack(fill="x", padx=16)
        ctk.CTkLabel(cert_row, textvariable=self._cert_path_var, anchor="w").pack(side="left", fill="x", expand=True)
        ctk.CTkButton(cert_row, text="Datei waehlen", width=110, command=self._pick_cert).pack(side="right")
        self.cert_password_entry = self._labeled_entry(
            cert_card, "Zertifikat-Passwort", state.env.get("PAPERLESS_CLIENT_CERT_PASSWORD") or "", show="*"
        )

        export_card = self._section(
            "Exportordner",
            "Wohin 'ORDNER JETZT GENERIEREN' die fertigen Monatsordner schreibt - z.B. ein geteilter "
            "OneDrive-/Steuerberater-Ordner. Leer = Standard neben den App-Daten.",
        )
        self._selected_export_dir = state.config.get("export_dir") or None
        self._export_dir_var = ctk.StringVar(value=self._selected_export_dir or "Standard")
        export_row = ctk.CTkFrame(export_card, fg_color="transparent")
        export_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(export_row, textvariable=self._export_dir_var, anchor="w", wraplength=280).pack(
            side="left", fill="x", expand=True
        )
        ctk.CTkButton(export_row, text="Ordner waehlen", width=120, command=self._pick_export_dir).pack(side="right")
        ctk.CTkButton(
            export_row, text="Standard", width=80, fg_color="transparent", border_width=1,
            border_color=COLORS["text_muted"], hover_color=COLORS["bg_card_hover"], command=self._reset_export_dir,
        ).pack(side="right", padx=(0, 8))

        detect_card = self._section("Beleg-Erkennung")
        detection = state.config.get("amount_detection", {})
        self.method_var = ctk.StringVar(value="custom_field" if detection.get("method") == "custom_field" else "filename_regex")
        method_row = ctk.CTkFrame(detect_card, fg_color="transparent")
        method_row.pack(fill="x", padx=16)
        ctk.CTkRadioButton(
            method_row, text="Dateiname (Regex)", variable=self.method_var, value="filename_regex", command=self._refresh_method_fields
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            method_row, text="Paperless Custom Field", variable=self.method_var, value="custom_field", command=self._refresh_method_fields
        ).pack(side="left")

        self.regex_entry = self._labeled_entry(detect_card, "Regex-Muster (1. Gruppe = Betrag)", detection.get("regex_pattern") or r"_EUR(\d+\.\d+)")

        ctk.CTkLabel(detect_card, text="Custom Field mit Rechnungsbetrag").pack(padx=16, anchor="w")
        self.custom_field_var = ctk.StringVar(value=detection.get("custom_field_name") or "")
        self.custom_field_combo = ctk.CTkComboBox(detect_card, values=[], variable=self.custom_field_var)
        self._detect_card = detect_card

        self._csv_columns = list(state.csv_columns or [])
        self.map_date_combo = self.map_amount_combo = self.map_purpose_combo = self.map_counterparty_combo = None
        if self._csv_columns:
            csv_card = self._section(
                "CSV-Spalten-Zuordnung",
                "Gilt fuer das aktuell geladene CSV-Format. Absender/Empfaenger wirkt sofort auf bereits "
                "geladene Buchungen, Datum/Betrag/Verwendungszweck erst beim naechsten Import dieser Datei.",
            )

            col_sig = compute_csv_signature(self._csv_columns)
            current_mapping = state.config.get("csv_mappings", {}).get(col_sig) or state.pending_mapping or {}

            ctk.CTkLabel(csv_card, text="Spalte fuer Datum").pack(padx=16, anchor="w")
            self.map_date_combo = ctk.CTkComboBox(csv_card, values=self._csv_columns)
            if current_mapping.get("date_column") in self._csv_columns:
                self.map_date_combo.set(current_mapping["date_column"])
            self.map_date_combo.pack(fill="x", padx=16, pady=(0, 8))

            ctk.CTkLabel(csv_card, text="Spalte fuer Betrag").pack(padx=16, anchor="w")
            self.map_amount_combo = ctk.CTkComboBox(csv_card, values=self._csv_columns)
            if current_mapping.get("amount_column") in self._csv_columns:
                self.map_amount_combo.set(current_mapping["amount_column"])
            self.map_amount_combo.pack(fill="x", padx=16, pady=(0, 8))

            ctk.CTkLabel(self.scroll, text="Spalte fuer Verwendungszweck").pack(padx=16, anchor="w")
            self.map_purpose_combo = ctk.CTkComboBox(csv_card, values=self._csv_columns)
            if current_mapping.get("purpose_column") in self._csv_columns:
                self.map_purpose_combo.set(current_mapping["purpose_column"])
            self.map_purpose_combo.pack(fill="x", padx=16, pady=(0, 8))

            ctk.CTkLabel(csv_card, text="Spalte fuer Absender/Empfaenger (optional)").pack(padx=16, anchor="w")
            counterparty_values = [MappingDialog.NONE_OPTION] + self._csv_columns
            self.map_counterparty_combo = ctk.CTkComboBox(csv_card, values=counterparty_values)
            current_counterparty = current_mapping.get("counterparty_column")
            self.map_counterparty_combo.set(
                current_counterparty if current_counterparty in self._csv_columns else MappingDialog.NONE_OPTION
            )
            self.map_counterparty_combo.pack(fill="x", padx=16, pady=(0, 8))

        noise_card = self._section(
            "Verwendungszweck: Rauschbegriffe ausblenden",
            "Nur in der Kartenanzeige entfernt (Export/Zuordnung unveraendert). IBAN/BIC werden immer automatisch entfernt.",
        )
        self._noise_terms = list(state.config.get("purpose_noise_terms", []))
        self.noise_list_frame = ctk.CTkFrame(noise_card, fg_color="transparent")
        self.noise_list_frame.pack(fill="x", padx=16, pady=(4, 4))
        self._render_noise_terms()

        noise_add_row = ctk.CTkFrame(noise_card, fg_color="transparent")
        noise_add_row.pack(fill="x", padx=16, pady=(0, 8))
        self.noise_entry = ctk.CTkEntry(noise_add_row, placeholder_text="z.B. MC Hauptkarte")
        self.noise_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(noise_add_row, text="+", width=36, command=self._add_noise_term).pack(side="right")

        tags_card = self._section(
            "Eigene Tags verwalten",
            "Loescht nur die Tag-Definition aus der Schnellauswahl/Sonstiges-Liste. Bereits getaggte Buchungen behalten ihren Tag.",
        )
        self._custom_tags = dict(state.config.get("custom_tags", {}))
        self.custom_tags_list_frame = ctk.CTkFrame(tags_card, fg_color="transparent")
        self.custom_tags_list_frame.pack(fill="x", padx=16, pady=(4, 8))
        self._render_custom_tags()

        self.status_label = ctk.CTkLabel(self.scroll, text="", text_color="#e03131")
        self.status_label.pack(pady=(8, 16))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=16, side="bottom")
        ctk.CTkButton(btn_row, text="Verbindung testen", command=self._test_connection).pack(side="left")
        ctk.CTkButton(btn_row, text="Speichern", command=self._save).pack(side="right")

        self._refresh_method_fields()

    def _section(self, title: str, description: str | None = None) -> ctk.CTkFrame:
        """Baut eine dezent abgesetzte, abgerundete Karte fuer eine
        zusammengehoerige Gruppe von Feldern - dieselbe Optik wie die
        KPI-/Transaktions-Karten im Hauptfenster, statt eine flache Liste
        von Labels/Eingabefeldern ohne visuelle Gruppierung."""
        card = ctk.CTkFrame(self.scroll, corner_radius=12, fg_color=COLORS["bg_kpi"])
        card.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(card, text=title, font=FONT_SUBTITLE, text_color=COLORS["text_primary"]).pack(
            pady=(14, 2 if description else 10), padx=16, anchor="w"
        )
        if description:
            ctk.CTkLabel(
                card, text=description, text_color=COLORS["text_muted"], font=("", 10), wraplength=460, justify="left"
            ).pack(padx=16, anchor="w", pady=(0, 10))
        return card

    def _render_noise_terms(self):
        for child in self.noise_list_frame.winfo_children():
            child.destroy()
        if not self._noise_terms:
            ctk.CTkLabel(self.noise_list_frame, text="(keine)", text_color="gray").pack(anchor="w")
            return
        for term in self._noise_terms:
            row = ctk.CTkFrame(self.noise_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=term, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row,
                text="×",
                width=28,
                fg_color="transparent",
                text_color="#e03131",
                hover_color=("gray85", "gray25"),
                command=lambda t=term: self._remove_noise_term(t),
            ).pack(side="right")

    def _add_noise_term(self):
        val = self.noise_entry.get().strip()
        if val and val not in self._noise_terms:
            self._noise_terms.append(val)
            self.noise_entry.delete(0, "end")
            self._render_noise_terms()

    def _remove_noise_term(self, term):
        self._noise_terms.remove(term)
        self._render_noise_terms()

    def _render_custom_tags(self):
        for child in self.custom_tags_list_frame.winfo_children():
            child.destroy()
        if not self._custom_tags:
            ctk.CTkLabel(self.custom_tags_list_frame, text="(keine eigenen Tags)", text_color="gray").pack(anchor="w")
            return
        for name in sorted(self._custom_tags, key=lambda n: -self._custom_tags[n]):
            row = ctk.CTkFrame(self.custom_tags_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            count = self._custom_tags[name]
            ctk.CTkLabel(row, text=f"{name}  ({count}x verwendet)", anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row,
                text="×",
                width=28,
                fg_color="transparent",
                text_color="#e03131",
                hover_color=("gray85", "gray25"),
                command=lambda n=name: self._remove_custom_tag(n),
            ).pack(side="right")

    def _remove_custom_tag(self, name):
        self._custom_tags.pop(name, None)
        self._render_custom_tags()

    def _labeled_entry(self, parent, label, value, show=None):
        ctk.CTkLabel(parent, text=label).pack(padx=16, anchor="w")
        entry = ctk.CTkEntry(parent, show=show, fg_color=COLORS["bg_input"])
        entry.insert(0, value)
        entry.pack(fill="x", padx=16, pady=(0, 8))
        return entry

    def _pick_export_dir(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(title="Exportordner waehlen")
        if path:
            self._selected_export_dir = path
            self._export_dir_var.set(path)

    def _reset_export_dir(self):
        self._selected_export_dir = None
        self._export_dir_var.set("Standard")

    def _pick_cert(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Client-Zertifikat waehlen", filetypes=[("PKCS#12", "*.p12 *.pfx"), ("Alle Dateien", "*.*")]
        )
        if path:
            self._selected_cert_file = path
            self._cert_path_var.set(Path(path).name)

    def _refresh_method_fields(self):
        if self.method_var.get() == "filename_regex":
            self.custom_field_combo.pack_forget()
            self.regex_entry.pack(fill="x", padx=16, pady=(0, 8))
        else:
            self.regex_entry.pack_forget()
            self._load_custom_fields()
            self.custom_field_combo.pack(fill="x", padx=16, pady=(0, 8))

    def _build_temp_client(self):
        url = self.url_entry.get().strip()
        token = self.token_entry.get().strip()
        if not url or not token:
            return None
        cert_path = None
        if self._selected_cert_file:
            cert_path = self._selected_cert_file
        elif self._cert_path_var.get() and self._cert_path_var.get() != "kein Zertifikat":
            candidate = self.state_ref.base_dir / self._cert_path_var.get()
            if candidate.exists():
                cert_path = str(candidate)
        try:
            return PaperlessClient(url, token, client_cert_path=cert_path, client_cert_password=self.cert_password_entry.get() or None)
        except Exception:
            return None

    def _load_custom_fields(self):
        if self._custom_fields_cache:
            return
        client = self._build_temp_client()
        if not client:
            return
        try:
            fields = client.get_custom_fields()
            self._custom_fields_cache = fields
            self.custom_field_combo.configure(values=[f["name"] for f in fields])
        except Exception as exc:
            self.status_label.configure(text=f"Custom Fields nicht ladbar: {exc}")

    def _test_connection(self):
        client = self._build_temp_client()
        if client and client.test_connection():
            self.status_label.configure(text="Verbindung erfolgreich.", text_color="#2f9e44")
        else:
            self.status_label.configure(text="Verbindung fehlgeschlagen - URL/Token pruefen.", text_color="#e03131")

    def _save(self):
        state = self.state_ref
        url = self.url_entry.get().strip()
        token = self.token_entry.get().strip()
        if not url or not token or token == PLACEHOLDER_TOKEN:
            self.status_label.configure(text="URL und ein echter Token sind Pflicht.", text_color="#e03131")
            return

        env_updates = {
            "PAPERLESS_URL": url.rstrip("/"),
            "PAPERLESS_TOKEN": token,
            "COMPANY_NAME": self.company_entry.get().strip(),
        }
        if self._selected_cert_file:
            cert_dest = state.base_dir / "paperless_client_cert.p12"
            cert_dest.write_bytes(Path(self._selected_cert_file).read_bytes())
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = cert_dest.name
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = self.cert_password_entry.get()
        elif self._cert_path_var.get() and self._cert_path_var.get() != "kein Zertifikat":
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = self._cert_path_var.get()
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = self.cert_password_entry.get()
        state.save_env(env_updates)

        detection = dict(state.config.get("amount_detection", {}))
        detection["method"] = self.method_var.get()
        if self.method_var.get() == "filename_regex":
            detection["regex_pattern"] = self.regex_entry.get().strip() or r"_EUR(\d+\.\d+)"
        else:
            name = self.custom_field_var.get()
            match = next((f for f in self._custom_fields_cache if f["name"] == name), None)
            if match:
                detection["custom_field_id"] = match["id"]
                detection["custom_field_name"] = match["name"]
        state.config["amount_detection"] = detection
        state.config["purpose_noise_terms"] = list(self._noise_terms)
        state.config["custom_tags"] = dict(self._custom_tags)
        state.config["export_dir"] = self._selected_export_dir

        if self._csv_columns and self.map_date_combo:
            col_sig = compute_csv_signature(self._csv_columns)
            counterparty_val = self.map_counterparty_combo.get()
            new_mapping = {
                "date_column": self.map_date_combo.get(),
                "amount_column": self.map_amount_combo.get(),
                "purpose_column": self.map_purpose_combo.get(),
                "counterparty_column": None if counterparty_val == MappingDialog.NONE_OPTION else counterparty_val,
            }
            state.config.setdefault("csv_mappings", {})[col_sig] = new_mapping
            state.pending_mapping = new_mapping

        state.save_config()
        state.reapply_counterparty_mapping()

        self.destroy()
        self.on_saved()


class MappingDialog(ctk.CTkToplevel):
    NONE_OPTION = "— keine —"

    def __init__(self, master, columns: list[str], on_confirm):
        super().__init__(master)
        self.title("CSV-Spalten zuordnen")
        self.geometry("420x460")
        self.transient(master)
        self.grab_set()
        apply_window_icon(self)
        self.on_confirm = on_confirm

        ctk.CTkLabel(self, text="Spalte fuer Datum").pack(padx=16, pady=(16, 0), anchor="w")
        self.date_combo = ctk.CTkComboBox(self, values=columns)
        self.date_combo.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(self, text="Spalte fuer Betrag").pack(padx=16, anchor="w")
        self.amount_combo = ctk.CTkComboBox(self, values=columns)
        self.amount_combo.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(self, text="Spalte fuer Verwendungszweck").pack(padx=16, anchor="w")
        self.purpose_combo = ctk.CTkComboBox(self, values=columns)
        self.purpose_combo.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(self, text="Spalte fuer Absender/Empfaenger (optional)").pack(padx=16, anchor="w")
        counterparty_values = [self.NONE_OPTION] + columns
        self.counterparty_combo = ctk.CTkComboBox(self, values=counterparty_values)
        self.counterparty_combo.set(self._guess_counterparty_column(columns))
        self.counterparty_combo.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkButton(self, text="Bestaetigen", command=self._confirm).pack(pady=16)

    def _guess_counterparty_column(self, columns: list[str]) -> str:
        """Deutsche Bank-CSVs nennen die Gegenpartei meist so - falls
        vorhanden, direkt vorauswaehlen statt 'keine'."""
        for candidate in ("Name Zahlungsbeteiligter", "Beguenstigter/Zahlungspflichtiger", "Empfaenger/Zahlungspflichtiger"):
            if candidate in columns:
                return candidate
        return self.NONE_OPTION

    def _confirm(self):
        counterparty = self.counterparty_combo.get()
        if counterparty == self.NONE_OPTION:
            counterparty = None
        self.on_confirm(self.date_combo.get(), self.amount_combo.get(), self.purpose_combo.get(), counterparty)
        self.destroy()


class DocumentSearchDialog(ctk.CTkToplevel):
    """Such-Modal fuer rote Karten ('Dokument aus Paperless waehlen') -
    live gefilterte Liste aller Paperless-Dokumente. Wird ein Custom Field
    benutzt und ist der Betrag dort noch leer, erscheint zusaetzlich ein
    Eingabefeld, um den Wert nachzutragen."""

    def __init__(self, master, docs: list[dict], show_value_entry: bool, default_value: str, on_select):
        super().__init__(master)
        self.title("Paperless-Dokument waehlen")
        self.geometry("560x560")
        self.transient(master)
        self.grab_set()
        apply_window_icon(self)

        self.docs = docs
        self.on_select = on_select
        self.selected_doc = None
        self.show_value_entry = show_value_entry
        self.default_value = default_value

        self.search_var = ctk.StringVar()
        entry = ctk.CTkEntry(self, textvariable=self.search_var, placeholder_text="Suche nach Titel/Korrespondent...")
        entry.pack(fill="x", padx=16, pady=(16, 8))
        self.search_var.trace_add("write", lambda *_: self._refresh_list())

        self.list_frame = LeakSafeScrollableFrame(self, label_text="")
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        self.value_label = ctk.CTkLabel(self, text="Wert fuer das Custom Field:")
        self.value_entry = ctk.CTkEntry(self)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=16, side="bottom")
        ctk.CTkButton(btn_row, text="Abbrechen", command=self.destroy).pack(side="left")
        self.confirm_btn = ctk.CTkButton(btn_row, text="Verknuepfen", command=self._confirm, state="disabled")
        self.confirm_btn.pack(side="right")

        self._doc_buttons = []
        self._refresh_list()

    @staticmethod
    def _label_for(doc: dict) -> str:
        label = f"#{doc['id']} - {doc['title'] or doc['original_file_name'] or 'ohne Titel'}"
        if doc.get("correspondent_name"):
            label += f" · {doc['correspondent_name']}"
        label += f" · {doc['date'].strftime('%d.%m.%Y') if doc.get('date') else 'kein Datum'}"
        label += f" (aktuell: {doc['amount'] if doc.get('amount') is not None else 'leer'})"
        return label

    def _refresh_list(self):
        query = self.search_var.get().strip().lower()
        for child in self.list_frame.winfo_children():
            child.destroy()
        for doc in self.docs:
            label = self._label_for(doc)
            if query and query not in label.lower():
                continue
            btn = ctk.CTkButton(
                self.list_frame,
                text=label,
                anchor="w",
                fg_color="transparent",
                text_color=("black", "white"),
                hover_color=("gray85", "gray25"),
                command=lambda d=doc: self._select(d),
            )
            btn.pack(fill="x", pady=2)

    def _select(self, doc):
        self.selected_doc = doc
        self.confirm_btn.configure(state="normal")
        if self.show_value_entry and doc.get("amount") is None:
            self.value_label.pack(anchor="w", padx=16)
            self.value_entry.delete(0, "end")
            self.value_entry.insert(0, self.default_value)
            self.value_entry.pack(fill="x", padx=16, pady=(0, 8))
        else:
            self.value_label.pack_forget()
            self.value_entry.pack_forget()

    def _confirm(self):
        if not self.selected_doc:
            return
        value = self.value_entry.get().strip() if self.value_entry.winfo_ismapped() else None
        self.on_select(self.selected_doc["id"], value)
        self.destroy()
