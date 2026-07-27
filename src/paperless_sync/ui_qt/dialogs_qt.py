"""Qt-Dialoge fuer die PySide6-UI (desktop_app_qt.py) - Pendant zu
dialogs.py (CustomTkinter-Version). Wird schrittweise ausgebaut."""
from __future__ import annotations

import webbrowser
from datetime import datetime, date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QThread, QObject, Signal, QUrl
from PySide6.QtGui import QIcon, QDesktopServices
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
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QStackedWidget,
)

from paperless_sync.core.backup import create_backup, restore_backup, backup_filename, WrongBackupPasswordError
from paperless_sync.core.secrets_manager import SecretsLockedError
from paperless_sync.core.config_manager import (
    get_resource_dir,
    csv_signature as compute_csv_signature,
    PLACEHOLDER_TOKEN,
    get_effective_enable_banking_key_path,
)
from paperless_sync.core.paperless_client import PaperlessClient
from paperless_sync.core.csv_utils import parse_date
from paperless_sync.core.enable_banking_client import (
    DEFAULT_REDIRECT_URL,
    EnableBankingClient,
    EnableBankingError,
    authorize as enable_banking_authorize,
    transactions_to_dataframe,
)
from .theme_qt import COLORS, font as qfont, NoScrollComboBox
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


