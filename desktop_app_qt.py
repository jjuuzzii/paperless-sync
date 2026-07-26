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

Start (Quellcode):  python desktop_app_qt.py
"""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QIcon, QFontMetrics, QPixmap
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
)

from desktop_state import AppState
from desktop_controller import Controller, BUILTIN_TAGS, TAG_ICONS
from dialogs_qt import MappingDialog, SettingsDialog, DocumentSearchDialog
from config_manager import get_resource_dir
from theme_qt import COLORS, TAG_COLORS, TAG_COLORS_DIM, custom_tag_color, font as qfont
from i18n import tr, set_language

# Gleiche Bereinigung wie in desktop_app.py: IBAN/BIC sind im
# Verwendungszweck nie hilfreich, nur fuer die Anzeige entfernt.
_IBAN_RE = re.compile(r"IBAN:?\s*[A-Z]{2}\d{2}[A-Z0-9]{10,30}", re.IGNORECASE)
_BIC_RE = re.compile(r"BIC:?\s*[A-Z0-9]{8,11}", re.IGNORECASE)


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
        # Ein hochgeladenes Firmenlogo (siehe SettingsDialog) ersetzt das
        # mitgelieferte Standard-Icon als Fenster-/Taskleisten-Symbol, falls
        # vorhanden - sonst Fallback auf icon.ico.
        custom_path = self._custom_icon_path()
        icon_path = custom_path if custom_path and custom_path.exists() else get_resource_dir() / "icon.ico"
        if icon_path.exists():
            # QIcon liest ALLE eingebetteten Groessen aus der .ico selbst
            # und waehlt je nach Kontext (Titelleiste/Taskleiste) die
            # passende - anders als bei Tk ist dafuer keine manuelle
            # WM_SETICON-Behandlung noetig (siehe icon_utils.py).
            self.setWindowIcon(QIcon(str(icon_path)))

    def _custom_icon_path(self):
        name = self.app_state.config.get("company_icon_path")
        return (self.app_state.base_dir / name) if name else None

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
        title_col = QVBoxLayout()
        title = QLabel("📎 Paperless Sync")
        title.setFont(qfont(16, bold=True))
        title.setStyleSheet(f"color: {COLORS['text_primary']}; border: none;")
        title_col.addWidget(title)

        company = QLabel(self.app_state.env.get("COMPANY_NAME") or "")
        company.setStyleSheet(f"color: {COLORS['text_muted']}; border: none;")
        title_col.addWidget(company)
        title_row.addLayout(title_col, stretch=1)

        # Firmenlogo (siehe SettingsDialog-Upload) oben rechts in der
        # Sidebar, sofern eines hinterlegt ist - sonst bleibt die Zeile
        # einspaltig (kein Platzhalter-Icon, um bei fehlendem Logo keine
        # unnoetige Leerflaeche zu erzeugen).
        logo_path = self._custom_icon_path()
        if logo_path and logo_path.exists():
            logo_lbl = QLabel()
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                logo_lbl.setPixmap(
                    pixmap.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                logo_lbl.setAlignment(Qt.AlignTop | Qt.AlignRight)
                title_row.addWidget(logo_lbl, alignment=Qt.AlignTop)
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

        upload_btn = QPushButton(tr("Datei auswaehlen"))
        upload_btn.setStyleSheet(self._flat_button_style())
        upload_btn.clicked.connect(self._on_upload_csv_click)
        layout.addWidget(upload_btn)
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
        self.month_combo = QComboBox()
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
        return tr("Keine Datei gewaehlt")

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
        main_layout.addLayout(kpi_row)
        main_layout.addSpacing(14)

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

        main_layout.addWidget(self.tabs)
        root_layout.addWidget(main)

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

    def _render_status(self):
        if not self.app_state.client:
            self.status_paperless.set_color(COLORS["text_muted"])
            self.status_paperless.set_text(tr("Paperless: nicht konfiguriert"))
        elif self._paperless_checking:
            self.status_paperless.set_color(COLORS["text_muted"])
            self.status_paperless.set_text(tr("Paperless: wird geprueft..."))
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

        success_txs = self.app_state.success_transactions
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
            self.success_layout.addWidget(self._placeholder_label(tr("Noch keine zugeordneten Belege.")))

        for tx in self.app_state.unclear_transactions:
            w = self._render_action_card(tx, is_unclear=True)
            self.action_layout.addWidget(w)
            self._card_widgets[tx["id"]] = w

        missing_txs = self.app_state.missing_transactions
        visible_missing = missing_txs[: self._action_reveal]
        for tx in visible_missing:
            w = self._render_action_card(tx, is_unclear=False)
            self.action_layout.addWidget(w)
            self._card_widgets[tx["id"]] = w
        if len(missing_txs) > len(visible_missing):
            self.action_layout.addWidget(
                self._build_load_more_button(len(missing_txs) - len(visible_missing), "action")
            )
        elif not missing_txs and not self.app_state.unclear_transactions:
            self.action_layout.addWidget(self._placeholder_label(tr("Alles zugeordnet! 🎉")))

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
            if tx["status"] in ("matched", "tagged", "uploaded"):
                w = self._render_success_card(tx)
                self.success_layout.insertWidget(max(0, self.success_layout.count() - 1), w)
            elif tx["status"] == "unclear":
                w = self._render_action_card(tx, is_unclear=True)
                self.action_layout.insertWidget(0, w)
            else:
                w = self._render_action_card(tx, is_unclear=False)
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
        if tx["status"] == "matched":
            doc_count = len(tx.get("matched_docs") or [])
            label = f"🔗 {tr('Automatisch zugeordnet')}" if doc_count <= 1 else f"🔗 {tr('{doc_count} Belege verknuepft', doc_count=doc_count)}"
            bottom.addWidget(self._pill(label, COLORS["green"], COLORS["green_dim"]))
        elif tx["status"] == "uploaded":
            bottom.addWidget(self._pill(f"📤 {tr('Hochgeladen')}", COLORS["blue"], COLORS["blue_dim"]))
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
        undo_btn = QPushButton(f"↩ {tr('Rueckgaengig')}")
        undo_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['text_muted']}; border: none; }}"
            f"QPushButton:hover {{ color: {COLORS['text_primary']}; }}"
        )
        undo_btn.clicked.connect(lambda _=False, t=tx: self._on_undo_click(t["id"]))
        bottom.addWidget(undo_btn)
        outer.addLayout(bottom)
        if tx["status"] == "matched":
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

        add_btn = QPushButton(f"+ {tr('Beleg')}")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLORS['blue']}; border: none; font-size: 8pt; }}"
            f"QPushButton:hover {{ color: #4a76d6; }}"
        )
        add_btn.clicked.connect(lambda _=False, t=tx: self._on_pick_existing_doc(t))
        row.addWidget(add_btn)
        row.addStretch()
        outer.addLayout(row)

    def _render_action_card(self, tx: dict, is_unclear: bool) -> QFrame:
        border_color = COLORS["amber"] if is_unclear else COLORS["red_border"]
        card = CardFrame(COLORS["bg_card"], border_color, border_width=2)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        if is_unclear:
            badge = QLabel(tr("MEHRFACH-MATCH"))
            badge.setStyleSheet(
                f"background-color: {COLORS['amber']}; color: #1e1e2e; border-radius: 8px; "
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

        if is_unclear:
            warn = QFrame()
            warn.setStyleSheet(f"background-color: {COLORS['amber_dim']}; border-radius: 10px; border: none;")
            warn_layout = QHBoxLayout(warn)
            warn_lbl = QLabel(f"⚠️  {tr('Betrag tritt mehrfach auf - bitte manuell zuordnen.')}")
            warn_lbl.setStyleSheet(f"color: {COLORS['amber']}; font-weight: bold; border: none;")
            _make_wrap_safe(warn_lbl)
            warn_layout.addWidget(warn_lbl)
            outer.addWidget(warn)
            self._build_ambiguous_picker(outer, tx)
            self._build_tag_row(outer, tx)
            return card

        if tx.get("suggested_tag"):
            sugg = tx["suggested_tag"]
            icon = TAG_ICONS.get(sugg, "🏷️")
            label = tr(sugg.capitalize()) if sugg in BUILTIN_TAGS else sugg
            sugg_row = QFrame()
            sugg_row.setStyleSheet(f"background-color: {COLORS['blue_dim']}; border-radius: 10px; border: none;")
            sugg_layout = QHBoxLayout(sugg_row)
            sugg_lbl = QLabel(
                f"💡  {tr('Vorschlag: {icon} {label} - aehnliche Buchung, bereits so getaggt', icon=icon, label=label)}"
            )
            sugg_lbl.setStyleSheet(f"color: {COLORS['blue']}; font-weight: bold; border: none;")
            _make_wrap_safe(sugg_lbl)
            sugg_layout.addWidget(sugg_lbl, stretch=1)
            apply_btn = QPushButton(f"✓ {tr('Uebernehmen')}")
            apply_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['blue']}; color: white; border-radius: 10px; "
                f"padding: 6px 14px; }} QPushButton:hover {{ background-color: #4a76d6; }}"
            )
            apply_btn.clicked.connect(lambda _=False, t=tx, s=sugg: self._on_tag_click(t["id"], s))
            sugg_layout.addWidget(apply_btn)
            outer.addWidget(sugg_row)

        # "Aus Paperless waehlen" ist der haeufige Weg (Beleg existiert
        # schon dort), die Drop-Zone wird kaum gebraucht - beide daher
        # nebeneinander in einer schmalen Zeile statt der Drop-Zone einen
        # eigenen grossen, dominanten Block zu geben.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        paperless_btn = _outline_button(f"📂  {tr('Aus Paperless waehlen')}", COLORS["blue"])
        pick_row.addWidget(paperless_btn)
        paperless_btn.clicked.connect(lambda _=False, t=tx: self._on_pick_existing_doc(t))

        drop_zone = DropZone(lambda t=tx: self._on_pick_pdf_file(t["id"]))
        drop_zone.file_dropped.connect(lambda path, t=tx: self._on_pdf_dropped(t["id"], path))
        pick_row.addWidget(drop_zone, stretch=1)
        outer.addLayout(pick_row)

        self._build_tag_row(outer, tx)
        return card

    def _build_ambiguous_picker(self, outer: QVBoxLayout, tx: dict):
        candidates = tx.get("candidate_docs") or []
        row = QHBoxLayout()
        combo = QComboBox()
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {COLORS['bg_input']}; color: {COLORS['text_primary']}; "
            f"border-radius: 10px; padding: 8px; border: none; }}"
        )
        # Ohne Begrenzung richtet Qt die Combobox-Breite am LAENGSTEN Eintrag
        # aus (AdjustToContentsOnFirstShow) - bei langen Paperless-Titeln
        # kann das die ganze Karte in die Breite zwingen. Mit fester
        # Content-Laenge zeigt die geschlossene Box stattdessen "...", die
        # Dropdown-Liste selbst bleibt unveraendert vollstaendig lesbar.
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(20)
        labels = [self._candidate_label(d) for d in candidates]
        label_to_doc = {label: doc for label, doc in zip(labels, candidates)}
        combo.addItems(labels or [tr("Keine Kandidaten geladen")])
        row.addWidget(combo, stretch=1)

        def _confirm():
            doc = label_to_doc.get(combo.currentText())
            if doc is None:
                return
            try:
                self.controller.on_ambiguous_doc_selected(tx["id"], doc["id"])
            except Exception as exc:
                QMessageBox.critical(self, tr("Fehlgeschlagen"), str(exc))
                return
            self._refresh_single_transaction(tx["id"])

        confirm_btn = QPushButton(tr("Zuordnen"))
        confirm_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['amber']}; color: #1e1e2e; border-radius: 10px; "
            f"padding: 8px 16px; font-weight: bold; }}"
        )
        confirm_btn.clicked.connect(_confirm)
        row.addWidget(confirm_btn)
        outer.addLayout(row)

    @staticmethod
    def _candidate_label(doc: dict) -> str:
        label = f"#{doc['id']} - {doc['title'] or doc.get('original_file_name') or tr('ohne Titel')}"
        if doc.get("correspondent_name"):
            label += f" · {doc['correspondent_name']}"
        label += f" · {doc['date'].strftime('%d.%m.%Y') if doc.get('date') else tr('kein Datum')}"
        return label

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
        combo = QComboBox()
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
        path, _ = QFileDialog.getOpenFileName(self, tr("PDF waehlen"), "", "PDF (*.pdf)")
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

    def _on_settings_saved(self):
        # state.save_env() (in SettingsDialog._save) hat den Client bereits
        # per reload_env_and_client() neu aufgebaut - hier nur noch pruefen
        # und neu rendern.
        self._refresh_connection_status()
        self.render()

    def _on_upload_csv_click(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Bank-Kontoauszug waehlen"), "", "CSV-Dateien (*.csv)")
        if not path:
            return
        try:
            mapping_ready = self.controller.on_csv_upload(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("CSV-Import fehlgeschlagen"), str(exc))
            return
        if not mapping_ready:
            dlg = MappingDialog(self, self.app_state.csv_columns, self._on_mapping_confirmed)
            dlg.exec()
        else:
            self.render()

    def _on_mapping_confirmed(self, date_col, amount_col, purpose_col, counterparty_col=None):
        try:
            self.controller.on_mapping_confirm(date_col, amount_col, purpose_col, counterparty_col)
        except Exception as exc:
            QMessageBox.critical(self, tr("Mapping fehlgeschlagen"), str(exc))
            return
        self.render()

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
            QMessageBox.warning(self, tr("Kein Monat"), tr("Bitte zuerst einen Monat waehlen."))
            return
        try:
            export_path = self.controller.on_generate_export_click(month)
        except Exception as exc:
            QMessageBox.critical(self, tr("Export fehlgeschlagen"), str(exc))
            return
        QMessageBox.information(
            self, tr("Export fertig"), tr("Ordner erstellt:\n{export_path}", export_path=export_path)
        )

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
