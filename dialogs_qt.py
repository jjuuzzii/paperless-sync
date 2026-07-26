"""Qt-Dialoge fuer die PySide6-UI (desktop_app_qt.py) - Pendant zu
dialogs.py (CustomTkinter-Version). Wird schrittweise ausgebaut."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QIcon
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QComboBox,
    QPushButton,
    QLineEdit,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QFrame,
    QScrollArea,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
)

from paperless_sync.core.backup import create_backup, restore_backup, backup_filename
from paperless_sync.core.config_manager import get_resource_dir, csv_signature as compute_csv_signature, PLACEHOLDER_TOKEN
from paperless_sync.core.paperless_client import PaperlessClient
from theme_qt import COLORS, font as qfont
from paperless_sync.core.i18n import tr, set_language, get_language


def _apply_window_icon(window):
    # Immer das mitgelieferte Standard-Icon - das hochgeladene Firmenlogo
    # wird bewusst NUR in der Sidebar des Hauptfensters angezeigt (siehe
    # DesktopAppQt._refresh_logo), nicht als Fenster-/Taskleisten-Icon.
    icon_path = get_resource_dir() / "icon.ico"
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))


def _combo_style() -> str:
    return (
        f"QComboBox {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
        f"border-radius: 10px; padding: 8px; border: none; }}"
    )


def _entry_style() -> str:
    return (
        f"QLineEdit {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
        f"border-radius: 10px; padding: 8px; border: none; }}"
    )


def _none_option() -> str:
    """Auswahl 'keine Spalte' fuer optionale CSV-Spalten-Zuordnungen - als
    Funktion statt Klassenkonstante, damit sie bei einem Sprachwechsel
    (siehe i18n.set_language) nicht auf den beim Modul-Import aktiven
    Sprachstand eingefroren bleibt."""
    return tr("— keine —")


def _small_x_button() -> QPushButton:
    btn = QPushButton("×")
    btn.setFixedWidth(28)
    btn.setStyleSheet(
        f"QPushButton {{ background: transparent; color: {COLORS['red']}; border: none; font-weight: bold; }}"
        f"QPushButton:hover {{ color: #ff6b6b; }}"
    )
    return btn


class PdfViewerDialog(QDialog):
    """Zeigt einen Beleg (PDF-Bytes, egal ob von Paperless heruntergeladen
    oder lokal hochgeladen) nativ eingebettet an (QtPdf/QPdfView) - damit
    man beim Zuordnen/Pruefen nicht jedes Mal die Datei extern oeffnen
    muss."""

    def __init__(self, parent, pdf_bytes: bytes, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 960)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)

        # QByteArray/QBuffer muessen als Attribute am Leben gehalten werden,
        # solange das QPdfDocument daraus liest - anders als beim Laden von
        # einer Datei raeumt Python sie sonst vorzeitig weg (Absturz-Risiko).
        self._byte_array = QByteArray(pdf_bytes)
        self._buffer = QBuffer(self._byte_array)
        self._buffer.open(QIODevice.ReadOnly)

        self.document = QPdfDocument(self)
        self.document.load(self._buffer)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self.document.status() != QPdfDocument.Status.Ready:
            error_lbl = QLabel(tr("PDF konnte nicht geladen werden."))
            error_lbl.setStyleSheet(f"color: {COLORS['red']}; padding: 24px;")
            error_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(error_lbl, stretch=1)
        else:
            self.view = QPdfView(self)
            self.view.setDocument(self.document)
            self.view.setPageMode(QPdfView.PageMode.MultiPage)
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            layout.addWidget(self.view, stretch=1)

        close_row = QHBoxLayout()
        close_row.setContentsMargins(12, 8, 12, 12)
        close_row.addStretch()
        close_btn = QPushButton(tr("Schliessen"))
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['bg_card']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)


class SettingsDialog(QDialog):
    def __init__(self, parent, state, on_saved):
        super().__init__(parent)
        self.setWindowTitle(tr("Einstellungen"))
        self.resize(560, 760)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)
        self.state_ref = state
        self.on_saved = on_saved
        self._selected_cert_file = None
        self._custom_fields_cache = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(16, 16, 16, 16)
        self.scroll_layout.setSpacing(0)
        scroll.setWidget(self.scroll_content)
        outer.addWidget(scroll, stretch=1)

        access_card = self._section(tr("Zugangsdaten"))
        self.company_entry = self._labeled_entry(access_card, tr("Firmenname"), state.env.get("COMPANY_NAME") or "")
        self.url_entry = self._labeled_entry(access_card, tr("Paperless-URL"), state.env.get("PAPERLESS_URL") or "http://localhost:8000")
        existing_token = "" if state.env.get("PAPERLESS_TOKEN") in ("", PLACEHOLDER_TOKEN) else state.env.get("PAPERLESS_TOKEN")
        self.token_entry = self._labeled_entry(access_card, tr("Paperless-API-Token"), existing_token, password=True)

        cert_card = self._section(tr("Optional: Client-Zertifikat (mTLS)"))
        self._cert_path_display = state.env.get("PAPERLESS_CLIENT_CERT_PATH") or tr("kein Zertifikat")
        cert_row = QHBoxLayout()
        self.cert_label = QLabel(self._cert_path_display)
        self.cert_label.setStyleSheet("border: none;")
        cert_row.addWidget(self.cert_label, stretch=1)
        cert_btn = QPushButton(tr("Datei waehlen"))
        cert_btn.setStyleSheet(self._button_style())
        cert_btn.clicked.connect(self._pick_cert)
        cert_row.addWidget(cert_btn)
        cert_card.layout().addLayout(cert_row)
        self.cert_password_entry = self._labeled_entry(
            cert_card, tr("Zertifikat-Passwort"), state.env.get("PAPERLESS_CLIENT_CERT_PASSWORD") or "", password=True
        )

        export_card = self._section(
            tr("Exportordner"),
            tr(
                "Wohin 'ORDNER JETZT GENERIEREN' die fertigen Monatsordner schreibt - z.B. ein geteilter "
                "OneDrive-/Steuerberater-Ordner. Standard = neben den App-Daten."
            ),
        )
        self._selected_export_dir = state.config.get("export_dir") or None
        export_row = QHBoxLayout()
        self.export_dir_label = QLabel(self._selected_export_dir or tr("Standard"))
        self.export_dir_label.setWordWrap(True)
        self.export_dir_label.setStyleSheet("border: none;")
        export_row.addWidget(self.export_dir_label, stretch=1)
        export_reset_btn = QPushButton(tr("Standard"))
        export_reset_btn.setStyleSheet(self._outline_button_style())
        export_reset_btn.clicked.connect(self._reset_export_dir)
        export_row.addWidget(export_reset_btn)
        export_pick_btn = QPushButton(tr("Ordner waehlen"))
        export_pick_btn.setStyleSheet(self._button_style())
        export_pick_btn.clicked.connect(self._pick_export_dir)
        export_row.addWidget(export_pick_btn)
        export_card.layout().addLayout(export_row)

        detect_card = self._section(tr("Beleg-Erkennung"))
        detection = state.config.get("amount_detection", {})
        method_row = QHBoxLayout()
        self.regex_radio = QRadioButton(tr("Dateiname (Regex)"))
        self.custom_field_radio = QRadioButton(tr("Paperless Custom Field"))
        self.regex_radio.setStyleSheet("border: none;")
        self.custom_field_radio.setStyleSheet("border: none;")
        method_group = QButtonGroup(self)
        method_group.addButton(self.regex_radio)
        method_group.addButton(self.custom_field_radio)
        if detection.get("method") == "custom_field":
            self.custom_field_radio.setChecked(True)
        else:
            self.regex_radio.setChecked(True)
        self.regex_radio.toggled.connect(self._refresh_method_fields)
        method_row.addWidget(self.regex_radio)
        method_row.addWidget(self.custom_field_radio)
        method_row.addStretch()
        detect_card.layout().addLayout(method_row)

        self.regex_entry = self._labeled_entry(
            detect_card, tr("Regex-Muster (1. Gruppe = Betrag)"), detection.get("regex_pattern") or r"_EUR(\d+\.\d+)"
        )

        self.custom_field_label = QLabel(tr("Custom Field mit Rechnungsbetrag"))
        self.custom_field_label.setStyleSheet("border: none;")
        detect_card.layout().addWidget(self.custom_field_label)
        self.custom_field_combo = QComboBox()
        self.custom_field_combo.setEditable(True)
        self.custom_field_combo.setStyleSheet(_combo_style())
        if detection.get("custom_field_name"):
            self.custom_field_combo.addItem(detection["custom_field_name"])
        detect_card.layout().addWidget(self.custom_field_combo)

        self._csv_columns = list(state.csv_columns or [])
        self.map_date_combo = self.map_amount_combo = self.map_purpose_combo = self.map_counterparty_combo = None
        if self._csv_columns:
            csv_card = self._section(
                tr("CSV-Spalten-Zuordnung"),
                tr(
                    "Gilt fuer das aktuell geladene CSV-Format. Absender/Empfaenger wirkt sofort auf bereits "
                    "geladene Buchungen, Datum/Betrag/Verwendungszweck erst beim naechsten Import dieser Datei."
                ),
            )
            col_sig = compute_csv_signature(self._csv_columns)
            current_mapping = state.config.get("csv_mappings", {}).get(col_sig) or state.pending_mapping or {}

            self.map_date_combo = self._labeled_combo(csv_card, tr("Spalte fuer Datum"), self._csv_columns, current_mapping.get("date_column"))
            self.map_amount_combo = self._labeled_combo(csv_card, tr("Spalte fuer Betrag"), self._csv_columns, current_mapping.get("amount_column"))
            self.map_purpose_combo = self._labeled_combo(csv_card, tr("Spalte fuer Verwendungszweck"), self._csv_columns, current_mapping.get("purpose_column"))
            counterparty_values = [_none_option()] + self._csv_columns
            current_counterparty = current_mapping.get("counterparty_column")
            self.map_counterparty_combo = self._labeled_combo(
                csv_card, tr("Spalte fuer Absender/Empfaenger (optional)"), counterparty_values,
                current_counterparty if current_counterparty in self._csv_columns else _none_option(),
            )

        noise_card = self._section(
            tr("Verwendungszweck: Rauschbegriffe ausblenden"),
            tr("Nur in der Kartenanzeige entfernt (Export/Zuordnung unveraendert). IBAN/BIC werden immer automatisch entfernt."),
        )
        self._noise_terms = list(state.config.get("purpose_noise_terms", []))
        self.noise_list_layout = QVBoxLayout()
        noise_card.layout().addLayout(self.noise_list_layout)
        self._render_noise_terms()

        noise_add_row = QHBoxLayout()
        self.noise_entry = QLineEdit()
        self.noise_entry.setPlaceholderText(tr("z.B. MC Hauptkarte"))
        self.noise_entry.setStyleSheet(_entry_style())
        noise_add_row.addWidget(self.noise_entry, stretch=1)
        noise_add_btn = QPushButton("+")
        noise_add_btn.setFixedWidth(36)
        noise_add_btn.setStyleSheet(self._button_style())
        noise_add_btn.clicked.connect(self._add_noise_term)
        noise_add_row.addWidget(noise_add_btn)
        noise_card.layout().addLayout(noise_add_row)

        tags_card = self._section(
            tr("Eigene Tags verwalten"),
            tr("Loescht nur die Tag-Definition aus der Schnellauswahl/Sonstiges-Liste. Bereits getaggte Buchungen behalten ihren Tag."),
        )
        self._custom_tags = dict(state.config.get("custom_tags", {}))
        self.tags_list_layout = QVBoxLayout()
        tags_card.layout().addLayout(self.tags_list_layout)
        self._render_custom_tags()

        paperless_tag_card = self._section(
            tr("Paperless-Erfolgs-Tag"),
            tr(
                "Setzt in Paperless selbst einen Tag auf Dokumente, die erfolgreich einer Buchung zugeordnet "
                "wurden (automatischer Match, manuelle Verknuepfung, aufgeloester Mehrfach-Match). Gilt nicht "
                "fuer frisch hochgeladene PDFs (Paperless verarbeitet die erst asynchron)."
            ),
        )
        self.paperless_tag_checkbox = QCheckBox(tr("Aktiviert"))
        self.paperless_tag_checkbox.setStyleSheet("border: none;")
        self.paperless_tag_checkbox.setChecked(bool(state.config.get("paperless_success_tag_enabled", True)))
        paperless_tag_card.layout().addWidget(self.paperless_tag_checkbox)
        self.paperless_tag_name_entry = self._labeled_entry(
            paperless_tag_card, tr("Tag-Name"), state.config.get("paperless_success_tag_name") or "Abgeglichen"
        )

        logo_card = self._section(
            tr("Firmenlogo"),
            tr("Eigenes Logo statt der Buerklammer oben links in der Seitenleiste. Nur PNG, quadratisch empfohlen."),
        )
        self._selected_logo_file = None
        self._logo_removed = False
        self._logo_path_display = state.config.get("company_icon_path") or tr("kein Logo")
        logo_row = QHBoxLayout()
        self.logo_label = QLabel(self._logo_path_display)
        self.logo_label.setStyleSheet("border: none;")
        logo_row.addWidget(self.logo_label, stretch=1)
        logo_reset_btn = QPushButton(tr("Zuruecksetzen"))
        logo_reset_btn.setStyleSheet(self._outline_button_style())
        logo_reset_btn.clicked.connect(self._reset_logo)
        logo_row.addWidget(logo_reset_btn)
        logo_pick_btn = QPushButton(tr("Logo waehlen"))
        logo_pick_btn.setStyleSheet(self._button_style())
        logo_pick_btn.clicked.connect(self._pick_logo)
        logo_row.addWidget(logo_pick_btn)
        logo_card.layout().addLayout(logo_row)

        lang_card = self._section(
            tr("Sprache"),
            tr("Sprache der Oberflaeche. Wirkt erst nach einem Neustart der App."),
        )
        lang_row = QHBoxLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.setStyleSheet(_combo_style())
        self.lang_combo.addItems(["Deutsch", "English"])
        self.lang_combo.setCurrentText("English" if get_language() == "en" else "Deutsch")
        lang_row.addWidget(self.lang_combo, stretch=1)
        lang_card.layout().addLayout(lang_row)

        backup_card = self._section(
            tr("Datensicherung"),
            tr(
                "Sichert Einstellungen, gelernte Tags, Paperless-Zugangsdaten und den aktuellen Arbeitsstand "
                "als ZIP - z.B. vor einem Rechnerwechsel."
            ),
        )
        backup_row = QHBoxLayout()
        backup_btn = QPushButton(f"⬇ {tr('Backup erstellen')}")
        backup_btn.setStyleSheet(self._button_style())
        backup_btn.clicked.connect(self._create_backup)
        backup_row.addWidget(backup_btn)
        restore_btn = QPushButton(f"♻ {tr('Backup wiederherstellen')}")
        restore_btn.setStyleSheet(self._outline_button_style())
        restore_btn.clicked.connect(self._restore_backup)
        backup_row.addWidget(restore_btn)
        backup_card.layout().addLayout(backup_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
        self.status_label.setWordWrap(True)
        self.scroll_layout.addWidget(self.status_label)
        self.scroll_layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 8, 16, 16)
        test_btn = QPushButton(tr("Verbindung testen"))
        test_btn.setStyleSheet(self._button_style())
        test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(test_btn)
        btn_row.addStretch()
        save_btn = QPushButton(tr("Speichern"))
        save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
            f"padding: 10px 20px; font-weight: bold; }} QPushButton:hover {{ background-color: #4a76d6; }}"
        )
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

        self._refresh_method_fields()

    # ------------------------------------------------------------------
    def _button_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {COLORS['bg_card']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 8px 14px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )

    def _outline_button_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: 1px solid "
            f"{COLORS['text_muted']}; border-radius: 10px; padding: 8px 14px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )

    def _section(self, title: str, description: str | None = None) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {COLORS['bg_kpi']}; border-radius: 12px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setFont(qfont(11, bold=True))
        title_lbl.setStyleSheet("border: none;")
        layout.addWidget(title_lbl)
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt; border: none;")
            layout.addWidget(desc_lbl)
        self.scroll_layout.addWidget(card)
        self.scroll_layout.addSpacing(14)
        return card

    def _labeled_entry(self, parent_card: QFrame, label: str, value: str, password: bool = False) -> QLineEdit:
        lbl = QLabel(label)
        lbl.setStyleSheet("border: none;")
        parent_card.layout().addWidget(lbl)
        entry = QLineEdit(value)
        entry.setStyleSheet(_entry_style())
        if password:
            entry.setEchoMode(QLineEdit.Password)
        parent_card.layout().addWidget(entry)
        return entry

    def _labeled_combo(self, parent_card: QFrame, label: str, values: list[str], current: str | None) -> QComboBox:
        lbl = QLabel(label)
        lbl.setStyleSheet("border: none;")
        parent_card.layout().addWidget(lbl)
        combo = QComboBox()
        combo.setStyleSheet(_combo_style())
        combo.addItems(values)
        if current and current in values:
            combo.setCurrentText(current)
        parent_card.layout().addWidget(combo)
        return combo

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_noise_terms(self):
        self._clear_layout(self.noise_list_layout)
        if not self._noise_terms:
            lbl = QLabel(tr("(keine)"))
            lbl.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
            self.noise_list_layout.addWidget(lbl)
            return
        for term in self._noise_terms:
            row = QHBoxLayout()
            lbl = QLabel(term)
            lbl.setStyleSheet("border: none;")
            row.addWidget(lbl, stretch=1)
            btn = _small_x_button()
            btn.clicked.connect(lambda _=False, t=term: self._remove_noise_term(t))
            row.addWidget(btn)
            self.noise_list_layout.addLayout(row)

    def _add_noise_term(self):
        val = self.noise_entry.text().strip()
        if val and val not in self._noise_terms:
            self._noise_terms.append(val)
            self.noise_entry.clear()
            self._render_noise_terms()

    def _remove_noise_term(self, term):
        self._noise_terms.remove(term)
        self._render_noise_terms()

    def _render_custom_tags(self):
        self._clear_layout(self.tags_list_layout)
        if not self._custom_tags:
            lbl = QLabel(tr("(keine eigenen Tags)"))
            lbl.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
            self.tags_list_layout.addWidget(lbl)
            return
        for name in sorted(self._custom_tags, key=lambda n: -self._custom_tags[n]):
            row = QHBoxLayout()
            lbl = QLabel(tr("{name}  ({count}x verwendet)", name=name, count=self._custom_tags[name]))
            lbl.setStyleSheet("border: none;")
            row.addWidget(lbl, stretch=1)
            btn = _small_x_button()
            btn.clicked.connect(lambda _=False, n=name: self._remove_custom_tag(n))
            row.addWidget(btn)
            self.tags_list_layout.addLayout(row)

    def _remove_custom_tag(self, name):
        self._custom_tags.pop(name, None)
        self._render_custom_tags()

    def _pick_export_dir(self):
        path = QFileDialog.getExistingDirectory(self, tr("Exportordner waehlen"))
        if path:
            self._selected_export_dir = path
            self.export_dir_label.setText(path)

    def _reset_export_dir(self):
        self._selected_export_dir = None
        self.export_dir_label.setText(tr("Standard"))

    def _pick_cert(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Client-Zertifikat waehlen"), "", "PKCS#12 (*.p12 *.pfx);;Alle Dateien (*)")
        if path:
            self._selected_cert_file = path
            self.cert_label.setText(Path(path).name)

    def _pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Logo waehlen"), "", "PNG (*.png)")
        if path:
            self._selected_logo_file = path
            self._logo_removed = False
            self.logo_label.setText(Path(path).name)

    def _reset_logo(self):
        self._selected_logo_file = None
        self._logo_removed = True
        self.logo_label.setText(tr("kein Logo"))

    def _refresh_method_fields(self):
        is_custom_field = self.custom_field_radio.isChecked()
        self.regex_entry.setVisible(not is_custom_field)
        self.custom_field_label.setVisible(is_custom_field)
        self.custom_field_combo.setVisible(is_custom_field)
        if is_custom_field:
            self._load_custom_fields()

    def _build_temp_client(self):
        url = self.url_entry.text().strip()
        token = self.token_entry.text().strip()
        if not url or not token:
            return None
        cert_path = None
        if self._selected_cert_file:
            cert_path = self._selected_cert_file
        elif self._cert_path_display and self._cert_path_display != tr("kein Zertifikat"):
            candidate = self.state_ref.base_dir / self._cert_path_display
            if candidate.exists():
                cert_path = str(candidate)
        try:
            return PaperlessClient(url, token, client_cert_path=cert_path, client_cert_password=self.cert_password_entry.text() or None)
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
            current = self.custom_field_combo.currentText()
            self.custom_field_combo.clear()
            self.custom_field_combo.addItems([f["name"] for f in fields])
            if current:
                self.custom_field_combo.setCurrentText(current)
        except Exception as exc:
            self.status_label.setText(tr("Custom Fields nicht ladbar: {exc}", exc=exc))

    def _create_backup(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Backup speichern"), backup_filename(), "ZIP-Archiv (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            Path(path).write_bytes(create_backup(self.state_ref.base_dir))
        except Exception as exc:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Backup fehlgeschlagen: {exc}", exc=exc))
            return
        self.status_label.setStyleSheet(f"color: {COLORS['green']}; border: none;")
        self.status_label.setText(tr("Backup gespeichert: {path}", path=path))

    def _restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Backup-ZIP waehlen"), "", "ZIP-Archiv (*.zip)")
        if not path:
            return
        confirm = QMessageBox.question(
            self,
            tr("Backup wiederherstellen"),
            tr(
                "Ueberschreibt Einstellungen, Zugangsdaten und den aktuellen Arbeitsstand unwiderruflich.\n\n"
                "Die App wird danach beendet und muss manuell neu gestartet werden, damit der wiederhergestellte "
                "Stand geladen wird. Fortfahren?"
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            restored = restore_backup(self.state_ref.base_dir, Path(path).read_bytes())
        except Exception as exc:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Wiederherstellung fehlgeschlagen: {exc}", exc=exc))
            return
        if not restored:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(
                tr("Das ZIP enthaelt keine bekannten Backup-Dateien (config.json / .env / session_state.json).")
            )
            return
        QMessageBox.information(
            self,
            tr("Backup wiederhergestellt"),
            tr(
                "Wiederhergestellt: {files}.\n\nDie App wird jetzt beendet - bitte manuell neu starten.",
                files=", ".join(restored),
            ),
        )
        QApplication.instance().quit()

    def _test_connection(self):
        client = self._build_temp_client()
        if client and client.test_connection():
            self.status_label.setStyleSheet(f"color: {COLORS['green']}; border: none;")
            self.status_label.setText(tr("Verbindung erfolgreich."))
        else:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Verbindung fehlgeschlagen - URL/Token pruefen."))

    def _save(self):
        state = self.state_ref
        url = self.url_entry.text().strip()
        token = self.token_entry.text().strip()
        if not url or not token or token == PLACEHOLDER_TOKEN:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("URL und ein echter Token sind Pflicht."))
            return

        env_updates = {
            "PAPERLESS_URL": url.rstrip("/"),
            "PAPERLESS_TOKEN": token,
            "COMPANY_NAME": self.company_entry.text().strip(),
        }
        if self._selected_cert_file:
            cert_dest = state.base_dir / "paperless_client_cert.p12"
            cert_dest.write_bytes(Path(self._selected_cert_file).read_bytes())
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = cert_dest.name
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = self.cert_password_entry.text()
        elif self._cert_path_display and self._cert_path_display != tr("kein Zertifikat"):
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = self._cert_path_display
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = self.cert_password_entry.text()
        state.save_env(env_updates)

        detection = dict(state.config.get("amount_detection", {}))
        detection["method"] = "custom_field" if self.custom_field_radio.isChecked() else "filename_regex"
        if detection["method"] == "filename_regex":
            detection["regex_pattern"] = self.regex_entry.text().strip() or r"_EUR(\d+\.\d+)"
        else:
            name = self.custom_field_combo.currentText()
            match = next((f for f in self._custom_fields_cache if f["name"] == name), None)
            if match:
                detection["custom_field_id"] = match["id"]
                detection["custom_field_name"] = match["name"]
        state.config["amount_detection"] = detection
        state.config["purpose_noise_terms"] = list(self._noise_terms)
        state.config["custom_tags"] = dict(self._custom_tags)
        state.config["export_dir"] = self._selected_export_dir
        state.config["paperless_success_tag_enabled"] = self.paperless_tag_checkbox.isChecked()
        state.config["paperless_success_tag_name"] = self.paperless_tag_name_entry.text().strip() or "Abgeglichen"

        if self._selected_logo_file:
            # Nur PNG zulassen (Transparenz, kein Kompressionsartefakt-Risiko
            # bei der kleinen Darstellung in Sidebar/Titelleiste/Taskleiste) -
            # der Datei-Dialog filtert bereits auf *.png, hier zusaetzlich
            # defensiv geprueft, falls doch ein anderer Dateiname eingetippt wurde.
            if Path(self._selected_logo_file).suffix.lower() != ".png":
                self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
                self.status_label.setText(tr("Nur PNG-Dateien werden als Firmenlogo unterstuetzt."))
                return
            logo_dest = state.base_dir / "company_icon.png"
            logo_dest.write_bytes(Path(self._selected_logo_file).read_bytes())
            state.config["company_icon_path"] = logo_dest.name
        elif self._logo_removed:
            state.config["company_icon_path"] = None

        new_lang = "en" if self.lang_combo.currentText() == "English" else "de"
        state.config["language"] = new_lang
        set_language(new_lang)

        if self._csv_columns and self.map_date_combo:
            col_sig = compute_csv_signature(self._csv_columns)
            counterparty_val = self.map_counterparty_combo.currentText()
            new_mapping = {
                "date_column": self.map_date_combo.currentText(),
                "amount_column": self.map_amount_combo.currentText(),
                "purpose_column": self.map_purpose_combo.currentText(),
                "counterparty_column": None if counterparty_val == _none_option() else counterparty_val,
            }
            state.config.setdefault("csv_mappings", {})[col_sig] = new_mapping
            state.pending_mapping = new_mapping

        state.save_config()
        state.reapply_counterparty_mapping()

        self.accept()
        self.on_saved()


class MappingDialog(QDialog):
    def __init__(self, parent, columns: list[str], on_confirm):
        super().__init__(parent)
        self.setWindowTitle(tr("CSV-Spalten zuordnen"))
        self.resize(420, 380)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)
        self.on_confirm = on_confirm

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        layout.addWidget(QLabel(tr("Spalte fuer Datum")))
        self.date_combo = QComboBox()
        self.date_combo.setStyleSheet(_combo_style())
        self.date_combo.addItems(columns)
        layout.addWidget(self.date_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte fuer Betrag")))
        self.amount_combo = QComboBox()
        self.amount_combo.setStyleSheet(_combo_style())
        self.amount_combo.addItems(columns)
        layout.addWidget(self.amount_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte fuer Verwendungszweck")))
        self.purpose_combo = QComboBox()
        self.purpose_combo.setStyleSheet(_combo_style())
        self.purpose_combo.addItems(columns)
        layout.addWidget(self.purpose_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte fuer Absender/Empfaenger (optional)")))
        self.counterparty_combo = QComboBox()
        self.counterparty_combo.setStyleSheet(_combo_style())
        counterparty_values = [_none_option()] + columns
        self.counterparty_combo.addItems(counterparty_values)
        guessed = self._guess_counterparty_column(columns)
        self.counterparty_combo.setCurrentText(guessed)
        layout.addWidget(self.counterparty_combo)
        layout.addSpacing(16)

        confirm_btn = QPushButton(tr("Bestaetigen"))
        confirm_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
            f"padding: 10px; font-weight: bold; }} QPushButton:hover {{ background-color: #4a76d6; }}"
        )
        confirm_btn.clicked.connect(self._confirm)
        layout.addWidget(confirm_btn)
        layout.addStretch()

    def _guess_counterparty_column(self, columns: list[str]) -> str:
        for candidate in ("Name Zahlungsbeteiligter", "Beguenstigter/Zahlungspflichtiger", "Empfaenger/Zahlungspflichtiger"):
            if candidate in columns:
                return candidate
        return _none_option()

    def _confirm(self):
        counterparty = self.counterparty_combo.currentText()
        if counterparty == _none_option():
            counterparty = None
        self.on_confirm(
            self.date_combo.currentText(), self.amount_combo.currentText(), self.purpose_combo.currentText(), counterparty
        )


class DocumentSearchDialog(QDialog):
    """Such-Modal fuer 'Aus Paperless waehlen' - live gefilterte Liste aller
    Paperless-Dokumente. Mehrfachauswahl (Strg/Umschalt-Klick) moeglich, z.B.
    fuer eine Sammelabbuchung mit mehreren Einzelrechnungen (Amazon o.ae.) -
    bereits mit dieser Buchung verknuepfte Dokumente werden ausgegraut und
    lassen sich nicht erneut auswaehlen. Wird ein Custom Field fuer die
    Betragserkennung genutzt und ist der Betrag dort noch leer, erscheint
    bei genau EINER neuen Auswahl zusaetzlich ein Eingabefeld, um den Wert
    nachzutragen (bei Mehrfachauswahl nicht sinnvoll, da kein einzelner
    Wert eindeutig waere)."""

    def __init__(
        self, parent, docs: list[dict], already_linked_ids: set[int], show_value_entry: bool, default_value: str,
        on_confirm, on_preview=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Beleg aus Paperless waehlen"))
        self.resize(640, 580)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)

        self.docs = docs
        self.already_linked_ids = already_linked_ids
        self.show_value_entry = show_value_entry
        self.default_value = default_value
        self.on_confirm = on_confirm
        self.on_preview = on_preview

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        hint = QLabel(tr("Mehrfachauswahl mit Strg/Umschalt-Klick moeglich - z.B. bei einer Sammelabbuchung mit mehreren Einzelrechnungen."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt;")
        layout.addWidget(hint)

        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText(tr("Suchen (Titel, Absender, Dateiname)..."))
        self.search_entry.setStyleSheet(_entry_style())
        self.search_entry.textChanged.connect(self._filter)
        layout.addWidget(self.search_entry)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; border: none; padding: 4px; }}"
            f"QListWidget::item {{ padding: 6px; }}"
            f"QListWidget::item:selected {{ background-color: {COLORS['blue_dim']}; color: {COLORS['blue']}; border-radius: 6px; }}"
        )
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, stretch=1)
        self._populate(docs)

        self.value_label = QLabel(tr("Wert fuer das Custom Field:"))
        self.value_entry = QLineEdit()
        self.value_entry.setStyleSheet(_entry_style())
        self.value_label.hide()
        self.value_entry.hide()
        layout.addWidget(self.value_label)
        layout.addWidget(self.value_entry)

        btn_row = QHBoxLayout()
        if self.on_preview is not None:
            self.preview_btn = QPushButton(f"👁 {tr('Vorschau')}")
            self.preview_btn.setEnabled(False)
            self.preview_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {COLORS['blue']}; padding: 8px 16px; }}"
                f"QPushButton:disabled {{ color: {COLORS['text_muted']}; }}"
            )
            self.preview_btn.clicked.connect(self._preview)
            btn_row.addWidget(self.preview_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("Abbrechen"))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; padding: 8px 16px; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.confirm_btn = QPushButton(tr("Verknuepfen"))
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
            f"padding: 8px 18px; font-weight: bold; }} QPushButton:hover {{ background-color: #4a76d6; }}"
            f"QPushButton:disabled {{ background-color: {COLORS['bg_card_hover']}; color: {COLORS['text_muted']}; }}"
        )
        self.confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(self.confirm_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _label_for(doc: dict) -> str:
        label = f"#{doc['id']} - {doc.get('title') or doc.get('original_file_name') or tr('ohne Titel')}"
        if doc.get("correspondent_name"):
            label += f" · {doc['correspondent_name']}"
        label += f" · {doc['date'].strftime('%d.%m.%Y') if doc.get('date') else tr('kein Datum')}"
        return label

    def _populate(self, docs: list[dict]):
        self.list_widget.clear()
        for doc in docs:
            label = self._label_for(doc)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, doc)
            if doc["id"] in self.already_linked_ids:
                item.setText(label + tr("  (bereits verknuepft)"))
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            self.list_widget.addItem(item)

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _selected_docs(self) -> list[dict]:
        return [item.data(Qt.UserRole) for item in self.list_widget.selectedItems()]

    def _on_selection_changed(self):
        selected = self._selected_docs()
        self.confirm_btn.setEnabled(bool(selected))
        if self.on_preview is not None:
            self.preview_btn.setEnabled(len(selected) == 1)
        show_entry = self.show_value_entry and len(selected) == 1 and selected[0].get("amount") is None
        self.value_label.setVisible(show_entry)
        self.value_entry.setVisible(show_entry)
        if show_entry and not self.value_entry.text():
            self.value_entry.setText(self.default_value)

    def _preview(self):
        selected = self._selected_docs()
        if len(selected) == 1 and self.on_preview is not None:
            self.on_preview(selected[0])

    def _confirm(self):
        selected = self._selected_docs()
        if not selected:
            return
        value = self.value_entry.text().strip() if self.value_entry.isVisible() and self.value_entry.text().strip() else None
        self.on_confirm([doc["id"] for doc in selected], value)
        self.accept()
