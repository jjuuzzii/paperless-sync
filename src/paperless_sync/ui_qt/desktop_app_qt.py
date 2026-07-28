"""Paperless Sync - Desktop-UI, PySide6/Qt-Variante.

Parallel zur bestehenden CustomTkinter-UI (desktop_app.py) - siehe
Zusammenfassung im Chat: CTkScrollableFrame hat ein bekanntes, vom
customtkinter-Maintainer selbst als "kann ich evtl. nicht loesen"
eingestuftes Scroll-Tearing-Problem bei schnellem Scrollen
(TomSchimansky/CustomTkinter#1510). Qt hat eine robustere, doppelt
gepufferte Rendering-Pipeline - diese Datei testet/baut die UI-Schicht neu
in Qt, waehrend das bestehende desktop_app.py unveraendert als
funktionierende Alternative erhalten bleibt.

Nutzt DIESELBE framework-unabhaengige Backend-Schicht wie die CTk-Version
(desktop_state.AppState, desktop_controller.Controller, matcher.py,
exporter.py, csv_utils.py, config_manager.py, paperless_client.py,
session_store.py) - keine Aenderung dort noetig.

Start (Quellcode):  python run_app.py (im Repo-Root)
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QIcon, QFontMetrics, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QComboBox,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QTabWidget,
    QSizePolicy,
    QFileDialog,
    QMessageBox,
    QLineEdit,
    QInputDialog,
    QDialog,
    QListWidget,
    QProgressDialog,
)

# sys.path (Repo-Root + src/) wird vom Einstiegspunkt (run_app.py) VOR dem
# Import dieses Moduls gesetzt - noetig fuer "from version import ..."
# (Repo-Root) und die paperless_sync.core/state-Imports unten (src/).
from paperless_sync.state.desktop_state import AppState
from paperless_sync.state.desktop_controller import Controller, BUILTIN_TAGS, TAG_ICONS
from .dialogs_qt import (
    MappingDialog,
    SettingsDialog,
    DocumentSearchDialog,
    PdfViewerDialog,
    SearchableListDialog,
    EnableBankingSetupWizard,
    EnableBankingDateRangeDialog,
    FiscalYearExportDialog,
    _EnableBankingAuthWorker,
    _last_day_of_month,
)
from paperless_sync.core.config_manager import get_resource_dir, get_effective_enable_banking_key_path
from .theme_qt import COLORS, TAG_COLORS, TAG_COLORS_DIM, custom_tag_color, font as qfont, NoScrollComboBox
from paperless_sync.core.i18n import tr, set_language
from paperless_sync.core.tx_status import TxStatus, DONE_STATUSES
from paperless_sync.core.exporter import (
    count_open_items,
    current_fiscal_year_start,
    fiscal_year_label,
    fiscal_year_open_items_summary,
    get_fiscal_year_months,
    zip_export_folder,
)
from paperless_sync.core.csv_utils import parse_date
from paperless_sync.core.enable_banking_client import (
    EnableBankingClient,
    EnableBankingError,
    transactions_to_dataframe,
    ENABLE_BANKING_MAPPING,
)
from version import __version__

# Gleiche Bereinigung wie in desktop_app.py: IBAN/BIC sind im
# Verwendungszweck nie hilfreich, nur fuer die Anzeige entfernt.
_IBAN_RE = re.compile(r"IBAN:?\s*[A-Z]{2}\d{2}[A-Z0-9]{10,30}", re.IGNORECASE)
_BIC_RE = re.compile(r"BIC:?\s*[A-Z0-9]{8,11}", re.IGNORECASE)

# Einheitliches Badge-System fuer alle offenen Status-Werte, die eine
# eigene Kennzeichnung brauchen (siehe tx_status.TxStatus/CLAUDE.md) -
# EINE zentrale Zuordnung Status -> (Anzeigename, Farbschluessel in COLORS)
# statt einer Sonderloesung pro Fall. UNRESOLVED bekommt bewusst kein
# Badge: die rote Rahmenfarbe der Karte ist dort das Signal (wie bisher),
# ein zusaetzliches "OFFEN"-Badge auf jeder roten Karte waere nur Rauschen.
# Erledigte Status (MATCHED/TAGGED) haben ihre bestehenden Pills in
# _render_success_card und tauchen hier nicht auf.
STATUS_BADGES = {
    TxStatus.MULTI_MATCH: ("MEHRFACH-MATCH", "amber"),
    TxStatus.DUPLICATE_SUSPECT: ("DUPLIKAT-VERDACHT", "purple"),
    TxStatus.SPLIT_PAYMENT: ("TEILZAHLUNG?", "teal"),
}


def _parse_amount_filter(text: str):
    """'50' -> (50.0, 50.0) exakt, '50-100' -> (50.0, 100.0) Bereich, leer/
    ungueltig -> None (kein Filter aktiv). Bindestrich-Suche startet erst ab
    Position 1, damit ein fuehrendes '-' (Vorzeichen eines negativen
    Einzelwerts wie '-50') nicht faelschlich als Bereichs-Trenner gilt."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    dash_index = text.find("-", 1)
    if dash_index != -1:
        try:
            lo = float(text[:dash_index])
            hi = float(text[dash_index + 1 :])
        except ValueError:
            return None
        return (min(lo, hi), max(lo, hi))
    try:
        value = float(text)
    except ValueError:
        return None
    return (value, value)


def _transaction_matches_filter(tx: dict, query_text: str, amount_range, date_from, date_to) -> bool:
    """Kombiniert Freitext (Verwendungszweck/Absender-Empfaenger), Betrags-
    und Datumsbereich per UND - siehe Suchfeld in _build_main_area."""
    if query_text:
        haystack = f"{tx.get('purpose') or ''} {tx.get('counterparty') or ''}".lower()
        if query_text.lower() not in haystack:
            return False
    if amount_range is not None:
        lo, hi = amount_range
        if not (lo <= tx["amount_abs"] <= hi):
            return False
    if date_from is not None and tx["date"] < date_from:
        return False
    if date_to is not None and tx["date"] > date_to:
        return False
    return True


def _display_purpose(purpose: str, noise_terms: list[str] | None = None) -> str:
    text = _IBAN_RE.sub("", purpose)
    text = _BIC_RE.sub("", text)
    for term in noise_terms or []:
        if term:
            text = re.sub(re.escape(term), "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" -,")
    return text or purpose


def _selectable(label: QLabel) -> QLabel:
    """QLabel erlaubt standardmaessig KEIN Markieren/Kopieren per Maus
    (anders als z.B. ein Browser) - fuer Buchungsdaten (Verwendungszweck,
    Gegenpartei, Referenznummern, Betrag) soll sich der Text aber wie
    gewohnt per Maus markieren und mit Strg+C kopieren lassen."""
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setCursor(Qt.IBeamCursor)
    return label


def _make_wrap_safe(label: QLabel) -> None:
    """setWordWrap allein reicht in einer QHBoxLayout nicht aus: ohne eine
    Obergrenze fordert Qt weiterhin die volle sizeHint-Breite des
    unumgebrochenen Texts an, was die ganze Karte (und den Scroll-Bereich)
    in die Breite zwingt statt den Text umzubrechen. Ignored als
    horizontale SizePolicy erlaubt dem Layout zusaetzlich, das Label bei
    Bedarf auch UNTER die maximale Breite zu quetschen (z.B. bei einem
    kleineren Fenster) statt einen horizontalen Scrollbalken zu erzwingen."""
    label.setWordWrap(True)
    label.setMaximumWidth(560)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)


def _elide_label(label: QLabel, full_text: str, max_width: int) -> None:
    """Schneidet full_text rechts ab ('…'), statt es umzubrechen - bei
    Bank-Verwendungszwecken stehen die eigentlich interessanten Angaben
    (Empfaenger, Zweck) meist vorne, der abgeschnittene Teil ist fast immer
    nur Referenz-/Terminalnummern. Haelt die Karte dadurch einzeilig UND
    schmal, statt durch Umbruch in die Hoehe oder durch volle Breite in die
    Breite zu wachsen. Der volle Text bleibt per Tooltip (Maus-Hover)
    abrufbar."""
    label.setMaximumWidth(max_width)
    metrics = QFontMetrics(label.font())
    elided = metrics.elidedText(full_text, Qt.ElideRight, max_width)
    label.setText(elided)
    label.setToolTip(full_text if elided != full_text else "")


class ConnectionCheckWorker(QObject):
    finished = Signal(bool)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        self.finished.emit(bool(self.client and self.client.test_connection()))


class MatchWorker(QObject):
    """Fuehrt den Paperless-Abgleich (Netzwerk-I/O, kann bei einer
    langsamen/fehlkonfigurierten Instanz mehrere Timeouts hintereinander
    dauern) in einem eigenen Thread aus - sonst friert die komplette UI
    fuer die Dauer des Requests ein, was wie ein Absturz wirkt (siehe
    ConnectionCheckWorker, gleiches Muster)."""

    finished = Signal(object, object)  # (count, error_message) - genau eines von beiden ist None

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        try:
            count = self.controller.on_match_click()
        except Exception as exc:
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(count, None)




class DocDownloadWorker(QObject):
    """Laedt EIN Paperless-Dokument (PDF-Bytes) im Hintergrund - fuer den
    PDF-Viewer (siehe PdfViewerDialog). Gleiches Freeze-Risiko/Muster wie
    MatchWorker: ein synchroner Download im UI-Thread wuerde bei einer
    langsamen Verbindung die Oberflaeche einfrieren lassen."""

    finished = Signal(object, object)  # (pdf_bytes, error_message) - genau eines von beiden ist None

    def __init__(self, client, doc_id: int):
        super().__init__()
        self.client = client
        self.doc_id = doc_id

    def run(self):
        try:
            pdf_bytes = self.client.download_document(self.doc_id)
        except Exception as exc:
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(pdf_bytes, None)


class FiscalYearExportWorker(QObject):
    """Baut den Jahresexport (12x generate_export + drei Jahres-
    Zusammenfassungen, siehe exporter.export_fiscal_year) in einem eigenen
    Thread aus - kann bei vielen Belegen/Paperless-Downloads mehrere
    Sekunden dauern, sonst friert die UI dafuer komplett ein (gleiches
    Muster wie MatchWorker). Meldet den Fortschritt ueber das progress-
    Signal, das export_fiscal_year je Verarbeitungsschritt aufruft."""

    progress = Signal(int, int, str)  # (aktueller Schritt, Gesamtschritte, Beschriftung)
    finished = Signal(object, object, object)  # (export_path, warnings, error_message) - error_message None bei Erfolg

    def __init__(self, controller, start_year: int):
        super().__init__()
        self.controller = controller
        self.start_year = start_year

    def run(self):
        try:
            export_path, warnings = self.controller.on_export_fiscal_year_click(
                self.start_year, on_progress=lambda step, total, label: self.progress.emit(step, total, label)
            )
        except Exception as exc:
            self.finished.emit(None, None, str(exc))
        else:
            self.finished.emit(export_path, warnings, None)