class SearchableListDialog(QDialog):
    """Durchsuchbare Auswahlliste - fuer sehr lange Listen (z.B. hunderte
    Banken pro Land beim Enable-Banking-Import), bei denen
    QInputDialog.getItem() unhandlich waere. Tippen filtert die Liste live;
    generisch gehalten, auch fuer andere lange Auswahllisten wiederverwendbar.
    Urspruenglich in desktop_app_qt.py, hierher verschoben, damit auch der
    Enable-Banking-Einrichtungsassistent (EnableBankingSetupWizard, dieselbe
    Datei) sie nutzen kann, ohne einen Ruecksprung-Import von dort zu
    brauchen."""

    def __init__(self, parent, title: str, items: list[str], preselect: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self._all_items = items
        self._selected: str | None = None

        layout = QVBoxLayout(self)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(tr("Suchen..."))
        self._filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter_edit)

        self._list_widget = QListWidget()
        self._list_widget.itemDoubleClicked.connect(lambda _item: self._accept_current())
        layout.addWidget(self._list_widget, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("Abbrechen"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton(tr("Auswählen"))
        ok_btn.clicked.connect(self._accept_current)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._populate(items)
        if preselect:
            matches = self._list_widget.findItems(preselect, Qt.MatchExactly)
            if matches:
                self._list_widget.setCurrentItem(matches[0])
                self._list_widget.scrollToItem(matches[0])

    def _populate(self, items: list[str]):
        self._list_widget.clear()
        self._list_widget.addItems(items)
        if self._list_widget.count() > 0 and self._list_widget.currentRow() < 0:
            self._list_widget.setCurrentRow(0)

    def _apply_filter(self, text: str):
        text_lower = text.lower()
        self._populate([i for i in self._all_items if text_lower in i.lower()])

    def _accept_current(self):
        item = self._list_widget.currentItem()
        if item is not None:
            self._selected = item.text()
            self.accept()

    def selected_item(self) -> str | None:
        return self._selected


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
        cert_btn = QPushButton(tr("Datei wählen"))
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
        export_pick_btn = QPushButton(tr("Ordner wählen"))
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
        self.custom_field_combo = NoScrollComboBox()
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
                    "Gilt für das aktuell geladene CSV-Format. Absender/Empfänger wirkt sofort auf bereits "
                    "geladene Buchungen, Datum/Betrag/Verwendungszweck erst beim nächsten Import dieser Datei."
                ),
            )
            col_sig = compute_csv_signature(self._csv_columns)
            current_mapping = state.config.get("csv_mappings", {}).get(col_sig) or state.pending_mapping or {}

            self.map_date_combo = self._labeled_combo(csv_card, tr("Spalte für Datum"), self._csv_columns, current_mapping.get("date_column"))
            self.map_amount_combo = self._labeled_combo(csv_card, tr("Spalte für Betrag"), self._csv_columns, current_mapping.get("amount_column"))
            self.map_purpose_combo = self._labeled_combo(csv_card, tr("Spalte für Verwendungszweck"), self._csv_columns, current_mapping.get("purpose_column"))
            counterparty_values = [_none_option()] + self._csv_columns
            current_counterparty = current_mapping.get("counterparty_column")
            self.map_counterparty_combo = self._labeled_combo(
                csv_card, tr("Spalte für Absender/Empfänger (optional)"), counterparty_values,
                current_counterparty if current_counterparty in self._csv_columns else _none_option(),
            )

        noise_card = self._section(
            tr("Verwendungszweck: Rauschbegriffe ausblenden"),
            tr("Nur in der Kartenanzeige entfernt (Export/Zuordnung unverändert). IBAN/BIC werden immer automatisch entfernt."),
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
            tr("Löscht nur die Tag-Definition aus der Schnellauswahl/Sonstiges-Liste. Bereits getaggte Buchungen behalten ihren Tag."),
        )
        self._custom_tags = dict(state.config.get("custom_tags", {}))
        self.tags_list_layout = QVBoxLayout()
        tags_card.layout().addLayout(self.tags_list_layout)
        self._render_custom_tags()

        paperless_tag_card = self._section(
            tr("Paperless-Erfolgs-Tag"),
            tr(
                "Setzt in Paperless selbst einen Tag auf Dokumente, die erfolgreich einer Buchung zugeordnet "
                "wurden (automatischer Match, manuelle Verknüpfung, aufgelöster Mehrfach-Match). Gilt nicht "
                "für frisch hochgeladene PDFs (Paperless verarbeitet die erst asynchron)."
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
            tr("Eigenes Logo statt der Büroklammer oben links in der Seitenleiste. Nur PNG, quadratisch empfohlen."),
        )
        self._selected_logo_file = None
        self._logo_removed = False
        self._logo_path_display = state.config.get("company_icon_path") or tr("kein Logo")
        logo_row = QHBoxLayout()
        self.logo_label = QLabel(self._logo_path_display)
        self.logo_label.setStyleSheet("border: none;")
        logo_row.addWidget(self.logo_label, stretch=1)
        logo_reset_btn = QPushButton(tr("Zurücksetzen"))
        logo_reset_btn.setStyleSheet(self._outline_button_style())
        logo_reset_btn.clicked.connect(self._reset_logo)
        logo_row.addWidget(logo_reset_btn)
        logo_pick_btn = QPushButton(tr("Logo wählen"))
        logo_pick_btn.setStyleSheet(self._button_style())
        logo_pick_btn.clicked.connect(self._pick_logo)
        logo_row.addWidget(logo_pick_btn)
        logo_card.layout().addLayout(logo_row)

        lang_card = self._section(
            tr("Sprache"),
            tr("Sprache der Oberfläche. Wirkt erst nach einem Neustart der App."),
        )
        lang_row = QHBoxLayout()
        self.lang_combo = NoScrollComboBox()
        self.lang_combo.setStyleSheet(_combo_style())
        self.lang_combo.addItems(["Deutsch", "English"])
        self.lang_combo.setCurrentText("English" if get_language() == "en" else "Deutsch")
        lang_row.addWidget(self.lang_combo, stretch=1)
        lang_card.layout().addLayout(lang_row)

        bank_card = self._section(
            tr("Bank-Import (Enable Banking)"),
            tr(
                "Direkter Import von Kontobewegungen ueber deine eigene Enable-Banking-Anwendung, als "
                "Alternative zum manuellen CSV-Export."
            ),
        )
        self.bank_app_id_label = QLabel("")
        self.bank_app_id_label.setStyleSheet("border: none;")
        bank_card.layout().addWidget(self.bank_app_id_label)
        self.bank_key_label = QLabel("")
        self.bank_key_label.setWordWrap(True)
        self.bank_key_label.setStyleSheet("border: none;")
        bank_card.layout().addWidget(self.bank_key_label)
        self.bank_last_import_label = QLabel("")
        self.bank_last_import_label.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
        bank_card.layout().addWidget(self.bank_last_import_label)

        bank_btn_row = QHBoxLayout()
        wizard_btn = QPushButton(tr("Einrichtungsassistent starten"))
        wizard_btn.setStyleSheet(self._button_style())
        wizard_btn.clicked.connect(self._open_enable_banking_wizard)
        bank_btn_row.addWidget(wizard_btn)
        self.bank_reset_btn = QPushButton(tr("Verbindung zurücksetzen"))
        self.bank_reset_btn.setStyleSheet(self._outline_button_style())
        self.bank_reset_btn.clicked.connect(self._reset_enable_banking)
        bank_btn_row.addWidget(self.bank_reset_btn)
        bank_card.layout().addLayout(bank_btn_row)

        bank_individual_row = QHBoxLayout()
        change_app_id_btn = QPushButton(tr("Application-ID ändern"))
        change_app_id_btn.setStyleSheet(self._outline_button_style())
        change_app_id_btn.clicked.connect(self._change_enable_banking_app_id)
        bank_individual_row.addWidget(change_app_id_btn)
        change_key_btn = QPushButton(tr("Schlüssel-Pfad ändern"))
        change_key_btn.setStyleSheet(self._outline_button_style())
        change_key_btn.clicked.connect(self._change_enable_banking_key_path)
        bank_individual_row.addWidget(change_key_btn)
        bank_card.layout().addLayout(bank_individual_row)
        self._refresh_bank_status()

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
        combo = NoScrollComboBox()
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
        path = QFileDialog.getExistingDirectory(self, tr("Exportordner wählen"))
        if path:
            self._selected_export_dir = path
            self.export_dir_label.setText(path)

    def _reset_export_dir(self):
        self._selected_export_dir = None
        self.export_dir_label.setText(tr("Standard"))

    def _pick_cert(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Client-Zertifikat wählen"), "", "PKCS#12 (*.p12 *.pfx);;Alle Dateien (*)")
        if path:
            self._selected_cert_file = path
            self.cert_label.setText(Path(path).name)

    def _pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Logo wählen"), "", "PNG (*.png)")
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

    def _prompt_backup_password(self, title: str, label: str) -> str | None:
        """None = Nutzer hat abgebrochen. Leerer String = bewusst kein
        Passwort gesetzt (muss vom Aufrufer ggf. gesondert bestaetigt
        werden, siehe _create_backup)."""
        text, ok = QInputDialog.getText(self, title, label, QLineEdit.Password)
        if not ok:
            return None
        return text

    def _create_backup(self):
        password = self._prompt_backup_password(
            tr("Backup-Passwort"),
            tr("Passwort für dieses Backup (leer lassen für kein Passwort):"),
        )
        if password is None:
            return
        if not password:
            confirm = QMessageBox.question(
                self,
                tr("Kein Passwort gesetzt"),
                tr(
                    "Ohne Passwort ist das Backup NICHT verschlüsselt - jeder mit Zugriff auf die Datei kann "
                    "deine Paperless-Zugangsdaten (Token, ggf. Zertifikat-Passwort) direkt auslesen. "
                    "Wirklich ohne Passwort fortfahren?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        path, _ = QFileDialog.getSaveFileName(self, tr("Backup speichern"), backup_filename(), "ZIP-Archiv (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            Path(path).write_bytes(create_backup(self.state_ref.base_dir, password=password or None))
        except SecretsLockedError:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Zugangsdaten sind gerade gesperrt (Passphrase nötig) - Backup nicht möglich."))
            return
        except Exception as exc:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Backup fehlgeschlagen: {exc}", exc=exc))
            return
        self.status_label.setStyleSheet(f"color: {COLORS['green']}; border: none;")
        self.status_label.setText(tr("Backup gespeichert: {path}", path=path))

    def _restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Backup-ZIP wählen"), "", "ZIP-Archiv (*.zip)")
        if not path:
            return
        confirm = QMessageBox.question(
            self,
            tr("Backup wiederherstellen"),
            tr(
                "Überschreibt Einstellungen, Zugangsdaten und den aktuellen Arbeitsstand unwiderruflich.\n\n"
                "Die App wird danach beendet und muss manuell neu gestartet werden, damit der wiederhergestellte "
                "Stand geladen wird. Fortfahren?"
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        password = self._prompt_backup_password(
            tr("Backup entschlüsseln"),
            tr("Passwort dieses Backups (leer lassen, falls keins gesetzt wurde):"),
        )
        if password is None:
            return
        try:
            restored = restore_backup(self.state_ref.base_dir, Path(path).read_bytes(), password=password or None)
        except WrongBackupPasswordError:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Falsches Passwort (oder das Backup ist verschlüsselt)."))
            return
        except SecretsLockedError:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Zugangsdaten sind gerade gesperrt (Passphrase nötig) - Wiederherstellung nicht möglich."))
            return
        except Exception as exc:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(tr("Wiederherstellung fehlgeschlagen: {exc}", exc=exc))
            return
        if not restored:
            self.status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.status_label.setText(
                tr("Das ZIP enthält keine bekannten Backup-Dateien (config.json / .env / session_state.json).")
            )
            return
        # WICHTIG: DesktopAppQt.closeEvent() ruft beim Beenden unbedingt
        # app_state.persist_session() auf (fuer den normalen Fall: nichts
        # beim Schliessen verlieren) - das wuerde hier die gerade erst von
        # restore_backup() geschriebene session_state.json sofort wieder
        # mit dem noch im Speicher stehenden ALTEN Stand ueberschreiben.
        # persist_session() selbst bricht fruehzeitig ab, wenn transactions
        # leer ist - genau das hier bewusst erzwingen, damit der Restore
        # tatsaechlich bestehen bleibt (echter Bug, der genau so aufgetreten
        # ist, siehe Chat).
        self.state_ref.transactions = []
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
            self.status_label.setText(tr("Verbindung fehlgeschlagen - URL/Token prüfen."))

    # --- Bank-Import (Enable Banking) --------------------------------------
    def _refresh_bank_status(self):
        eb_config = self.state_ref.config.get("enable_banking") or {}
        app_id_configured = bool(eb_config.get("application_id"))
        key_path = get_effective_enable_banking_key_path(self.state_ref.config)
        key_found = key_path.exists()

        self.bank_app_id_label.setText(f"{'✓' if app_id_configured else '✗'} {tr('Application-ID hinterlegt')}")
        self.bank_app_id_label.setStyleSheet(
            f"color: {COLORS['green'] if app_id_configured else COLORS['text_muted']}; border: none;"
        )
        self.bank_key_label.setText(f"{'✓' if key_found else '✗'} {tr('Schlüssel gefunden')}: {key_path}")
        self.bank_key_label.setStyleSheet(
            f"color: {COLORS['green'] if key_found else COLORS['text_muted']}; border: none;"
        )

        last_import = eb_config.get("last_import_at")
        if last_import:
            try:
                last_import_text = datetime.fromisoformat(last_import).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                last_import_text = last_import
        else:
            last_import_text = tr("noch nie")
        self.bank_last_import_label.setText(f"{tr('Letzter erfolgreicher Import')}: {last_import_text}")

        self.bank_reset_btn.setEnabled(app_id_configured or key_found or bool(eb_config.get("key_path")))

    def _open_enable_banking_wizard(self):
        wizard = EnableBankingSetupWizard(self, self.state_ref)
        wizard.exec()
        self._refresh_bank_status()

    def _reset_enable_banking(self):
        """Loescht application_id/redirect_url/key_path aus der Config nach
        Sicherheitsabfrage - die .pem-Datei selbst wird NICHT automatisch
        geloescht, das macht der Nutzer manuell (siehe CLAUDE.md: vor dem
        Loeschen von Dateien immer nachfragen - hier loeschen wir nur einen
        Config-Verweis, keine Datei)."""
        confirm = QMessageBox.question(
            self,
            tr("Verbindung zurücksetzen"),
            tr(
                "Löscht Application-ID, Redirect-URL und Schlüssel-Pfad aus den Einstellungen. Die "
                ".pem-Datei selbst wird NICHT automatisch gelöscht. Wirklich zurücksetzen?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self.state_ref.config["enable_banking"] = {
            "application_id": None,
            "redirect_url": DEFAULT_REDIRECT_URL,
            "key_path": None,
            "last_import_at": None,
        }
        self.state_ref.save_config()
        self._refresh_bank_status()

    def _change_enable_banking_app_id(self):
        eb_config = self.state_ref.config.setdefault("enable_banking", {})
        current = eb_config.get("application_id") or ""
        new_value, ok = QInputDialog.getText(self, tr("Application-ID ändern"), tr("Neue Application-ID:"), text=current)
        if not ok:
            return
        eb_config["application_id"] = new_value.strip() or None
        self.state_ref.save_config()
        self._refresh_bank_status()

    def _change_enable_banking_key_path(self):
        file_path, _ = QFileDialog.getOpenFileName(self, tr("Schlüssel-Datei wählen"), "", "PEM (*.pem)")
        if not file_path:
            return
        eb_config = self.state_ref.config.setdefault("enable_banking", {})
        eb_config["key_path"] = file_path
        self.state_ref.save_config()
        self._refresh_bank_status()

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
                self.status_label.setText(tr("Nur PNG-Dateien werden als Firmenlogo unterstützt."))
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


class _EnableBankingAuthWorker(QObject):
    """Fuehrt den kompletten (blockierenden) Autorisierungs-Flow -
    enable_banking_client.authorize() + client.create_session() - in einem
    eigenen Thread aus, sonst friert die UI fuer die Dauer des Bank-Logins
    im Browser ein (bis zu 5 Minuten, siehe enable_banking_client.py)."""

    finished = Signal(object, object)  # (session_dict, error_message) - genau eines von beiden ist None

    def __init__(self, client: EnableBankingClient, aspsp_name: str, aspsp_country: str, redirect_url: str):
        super().__init__()
        self.client = client
        self.aspsp_name = aspsp_name
        self.aspsp_country = aspsp_country
        self.redirect_url = redirect_url

    def run(self):
        try:
            code = enable_banking_authorize(self.client, self.aspsp_name, self.aspsp_country, self.redirect_url)
            session = self.client.create_session(code)
        except EnableBankingError as exc:
            self.finished.emit(None, str(exc))
        except Exception as exc:  # unerwarteter Fehler (z.B. Netzwerk) - trotzdem verstaendlich melden statt abzustuerzen
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(session, None)


class EnableBankingSetupWizard(QDialog):
    """Mehrseitiger Einrichtungsassistent fuer den Enable-Banking-Bank-
    Import (siehe enable_banking_client.py) - jederzeit ueber 'Abbrechen'
    verlassbar. Bereits gespeicherte Zwischenschritte gehen dabei nicht
    verloren: Application-ID (Seite 4) und ein erfolgreicher Verbindungs-
    test (Seite 6) schreiben JEWEILS SOFORT in config_manager, statt erst
    am Ende gesammelt zu speichern wie bei SettingsDialog._save()."""

    PAGE_INTRO, PAGE_REGISTER, PAGE_KEY, PAGE_APP_ID, PAGE_LINK_ACCOUNT, PAGE_TEST = range(6)
    PAGE_COUNT = 6

    def __init__(self, parent, state):
        super().__init__(parent)
        self.setWindowTitle(tr("Bank-Import einrichten"))
        self.resize(560, 640)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)
        self.state_ref = state
        self._test_client = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(12)

        self.step_label = QLabel("")
        self.step_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt;")
        outer.addWidget(self.step_label)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, stretch=1)

        self.stack.addWidget(self._build_page_intro())
        self.stack.addWidget(self._build_page_register())
        self.stack.addWidget(self._build_page_key())
        self.stack.addWidget(self._build_page_app_id())
        self.stack.addWidget(self._build_page_link_account())
        self.stack.addWidget(self._build_page_test())

        nav_row = QHBoxLayout()
        self.back_btn = QPushButton(tr("Zurück"))
        self.back_btn.setStyleSheet(self._secondary_button_style())
        self.back_btn.clicked.connect(self._go_back)
        nav_row.addWidget(self.back_btn)
        nav_row.addStretch()
        cancel_btn = QPushButton(tr("Abbrechen"))
        cancel_btn.setStyleSheet(self._secondary_button_style())
        cancel_btn.clicked.connect(self.reject)
        nav_row.addWidget(cancel_btn)
        self.next_btn = QPushButton(tr("Weiter"))
        self.next_btn.setStyleSheet(self._primary_button_style())
        self.next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self.next_btn)
        outer.addLayout(nav_row)

        self.stack.currentChanged.connect(self._on_page_changed)
        self._on_page_changed(0)

    # --- Style-Helfer (eigene Klasse, nicht von SettingsDialog abgeleitet) ---
    def _primary_button_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
            f"padding: 10px 18px; font-weight: bold; }} QPushButton:hover {{ background-color: #4a76d6; }}"
        )

    def _secondary_button_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: 1px solid "
            f"{COLORS['text_muted']}; border-radius: 10px; padding: 10px 18px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )

    # --- Navigation -------------------------------------------------------
    def _on_page_changed(self, index: int):
        self.step_label.setText(tr("Schritt {current} von {total}", current=index + 1, total=self.PAGE_COUNT))
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setText(tr("Fertig") if index == self.PAGE_COUNT - 1 else tr("Weiter"))
        if index == self.PAGE_KEY:
            self._refresh_key_check()

    def _go_back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))

    def _go_next(self):
        index = self.stack.currentIndex()
        if index == self.PAGE_KEY and not get_effective_enable_banking_key_path(self.state_ref.config).exists():
            QMessageBox.warning(
                self, tr("Kein Schlüssel gefunden"),
                tr("Bitte zuerst die .pem-Datei in den angezeigten Ordner legen und 'Prüfen' klicken."),
            )
            return
        if index == self.PAGE_APP_ID:
            app_id = self.app_id_entry.text().strip()
            if not app_id:
                QMessageBox.warning(self, tr("Application-ID fehlt"), tr("Bitte eine Application-ID eintragen."))
                return
            self.state_ref.config.setdefault("enable_banking", {})["application_id"] = app_id
            self.state_ref.save_config()
        if index == self.PAGE_COUNT - 1:
            self.accept()
            return
        self.stack.setCurrentIndex(index + 1)

    # --- Seite 1: Einfuehrung ----------------------------------------------
    def _build_page_intro(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel(tr("Bank-Import über Enable Banking einrichten"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)
        text = QLabel(
            tr(
                "Enable Banking ist eine Open-Banking-Schnittstelle, über die Kontobewegungen direkt von "
                "deiner Bank abgerufen werden können - als Alternative zum manuellen CSV-Export. Du "
                "registrierst dafür eine eigene, kostenlose Anwendung mit eigenem Zugang. Die Einrichtung "
                "dauert ca. 5 Minuten und ist einmalig."
            )
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        open_btn = QPushButton(tr("enablebanking.com/sign-in öffnen"))
        open_btn.setStyleSheet(self._primary_button_style())
        open_btn.clicked.connect(lambda: webbrowser.open("https://enablebanking.com/sign-in"))
        layout.addWidget(open_btn)
        layout.addStretch()
        return page

    # --- Seite 2: Anwendung registrieren ------------------------------------
    def _build_page_register(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel(tr("Anwendung registrieren"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)
        text = QLabel(
            tr("Im Enable-Banking-Control-Panel unter 'Applications' auf 'Add a new application' klicken und folgende Werte eintragen:")
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        layout.addWidget(self._copyable_row(tr("Environment"), "Production"))
        redirect_url = (self.state_ref.config.get("enable_banking") or {}).get("redirect_url") or DEFAULT_REDIRECT_URL
        layout.addWidget(self._copyable_row(tr("Redirect URL"), redirect_url))
        layout.addWidget(self._copyable_row(tr("Application Name"), tr("frei wählbar, z.B. 'Paperless Sync'"), copyable=False))

        hint = QFrame()
        hint.setStyleSheet(f"background-color: {COLORS['blue_dim']}; border-radius: 10px;")
        hint_layout = QVBoxLayout(hint)
        hint_lbl = QLabel(
            tr(
                "💡 Beim Anlegen wird automatisch ein privater Schlüssel als .pem-Datei heruntergeladen - "
                "Download-Fenster offen lassen, wird im nächsten Schritt gebraucht."
            )
        )
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet(f"color: {COLORS['blue']}; border: none;")
        hint_layout.addWidget(hint_lbl)
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def _copyable_row(self, label: str, value: str, copyable: bool = True) -> QFrame:
        row_frame = QFrame()
        row_frame.setStyleSheet(f"background-color: {COLORS['bg_kpi']}; border-radius: 10px;")
        row = QHBoxLayout(row_frame)
        row.setContentsMargins(12, 8, 12, 8)
        text_col = QVBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8pt; border: none;")
        text_col.addWidget(lbl)
        value_lbl = QLabel(value)
        value_lbl.setWordWrap(True)
        value_lbl.setStyleSheet("border: none;")
        text_col.addWidget(value_lbl)
        row.addLayout(text_col, stretch=1)
        if copyable:
            copy_btn = QPushButton(tr("Kopieren"))
            copy_btn.setStyleSheet(self._secondary_button_style())
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(value))
            row.addWidget(copy_btn)
        return row_frame

    # --- Seite 3: Schluessel ablegen -----------------------------------------
    def _build_page_key(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel(tr("Schlüssel ablegen"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)

        key_path = get_effective_enable_banking_key_path(self.state_ref.config)
        path_lbl = QLabel(str(key_path))
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet(f"background-color: {COLORS['bg_input']}; border-radius: 8px; padding: 8px;")
        layout.addWidget(path_lbl)

        text = QLabel(tr("Verschiebe die heruntergeladene .pem-Datei in diesen Ordner."))
        text.setWordWrap(True)
        layout.addWidget(text)

        btn_row = QHBoxLayout()
        open_folder_btn = QPushButton(tr("Ordner öffnen"))
        open_folder_btn.setStyleSheet(self._secondary_button_style())
        open_folder_btn.clicked.connect(lambda p=key_path: self._open_key_folder(p))
        btn_row.addWidget(open_folder_btn)
        check_btn = QPushButton(tr("Prüfen"))
        check_btn.setStyleSheet(self._primary_button_style())
        check_btn.clicked.connect(self._refresh_key_check)
        btn_row.addWidget(check_btn)
        layout.addLayout(btn_row)

        self.key_status_label = QLabel("")
        self.key_status_label.setWordWrap(True)
        layout.addWidget(self.key_status_label)
        layout.addStretch()
        return page

    def _open_key_folder(self, key_path: Path):
        key_path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(key_path.parent)))

    def _refresh_key_check(self):
        key_path = get_effective_enable_banking_key_path(self.state_ref.config)
        if key_path.exists():
            self.key_status_label.setStyleSheet(f"color: {COLORS['green']}; border: none;")
            self.key_status_label.setText(f"✓ {tr('Schlüssel gefunden.')}")
        else:
            self.key_status_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
            self.key_status_label.setText(f"✗ {tr('Noch keine .pem-Datei gefunden.')}")

    # --- Seite 4: Application-ID --------------------------------------------
    def _build_page_app_id(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel(tr("Application-ID eintragen"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)
        text = QLabel(tr("Zu finden im Enable-Banking-Control-Panel unter deiner Anwendung ('Application ID')."))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.app_id_entry = QLineEdit((self.state_ref.config.get("enable_banking") or {}).get("application_id") or "")
        self.app_id_entry.setStyleSheet(_entry_style())
        layout.addWidget(self.app_id_entry)
        layout.addStretch()
        return page

    # --- Seite 5: Eigenes Konto verknuepfen ----------------------------------
    def _build_page_link_account(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel(tr("Eigenes Konto verknüpfen"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)
        text = QLabel(
            tr(
                "Im Enable-Banking-Control-Panel bei deiner Anwendung das eigene Konto whitelisten (im "
                "'restricted production'-Modus ist dafür kein separater Vertrag nötig, solange die "
                "Anwendung nur von dir selbst genutzt wird)."
            )
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        open_btn = QPushButton(tr("Enable Banking Control Panel öffnen"))
        open_btn.setStyleSheet(self._primary_button_style())
        open_btn.clicked.connect(lambda: webbrowser.open("https://enablebanking.com/sign-in"))
        layout.addWidget(open_btn)
        layout.addStretch()
        return page

    # --- Seite 6: Test -----------------------------------------------------
    def _build_page_test(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        title = QLabel(tr("Verbindung testen"))
        title.setFont(qfont(13, bold=True))
        layout.addWidget(title)
        text = QLabel(
            tr("Testet den kompletten Ablauf einmal: Bank-Login im Browser, danach Abruf der letzten Kontobewegungen als Vorschau.")
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        country_row = QHBoxLayout()
        country_row.addWidget(QLabel(tr("Land:")))
        self.test_country_entry = QLineEdit()
        self.test_country_entry.setPlaceholderText(tr("z.B. AT, DE"))
        self.test_country_entry.setStyleSheet(_entry_style())
        self.test_country_entry.setMaximumWidth(100)
        country_row.addWidget(self.test_country_entry)
        country_row.addStretch()
        layout.addLayout(country_row)

        self.test_btn = QPushButton(tr("Verbindung testen"))
        self.test_btn.setStyleSheet(self._primary_button_style())
        self.test_btn.clicked.connect(self._on_test_connection)
        layout.addWidget(self.test_btn)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        layout.addWidget(self.test_result_label)
        layout.addStretch()
        return page

    def _on_test_connection(self):
        country = self.test_country_entry.text().strip().upper()
        if not country:
            QMessageBox.warning(self, tr("Land fehlt"), tr("Bitte zuerst einen Ländercode eingeben."))
            return

        eb_config = self.state_ref.config.get("enable_banking") or {}
        application_id = eb_config.get("application_id")
        key_path = get_effective_enable_banking_key_path(self.state_ref.config)
        redirect_url = eb_config.get("redirect_url") or DEFAULT_REDIRECT_URL

        try:
            client = EnableBankingClient(application_id=application_id, key_path=key_path)
            aspsps = client.get_aspsps(country)
        except EnableBankingError as exc:
            self._show_test_error(str(exc))
            return

        if not aspsps:
            self._show_test_error(tr("Für {country} wurden keine Banken gefunden.", country=country))
            return

        names = [a.get("name", "?") for a in aspsps]
        picker = SearchableListDialog(self, tr("Bank wählen"), names)
        if picker.exec() != QDialog.Accepted:
            return
        bank_name = picker.selected_item()
        if not bank_name:
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText(f"⏳ {tr('Warte auf Bank-Login...')}")
        self._test_client = client
        self._test_thread = QThread()
        self._test_worker = _EnableBankingAuthWorker(client, bank_name, country, redirect_url)
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.finished.connect(self._on_test_auth_finished)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    def _on_test_auth_finished(self, session, error):
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("Verbindung testen"))
        if error:
            self._show_test_error(error)
            return

        accounts = (session or {}).get("accounts") or []
        if not accounts:
            self._show_test_error(tr("Keine autorisierten Konten in der Session gefunden."))
            return

        try:
            raw_txs = self._test_client.get_transactions(accounts[0]["uid"])
        except EnableBankingError as exc:
            self._show_test_error(str(exc))
            return

        preview_df = transactions_to_dataframe(raw_txs[:5])
        preview_lines = [
            f"{row['Datum']}  {row['Betrag']} €  {str(row['Verwendungszweck'])[:40]}"
            for row in preview_df.to_dict("records")
        ]
        preview_text = "\n".join(preview_lines) or tr("(keine Buchungen im Standardzeitraum)")

        self.test_result_label.setStyleSheet(f"color: {COLORS['green']}; border: none;")
        self.test_result_label.setText(f"✓ {tr('Verbindung erfolgreich!')}\n\n{preview_text}")

        eb_config = self.state_ref.config.setdefault("enable_banking", {})
        eb_config["last_import_at"] = datetime.now().isoformat()
        self.state_ref.save_config()

    def _show_test_error(self, message: str):
        self.test_result_label.setStyleSheet(f"color: {COLORS['red']}; border: none;")
        hint = ""
        lowered = message.lower()
        if "application" in lowered or "unauthorized" in lowered or "401" in lowered:
            hint = f"\n\n{tr('Mögliche Ursache: Application-ID falsch oder Konto noch nicht verknüpft.')}"
        self.test_result_label.setText(f"✗ {message}{hint}")


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


class EnableBankingDateRangeDialog(QDialog):
    """Zeitraum-Auswahl vor dem eigentlichen Bank-Import (siehe
    desktop_app_qt._on_bank_import_click) - vorbelegt mit dem aktuell in
    der Sidebar gewaehlten Monat (AppState.selected_month), frei
    aenderbar. Manche Banken stellen ueber die Schnittstelle nur eine
    begrenzte Historie bereit (haeufig ~90 Tage) - der Hinweistext macht
    das vorab transparent, damit ein kuerzeres Ergebnis nicht wie ein
    Fehler wirkt."""

    def __init__(self, parent, default_from: date, default_to: date):
        super().__init__(parent)
        self.setWindowTitle(tr("Zeitraum wählen"))
        self.resize(380, 300)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_main']}; }} QLabel {{ color: {COLORS['text_primary']}; }}")
        _apply_window_icon(self)
        self._confirmed_range = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel(tr("Zeitraum für den Bank-Import"))
        title.setFont(qfont(12, bold=True))
        layout.addWidget(title)

        quick_row = QHBoxLayout()
        for label, handler in (
            (tr("Aktueller Monat"), self._set_current_month),
            (tr("Letzte 30 Tage"), lambda: self._set_last_n_days(30)),
            (tr("Letzte 90 Tage"), lambda: self._set_last_n_days(90)),
        ):
            btn = QPushButton(label)
            btn.setStyleSheet(self._quick_button_style())
            btn.clicked.connect(handler)
            quick_row.addWidget(btn)
        layout.addLayout(quick_row)

        from_row = QHBoxLayout()
        from_row.addWidget(QLabel(tr("Von:")))
        self.date_from_entry = QLineEdit(default_from.strftime("%d.%m.%Y"))
        self.date_from_entry.setStyleSheet(_entry_style())
        from_row.addWidget(self.date_from_entry)
        layout.addLayout(from_row)

        to_row = QHBoxLayout()
        to_row.addWidget(QLabel(tr("Bis:")))
        self.date_to_entry = QLineEdit(default_to.strftime("%d.%m.%Y"))
        self.date_to_entry.setStyleSheet(_entry_style())
        to_row.addWidget(self.date_to_entry)
        layout.addLayout(to_row)

        hint = QLabel(tr("Manche Banken begrenzen den abrufbaren Zeitraum, unabhängig von deiner Auswahl hier."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8pt;")
        layout.addWidget(hint)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("Abbrechen"))
        cancel_btn.setStyleSheet(self._quick_button_style())
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        confirm_btn = QPushButton(tr("Weiter"))
        confirm_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
            f"padding: 8px 16px; font-weight: bold; }} QPushButton:hover {{ background-color: #4a76d6; }}"
        )
        confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _quick_button_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: 1px solid "
            f"{COLORS['text_muted']}; border-radius: 8px; padding: 6px 10px; font-size: 8pt; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )

    def _set_current_month(self):
        today = date.today()
        self.date_from_entry.setText(date(today.year, today.month, 1).strftime("%d.%m.%Y"))
        self.date_to_entry.setText(_last_day_of_month(today.year, today.month).strftime("%d.%m.%Y"))

    def _set_last_n_days(self, n: int):
        today = date.today()
        self.date_from_entry.setText((today - timedelta(days=n)).strftime("%d.%m.%Y"))
        self.date_to_entry.setText(today.strftime("%d.%m.%Y"))

    def _confirm(self):
        date_from = parse_date(self.date_from_entry.text())
        date_to = parse_date(self.date_to_entry.text())
        if not date_from or not date_to:
            QMessageBox.warning(self, tr("Ungültiges Datum"), tr("Bitte gültige Datumswerte im Format TT.MM.JJJJ eingeben."))
            return
        if date_from > date_to:
            QMessageBox.warning(self, tr("Ungültiger Zeitraum"), tr("Das Von-Datum muss vor dem Bis-Datum liegen."))
            return
        self._confirmed_range = (date_from, date_to)
        self.accept()

    def selected_range(self):
        return self._confirmed_range


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

        layout.addWidget(QLabel(tr("Spalte für Datum")))
        self.date_combo = NoScrollComboBox()
        self.date_combo.setStyleSheet(_combo_style())
        self.date_combo.addItems(columns)
        layout.addWidget(self.date_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte für Betrag")))
        self.amount_combo = NoScrollComboBox()
        self.amount_combo.setStyleSheet(_combo_style())
        self.amount_combo.addItems(columns)
        layout.addWidget(self.amount_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte für Verwendungszweck")))
        self.purpose_combo = NoScrollComboBox()
        self.purpose_combo.setStyleSheet(_combo_style())
        self.purpose_combo.addItems(columns)
        layout.addWidget(self.purpose_combo)
        layout.addSpacing(8)

        layout.addWidget(QLabel(tr("Spalte für Absender/Empfänger (optional)")))
        self.counterparty_combo = NoScrollComboBox()
        self.counterparty_combo.setStyleSheet(_combo_style())
        counterparty_values = [_none_option()] + columns
        self.counterparty_combo.addItems(counterparty_values)
        guessed = self._guess_counterparty_column(columns)
        self.counterparty_combo.setCurrentText(guessed)
        layout.addWidget(self.counterparty_combo)
        layout.addSpacing(16)

        confirm_btn = QPushButton(tr("Bestätigen"))
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
    """Such-Modal fuer 'Aus Paperless wählen' - live gefilterte Liste aller
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
        self.setWindowTitle(tr("Beleg aus Paperless wählen"))
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

        hint = QLabel(tr("Mehrfachauswahl mit Strg/Umschalt-Klick möglich - z.B. bei einer Sammelabbuchung mit mehreren Einzelrechnungen."))
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

        self.value_label = QLabel(tr("Wert für das Custom Field:"))
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
        self.confirm_btn = QPushButton(tr("Verknüpfen"))
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
                item.setText(label + tr("  (bereits verknüpft)"))
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
