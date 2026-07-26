"""Paperless Sync - Desktop-UI (CustomTkinter + natives Drag & Drop).

ARCHIVIERT: durch die Qt-Oberflaeche abgeloest, nicht mehr aktiv gepflegt -
siehe legacy/README.md. Aktuelle Oberflaeche: run_app.py im Repo-Root
(UI-Quellcode in src/paperless_sync/ui_qt/).

Reine UI-Schicht: alle Geschaeftslogik lebt in desktop_controller.py /
desktop_state.py und den bestehenden Backend-Modulen (matcher.py,
paperless_client.py, exporter.py, csv_utils.py, config_manager.py,
session_store.py). Diese Datei ruft nur Controller-Methoden auf und
rendert danach neu.

Start (Quellcode):  python legacy/desktop_app.py
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import tkinterdnd2

# icon_utils liegt (noch) im Repo-Root, dieses Skript aber in legacy/ -
# Root-Verzeichnis muss deshalb explizit auf den Suchpfad (dialogs/
# ctk_fixes/theme bleiben Geschwisterdateien in legacy/ und brauchen das
# nicht). src/ zusaetzlich fuer AppState/Controller (jetzt
# paperless_sync.state.*) und die transitiv genutzten
# paperless_sync.core.*-Module.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "src"))

from paperless_sync.state.desktop_state import AppState
from paperless_sync.state.desktop_controller import Controller, BUILTIN_TAGS, TAG_ICONS
from dialogs import SettingsDialog, MappingDialog, DocumentSearchDialog
from icon_utils import apply_window_icon
from ctk_fixes import LeakSafeScrollableFrame
from theme import (
    COLORS,
    TAG_COLORS,
    DEFAULT_TAG_COLOR,
    custom_tag_color as _custom_tag_color,
    FONT_TITLE,
    FONT_SUBTITLE,
    FONT_SMALL,
    FONT_PURPOSE,
    FONT_PURPOSE_SECONDARY,
    FONT_AMOUNT,
    FONT_KPI_NUMBER,
    FONT_KPI_TITLE,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# IBAN/BIC im Verwendungszweck sind fuer die Beleg-Zuordnung nie hilfreich
# (nur Rauschen) - werden nur fuer die Kartenanzeige entfernt, die
# zugrundeliegenden tx["purpose"]-Rohdaten (Matching/Export/Tag-Lernen)
# bleiben unveraendert.
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


def _parse_dnd_files(data: str) -> list[str]:
    """tkinterdnd2 liefert abgelegte Pfade als einen String, wobei Pfade mit
    Leerzeichen in {geschweifte Klammern} eingefasst werden."""
    files: list[str] = []
    buf = ""
    in_brace = False
    for ch in data:
        if ch == "{":
            in_brace = True
            continue
        if ch == "}":
            in_brace = False
            files.append(buf)
            buf = ""
            continue
        if ch == " " and not in_brace:
            if buf:
                files.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        files.append(buf)
    return files


class DesktopApp(ctk.CTk, tkinterdnd2.TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = tkinterdnd2.TkinterDnD._require(self)

        self.title("Paperless Sync")
        self.geometry("1360x860")
        self.minsize(1080, 680)
        self.configure(fg_color=COLORS["bg_main"])
        self._set_app_icon()

        self.app_state = AppState()
        self.controller = Controller(self.app_state)
        self._custom_fields_cache: list[dict] | None = None
        self._paperless_connected = False
        self._paperless_checking = False
        self._card_widgets: dict[str, ctk.CTkFrame] = {}  # tx_id -> aktuell angezeigtes Karten-/Zeilen-Widget
        self._success_placeholder = None
        self._action_placeholder = None
        self._success_load_more_btn = None
        self._action_load_more_btn = None

        self._build_sidebar()
        self._build_main_area()

        if self.app_state.is_configured():
            self._refresh_connection_status()
        else:
            self.after(200, self._open_settings)

        months = self.app_state.months
        self.app_state.selected_month = months[-1] if months else None

        self.render()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_app_icon(self):
        """Siehe icon_utils.apply_window_icon - gilt nur fuer DIESES
        (Haupt-)Fenster; jeder Dialog (Einstellungen, CSV-Mapping,
        Dokumentsuche, 'Neuer Tag') muss das separat fuer sich aufrufen."""
        apply_window_icon(self)

    # ------------------------------------------------------------------
    # kleine Stil-Helfer
    # ------------------------------------------------------------------
    def _pill(self, parent, text: str, color: str, bg: str):
        pill = ctk.CTkFrame(parent, corner_radius=12, fg_color=bg)
        ctk.CTkLabel(pill, text=text, text_color=color, font=("", 11, "bold")).pack(padx=12, pady=4)
        return pill

    def _build_purpose_block(self, parent, tx, wraplength: int | None = None):
        """Ist eine Absender/Empfaenger-Spalte gemappt (tx['counterparty']),
        wird die als fette Hauptzeile gezeigt und der (bereinigte)
        Verwendungszweck als kleinere Zweitzeile darunter - sonst wie bisher
        nur der Verwendungszweck fett."""
        noise_terms = self.app_state.config.get("purpose_noise_terms", [])
        purpose_text = _display_purpose(tx["purpose"], noise_terms)
        label_kwargs = {"wraplength": wraplength, "justify": "left"} if wraplength else {}

        counterparty = (tx.get("counterparty") or "").strip()
        if counterparty:
            ctk.CTkLabel(
                parent, text=counterparty, font=FONT_PURPOSE, text_color=COLORS["text_primary"], anchor="w", **label_kwargs
            ).pack(anchor="w", pady=(4, 0))
            ctk.CTkLabel(
                parent, text=purpose_text, font=FONT_PURPOSE_SECONDARY, text_color=COLORS["text_muted"], anchor="w",
                **label_kwargs,
            ).pack(anchor="w", pady=(2, 0))
        else:
            ctk.CTkLabel(
                parent, text=purpose_text, font=FONT_PURPOSE, text_color=COLORS["text_primary"], anchor="w", **label_kwargs
            ).pack(anchor="w", pady=(4, 0))

    def _outline_button(self, parent, text, color, command, width=None):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width or 0,
            corner_radius=18,
            fg_color="transparent",
            border_width=2,
            border_color=color,
            text_color=color,
            hover_color=COLORS["bg_card_hover"],
            font=("", 12, "bold"),
        )

    # ------------------------------------------------------------------
    # Aufbau: Sidebar
    # ------------------------------------------------------------------
    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color=COLORS["bg_sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(sidebar, text="📎 Paperless Sync", font=FONT_TITLE, text_color=COLORS["text_primary"]).pack(
            pady=(28, 2), padx=24, anchor="w"
        )
        ctk.CTkLabel(sidebar, text=self.app_state.env.get("COMPANY_NAME") or "", text_color=COLORS["text_muted"]).pack(
            padx=24, anchor="w", pady=(0, 24)
        )

        self._outline_button(sidebar, "⚙️  Einstellungen", COLORS["text_muted"], self._open_settings).pack(
            fill="x", padx=24, pady=(0, 24)
        )

        ctk.CTkLabel(sidebar, text="1 · CSV-UPLOAD", font=FONT_SUBTITLE, text_color=COLORS["text_muted"]).pack(
            padx=24, anchor="w"
        )
        self.csv_name_label = ctk.CTkLabel(
            sidebar,
            text=self._csv_label_text(),
            text_color=COLORS["text_muted"],
            wraplength=240,
            justify="left",
            font=FONT_SMALL,
        )
        self.csv_name_label.pack(padx=24, anchor="w", pady=(4, 10))
        ctk.CTkButton(
            sidebar,
            text="Datei auswaehlen",
            command=self._on_upload_csv_click,
            corner_radius=10,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
        ).pack(fill="x", padx=24, pady=(0, 12))

        ctk.CTkButton(
            sidebar,
            text="🔍  Mit Paperless abgleichen",
            command=self._on_match_click,
            corner_radius=10,
            fg_color=COLORS["blue_dim"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["blue"],
        ).pack(fill="x", padx=24, pady=(0, 24))

        ctk.CTkLabel(sidebar, text="2 · MONAT", font=FONT_SUBTITLE, text_color=COLORS["text_muted"]).pack(
            padx=24, anchor="w"
        )
        self.month_combo = ctk.CTkComboBox(
            sidebar,
            values=["-"],
            command=self._on_month_selected,
            corner_radius=10,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["bg_card_hover"],
            border_width=0,
        )
        self.month_combo.pack(fill="x", padx=24, pady=(4, 24))

        status_frame = ctk.CTkFrame(sidebar, fg_color=COLORS["bg_card"], corner_radius=10)
        status_frame.pack(fill="x", padx=24, pady=(0, 16))
        self.status_paperless = ctk.CTkLabel(
            status_frame, text="⚪ Paperless: nicht konfiguriert", anchor="w", font=FONT_SMALL
        )
        self.status_paperless.pack(padx=14, pady=(12, 4), anchor="w")
        self.status_export = ctk.CTkLabel(
            status_frame, text="🟡 Exportordner: noch keine Auswahl", anchor="w", font=FONT_SMALL
        )
        self.status_export.pack(padx=14, pady=(0, 12), anchor="w")

        ctk.CTkButton(
            sidebar,
            text="ORDNER JETZT GENERIEREN",
            fg_color="#e0402a",
            hover_color="#b8331f",
            text_color="white",
            height=56,
            corner_radius=14,
            font=("", 15, "bold"),
            command=self._on_generate_export_click,
        ).pack(fill="x", padx=24, pady=(8, 28), side="bottom")

    def _csv_label_text(self) -> str:
        return self.app_state.csv_signature or "Keine Datei gewaehlt"

    # ------------------------------------------------------------------
    # Aufbau: Hauptbereich (KPIs + Tabs)
    # ------------------------------------------------------------------
    def _build_main_area(self):
        main = ctk.CTkFrame(self, fg_color=COLORS["bg_main"])
        main.pack(side="left", fill="both", expand=True, padx=28, pady=28)

        kpi_row = ctk.CTkFrame(main, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, 24))
        self.kpi_success = self._build_kpi_card(kpi_row, "✅  ZUGEORDNETE BELEGE", COLORS["green"])
        self.kpi_action = self._build_kpi_card(kpi_row, "⚠️  AKTION ERFORDERLICH", COLORS["red"])
        self.kpi_multi = self._build_kpi_card(kpi_row, "🟡  MEHRFACH-MATCH", COLORS["amber"])

        self.tabview = ctk.CTkTabview(
            main,
            fg_color=COLORS["bg_kpi"],
            segmented_button_fg_color=COLORS["bg_kpi"],
            segmented_button_selected_color=COLORS["blue_dim"],
            segmented_button_selected_hover_color=COLORS["blue_dim"],
            segmented_button_unselected_color=COLORS["bg_kpi"],
            text_color=COLORS["text_primary"],
            corner_radius=16,
        )
        self.tabview.pack(fill="both", expand=True)
        self.success_tab = self.tabview.add("✅  Erfolgreich")
        self.action_tab = self.tabview.add("⚠️  Unklar / Fehlt")

        # fg_color bewusst opak (nicht "transparent") und identisch zum
        # Tabview-Hintergrund: bei "transparent" wird beim Scrollen des
        # internen Canvas kein Hintergrund neu gezeichnet, was auf Windows
        # zu Schmier-/Geisterbildern fuehrt (bekannter CTkScrollableFrame-Bug,
        # siehe TomSchimansky/CustomTkinter#1510). Ein opaker fg_color zwingt
        # jeden Scroll-Schritt, die Flaeche sauber neu zu uebermalen.
        self.success_frame = LeakSafeScrollableFrame(self.success_tab, label_text="", fg_color=COLORS["bg_kpi"])
        self.success_frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.action_frame = LeakSafeScrollableFrame(self.action_tab, label_text="", fg_color=COLORS["bg_kpi"])
        self.action_frame.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_kpi_card(self, parent, title, value_color):
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_kpi"], corner_radius=16)
        card.pack(side="left", fill="both", expand=True, padx=10)
        ctk.CTkLabel(card, text=title, text_color=COLORS["text_muted"], font=FONT_KPI_TITLE).pack(
            pady=(22, 2), padx=24, anchor="w"
        )
        number_label = ctk.CTkLabel(card, text="0", font=FONT_KPI_NUMBER, text_color=value_color)
        number_label.pack(pady=(0, 22), padx=24, anchor="w")
        return number_label

    # ------------------------------------------------------------------
    # Render: voller Rebuild (CSV-Upload, Mapping, Paperless-Abgleich - alles,
    # was potenziell VIELE Transaktionen gleichzeitig aendert). Fuer
    # Einzel-Aktionen (Tag setzen, PDF hochladen, Dokument waehlen) wird
    # stattdessen _refresh_single_transaction() genutzt: das aktualisiert nur
    # das eine betroffene Karten-Widget statt alle neu aufzubauen - bei
    # vielen Transaktionen (50-100+) macht das den Unterschied zwischen
    # "spuerbar langsam bei jedem Klick" und "sofort".
    # ------------------------------------------------------------------
    def render(self):
        self.csv_name_label.configure(text=self._csv_label_text())
        self._update_kpis()
        self._render_status()
        self._render_month_combo()
        self._render_tabs()

    def _update_kpis(self):
        self.kpi_success.configure(text=str(len(self.app_state.success_transactions)))
        self.kpi_action.configure(text=str(len(self.app_state.missing_transactions)))
        self.kpi_multi.configure(text=str(len(self.app_state.unclear_transactions)))

    def _render_status(self):
        if not self.app_state.client:
            self.status_paperless.configure(text="⚪ Paperless: nicht konfiguriert")
        elif self._paperless_checking:
            self.status_paperless.configure(text="⚪ Paperless: wird geprueft...")
        elif self._paperless_connected:
            self.status_paperless.configure(text="🟢 Paperless: Verbunden")
        else:
            self.status_paperless.configure(text="🔴 Paperless: nicht erreichbar")

        ready = bool(self.app_state.transactions) and bool(self.app_state.selected_month)
        self.status_export.configure(text="🟢 Exportordner: Bereit" if ready else "🟡 Exportordner: noch keine Auswahl")

    def _render_month_combo(self):
        months = self.app_state.months
        self.month_combo.configure(values=months or ["-"])
        if self.app_state.selected_month not in months:
            self.app_state.selected_month = months[-1] if months else None
        self.month_combo.set(self.app_state.selected_month or "-")

    def _render_tabs(self):
        # Monatswechsel (egal ob per Dropdown oder durch einen neuen
        # CSV-Import) faengt wieder begrenzt an, statt einen zuvor per
        # "weitere anzeigen" hochgezaehlten Stand aus einem ANDEREN Monat
        # zu uebernehmen.
        if getattr(self, "_last_rendered_month", "__unset__") != self.app_state.selected_month:
            self.app_state.success_reveal_count = self.app_state.DEFAULT_REVEAL_COUNT
            self.app_state.action_reveal_count = self.app_state.DEFAULT_REVEAL_COUNT
            self._last_rendered_month = self.app_state.selected_month

        for child in self.success_frame.winfo_children():
            child.destroy()
        for child in self.action_frame.winfo_children():
            child.destroy()
        self._card_widgets.clear()
        self._success_placeholder = None
        self._action_placeholder = None
        self._success_load_more_btn = None
        self._action_load_more_btn = None

        # Jede Karte besteht aus 20-50+ Widgets (siehe AppState.DEFAULT_
        # REVEAL_COUNT) - bei vielen offenen Buchungen wird nur ein
        # begrenzter Anfangsstand gerendert, der Rest kommt erst per
        # "weitere anzeigen" dazu. Haelt das Scrollen auch bei sehr grossen
        # CSVs fluessig, ohne dass irgendwo Buchungen "verschwinden" -
        # Matching/Export/KPI-Zaehler sehen weiterhin ALLE Transaktionen.
        success_txs = self.app_state.success_transactions
        visible_success = success_txs[: self.app_state.success_reveal_count]
        for tx in visible_success:
            self._card_widgets[tx["id"]] = self._render_success_card(self.success_frame, tx)
        if len(success_txs) > len(visible_success):
            self._success_load_more_btn = self._build_load_more_button(
                self.success_frame, len(success_txs) - len(visible_success), "success"
            )

        for tx in self.app_state.unclear_transactions:
            self._card_widgets[tx["id"]] = self._render_action_card(self.action_frame, tx, is_unclear=True)

        missing_txs = self.app_state.missing_transactions
        visible_missing = missing_txs[: self.app_state.action_reveal_count]
        for tx in visible_missing:
            self._card_widgets[tx["id"]] = self._render_action_card(self.action_frame, tx, is_unclear=False)
        if len(missing_txs) > len(visible_missing):
            self._action_load_more_btn = self._build_load_more_button(
                self.action_frame, len(missing_txs) - len(visible_missing), "action"
            )

        self._sync_empty_state_placeholders()

    def _build_load_more_button(self, parent, remaining: int, which: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 16))
        ctk.CTkButton(
            row,
            text=f"↓ {remaining} weitere anzeigen",
            corner_radius=10,
            fg_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            command=lambda: self._on_load_more(which),
        ).pack()
        return row

    def _on_load_more(self, which: str):
        if which == "success":
            self.app_state.success_reveal_count += self.app_state.DEFAULT_REVEAL_COUNT
        else:
            self.app_state.action_reveal_count += self.app_state.DEFAULT_REVEAL_COUNT
        self._render_tabs()

    def _sync_empty_state_placeholders(self):
        """Zeigt/versteckt die 'Noch keine Belege'/'Alles zugeordnet'-Hinweise,
        je nachdem ob die jeweilige Liste gerade leer ist."""
        if not self.app_state.success_transactions and self._success_placeholder is None:
            self._success_placeholder = ctk.CTkLabel(
                self.success_frame, text="Noch keine zugeordneten Belege.", text_color=COLORS["text_muted"]
            )
            self._success_placeholder.pack(pady=30)
        elif self.app_state.success_transactions and self._success_placeholder is not None:
            self._success_placeholder.destroy()
            self._success_placeholder = None

        action_empty = not self.app_state.missing_transactions and not self.app_state.unclear_transactions
        if action_empty and self._action_placeholder is None:
            self._action_placeholder = ctk.CTkLabel(
                self.action_frame, text="Alles zugeordnet! 🎉", text_color=COLORS["text_muted"]
            )
            self._action_placeholder.pack(pady=30)
        elif not action_empty and self._action_placeholder is not None:
            self._action_placeholder.destroy()
            self._action_placeholder = None

    def _refresh_single_transaction(self, tx_id: str):
        """Aktualisiert NUR das Widget der uebergebenen Transaktion (statt
        beide Tabs komplett neu aufzubauen) - wird nach Einzel-Aktionen wie
        Tag setzen, PDF hochladen oder Dokument waehlen aufgerufen."""
        old_widget = self._card_widgets.pop(tx_id, None)
        if old_widget is not None:
            old_widget.destroy()

        tx = next((t for t in self.app_state.visible_transactions if t["id"] == tx_id), None)
        if tx is not None:
            if tx["status"] in ("matched", "tagged", "uploaded"):
                widget = self._render_success_card(self.success_frame, tx)
                load_more = self._success_load_more_btn
            elif tx["status"] == "unclear":
                widget = self._render_action_card(self.action_frame, tx, is_unclear=True)
                load_more = self._action_load_more_btn
            else:
                widget = self._render_action_card(self.action_frame, tx, is_unclear=False)
                load_more = self._action_load_more_btn
            self._card_widgets[tx_id] = widget
            # pack() haengt neue Karten standardmaessig ans Ende an - der
            # "weitere anzeigen"-Button (falls vorhanden) muss aber immer
            # UNTER allen Karten bleiben, nicht darueber.
            if load_more is not None:
                load_more.pack_forget()
                load_more.pack(fill="x", pady=(4, 16))

        self._update_kpis()
        self._render_status()
        self._sync_empty_state_placeholders()

    # ------------------------------------------------------------------
    # Karten: Erfolgreich
    # ------------------------------------------------------------------
    def _render_success_card(self, parent, tx):
        card = ctk.CTkFrame(
            parent, corner_radius=14, fg_color=COLORS["bg_card"], border_width=1, border_color=COLORS["border"]
        )
        card.pack(fill="x", pady=(0, 16), padx=6)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 4))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            left, text=f"#{tx.get('display_number') or tx['id']}  ·  {tx['date'].strftime('%d.%m.%Y')}", text_color=COLORS["text_muted"], font=FONT_SMALL
        ).pack(anchor="w")
        self._build_purpose_block(left, tx)

        amount_color = COLORS["green"] if tx["amount_raw"] >= 0 else COLORS["red"]
        ctk.CTkLabel(top, text=f"{tx['amount_abs']:.2f} €", font=FONT_AMOUNT, text_color=amount_color).pack(
            side="right", anchor="n"
        )

        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(4, 16))
        if tx["status"] == "matched":
            self._pill(bottom, "🔗 Automatisch zugeordnet", COLORS["green"], COLORS["green_dim"]).pack(side="left")
        elif tx["status"] == "uploaded":
            self._pill(bottom, "📤 Hochgeladen", COLORS["blue"], COLORS["blue_dim"]).pack(side="left")
        else:
            tag = tx.get("tag") or "SONSTIGES"
            icon = TAG_ICONS.get(tag, "🏷️")
            if tag in TAG_COLORS:
                _name, color, bg = TAG_COLORS[tag]
            else:
                color, bg = _custom_tag_color(tag)
            label = tag.capitalize() if tag in BUILTIN_TAGS else tag
            self._pill(bottom, f"{icon} {label}", color, bg).pack(side="left")

        ctk.CTkButton(
            bottom,
            text="↩ Rueckgaengig",
            width=110,
            corner_radius=10,
            fg_color="transparent",
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_muted"],
            command=lambda t=tx: self._on_undo_click(t["id"]),
        ).pack(side="right")
        return card

    # ------------------------------------------------------------------
    # Karten: Fehlt (rot) / Mehrfach-Match (gelb)
    # ------------------------------------------------------------------
    def _render_action_card(self, parent, tx, is_unclear: bool):
        border_color = COLORS["amber"] if is_unclear else COLORS["red_border"]
        card = ctk.CTkFrame(
            parent, corner_radius=14, fg_color=COLORS["bg_card"], border_width=2, border_color=border_color
        )
        card.pack(fill="x", pady=(0, 16), padx=6)

        if is_unclear:
            badge = ctk.CTkFrame(card, corner_radius=8, fg_color=COLORS["amber"])
            badge.pack(anchor="w", padx=20, pady=(18, 0))
            ctk.CTkLabel(badge, text="🟡  MEHRFACH-MATCH", text_color="#1e1e2e", font=("", 11, "bold")).pack(
                padx=10, pady=3
            )

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(14 if is_unclear else 18, 4))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            left, text=f"#{tx.get('display_number') or tx['id']}  ·  {tx['date'].strftime('%d.%m.%Y')}", text_color=COLORS["text_muted"], font=FONT_SMALL
        ).pack(anchor="w")
        self._build_purpose_block(left, tx, wraplength=560)

        amount_color = COLORS["green"] if tx["amount_raw"] >= 0 else COLORS["red"]
        amount_text = f"{'▲' if tx['amount_raw'] >= 0 else '▼'} {tx['amount_abs']:.2f} €"
        ctk.CTkLabel(top, text=amount_text, font=FONT_AMOUNT, text_color=amount_color).pack(side="right", anchor="n")

        if is_unclear:
            warn = ctk.CTkFrame(card, corner_radius=10, fg_color=COLORS["amber_dim"])
            warn.pack(fill="x", padx=20, pady=(8, 0))
            ctk.CTkLabel(
                warn,
                text="⚠️  Betrag tritt mehrfach auf - bitte manuell zuordnen.",
                text_color=COLORS["amber"],
                font=("", 12, "bold"),
                wraplength=560,
                justify="left",
                anchor="w",
            ).pack(padx=14, pady=10, anchor="w")
            self._build_ambiguous_picker(card, tx)
            self._build_tag_row(card, tx)
            return card

        if tx.get("suggested_tag"):
            sugg = tx["suggested_tag"]
            icon = TAG_ICONS.get(sugg, "🏷️")
            label = sugg.capitalize() if sugg in BUILTIN_TAGS else sugg
            sugg_row = ctk.CTkFrame(card, fg_color=COLORS["blue_dim"], corner_radius=10)
            sugg_row.pack(fill="x", padx=20, pady=(8, 0))
            ctk.CTkLabel(
                sugg_row,
                text=f"💡  Vorschlag: {icon} {label} - aehnliche Buchung, bereits so getaggt",
                text_color=COLORS["blue"],
                font=("", 12, "bold"),
                wraplength=380,
                justify="left",
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=(14, 4), pady=10)
            ctk.CTkButton(
                sugg_row,
                text="✓ Uebernehmen",
                width=120,
                corner_radius=10,
                fg_color=COLORS["blue"],
                hover_color="#4a76d6",
                command=lambda t=tx, s=sugg: self._on_tag_click(t["id"], s),
            ).pack(side="right", padx=10, pady=8)

        self._build_drop_zone(card, tx)
        self._outline_button(
            card, "📂  Aus Paperless waehlen", COLORS["blue"], lambda t=tx: self._on_pick_existing_doc(t), width=220
        ).pack(anchor="w", padx=20, pady=(10, 0))
        self._build_tag_row(card, tx)
        return card

    @staticmethod
    def _candidate_label(doc: dict) -> str:
        label = f"#{doc['id']} - {doc['title'] or doc.get('original_file_name') or 'ohne Titel'}"
        if doc.get("correspondent_name"):
            label += f" · {doc['correspondent_name']}"
        label += f" · {doc['date'].strftime('%d.%m.%Y') if doc.get('date') else 'kein Datum'}"
        return label

    def _build_ambiguous_picker(self, card, tx):
        """Inline Combobox statt Modal-Dialog: die wenigen bereits ermittelten
        Kandidaten (tx['candidate_docs']) direkt in der Karte auswaehlen."""
        candidates = tx.get("candidate_docs") or []
        labels = [self._candidate_label(d) for d in candidates]
        label_to_doc = {label: doc for label, doc in zip(labels, candidates)}

        picker_row = ctk.CTkFrame(card, fg_color="transparent")
        picker_row.pack(fill="x", padx=20, pady=(12, 20))
        combo = ctk.CTkComboBox(
            picker_row,
            values=labels or ["Keine Kandidaten geladen"],
            width=380,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            button_color=COLORS["bg_card_hover"],
            border_width=0,
        )
        if labels:
            combo.set(labels[0])
        combo.pack(side="left", fill="x", expand=True, padx=(0, 10))

        def _confirm():
            doc = label_to_doc.get(combo.get())
            if doc is None:
                return
            try:
                self.controller.on_ambiguous_doc_selected(tx["id"], doc["id"])
            except Exception as exc:
                messagebox.showerror("Fehlgeschlagen", str(exc))
                return
            self._refresh_single_transaction(tx["id"])

        ctk.CTkButton(
            picker_row,
            text="Zuordnen",
            width=110,
            corner_radius=10,
            fg_color=COLORS["amber"],
            text_color="#1e1e2e",
            hover_color="#d99a2b",
            command=_confirm,
        ).pack(side="left")

    def _build_drop_zone(self, parent, tx):
        zone = ctk.CTkFrame(
            parent, height=64, fg_color=COLORS["dropzone_bg"], corner_radius=12, border_width=2, border_color=COLORS["blue"]
        )
        zone.pack(fill="x", padx=20, pady=(14, 0))
        label = ctk.CTkLabel(
            zone, text="⬆  PDF hier ablegen oder klicken zum Hochladen", text_color=COLORS["blue"], font=("", 13, "bold")
        )
        label.pack(expand=True, pady=18)

        tx_id = tx["id"]

        def _drop(event):
            paths = _parse_dnd_files(event.data)
            if paths:
                self._on_pdf_dropped(tx_id, paths[0])

        for widget in (zone, label):
            widget.drop_target_register(tkinterdnd2.DND_FILES)
            widget.dnd_bind("<<Drop>>", _drop)
            widget.bind("<Button-1>", lambda _e: self._on_pick_pdf_file(tx_id))

        return zone

    def _build_tag_row(self, parent, tx):
        container = ctk.CTkFrame(parent, fg_color="transparent")
        # pady unten grosszuegig (18px): bei zu wenig Abstand zum Kartenrand
        # hat der abgerundete/gefaerbte Rahmen (corner_radius=14) keinen Platz
        # mehr zum sauberen Zeichnen und wirkt unten "abgeschnitten".
        container.pack(fill="x", padx=20, pady=(14, 18))

        promoted = self.controller.top_custom_tags(limit=3)
        quick_tags = BUILTIN_TAGS + promoted

        # Schnell-Tag-Buttons brechen bei Bedarf auf mehrere Zeilen um (grobe
        # Breitenschaetzung anhand der Textlaenge) - bei vielen eigenen Tags
        # sollen sie nicht mit dem Sonstiges-Kombifeld/Anwenden-Button um den
        # Platz konkurrieren und diese aus dem sichtbaren Bereich draengen.
        tag_line = ctk.CTkFrame(container, fg_color="transparent")
        tag_line.pack(fill="x", anchor="w")
        used_width = 0
        max_line_width = 600
        for tag_name in quick_tags:
            icon = TAG_ICONS.get(tag_name, "🏷️")
            label = f"{icon} {tag_name.capitalize()}" if tag_name in BUILTIN_TAGS else f"{icon} {tag_name}"
            est_width = 30 + len(label) * 9
            if used_width > 0 and used_width + est_width > max_line_width:
                tag_line = ctk.CTkFrame(container, fg_color="transparent")
                tag_line.pack(fill="x", anchor="w", pady=(8, 0))
                used_width = 0
            if tag_name in TAG_COLORS:
                _name, color, _bg = TAG_COLORS[tag_name]
            else:
                color, _bg = _custom_tag_color(tag_name)
            self._outline_button(tag_line, label, color, lambda t=tag_name: self._on_tag_click(tx["id"], t)).pack(
                side="left", padx=(0, 8)
            )
            used_width += est_width + 8

        # Sonstiges-Kombifeld + Anwenden: IMMER eine eigene Zeile, unabhaengig
        # davon wie viele Schnell-Tag-Zeilen oben stehen - das war der Grund,
        # warum "Anwenden" bei vielen eigenen Tags abgeschnitten wirkte.
        sonstiges_row = ctk.CTkFrame(container, fg_color="transparent")
        sonstiges_row.pack(fill="x", anchor="w", pady=(10, 0))

        other_tags = [t for t in self.app_state.config.get("custom_tags", {}) if t not in promoted]
        combo = ctk.CTkComboBox(
            sonstiges_row,
            values=other_tags + ["+ neuer Tag"],
            width=150,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            button_color=COLORS["bg_card_hover"],
            border_width=0,
        )
        combo.set("Sonstiges...")
        combo.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            sonstiges_row,
            text="Anwenden",
            width=90,
            corner_radius=10,
            fg_color=COLORS["bg_card_hover"],
            command=lambda c=combo, txid=tx["id"]: self._on_sonstiges_apply(txid, c),
        ).pack(side="left")

    # ------------------------------------------------------------------
    # Event-Handler (rufen den Controller auf, rendern danach neu)
    # ------------------------------------------------------------------
    def _open_settings(self):
        SettingsDialog(self, self.app_state, self._on_settings_saved)

    def _on_settings_saved(self):
        self._refresh_connection_status()
        self.render()

    def _refresh_connection_status(self):
        """Der HTTP-Check (bis zu 10s Timeout, siehe paperless_client.test_connection)
        laeuft in einem Hintergrund-Thread, damit das Fenster sofort erscheint statt
        beim Start/Speichern der Einstellungen auf die Netzwerkantwort zu warten."""
        if not self.app_state.client:
            self._paperless_connected = False
            self._paperless_checking = False
            self._render_status()
            return

        self._paperless_checking = True
        self._render_status()
        client = self.app_state.client

        def _check():
            connected = client.test_connection()
            self.after(0, self._on_connection_checked, connected)

        threading.Thread(target=_check, daemon=True).start()

    def _on_connection_checked(self, connected: bool):
        if not self.winfo_exists():
            return  # Fenster wurde geschlossen, waehrend der Check noch lief
        self._paperless_checking = False
        self._paperless_connected = connected
        self._render_status()

    def _on_upload_csv_click(self):
        path = filedialog.askopenfilename(title="Bank-Kontoauszug waehlen", filetypes=[("CSV-Dateien", "*.csv")])
        if not path:
            return
        try:
            mapping_ready = self.controller.on_csv_upload(path)
        except Exception as exc:
            messagebox.showerror("CSV-Import fehlgeschlagen", str(exc))
            return
        if not mapping_ready:
            MappingDialog(self, self.app_state.csv_columns, self._on_mapping_confirmed)
        else:
            self.render()

    def _on_mapping_confirmed(self, date_col, amount_col, purpose_col, counterparty_col=None):
        try:
            self.controller.on_mapping_confirm(date_col, amount_col, purpose_col, counterparty_col)
        except Exception as exc:
            messagebox.showerror("Mapping fehlgeschlagen", str(exc))
            return
        self.render()

    def _on_match_click(self):
        try:
            count = self.controller.on_match_click()
        except Exception as exc:
            messagebox.showerror("Abgleich fehlgeschlagen", str(exc))
            return
        messagebox.showinfo("Abgleich abgeschlossen", f"{count} Paperless-Dokumente geladen.")
        self.render()

    def _on_month_selected(self, value):
        self.app_state.selected_month = value
        # Der gewaehlte Monat filtert jetzt auch die ANZEIGE (siehe
        # AppState.visible_transactions) - Tabs/KPIs muessen daher neu
        # aufgebaut werden, nicht nur der Status-Text.
        self._update_kpis()
        self._render_status()
        self._render_tabs()

    def _on_generate_export_click(self):
        month = self.app_state.selected_month
        if not month or month == "-":
            messagebox.showwarning("Kein Monat gewaehlt", "Bitte zuerst einen Monat waehlen.")
            return
        try:
            export_path = self.controller.on_generate_export_click(month)
        except Exception as exc:
            messagebox.showerror("Export fehlgeschlagen", str(exc))
            return
        messagebox.showinfo("Export erstellt", f"Export erstellt:\n{export_path}")

    def _on_tag_click(self, tx_id, tag_name):
        try:
            self.controller.on_apply_tag(tx_id, tag_name)
        except Exception as exc:
            messagebox.showerror("Fehlgeschlagen", str(exc))
            return
        self._refresh_single_transaction(tx_id)
        # on_apply_tag kann GESCHWISTER-Transaktionen (gleicher normalisierter
        # Verwendungszweck) einen neuen Vorschlag geben - deren Karten muessen
        # ebenfalls aktualisiert werden, nicht nur die aktiv getaggte.
        for tx in self.app_state.missing_transactions:
            if tx.get("suggested_tag"):
                self._refresh_single_transaction(tx["id"])

    def _on_undo_click(self, tx_id):
        self.controller.on_undo_resolution(tx_id)
        self._refresh_single_transaction(tx_id)

    def _on_sonstiges_apply(self, tx_id, combo):
        # Die Combobox ist frei betippbar - value kann also auch Freitext
        # sein, den der Nutzer direkt eingetippt hat statt "+ neuer Tag" aus
        # der Liste zu waehlen (z.B. versehentlich "+ Darlehen" nach dem
        # Vorbild von "+ neuer Tag"). Wird unten in beiden Faellen bereinigt.
        value = combo.get().strip()
        if value in ("", "Sonstiges..."):
            return
        if value == "+ neuer Tag":
            dialog = ctk.CTkInputDialog(text="Neuer Tag-Name:", title="Sonstiges")
            apply_window_icon(dialog)
            new_tag = dialog.get_input()
            if not new_tag or not new_tag.strip():
                return
            value = new_tag.strip()
        value = value.lstrip("+").strip()
        if not value:
            return
        self._on_tag_click(tx_id, value)

    def _on_pick_pdf_file(self, tx_id):
        path = filedialog.askopenfilename(title="PDF waehlen", filetypes=[("PDF", "*.pdf")])
        if path:
            self._on_pdf_dropped(tx_id, path)

    def _on_pdf_dropped(self, tx_id, filepath):
        try:
            self.controller.on_pdf_drop(tx_id, filepath)
        except Exception as exc:
            messagebox.showerror("Upload fehlgeschlagen", str(exc))
            return
        self._refresh_single_transaction(tx_id)

    def _on_pick_existing_doc(self, tx):
        if not self.app_state.paperless_docs_raw:
            messagebox.showinfo(
                "Hinweis", "Bitte zuerst '🔍 Mit Paperless abgleichen' klicken, um die Dokumentliste zu laden."
            )
            return
        method = self.app_state.config["amount_detection"].get("method")
        show_value_entry = method == "custom_field"
        default_value = f"{tx['amount_abs']:.2f}"
        if show_value_entry and self._get_custom_field_data_type() == "monetary":
            default_value = f"EUR{tx['amount_abs']:.2f}"

        def on_select(doc_id, value):
            try:
                self.controller.on_paperless_doc_selected(tx["id"], doc_id, value)
            except Exception as exc:
                messagebox.showerror("Fehlgeschlagen", str(exc))
                return
            self._refresh_single_transaction(tx["id"])

        DocumentSearchDialog(self, self.app_state.paperless_docs_raw, show_value_entry, default_value, on_select)

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

    def _on_close(self):
        self.app_state.persist_session()
        self.destroy()


def main():
    app = DesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