class CardFrame(QFrame):
    """Basis-Karte: abgerundet, mit Rahmenfarbe - Qt zeichnet das nativ
    (QSS border-radius), kein PIL-Bild-Rendering wie bei CustomTkinter noetig
    (das ist mit ein Grund, warum Qt hier performanter sein sollte)."""

    def __init__(self, bg: str, border_color: str, border_width: int = 1):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: {border_width}px solid {border_color}; "
            f"border-radius: 14px; }}"
        )


def _entry_style() -> str:
    return (
        f"QLineEdit {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
        f"border-radius: 10px; padding: 8px; border: none; }}"
    )


def _outline_button(text: str, color: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background-color: transparent; border: 1px solid {color}; border-radius: 10px; "
        f"color: {color}; padding: 3px 10px; font-weight: bold; font-size: 9pt; }}"
        f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
    )
    return btn


class DropZone(QFrame):
    """Native Qt-Drag&Drop (kein tkinterdnd2 wie bei der CTk-Version noetig -
    Qt kann das nativ)."""

    file_dropped = Signal(str)

    def __init__(self, on_click):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['dropzone_bg']}; border: 1px solid {COLORS['blue']}; "
            f"border-radius: 8px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        label = QLabel(f"⬆  {tr('PDF ablegen')}")
        label.setStyleSheet(f"color: {COLORS['blue']}; font-weight: bold; font-size: 9pt; border: none;")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self._on_click = on_click

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.file_dropped.emit(path)
                break

    def mousePressEvent(self, event):
        self._on_click()


class StatusDot(QWidget):
    """Statuszeile mit einem echten runden Punkt-Widget statt eines
    Farbkreis-Emoji-Zeichens (🟢🔴🟡⚪) - solche Emoji werden mit der
    System-Schriftart auf manchen Windows-Installationen nicht als sauberer,
    farbtreuer Kreis gerendert (z.B. eckig oder in falscher Farbe/Groesse),
    was hier als optischer Bug gemeldet wurde."""

    def __init__(self, color: str, text: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.dot = QFrame()
        self.dot.setFixedSize(10, 10)
        layout.addWidget(self.dot, alignment=Qt.AlignVCenter)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 9pt; border: none;")
        layout.addWidget(self.label, stretch=1)
        self.set_color(color)

    def set_color(self, color: str):
        self.dot.setStyleSheet(f"background-color: {color}; border-radius: 5px; border: none;")

    def set_text(self, text: str):
        self.label.setText(text)


class DesktopAppQt(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Paperless Sync")
        self.setMinimumSize(1080, 680)

        self.app_state = AppState()
        set_language(self.app_state.config.get("language") or "de")
        self._set_app_icon()
        self.controller = Controller(self.app_state)
        self._paperless_connected = False
        self._paperless_checking = False
        self._custom_fields_cache: list[dict] | None = None
        self._card_widgets: dict[str, QWidget] = {}
        self._success_reveal = self.app_state.DEFAULT_REVEAL_COUNT
        self._action_reveal = self.app_state.DEFAULT_REVEAL_COUNT
        self._last_rendered_month = "__unset__"

        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background-color: {COLORS['bg_main']};")
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._build_sidebar(root_layout)
        self._build_main_area(root_layout)

        self._selected_card_id: str | None = None
        self._success_card_ids: list[str] = []
        self._action_card_ids: list[str] = []
        self._build_keyboard_shortcuts()

        months = self.app_state.months
        self.app_state.selected_month = months[-1] if months else None

        if self.app_state.is_configured():
            self._refresh_connection_status()
        self.render()
        # resize() vor UND nach show() wurde beim Start trotzdem noch von
        # Qt/dem Fenstermanager auf eine kleinere Groesse zurueckgesetzt
        # (zweifach gemeldeter "Fenster startet zu klein"-Bug). Maximiert
        # gestartet (siehe main()) gibt es kein Zielmass mehr, das
        # ueberschrieben werden koennte - robuster als jede resize()-Zeit-
        # steuerung. setMinimumSize bleibt als Untergrenze, falls der
        # Nutzer manuell verkleinert/wiederherstellt.

    # ------------------------------------------------------------------
    def _set_app_icon(self):
        # Immer das mitgelieferte Standard-Icon fuer Fenster-/Taskleiste -
        # das hochgeladene Firmenlogo (siehe SettingsDialog) wird bewusst
        # NUR in der Sidebar angezeigt (siehe _refresh_logo), nicht hier.
        icon_path = get_resource_dir() / "icon.ico"
        if icon_path.exists():
            # QIcon liest ALLE eingebetteten Groessen aus der .ico selbst
            # und waehlt je nach Kontext (Titelleiste/Taskleiste) die
            # passende - anders als bei Tk ist dafuer keine manuelle
            # WM_SETICON-Behandlung noetig (siehe icon_utils.py).
            self.setWindowIcon(QIcon(str(icon_path)))

    def _custom_icon_path(self):
        name = self.app_state.config.get("company_icon_path")
        return (self.app_state.base_dir / name) if name else None

    def _refresh_logo(self):
        """Aktualisiert das Logo oben links in der Sidebar (Buerklammer-Text
        oder hochgeladenes Firmenlogo als Bild) anhand des aktuellen
        company_icon_path - separat von render() aufrufbar (siehe
        _on_settings_saved), damit ein frisch hochgeladenes Logo sofort
        sichtbar wird statt erst nach einem Neustart. Ruehrt bewusst NICHT
        an das Fenster-/Taskleisten-Icon (siehe _set_app_icon)."""
        logo_path = self._custom_icon_path()
        pixmap = QPixmap(str(logo_path)) if logo_path and logo_path.exists() else None
        if pixmap and not pixmap.isNull():
            self.logo_lbl.setText("")
            self.logo_lbl.setPixmap(pixmap.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.logo_lbl.setPixmap(QPixmap())
            self.logo_lbl.setText("📎")
            self.logo_lbl.setFont(qfont(18))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    def _build_sidebar(self, root_layout):
        sidebar = QFrame()
        sidebar.setFixedWidth(300)
        sidebar.setStyleSheet(f"background-color: {COLORS['bg_sidebar']};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        # Logo-Platz ganz links: Standardmaessig die Buerklammer als Text
        # (kein Bild - siehe _refresh_logo), ersetzt durch das hochgeladene
        # Firmenlogo, sobald eines hinterlegt ist.
        self.logo_lbl = QLabel()
        self.logo_lbl.setFixedSize(28, 28)
        self.logo_lbl.setAlignment(Qt.AlignCenter)
        title_row.addWidget(self.logo_lbl, alignment=Qt.AlignTop)

        title_col = QVBoxLayout()
        title = QLabel("Paperless Sync")
        title.setFont(qfont(16, bold=True))
        title.setStyleSheet(f"color: {COLORS['text_primary']}; border: none;")
        title_col.addWidget(title)

        company = QLabel(self.app_state.env.get("COMPANY_NAME") or "")
        company.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
        title_col.addWidget(company)
        title_row.addLayout(title_col, stretch=1)
        layout.addLayout(title_row)
        layout.addSpacing(20)

        settings_btn = _outline_button(f"⚙️  {tr('Einstellungen')}", COLORS["text_muted"])
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)
        layout.addSpacing(20)

        layout.addWidget(self._section_label(tr("1 · CSV-UPLOAD")))
        self.csv_name_label = QLabel(self._csv_label_text())
        self.csv_name_label.setWordWrap(True)
        self.csv_name_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt; border: none;")
        layout.addWidget(self.csv_name_label)

        upload_btn = QPushButton(tr("Datei auswählen"))
        upload_btn.setStyleSheet(self._flat_button_style())
        upload_btn.clicked.connect(self._on_upload_csv_click)
        layout.addWidget(upload_btn)
        layout.addSpacing(8)

        # Nur SICHTBAR, wenn eine eigene Enable-Banking-Anwendung eingerichtet
        # ist (Application-ID + gueltige .pem-Datei, siehe
        # EnableBankingSetupWizard) - das Widget selbst wird aber immer
        # angelegt (nicht nur bedingt), sonst gaebe es nach einer
        # Einrichtung ueber den Wizard (der aus einem bereits offenen
        # Settings-Dialog heraus gestartet wird, waehrend die Sidebar schon
        # laengst gebaut ist) gar kein Widget, das sichtbar gemacht werden
        # koennte - _build_sidebar() laeuft nur EINMAL in __init__. Sichtbarkeit
        # wird stattdessen bei jedem Settings-Schliessen neu geprueft, siehe
        # _open_settings()/_refresh_bank_import_visibility().
        self.bank_import_btn = QPushButton(tr("Von Bank importieren"))
        self.bank_import_btn.setToolTip(
            tr("Öffnet den Bank-Login im Browser – bei jedem Import erneut nötig.")
        )
        self.bank_import_btn.setStyleSheet(self._flat_button_style())
        self.bank_import_btn.clicked.connect(self._on_bank_import_click)
        self.bank_import_btn.setVisible(self._enable_banking_ready())
        layout.addWidget(self.bank_import_btn)
        layout.addSpacing(8)

        self.match_btn = QPushButton(f"🔍  {tr('Mit Paperless abgleichen')}")
        self._match_btn_default_text = self.match_btn.text()
        self.match_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['blue_dim']}; color: {COLORS['blue']}; "
            f"border-radius: 10px; padding: 10px; text-align: left; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )
        self.match_btn.clicked.connect(self._on_match_click)
        layout.addWidget(self.match_btn)
        layout.addSpacing(20)

        layout.addWidget(self._section_label(tr("2 · MONAT")))
        self.month_combo = NoScrollComboBox()
        self.month_combo.setStyleSheet(
            f"QComboBox {{ background-color: {COLORS['bg_card']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 8px; border: none; }}"
        )
        self.month_combo.currentTextChanged.connect(self._on_month_selected)
        layout.addWidget(self.month_combo)
        layout.addSpacing(16)

        status_frame = QFrame()
        status_frame.setStyleSheet(f"background-color: {COLORS['bg_card']}; border-radius: 10px;")
        status_layout = QVBoxLayout(status_frame)
        self.status_paperless = StatusDot(COLORS["text_muted"], tr("Paperless: nicht konfiguriert"))
        self.status_export = StatusDot(COLORS["amber"], tr("Exportordner: noch keine Auswahl"))
        status_layout.addWidget(self.status_paperless)
        status_layout.addWidget(self.status_export)
        layout.addWidget(status_frame)

        layout.addStretch()

        export_btn = QPushButton(tr("ORDNER JETZT GENERIEREN"))
        export_btn.setStyleSheet(
            "QPushButton { background-color: #e0402a; color: white; border-radius: 10px; padding: 14px; "
            "font-weight: bold; } QPushButton:hover { background-color: #b8331f; }"
        )
        export_btn.clicked.connect(self._on_generate_export_click)
        layout.addWidget(export_btn)

        layout.addSpacing(6)
        fiscal_year_export_btn = _outline_button(tr("JAHRESEXPORT"), COLORS["text_muted"])
        fiscal_year_export_btn.clicked.connect(self._on_export_fiscal_year_click)
        layout.addWidget(fiscal_year_export_btn)

        layout.addSpacing(8)
        version_lbl = QLabel(f"v{__version__}")
        version_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8pt; border: none;")
        version_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_lbl)

        self._refresh_logo()
        root_layout.addWidget(sidebar)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: bold; font-size: 10pt; border: none;")
        return lbl

    def _flat_button_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {COLORS['bg_card']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 10px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )

    def _csv_label_text(self) -> str:
        if self.app_state.csv_signature:
            return self.app_state.csv_signature
        return tr("Keine Datei gewählt")

    def _enable_banking_ready(self) -> bool:
        eb_config = self.app_state.config.get("enable_banking") or {}
        return bool(eb_config.get("application_id")) and get_effective_enable_banking_key_path(self.app_state.config).exists()

    # ------------------------------------------------------------------
    # Hauptbereich
    # ------------------------------------------------------------------
    def _build_main_area(self, root_layout):
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(20, 20, 20, 20)

        kpi_row = QHBoxLayout()
        self.kpi_success = self._build_kpi_card(kpi_row, f"✅  {tr('ZUGEORDNETE BELEGE')}", COLORS["green"])
        self.kpi_action = self._build_kpi_card(kpi_row, f"⚠️  {tr('AKTION ERFORDERLICH')}", COLORS["red"])
        self.kpi_multi = self._build_kpi_card(kpi_row, tr("MEHRFACH-MATCH"), COLORS["amber"], dot_color=COLORS["amber"])
        self.kpi_review = self._build_kpi_card(kpi_row, tr("ZU PRÜFEN"), COLORS["purple"], dot_color=COLORS["purple"])
        main_layout.addLayout(kpi_row)
        main_layout.addSpacing(14)

        self._build_filter_row(main_layout)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            f"QTabWidget::pane {{ background-color: {COLORS['bg_kpi']}; border-radius: 16px; border: none; }}"
            f"QTabBar::tab {{ background-color: {COLORS['bg_kpi']}; color: {COLORS['text_primary']}; "
            f"padding: 10px 20px; border-radius: 10px; margin: 4px; }}"
            f"QTabBar::tab:selected {{ background-color: {COLORS['blue_dim']}; }}"
        )

        self.success_scroll, self.success_container, self.success_layout = self._build_scroll_tab()
        self.tabs.addTab(self.success_scroll, f"✅  {tr('Erfolgreich')}")

        self.action_scroll, self.action_container, self.action_layout = self._build_scroll_tab()
        self.tabs.addTab(self.action_scroll, f"⚠️  {tr('Unklar / Fehlt')}")
        self.tabs.currentChanged.connect(lambda _index: self._update_filter_hint())

        main_layout.addWidget(self.tabs)
        root_layout.addWidget(main)

    def _build_filter_row(self, main_layout: QVBoxLayout):
        """Suchfeld + Betrags-/Datumsbereich, live filternd, kombinierbar
        mit den Tabs "Erfolgreich"/"Unklar / Fehlt" (UND-Verknuepfung, siehe
        _render_tabs). Darunter ein Transparenz-Hinweis, wie viele
        Buchungen der aktuelle Filter (Tab + Suche) gerade zeigt - ohne
        das koennten z.B. Luecken in der Nummern-Sequenz (#001, #002, #005)
        wie verlorene Buchungen wirken, obwohl nur gefiltert wird."""
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(f"🔍  {tr('Suchen (Verwendungszweck, Absender/Empfänger)...')}")
        self.search_edit.setStyleSheet(_entry_style())
        self.search_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.search_edit, stretch=2)

        self.amount_filter_edit = QLineEdit()
        self.amount_filter_edit.setPlaceholderText(tr("Betrag (z.B. 50 oder 50-100)"))
        self.amount_filter_edit.setStyleSheet(_entry_style())
        self.amount_filter_edit.setFixedWidth(170)
        self.amount_filter_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.amount_filter_edit)

        self.date_from_edit = QLineEdit()
        self.date_from_edit.setPlaceholderText(tr("Von (TT.MM.JJJJ)"))
        self.date_from_edit.setStyleSheet(_entry_style())
        self.date_from_edit.setFixedWidth(120)
        self.date_from_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.date_from_edit)

        self.date_to_edit = QLineEdit()
        self.date_to_edit.setPlaceholderText(tr("Bis (TT.MM.JJJJ)"))
        self.date_to_edit.setStyleSheet(_entry_style())
        self.date_to_edit.setFixedWidth(120)
        self.date_to_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.date_to_edit)

        reset_btn = QPushButton(f"✕ {tr('Zurücksetzen')}")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setStyleSheet(self._flat_button_style())
        reset_btn.clicked.connect(self._on_filter_reset)
        filter_row.addWidget(reset_btn)

        main_layout.addLayout(filter_row)
        main_layout.addSpacing(6)

        self.filter_hint_label = QLabel("")
        self.filter_hint_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt;")
        self.filter_hint_label.setToolTip(
            tr("Tastatur: ↑/↓ zum Navigieren, Strg+↓ springt zum nächsten offenen Posten")
        )
        main_layout.addWidget(self.filter_hint_label)
        main_layout.addSpacing(8)

    def _on_filter_changed(self, _text: str = ""):
        self._render_tabs()

    def _on_filter_reset(self):
        for edit in (self.search_edit, self.amount_filter_edit, self.date_from_edit, self.date_to_edit):
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        self._render_tabs()

    def _current_filter_values(self):
        query_text = self.search_edit.text().strip()
        amount_range = _parse_amount_filter(self.amount_filter_edit.text())
        date_from = parse_date(self.date_from_edit.text()) if self.date_from_edit.text().strip() else None
        date_to = parse_date(self.date_to_edit.text()) if self.date_to_edit.text().strip() else None
        return query_text, amount_range, date_from, date_to

    def _update_filter_hint(self):
        if self.tabs.currentIndex() == 0:
            shown, total, label = self._success_shown_count, self._success_total_count, tr("Erfolgreich")
        else:
            shown, total, label = self._action_shown_count, self._action_total_count, tr("Unklar / Fehlt")
        self.filter_hint_label.setText(
            tr("{shown} von {total} Buchungen sichtbar (Filter: {label})", shown=shown, total=total, label=label)
        )

    def _build_scroll_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(8)
        scroll.setWidget(container)
        return scroll, container, layout

    def _build_kpi_card(self, row: QHBoxLayout, title: str, color: str, dot_color: str | None = None) -> QLabel:
        card = QFrame()
        card.setStyleSheet(f"background-color: {COLORS['bg_kpi']}; border-radius: 14px;")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        if dot_color:
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")
            title_row.addWidget(dot, alignment=Qt.AlignVCenter)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: bold; font-size: 10pt; border: none;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        value_lbl = QLabel("0")
        value_lbl.setFont(qfont(22, bold=True))
        value_lbl.setStyleSheet(f"color: {color}; border: none;")
        layout.addLayout(title_row)
        layout.addWidget(value_lbl)
        row.addWidget(card)
        return value_lbl

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self):
        self.csv_name_label.setText(self._csv_label_text())
        self._update_kpis()
        self._render_status()
        self._render_month_combo()
        self._render_tabs()

    def _update_kpis(self):
        self.kpi_success.setText(str(len(self.app_state.success_transactions)))
        self.kpi_action.setText(str(len(self.app_state.missing_transactions)))
        self.kpi_multi.setText(str(len(self.app_state.unclear_transactions)))
        self.kpi_review.setText(str(len(self.app_state.review_transactions)))

    def _render_status(self):
        if not self.app_state.client:
            self.status_paperless.set_color(COLORS["text_muted"])
            self.status_paperless.set_text(tr("Paperless: nicht konfiguriert"))
        elif self._paperless_checking:
            self.status_paperless.set_color(COLORS["text_muted"])
            self.status_paperless.set_text(tr("Paperless: wird geprüft..."))
        elif self._paperless_connected:
            self.status_paperless.set_color(COLORS["green"])
            self.status_paperless.set_text(tr("Paperless: Verbunden"))
        else:
            self.status_paperless.set_color(COLORS["red"])
            self.status_paperless.set_text(tr("Paperless: nicht erreichbar"))
        ready = bool(self.app_state.transactions) and bool(self.app_state.selected_month)
        self.status_export.set_color(COLORS["green"] if ready else COLORS["amber"])
        self.status_export.set_text(tr("Exportordner: Bereit") if ready else tr("Exportordner: noch keine Auswahl"))

    def _render_month_combo(self):
        months = self.app_state.months
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItems(months or ["-"])
        if self.app_state.selected_month not in months:
            self.app_state.selected_month = months[-1] if months else None
        if self.app_state.selected_month:
            self.month_combo.setCurrentText(self.app_state.selected_month)
        self.month_combo.blockSignals(False)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_tabs(self):
        if self._last_rendered_month != self.app_state.selected_month:
            self._success_reveal = self.app_state.DEFAULT_REVEAL_COUNT
            self._action_reveal = self.app_state.DEFAULT_REVEAL_COUNT
            self._last_rendered_month = self.app_state.selected_month

        self._clear_layout(self.success_layout)
        self._clear_layout(self.action_layout)
        self._card_widgets.clear()

        query_text, amount_range, date_from, date_to = self._current_filter_values()

        def _filtered(txs):
            return [t for t in txs if _transaction_matches_filter(t, query_text, amount_range, date_from, date_to)]

        success_txs_all = self.app_state.success_transactions
        success_txs = _filtered(success_txs_all)
        visible_success = success_txs[: self._success_reveal]
        for tx in visible_success:
            w = self._render_success_card(tx)
            self.success_layout.addWidget(w)
            self._card_widgets[tx["id"]] = w
        if len(success_txs) > len(visible_success):
            self.success_layout.addWidget(
                self._build_load_more_button(len(success_txs) - len(visible_success), "success")
            )
        elif not success_txs:
            placeholder = tr("Noch keine zugeordneten Belege.") if not success_txs_all else tr("Keine Treffer für die aktuellen Filter.")
            self.success_layout.addWidget(self._placeholder_label(placeholder))

        # unclear_transactions (MULTI_MATCH) + review_transactions
        # (DUPLICATE_SUSPECT/SPLIT_PAYMENT) sind erfahrungsgemaess deutlich
        # weniger als missing_transactions - unpaginiert, wie bisher schon
        # bei unclear_transactions.
        priority_txs_all = self.app_state.unclear_transactions + self.app_state.review_transactions
        priority_txs = _filtered(priority_txs_all)
        for tx in priority_txs:
            w = self._render_action_card(tx)
            self.action_layout.addWidget(w)
            self._card_widgets[tx["id"]] = w

        missing_txs_all = self.app_state.missing_transactions
        missing_txs = _filtered(missing_txs_all)
        visible_missing = missing_txs[: self._action_reveal]
        for tx in visible_missing:
            w = self._render_action_card(tx)
            self.action_layout.addWidget(w)
            self._card_widgets[tx["id"]] = w
        if len(missing_txs) > len(visible_missing):
            self.action_layout.addWidget(
                self._build_load_more_button(len(missing_txs) - len(visible_missing), "action")
            )
        elif not missing_txs and not priority_txs:
            if missing_txs_all or priority_txs_all:
                self.action_layout.addWidget(self._placeholder_label(tr("Keine Treffer für die aktuellen Filter.")))
            else:
                self.action_layout.addWidget(self._placeholder_label(tr("Alles zugeordnet! 🎉")))

        self._success_shown_count = len(success_txs)
        self._success_total_count = len(success_txs_all)
        self._action_shown_count = len(priority_txs) + len(missing_txs)
        self._action_total_count = len(priority_txs_all) + len(missing_txs_all)
        self._update_filter_hint()

        # Kartenreihenfolge je Tab fuer die Pfeiltasten-Navigation (siehe
        # _navigate_cards) - _card_widgets selbst mischt beide Tabs.
        self._success_card_ids = [tx["id"] for tx in visible_success]
        self._action_card_ids = [tx["id"] for tx in priority_txs] + [tx["id"] for tx in visible_missing]
        # _render_tabs() baut bei jeder Filteraenderung ALLE Karten-Widgets
        # neu (siehe _clear_layout oben) - eine bestehende Auswahl muss
        # deshalb auf dem NEUEN Widget erneut hervorgehoben werden, sonst
        # ginge die Markierung bei jedem Tastendruck im Suchfeld verloren.
        if self._selected_card_id not in self._card_widgets:
            self._selected_card_id = None
        elif self._selected_card_id:
            self._set_card_highlight(self._selected_card_id, True)

    def _build_keyboard_shortcuts(self):
        """Pfeiltasten zum Navigieren in der jeweils aktiven Tab-Liste
        (Erfolgreich/Unklar+Fehlt) + Kuerzel fuer 'springe zum naechsten
        offenen Posten' (siehe CLAUDE.md/UI-Prompt Punkt 8). QShortcut statt
        keyPressEvent-Override, damit die Tasten unabhaengig davon greifen,
        welches Kind-Widget gerade den Fokus hat."""
        down = QShortcut(QKeySequence(Qt.Key_Down), self)
        down.activated.connect(lambda: self._navigate_cards(1))
        up = QShortcut(QKeySequence(Qt.Key_Up), self)
        up.activated.connect(lambda: self._navigate_cards(-1))
        next_open = QShortcut(QKeySequence("Ctrl+Down"), self)
        next_open.activated.connect(self._jump_to_next_open)

    def _navigate_cards(self, direction: int):
        # Pfeiltasten sollen normale Textbearbeitung in den Filterfeldern
        # nicht stoeren - dort werden sie ignoriert statt Karten zu
        # durchlaufen.
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        ids = self._success_card_ids if self.tabs.currentIndex() == 0 else self._action_card_ids
        if not ids:
            return
        if self._selected_card_id in ids:
            new_index = max(0, min(len(ids) - 1, ids.index(self._selected_card_id) + direction))
        else:
            new_index = 0 if direction > 0 else len(ids) - 1
        self._select_card(ids[new_index])

    def _jump_to_next_open(self):
        """'Springe zum naechsten offenen Posten' - wechselt bei Bedarf
        selbst in den Tab 'Unklar / Fehlt' (dort leben UNRESOLVED/
        MULTI_MATCH/DUPLICATE_SUSPECT/SPLIT_PAYMENT, siehe
        AppState.missing_transactions/unclear_transactions/
        review_transactions) und waehlt den naechsten Eintrag nach der
        aktuellen Auswahl (zyklisch)."""
        if self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        ids = self._action_card_ids
        if not ids:
            return
        if self._selected_card_id in ids:
            new_index = (ids.index(self._selected_card_id) + 1) % len(ids)
        else:
            new_index = 0
        self._select_card(ids[new_index])

    def _select_card(self, tx_id: str):
        if self._selected_card_id and self._selected_card_id != tx_id:
            self._set_card_highlight(self._selected_card_id, False)
        self._selected_card_id = tx_id
        self._set_card_highlight(tx_id, True)
        widget = self._card_widgets.get(tx_id)
        if widget is not None:
            scroll = self.success_scroll if self.tabs.currentIndex() == 0 else self.action_scroll
            scroll.ensureWidgetVisible(widget)

    def _set_card_highlight(self, tx_id: str, highlighted: bool):
        widget = self._card_widgets.get(tx_id)
        if widget is None:
            return
        base_style = widget.property("_base_style")
        if base_style is None:
            base_style = widget.styleSheet()
            widget.setProperty("_base_style", base_style)
        if highlighted:
            widget.setStyleSheet(base_style + f"QFrame {{ border: 3px solid {COLORS['blue']}; }}")
        else:
            widget.setStyleSheet(base_style)

    def _placeholder_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {COLORS['text_muted']}; padding: 30px; border: none;")
        return lbl

    def _build_load_more_button(self, remaining: int, which: str) -> QPushButton:
        btn = QPushButton(f"↓ {tr('{remaining} weitere anzeigen', remaining=remaining)}")
        btn.setStyleSheet(self._flat_button_style())
        btn.clicked.connect(lambda: self._on_load_more(which))
        return btn

    def _on_load_more(self, which: str):
        if which == "success":
            self._success_reveal += self.app_state.DEFAULT_REVEAL_COUNT
        else:
            self._action_reveal += self.app_state.DEFAULT_REVEAL_COUNT
        self._render_tabs()

    def _refresh_single_transaction(self, tx_id: str):
        old = self._card_widgets.pop(tx_id, None)
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        tx = next((t for t in self.app_state.visible_transactions if t["id"] == tx_id), None)
        if tx is not None:
            if tx["status"] in DONE_STATUSES:
                w = self._render_success_card(tx)
                self.success_layout.insertWidget(max(0, self.success_layout.count() - 1), w)
            elif tx["status"] in (TxStatus.MULTI_MATCH, TxStatus.DUPLICATE_SUSPECT, TxStatus.SPLIT_PAYMENT):
                w = self._render_action_card(tx)
                self.action_layout.insertWidget(0, w)
            else:
                w = self._render_action_card(tx)
                self.action_layout.insertWidget(max(0, self.action_layout.count() - 1), w)
            self._card_widgets[tx_id] = w
        self._update_kpis()
        self._render_status()

    # ------------------------------------------------------------------
    # Karten
    # ------------------------------------------------------------------
    _PURPOSE_MAX_WIDTH = 480

    def _purpose_block(self, layout: QVBoxLayout, tx: dict):
        noise_terms = self.app_state.config.get("purpose_noise_terms", [])
        purpose_text = _display_purpose(tx["purpose"], noise_terms)
        counterparty = (tx.get("counterparty") or "").strip()
        if counterparty:
            main_lbl = _selectable(QLabel())
            main_lbl.setFont(qfont(12, bold=True))
            main_lbl.setStyleSheet(f"color: {COLORS['text_primary']}; border: none;")
            _elide_label(main_lbl, counterparty, self._PURPOSE_MAX_WIDTH)
            sec_lbl = _selectable(QLabel())
            sec_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10pt; border: none;")
            _elide_label(sec_lbl, purpose_text, self._PURPOSE_MAX_WIDTH)
            layout.addWidget(main_lbl)
            layout.addWidget(sec_lbl)
        else:
            main_lbl = _selectable(QLabel())
            main_lbl.setFont(qfont(12, bold=True))
            main_lbl.setStyleSheet(f"color: {COLORS['text_primary']}; border: none;")
            _elide_label(main_lbl, purpose_text, self._PURPOSE_MAX_WIDTH)
            layout.addWidget(main_lbl)

    def _pill(self, text: str, color: str, bg: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"background-color: {bg}; color: {color}; border-radius: 12px; padding: 4px 12px; "
            f"font-weight: bold; font-size: 9pt;"
        )
        return lbl

    def _render_success_card(self, tx: dict) -> QFrame:
        card = CardFrame(COLORS["bg_card"], COLORS["border"], border_width=1)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        top = QHBoxLayout()
        left = QVBoxLayout()
        meta = _selectable(QLabel(f"#{tx.get('display_number') or tx['id']}  ·  {tx['date'].strftime('%d.%m.%Y')}"))
        meta.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt; border: none;")
        left.addWidget(meta)
        self._purpose_block(left, tx)
        top.addLayout(left, stretch=1)

        amount_color = COLORS["green"] if tx["amount_raw"] >= 0 else COLORS["red"]
        amount_lbl = _selectable(QLabel(f"{tx['amount_abs']:.2f} €"))
        amount_lbl.setFont(qfont(18, bold=True))
        amount_lbl.setStyleSheet(f"color: {amount_color}; border: none;")
        amount_lbl.setAlignment(Qt.AlignTop | Qt.AlignRight)
        top.addWidget(amount_lbl, alignment=Qt.AlignTop)
        outer.addLayout(top)

        bottom = QHBoxLayout()
        # Reihenfolge bewusst: uploaded_bytes zuerst pruefen - TxStatus.MATCHED
        # deckt sowohl Paperless-verknuepfte als auch direkt hochgeladene
        # Belege ab (siehe TxStatus), die beiden Faelle unterscheiden sich
        # nur an diesen Feldern, nicht mehr am Status.
        if tx.get("uploaded_bytes"):
            bottom.addWidget(self._pill(f"📤 {tr('Hochgeladen')}", COLORS["blue"], COLORS["blue_dim"]))
            view_btn = QPushButton(f"👁 {tr('Vorschau')}")
            view_btn.setCursor(Qt.PointingHandCursor)
            view_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {COLORS['blue']}; border: none; font-size: 8pt; }}"
                f"QPushButton:hover {{ color: #4a76d6; }}"
            )
            view_btn.clicked.connect(lambda _=False, t=tx: self._view_uploaded_pdf(t))
            bottom.addWidget(view_btn)
        elif tx["status"] == TxStatus.MATCHED:
            doc_count = len(tx.get("matched_docs") or [])
            label = f"🔗 {tr('Automatisch zugeordnet')}" if doc_count <= 1 else f"🔗 {tr('{doc_count} Belege verknüpft', doc_count=doc_count)}"
            bottom.addWidget(self._pill(label, COLORS["green"], COLORS["green_dim"]))
        else:
            tag = tx.get("tag") or "SONSTIGES"
            icon = TAG_ICONS.get(tag, "🏷️")
            if tag in TAG_COLORS:
                color, bg = TAG_COLORS[tag], TAG_COLORS_DIM[tag]
            else:
                color, bg = custom_tag_color(tag)
            label = tr(tag.capitalize()) if tag in BUILTIN_TAGS else tag
            bottom.addWidget(self._pill(f"{icon} {label}", color, bg))
        bottom.addStretch()
        undo_btn = QPushButton(f"↩ {tr('Rückgängig')}")
        undo_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: none; }}"
            f"QPushButton:hover {{ color: {COLORS['text_primary']}; }}"
        )
        undo_btn.clicked.connect(lambda _=False, t=tx: self._on_undo_click(t["id"]))
        bottom.addWidget(undo_btn)
        outer.addLayout(bottom)
        if tx.get("matched_docs"):
            self._build_doc_chip_row(outer, tx)
        return card

    def _build_doc_chip_row(self, outer: QVBoxLayout, tx: dict):
        """Zeigt jedes verknuepfte Dokument als kleinen Chip mit
        Entfernen-Button, plus '+ Beleg' um bei Sammelabbuchungen (z.B.
        Amazon: 1 Abbuchung, mehrere Einzelrechnungen) weitere Dokumente
        zu ergaenzen, ohne die ganze Zuordnung rueckgaengig machen zu
        muessen."""
        docs = tx.get("matched_docs") or []
        row = QHBoxLayout()
        row.setSpacing(6)
        for doc in docs:
            chip = QFrame()
            chip.setStyleSheet(f"background-color: {COLORS['bg_kpi']}; border-radius: 8px;")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 3, 4, 3)
            chip_layout.setSpacing(4)
            name = doc.get("original_file_name") or doc.get("title") or f"#{doc['id']}"
            if len(name) > 28:
                name = name[:26] + "…"
            name_lbl = QLabel(f"📄 {name}")
            name_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8pt; border: none;")
            chip_layout.addWidget(name_lbl)
            view_btn = QPushButton("👁")
            view_btn.setFixedSize(18, 16)
            view_btn.setCursor(Qt.PointingHandCursor)
            view_btn.setToolTip(tr("Vorschau"))
            view_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {COLORS['blue']}; border: none; font-size: 8pt; }}"
                f"QPushButton:hover {{ color: #4a76d6; }}"
            )
            view_btn.clicked.connect(lambda _=False, d=doc: self._view_paperless_doc(d))
            chip_layout.addWidget(view_btn)
            remove_btn = QPushButton("✕")
            remove_btn.setFixedSize(16, 16)
            remove_btn.setCursor(Qt.PointingHandCursor)
            remove_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: none; font-size: 8pt; }}"
                f"QPushButton:hover {{ color: {COLORS['red']}; }}"
            )
            remove_btn.clicked.connect(lambda _=False, t=tx, d=doc: self._on_remove_doc_click(t["id"], d["id"]))
            chip_layout.addWidget(remove_btn)
            row.addWidget(chip)

        # Richtiger Button statt Textlink (siehe CLAUDE.md/UI-Prompt Punkt 5) -
        # Mehrfach-Beleg-Zuordnung (z.B. Amazon-Sammelabbuchung mit mehreren
        # Einzelrechnungen) ist ein Kernfeature, verdient also mehr Gewicht
        # als ein unauffaelliger Link.
        add_btn = _outline_button(f"➕  {tr('Beleg')}", COLORS["blue"])
        add_btn.clicked.connect(lambda _=False, t=tx: self._on_pick_existing_doc(t))
        row.addWidget(add_btn)
        row.addStretch()
        outer.addLayout(row)

    def _render_action_card(self, tx: dict) -> QFrame:
        status = tx["status"]
        is_unclear = status == TxStatus.MULTI_MATCH
        badge_info = STATUS_BADGES.get(status)
        border_color = COLORS[badge_info[1]] if badge_info else COLORS["red_border"]
        card = CardFrame(COLORS["bg_card"], border_color, border_width=2)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        if badge_info:
            label, color_key = badge_info
            badge = QLabel(tr(label))
            badge.setStyleSheet(
                f"background-color: {COLORS[color_key]}; color: #1e1e2e; border-radius: 8px; "
                f"padding: 4px 10px; font-weight: bold; font-size: 9pt;"
            )
            badge.setFixedWidth(badge.sizeHint().width())
            outer.addWidget(badge, alignment=Qt.AlignLeft)

        top = QHBoxLayout()
        left = QVBoxLayout()
        meta = _selectable(QLabel(f"#{tx.get('display_number') or tx['id']}  ·  {tx['date'].strftime('%d.%m.%Y')}"))
        meta.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9pt; border: none;")
        left.addWidget(meta)
        self._purpose_block(left, tx)
        top.addLayout(left, stretch=1)

        amount_color = COLORS["green"] if tx["amount_raw"] >= 0 else COLORS["red"]
        amount_text = f"{'▲' if tx['amount_raw'] >= 0 else '▼'} {tx['amount_abs']:.2f} €"
        amount_lbl = _selectable(QLabel(amount_text))
        amount_lbl.setFont(qfont(18, bold=True))
        amount_lbl.setStyleSheet(f"color: {amount_color}; border: none;")
        top.addWidget(amount_lbl, alignment=Qt.AlignTop)
        outer.addLayout(top)

        if badge_info and tx.get("note"):
            # Info-Banner in derselben Farbe wie das Badge - Text kommt aus
            # tx['note'] (siehe matcher.py: dort schon deutschsprachig und
            # je nach genauer Ursache formuliert, z.B. exakter Mehrfach-
            # treffer vs. Toleranz-Kandidaten vs. Duplikat vs. Teilzahlung -
            # kein hartkodierter Text mehr pro Status noetig.
            _, color_key = badge_info
            warn = QFrame()
            warn.setStyleSheet(f"background-color: {COLORS[color_key + '_dim']}; border-radius: 10px; border: none;")
            warn_layout = QHBoxLayout(warn)
            warn_lbl = QLabel(f"⚠️  {tx['note']}")
            warn_lbl.setStyleSheet(f"color: {COLORS[color_key]}; font-weight: bold; border: none;")
            _make_wrap_safe(warn_lbl)
            warn_layout.addWidget(warn_lbl)
            outer.addWidget(warn)

        if is_unclear:
            self._build_ambiguous_picker(outer, tx)
            self._build_tag_row(outer, tx)
            return card

        # DUPLICATE_SUSPECT/SPLIT_PAYMENT haben (noch) keine eigene
        # Kandidaten-Aktion (0 bzw. 2+ Dokumente, siehe
        # _build_ambiguous_picker-Kommentar) - fallen bewusst durch zu den
        # normalen Aktionen unten (Beleg hochladen/waehlen/taggen). Jede
        # dieser Aktionen ueberschreibt tx['status'] unabhaengig vom
        # aktuellen Wert (siehe Controller.on_apply_tag/on_pdf_drop/
        # on_documents_selected) - loest den Verdachtsfall also automatisch
        # mit auf.

        if tx.get("suggested_tag"):
            sugg = tx["suggested_tag"]
            icon = TAG_ICONS.get(sugg, "🏷️")
            label = tr(sugg.capitalize()) if sugg in BUILTIN_TAGS else sugg
            sugg_row = QFrame()
            sugg_row.setStyleSheet(f"background-color: {COLORS['blue_dim']}; border-radius: 10px; border: none;")
            sugg_layout = QHBoxLayout(sugg_row)
            sugg_lbl = QLabel(
                f"💡  {tr('Vorschlag: {icon} {label} - ähnliche Buchung, bereits so getaggt', icon=icon, label=label)}"
            )
            sugg_lbl.setStyleSheet(f"color: {COLORS['blue']}; font-weight: bold; border: none;")
            _make_wrap_safe(sugg_lbl)
            sugg_layout.addWidget(sugg_lbl, stretch=1)
            apply_btn = QPushButton(f"✓ {tr('Übernehmen')}")
            apply_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
                f"padding: 6px 14px; }} QPushButton:hover {{ background-color: #4a76d6; }}"
            )
            apply_btn.clicked.connect(lambda _=False, t=tx, s=sugg: self._on_tag_click(t["id"], s))
            sugg_layout.addWidget(apply_btn)
            outer.addWidget(sugg_row)

        # "Aus Paperless wählen" ist der haeufige Weg (Beleg existiert
        # schon dort), die Drop-Zone wird kaum gebraucht - beide daher
        # nebeneinander in einer schmalen Zeile statt der Drop-Zone einen
        # eigenen grossen, dominanten Block zu geben.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        paperless_btn = _outline_button(f"📂  {tr('Aus Paperless wählen')}", COLORS["blue"])
        pick_row.addWidget(paperless_btn)
        paperless_btn.clicked.connect(lambda _=False, t=tx: self._on_pick_existing_doc(t))

        drop_zone = DropZone(lambda t=tx: self._on_pick_pdf_file(t["id"]))
        drop_zone.file_dropped.connect(lambda path, t=tx: self._on_pdf_dropped(t["id"], path))
        pick_row.addWidget(drop_zone, stretch=1)
        outer.addLayout(pick_row)

        self._build_tag_row(outer, tx)
        return card

    def _build_ambiguous_picker(self, outer: QVBoxLayout, tx: dict):
        """Kandidatenliste (siehe MatchCandidate) - ein Klick auf einen
        Kandidaten verknuepft ihn direkt, kein separater Bestaetigungs-
        Schritt mehr noetig. Aufklappbar (bei genau einem Kandidaten
        direkt sichtbar), damit die Karte bei mehreren Vorschlaegen nicht
        unuebersichtlich wird. Nur Kandidaten mit genau einem Dokument sind
        hier waehlbar (siehe Controller.on_ambiguous_doc_selected) -
        Duplikat-/Teilzahlungs-Kandidaten (documents-Laenge 0 bzw. >1)
        haben noch keine eigene Karten-Aktion."""
        candidates = [c for c in (tx.get("candidate_docs") or []) if len(c.get("documents") or []) == 1]
        if not candidates:
            empty_lbl = QLabel(tr("Keine Kandidaten geladen"))
            empty_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
            outer.addWidget(empty_lbl)
            return

        list_frame = QFrame()
        list_frame.setStyleSheet("border: none;")
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(0, 6, 0, 0)
        list_layout.setSpacing(4)
        for candidate in candidates:
            list_layout.addWidget(self._build_candidate_row(tx, candidate))

        toggle_btn = QPushButton()
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet(self._flat_button_style())

        def _sync_toggle_text():
            expanded = list_frame.isVisible()
            arrow = "▾" if expanded else "▸"
            action = tr("ausblenden") if expanded else tr("anzeigen")
            noun = tr("Kandidat") if len(candidates) == 1 else tr("Kandidaten")
            toggle_btn.setText(f"{arrow}  {len(candidates)} {noun} {action}")

        def _toggle():
            list_frame.setVisible(not list_frame.isVisible())
            _sync_toggle_text()

        toggle_btn.clicked.connect(_toggle)
        list_frame.setVisible(len(candidates) == 1)  # bei genau 1 Vorschlag keine Klick-Huerde noetig
        _sync_toggle_text()

        outer.addWidget(toggle_btn)
        outer.addWidget(list_frame)

    def _build_candidate_row(self, tx: dict, candidate: dict) -> QPushButton:
        """Eine Zeile der Kandidatenliste: Dokumentname, Betrag, Datum,
        Differenz zur Buchung (nur bei Toleranz-Kandidaten - beim exakten
        Mehrfachtreffer ist amount_delta None). Die ganze Zeile ist
        klickbar - kein extra Bestaetigungs-Button noetig."""
        doc = candidate["documents"][0]
        name = doc.get("original_file_name") or doc.get("title") or tr("ohne Titel")
        if len(name) > 32:
            name = name[:30] + "…"
        date_text = doc["date"].strftime("%d.%m.%Y") if doc.get("date") else tr("kein Datum")
        amount_text = f"{doc['amount']:.2f} €" if doc.get("amount") is not None else "?"
        parts = [f"📄 {name}", amount_text, date_text]
        delta = candidate.get("amount_delta")
        if delta:
            parts.append(f"Δ {delta:+.2f} €")
        btn = QPushButton("   ·   ".join(parts))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
            f"border-radius: 8px; padding: 8px 10px; text-align: left; border: none; }}"
            f"QPushButton:hover {{ background-color: {COLORS['bg_card_hover']}; }}"
        )
        btn.clicked.connect(lambda _=False, t=tx, d=doc: self._on_candidate_click(t["id"], d["id"]))
        return btn

    def _on_candidate_click(self, tx_id: str, paperless_doc_id: int):
        try:
            self.controller.on_ambiguous_doc_selected(tx_id, paperless_doc_id)
        except Exception as exc:
            QMessageBox.critical(self, tr("Fehlgeschlagen"), str(exc))
            return
        self._refresh_single_transaction(tx_id)

    def _build_tag_row(self, outer: QVBoxLayout, tx: dict):
        promoted = self.controller.top_custom_tags(limit=3)
        quick_tags = BUILTIN_TAGS + promoted

        tag_line = QHBoxLayout()
        tag_line.setSpacing(6)
        for tag_name in quick_tags:
            icon = TAG_ICONS.get(tag_name, "🏷️")
            label = f"{icon} {tr(tag_name.capitalize())}" if tag_name in BUILTIN_TAGS else f"{icon} {tag_name}"
            if tag_name in TAG_COLORS:
                color = TAG_COLORS[tag_name]
            else:
                color, _bg = custom_tag_color(tag_name)
            btn = _outline_button(label, color)
            btn.clicked.connect(lambda _=False, t=tag_name: self._on_tag_click(tx["id"], t))
            tag_line.addWidget(btn)
        tag_line.addStretch()
        outer.addLayout(tag_line)

        other_tags = [t for t in self.app_state.config.get("custom_tags", {}) if t not in promoted]
        sonstiges_row = QHBoxLayout()
        combo = NoScrollComboBox()
        combo.setEditable(True)
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 6px; border: none; }}"
            f"QComboBox QLineEdit {{ background: transparent; border: none; "
            f"color: {COLORS['text_primary']}; }}"
        )
        combo.addItems([tr("Sonstiges...")] + other_tags + [tr("+ neuer Tag")])
        # setTextMargins() ist eine deterministische Qt-API, die direkt die
        # Textposition im internen Eingabefeld steuert - robuster als
        # QSS-padding, dessen Box-Model bei editierbaren Comboboxen mit dem
        # intern berechneten "Edit-Field-Rect" (inkl. Pfeil-Bereich)
        # kollidieren und die Wirkung teilweise wieder aufheben kann.
        combo.lineEdit().setTextMargins(8, 0, 0, 0)
        # Feste Mindestbreite: in einer engen Zeile (Tag-Buttons + diese
        # Combo + Anwenden-Button) konnte die Combo bei knappem Platz auf
        # wenige Pixel zusammengequetscht werden, wodurch der Text
        # ("Sonstiges...") am linken Rand abgeschnitten wirkte.
        combo.setMinimumWidth(140)
        sonstiges_row.addWidget(combo)
        apply_btn = QPushButton(tr("Anwenden"))
        apply_btn.setStyleSheet(self._flat_button_style())
        apply_btn.clicked.connect(lambda _=False, c=combo, txid=tx["id"]: self._on_sonstiges_apply(txid, c))
        sonstiges_row.addWidget(apply_btn)
        sonstiges_row.addStretch()
        outer.addLayout(sonstiges_row)

    # ------------------------------------------------------------------
    # Handler
    # ------------------------------------------------------------------
    def _on_undo_click(self, tx_id):
        self.controller.on_undo_resolution(tx_id)
        self._refresh_single_transaction(tx_id)

    def _on_tag_click(self, tx_id, tag_name):
        try:
            self.controller.on_apply_tag(tx_id, tag_name)
        except Exception as exc:
            QMessageBox.critical(self, tr("Fehlgeschlagen"), str(exc))
            return
        self._refresh_single_transaction(tx_id)
        for tx in self.app_state.missing_transactions:
            if tx.get("suggested_tag"):
                self._refresh_single_transaction(tx["id"])

    def _on_sonstiges_apply(self, tx_id, combo: QComboBox):
        value = combo.currentText().strip()
        if value in ("", tr("Sonstiges...")):
            return
        if value == tr("+ neuer Tag"):
            from PySide6.QtWidgets import QInputDialog

            new_tag, ok = QInputDialog.getText(self, tr("Sonstiges"), tr("Neuer Tag-Name:"))
            if not ok or not new_tag.strip():
                return
            value = new_tag.strip()
        value = value.lstrip("+").strip()
        if not value:
            return
        self._on_tag_click(tx_id, value)

    def _on_pick_pdf_file(self, tx_id):
        path, _ = QFileDialog.getOpenFileName(self, tr("PDF wählen"), "", "PDF (*.pdf)")
        if path:
            self._on_pdf_dropped(tx_id, path)

    def _on_pdf_dropped(self, tx_id, filepath):
        try:
            self.controller.on_pdf_drop(tx_id, filepath)
        except Exception as exc:
            QMessageBox.critical(self, tr("Upload fehlgeschlagen"), str(exc))
            return
        self._refresh_single_transaction(tx_id)

    def _on_pick_existing_doc(self, tx):
        if not self.app_state.paperless_docs_raw:
            QMessageBox.information(
                self, tr("Hinweis"),
                tr("Bitte zuerst '🔍 Mit Paperless abgleichen' klicken, um die Dokumentliste zu laden."),
            )
            return
        already_linked = {d["id"] for d in (tx.get("matched_docs") or [])}
        method = self.app_state.config["amount_detection"].get("method")
        show_value_entry = method == "custom_field"
        default_value = f"{tx['amount_abs']:.2f}"
        if show_value_entry and self._get_custom_field_data_type() == "monetary":
            default_value = f"EUR{tx['amount_abs']:.2f}"

        dlg = DocumentSearchDialog(
            self,
            self.app_state.paperless_docs_raw,
            already_linked,
            show_value_entry,
            default_value,
            lambda doc_ids, value, t=tx: self._on_docs_selected(t["id"], doc_ids, value),
            on_preview=self._view_paperless_doc,
        )
        dlg.exec()

    def _on_docs_selected(self, tx_id, doc_ids, custom_field_value):
        try:
            self.controller.on_documents_selected(tx_id, doc_ids, custom_field_value)
        except Exception as exc:
            QMessageBox.critical(self, tr("Fehlgeschlagen"), str(exc))
            return
        self._refresh_single_transaction(tx_id)

    def _on_remove_doc_click(self, tx_id, doc_id):
        self.controller.on_remove_doc(tx_id, doc_id)
        self._refresh_single_transaction(tx_id)

    def _open_pdf_viewer(self, pdf_bytes: bytes, title: str):
        dlg = PdfViewerDialog(self, pdf_bytes, title)
        dlg.exec()

    def _view_uploaded_pdf(self, tx: dict):
        pdf_bytes = tx.get("uploaded_bytes")
        if not pdf_bytes:
            return
        self._open_pdf_viewer(pdf_bytes, tx.get("uploaded_name") or tr("Beleg"))

    def _view_paperless_doc(self, doc: dict):
        """Laedt ein bereits verknuepftes Paperless-Dokument im Hintergrund
        (siehe DocDownloadWorker) und zeigt es im PDF-Viewer an - gleiches
        Freeze-Vermeidungs-Muster wie beim Abgleich (MatchWorker)."""
        if not self.app_state.client:
            return
        if getattr(self, "_doc_dl_thread", None) is not None and self._doc_dl_thread.isRunning():
            return
        title = doc.get("original_file_name") or doc.get("title") or f"#{doc['id']}"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._doc_dl_result = None
        self._doc_dl_thread = QThread()
        self._doc_dl_worker = DocDownloadWorker(self.app_state.client, doc["id"])
        self._doc_dl_worker.moveToThread(self._doc_dl_thread)
        self._doc_dl_thread.started.connect(self._doc_dl_worker.run)
        self._doc_dl_worker.finished.connect(lambda pdf_bytes, error, t=title: self._stash_doc_download(pdf_bytes, error, t))
        self._doc_dl_worker.finished.connect(self._doc_dl_thread.quit)
        # Das QPdfView/QPdfDocument fuer den Viewer erst NACH thread.finished
        # erzeugen (nicht schon im worker.finished-Slot, auch nicht nur um
        # einen QTimer.singleShot(0)-Tick verzoegert - beides reichte nicht
        # zuverlaessig aus) - thread.finished feuert erst, wenn die
        # Event-Loop des Worker-Threads tatsaechlich beendet ist. Vorher
        # QPdfView zu erzeugen fuehrte reproduzierbar (wenn auch nicht bei
        # jedem Versuch - reine Race Condition) zu "QObject::setParent:
        # Cannot set parent, new parent is in a different thread" und
        # einem Segfault kurz danach, vermutlich ein Konflikt mit QPdfViews
        # eigenem internen Rendering-Thread-Pool waehrend der
        # Worker-Thread noch nicht vollstaendig heruntergefahren ist.
        self._doc_dl_thread.finished.connect(self._on_doc_thread_finished)
        self._doc_dl_thread.start()

    def _stash_doc_download(self, pdf_bytes, error, title):
        self._doc_dl_result = (pdf_bytes, error, title)

    def _on_doc_thread_finished(self):
        QApplication.restoreOverrideCursor()
        pdf_bytes, error, title = self._doc_dl_result
        if error:
            QMessageBox.critical(self, tr("Fehlgeschlagen"), error)
            return
        self._open_pdf_viewer(pdf_bytes, title)

    def _get_custom_field_data_type(self):
        if self._custom_fields_cache is None:
            if self.app_state.client:
                try:
                    self._custom_fields_cache = self.app_state.client.get_custom_fields()
                except Exception:
                    self._custom_fields_cache = []
            else:
                self._custom_fields_cache = []
        field_id = self.app_state.config["amount_detection"].get("custom_field_id")
        for f in self._custom_fields_cache:
            if f.get("id") == field_id:
                return f.get("data_type")
        return None

    def _open_settings(self):
        dlg = SettingsDialog(self, self.app_state, self._on_settings_saved)
        dlg.exec()
        # Der Enable-Banking-Assistent (ueber "Einrichtungsassistent starten"
        # in den Settings gestartet) speichert Application-ID/Schluessel-Pfad
        # sofort selbst, unabhaengig davon, ob die Settings ueber "Speichern"
        # geschlossen werden - Sichtbarkeit des Bank-Import-Buttons deshalb
        # IMMER neu pruefen, wenn die Settings zugehen, nicht nur in
        # _on_settings_saved() (das laeuft nur bei explizitem "Speichern").
        self._refresh_bank_import_visibility()

    def _refresh_bank_import_visibility(self):
        self.bank_import_btn.setVisible(self._enable_banking_ready())

    def _on_settings_saved(self):
        # state.save_env() (in SettingsDialog._save) hat den Client bereits
        # per reload_env_and_client() neu aufgebaut - hier nur noch pruefen
        # und neu rendern.
        self._refresh_connection_status()
        self._refresh_logo()
        self.render()

    def _on_upload_csv_click(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Bank-Kontoauszug wählen"), "", "CSV-Dateien (*.csv)")
        if not path:
            return
        try:
            result = self.controller.on_csv_upload(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("CSV-Import fehlgeschlagen"), str(exc))
            return
        if result.get("account_mismatch"):
            confirm = QMessageBox.question(
                self,
                tr("Anderes Konto?"),
                result["account_mismatch"] + "\n\n" + tr("Trotzdem fortfahren und zusammenführen?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        if not result.get("mapping_ready"):
            dlg = MappingDialog(self, self.app_state.csv_columns, self._on_mapping_confirmed)
            dlg.exec()
        else:
            self.render()
            self._show_import_result(result.get("added", 0), result.get("duplicates", 0))

    def _show_import_result(self, added: int, duplicates: int):
        message = tr("{added} neue Buchung(en) hinzugefügt.", added=added)
        if duplicates:
            message += "\n" + tr("{duplicates} bereits vorhandene Buchung(en) übersprungen (Duplikat).", duplicates=duplicates)
        QMessageBox.information(self, tr("Import abgeschlossen"), message)

    def _default_bank_import_range(self) -> tuple[date, date]:
        """Vorbelegung fuer EnableBankingDateRangeDialog: erster/letzter Tag
        des aktuell in der Sidebar gewaehlten Monats (AppState.
        selected_month, Format 'YYYY-MM') - ohne Auswahl (z.B. vor dem
        ersten CSV-Import) faellt das auf den echten aktuellen
        Kalendermonat zurueck."""
        if self.app_state.selected_month:
            year, month = (int(p) for p in self.app_state.selected_month.split("-"))
        else:
            today = date.today()
            year, month = today.year, today.month
        return date(year, month, 1), _last_day_of_month(year, month)

    def _on_bank_import_click(self):
        """Ablauf: Zeitraum waehlen (vorbelegt mit dem aktuell gewaehlten
        Monat) - Land + Bank waehlen - automatischer Autorisierungs-Flow
        im Hintergrund-Thread (lokaler HTTP-Listener + redirectmeto.com,
        siehe enable_banking_client.authorize - blockiert bis zu 5 Minuten,
        daher Thread statt Direktaufruf) - Konto waehlen - Kontobewegungen
        abrufen und in die reguläre Matching-Pipeline uebernehmen."""
        eb_config = self.app_state.config.get("enable_banking") or {}
        application_id = eb_config.get("application_id")
        key_path = get_effective_enable_banking_key_path(self.app_state.config)
        redirect_url = eb_config.get("redirect_url") or ""

        default_from, default_to = self._default_bank_import_range()
        range_dialog = EnableBankingDateRangeDialog(self, default_from, default_to)
        if range_dialog.exec() != QDialog.Accepted:
            return
        self._bank_import_date_from, self._bank_import_date_to = range_dialog.selected_range()

        country, ok = QInputDialog.getText(self, tr("Land"), tr("Ländercode (z.B. AT, DE):"))
        if not ok or not country.strip():
            return
        country = country.strip().upper()

        try:
            client = EnableBankingClient(application_id=application_id, key_path=key_path)
            aspsps = client.get_aspsps(country)
        except EnableBankingError as exc:
            QMessageBox.critical(self, tr("Fehler"), str(exc))
            return

        if not aspsps:
            QMessageBox.information(
                self, tr("Keine Banken gefunden"), tr("Für {country} wurden keine Banken gefunden.", country=country)
            )
            return

        names = [a.get("name", "?") for a in aspsps]
        picker = SearchableListDialog(self, tr("Bank wählen"), names)
        if picker.exec() != QDialog.Accepted:
            return
        bank_name = picker.selected_item()
        if not bank_name:
            return

        self._bank_import_client = client
        if self.bank_import_btn:
            self.bank_import_btn.setEnabled(False)
            self.bank_import_btn.setText(f"⏳  {tr('Warte auf Bank-Login...')}")
        self._bank_thread = QThread()
        self._bank_worker = _EnableBankingAuthWorker(client, bank_name, country, redirect_url)
        self._bank_worker.moveToThread(self._bank_thread)
        self._bank_thread.started.connect(self._bank_worker.run)
        self._bank_worker.finished.connect(self._on_bank_auth_finished)
        self._bank_worker.finished.connect(self._bank_thread.quit)
        self._bank_thread.finished.connect(self._bank_thread.deleteLater)
        self._bank_thread.start()

    def _on_bank_auth_finished(self, session, error):
        """Konto waehlen, Buchungen abrufen und direkt in die Matching-
        Pipeline uebernehmen (render() danach zeigt sie wie gewohnt an)."""
        if self.bank_import_btn:
            self.bank_import_btn.setEnabled(True)
            self.bank_import_btn.setText(tr("Von Bank importieren"))

        if error:
            QMessageBox.critical(self, tr("Autorisierung fehlgeschlagen"), error)
            return
        accounts = (session or {}).get("accounts") or []
        if not accounts:
            QMessageBox.information(self, tr("Keine Konten"), tr("Keine autorisierten Konten in der Session gefunden."))
            return

        labels = [a.get("uid", str(i)) for i, a in enumerate(accounts)]
        account_uid, ok = QInputDialog.getItem(self, tr("Konto wählen"), tr("Konto:"), labels, editable=False)
        if not ok:
            return

        try:
            raw_txs = self._bank_import_client.get_transactions(
                account_uid, date_from=self._bank_import_date_from, date_to=self._bank_import_date_to
            )
        except EnableBankingError as exc:
            # Manche Banken stellen ueber die Schnittstelle nur eine
            # begrenzte Historie bereit (haeufig ~90 Tage), unabhaengig vom
            # angefragten Zeitraum - das kann sich als API-Fehler aeussern,
            # daher der Hinweis statt eines rohen Fehlertexts.
            QMessageBox.critical(
                self, tr("Fehler"),
                f"{exc}\n\n{tr('Diese Bank stellt evtl. nur einen kürzeren Zeitraum bereit als angefragt.')}",
            )
            return

        if not raw_txs:
            QMessageBox.information(self, tr("Keine Buchungen"), tr("Keine Kontobewegungen erhalten."))
            return

        df = transactions_to_dataframe(raw_txs)
        added, duplicates = self.controller.on_external_import(df, ENABLE_BANKING_MAPPING)

        eb_config = self.app_state.config.setdefault("enable_banking", {})
        eb_config["last_import_at"] = datetime.now().isoformat()
        self.app_state.save_config()

        self.render()
        self._show_import_result(added, duplicates)

    def _on_mapping_confirmed(self, date_col, amount_col, purpose_col, counterparty_col=None):
        try:
            added, duplicates = self.controller.on_mapping_confirm(date_col, amount_col, purpose_col, counterparty_col)
        except Exception as exc:
            QMessageBox.critical(self, tr("Mapping fehlgeschlagen"), str(exc))
            return
        self.render()
        self._show_import_result(added, duplicates)

    def _on_match_click(self):
        if getattr(self, "_match_thread", None) is not None and self._match_thread.isRunning():
            return
        self.match_btn.setEnabled(False)
        self.match_btn.setText(f"⏳  {tr('Gleiche ab ...')}")
        self._match_thread = QThread()
        self._match_worker = MatchWorker(self.controller)
        self._match_worker.moveToThread(self._match_thread)
        self._match_thread.started.connect(self._match_worker.run)
        self._match_worker.finished.connect(self._on_match_finished)
        self._match_worker.finished.connect(self._match_thread.quit)
        self._match_thread.start()

    def _on_match_finished(self, count, error):
        self.match_btn.setEnabled(True)
        self.match_btn.setText(self._match_btn_default_text)
        if error:
            QMessageBox.critical(self, tr("Abgleich fehlgeschlagen"), error)
            return
        self.render()
        QMessageBox.information(
            self, tr("Abgleich abgeschlossen"), tr("{count} Paperless-Dokumente geladen.", count=count)
        )

    def _on_month_selected(self, month: str):
        if not month or month == "-":
            return
        self.app_state.selected_month = month
        self._update_kpis()
        self._render_status()
        self._render_tabs()

    def _on_generate_export_click(self):
        month = self.app_state.selected_month
        if not month:
            QMessageBox.warning(self, tr("Kein Monat"), tr("Bitte zuerst einen Monat wählen."))
            return
        open_count = count_open_items(self.app_state.transactions, month)
        if open_count:
            confirm = QMessageBox.question(
                self,
                tr("Offene Posten"),
                tr(
                    "{open_count} Buchung(en) in diesem Monat sind noch ungeklärt (offen, "
                    "Mehrfach-Match oder Klärungsbedarf). Der Export ist trotzdem möglich - die "
                    "offenen Posten landen in 04_Offene_Posten.csv. Trotzdem fortfahren?",
                    open_count=open_count,
                ),
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        try:
            export_path, warnings = self.controller.on_generate_export_click(month)
        except Exception as exc:
            QMessageBox.critical(self, tr("Export fehlgeschlagen"), str(exc))
            return
        self._show_missing_document_warnings(warnings)
        self.render()  # zeigt evtl. aktualisierte Beleg-Titel/Dateinamen sofort an
        QMessageBox.information(
            self, tr("Export fertig"), tr("Ordner erstellt:\n{export_path}", export_path=export_path)
        )

    def _show_missing_document_warnings(self, warnings: list[str]):
        """Zeigt Belege an, die beim Export nicht mehr in Paperless gefunden
        wurden (siehe exporter.refresh_and_check_matched_documents) - der
        Export selbst ist trotzdem vollstaendig durchgelaufen, das ist nur
        eine Information zum Nachpruefen."""
        if not warnings:
            return
        QMessageBox.warning(
            self,
            tr("Belege nicht gefunden"),
            tr(
                "{count} zugeordnete(r) Beleg(e) konnte(n) nicht mehr in Paperless gefunden werden "
                "(vermutlich dort gelöscht) und fehlen deshalb im Export:\n\n{details}",
                count=len(warnings),
                details="\n".join(warnings),
            ),
        )

    def _on_export_fiscal_year_click(self):
        if not self.app_state.transactions:
            QMessageBox.warning(self, tr("Keine Buchungen"), tr("Bitte zuerst eine CSV laden."))
            return

        fiscal_config = self.app_state.config.get("fiscal_year", {})
        default_start_year = current_fiscal_year_start(fiscal_config)
        dlg = FiscalYearExportDialog(self, fiscal_config, default_start_year)
        if dlg.exec() != QDialog.Accepted or dlg.selected_start_year() is None:
            return
        start_year = dlg.selected_start_year()

        month_strs = get_fiscal_year_months(start_year, fiscal_config)
        total_open, months_with_open = fiscal_year_open_items_summary(self.app_state.transactions, month_strs)
        if total_open:
            confirm = QMessageBox.question(
                self,
                tr("Offene Posten"),
                tr(
                    "Es gibt noch {total_open} offene Posten über das Geschäftsjahr {year_label} verteilt, "
                    "in den Monaten: {months}. Der Export ist trotzdem möglich - die offenen Posten landen "
                    "zusätzlich in 00_Offene_Posten_Jahr.csv. Trotzdem fortfahren?",
                    total_open=total_open,
                    year_label=fiscal_year_label(start_year, fiscal_config),
                    months=", ".join(months_with_open),
                ),
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        # Laeuft im Hintergrund (siehe FiscalYearExportWorker) - 12 Monate +
        # PDF-Erzeugung koennen bei vielen Belegen mehrere Sekunden dauern,
        # der Fortschrittsdialog haelt die UI waehrenddessen ansprechbar
        # (kein Freeze) und zeigt sichtbaren Fortschritt statt eines
        # scheinbar haengenden Fensters.
        self._fiscal_export_progress = QProgressDialog(
            tr("Jahresexport wird vorbereitet ..."), "", 0, 0, self
        )
        self._fiscal_export_progress.setWindowTitle(tr("Jahresexport"))
        self._fiscal_export_progress.setWindowModality(Qt.WindowModal)
        self._fiscal_export_progress.setMinimumDuration(0)
        self._fiscal_export_progress.setCancelButton(None)  # laufender Export laesst sich nicht sauber abbrechen
        self._fiscal_export_progress.setValue(0)
        self._fiscal_export_progress.show()

        self._fiscal_export_thread = QThread()
        self._fiscal_export_worker = FiscalYearExportWorker(self.controller, start_year)
        self._fiscal_export_worker.moveToThread(self._fiscal_export_thread)
        self._fiscal_export_thread.started.connect(self._fiscal_export_worker.run)
        self._fiscal_export_worker.progress.connect(self._on_fiscal_export_progress)
        self._fiscal_export_worker.finished.connect(self._on_fiscal_export_finished)
        self._fiscal_export_worker.finished.connect(self._fiscal_export_thread.quit)
        self._fiscal_export_thread.start()

    def _on_fiscal_export_progress(self, step: int, total: int, label: str):
        self._fiscal_export_progress.setMaximum(total)
        self._fiscal_export_progress.setValue(step)
        self._fiscal_export_progress.setLabelText(label)

    def _on_fiscal_export_finished(self, export_path, warnings, error):
        self._fiscal_export_progress.close()
        if error:
            QMessageBox.critical(self, tr("Export fehlgeschlagen"), error)
            return

        self._show_missing_document_warnings(warnings or [])
        self.render()  # zeigt evtl. aktualisierte Beleg-Titel/Dateinamen sofort an

        zip_confirm = QMessageBox.question(
            self,
            tr("Jahresexport fertig"),
            tr("Jahresordner erstellt:\n{export_path}\n\nZusätzlich als ZIP speichern?", export_path=export_path),
            QMessageBox.Yes | QMessageBox.No,
        )
        if zip_confirm != QMessageBox.Yes:
            return
        zip_path, _ = QFileDialog.getSaveFileName(
            self, tr("Jahresexport-ZIP speichern"), f"{export_path.name}.zip", "ZIP-Archiv (*.zip)"
        )
        if not zip_path:
            return
        if not zip_path.lower().endswith(".zip"):
            zip_path += ".zip"
        try:
            Path(zip_path).write_bytes(zip_export_folder(export_path))
        except Exception as exc:
            QMessageBox.critical(self, tr("ZIP fehlgeschlagen"), str(exc))
            return
        QMessageBox.information(self, tr("ZIP gespeichert"), tr("ZIP gespeichert: {zip_path}", zip_path=zip_path))

    def _refresh_connection_status(self):
        if not self.app_state.client:
            self._paperless_connected = False
            self._render_status()
            return
        self._paperless_checking = True
        self._render_status()
        self._thread = QThread()
        self._worker = ConnectionCheckWorker(self.app_state.client)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_connection_checked)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _on_connection_checked(self, connected: bool):
        self._paperless_checking = False
        self._paperless_connected = connected
        self._render_status()

    def closeEvent(self, event):
        # Ein noch laufender QThread (Verbindungscheck im Hintergrund) darf
        # beim Prozessende nicht einfach "verschwinden" - das zerstoert ein
        # aktives Thread-Objekt, was in Qt zu einem harten Absturz beim
        # Interpreter-Shutdown fuehren kann (STATUS_STACK_BUFFER_OVERRUN
        # beobachtet). quit()+wait() sorgt fuer ein sauberes Ende zuerst.
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.isRunning():
            thread.quit()
            # quit() stoppt nur die Event-Loop, unterbricht aber NICHT einen
            # noch laufenden blockierenden Netzwerk-Call im Worker (siehe
            # paperless_client.test_connection(), timeout=10) - daher lang
            # genug warten, dass der Call auf jeden Fall selbst durchlaeuft,
            # statt das Thread-Objekt waehrend eines aktiven Calls zu
            # zerstoeren (fuehrt sonst zum Absturz beim Prozessende).
            thread.wait(11000)
        match_thread = getattr(self, "_match_thread", None)
        if match_thread is not None and match_thread.isRunning():
            # Gleiches Risiko wie oben, aber der Abgleich kann ueber mehrere
            # Seiten je einen eigenen 30s-Timeout durchlaufen (siehe
            # paperless_client.get_all_documents) - entsprechend grosszuegig
            # warten, bevor der Thread zerstoert wird.
            match_thread.quit()
            match_thread.wait(35000)
        doc_dl_thread = getattr(self, "_doc_dl_thread", None)
        if doc_dl_thread is not None and doc_dl_thread.isRunning():
            doc_dl_thread.quit()
            doc_dl_thread.wait(15000)
        self.app_state.persist_session()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = DesktopAppQt()
    start_size = (1360, 860)
    window.resize(*start_size)
    screen = app.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        window.move(geo.center() - window.rect().center())
    window.show()
    # Ein fruehere Versuch, eine konkrete Startgroesse per resize() zu setzen
    # (vor UND nach show()), wurde auf dem Zielsystem von einer verzoegerten
    # Layout-Passage wieder auf eine kleinere Groesse zurueckgesetzt - deshalb
    # zwischenzeitlich showMaximized() statt einer festen Groesse. Erneutes
    # resize() kurz NACH dem ersten show()-Zyklus (statt nur davor/direkt
    # danach) uebersteht diese verzoegerte Passage, da sie zu dem Zeitpunkt
    # bereits gelaufen ist.
    QTimer.singleShot(50, lambda: window.resize(*start_size))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
