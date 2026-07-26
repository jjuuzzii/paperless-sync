"""Farb-/Font-Konstanten fuer die PySide6/Qt-UI - Werte bewusst identisch zu
theme.py (der CustomTkinter-Version), damit beide UIs optisch gleich
aussehen. Getrennte Datei, weil Qt Farben/Schrift ueber QSS-Stylesheets bzw.
QFont statt CTk-kwargs setzt."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox

COLORS = {
    "bg_main": "#1e1e2e",
    "bg_sidebar": "#181825",
    "bg_card": "#2b2b3b",
    "bg_card_hover": "#33334a",
    "bg_kpi": "#24243a",
    "bg_input": "#2b2b3b",
    "border": "#3a3a52",
    "text_primary": "#f4f4f8",
    "text_muted": "#8d8fa8",
    "green": "#22c55e",
    "green_dim": "#1f4430",
    "red": "#ef4444",
    "red_dim": "#4a2226",
    "red_border": "#9c4a47",
    "amber": "#f5b642",
    "amber_dim": "#4a3b16",
    "blue": "#5b8def",
    "blue_dim": "#1f2f4a",
    "dropzone_bg": "#16233f",
    "purple": "#a78bfa",
    "purple_dim": "#332a4a",
    "pink": "#f472b6",
    "pink_dim": "#4a1f36",
    "teal": "#2dd4bf",
    "teal_dim": "#173c38",
    "orange": "#fb923c",
    "orange_dim": "#4a2f16",
    "indigo": "#818cf8",
    "indigo_dim": "#2a2a52",
}

TAG_COLORS = {
    "PRIVAT": COLORS["amber"],
    "EINZAHLUNG": COLORS["green"],
    "UMBUCHUNG": COLORS["blue"],
}
TAG_COLORS_DIM = {
    "PRIVAT": COLORS["amber_dim"],
    "EINZAHLUNG": COLORS["green_dim"],
    "UMBUCHUNG": COLORS["blue_dim"],
}

_CUSTOM_TAG_PALETTE = [
    (COLORS["purple"], COLORS["purple_dim"]),
    (COLORS["pink"], COLORS["pink_dim"]),
    (COLORS["teal"], COLORS["teal_dim"]),
    (COLORS["orange"], COLORS["orange_dim"]),
    (COLORS["indigo"], COLORS["indigo_dim"]),
]


def custom_tag_color(tag_name: str) -> tuple[str, str]:
    idx = sum(ord(c) for c in tag_name) % len(_CUSTOM_TAG_PALETTE)
    return _CUSTOM_TAG_PALETTE[idx]


def font(size: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSize(size)
    f.setBold(bold)
    return f


FONT_TITLE_SIZE = 14
FONT_SUBTITLE_SIZE = 11
FONT_SMALL_SIZE = 9
FONT_PURPOSE_SIZE = 12
FONT_PURPOSE_SECONDARY_SIZE = 10
FONT_AMOUNT_SIZE = 20
FONT_KPI_NUMBER_SIZE = 28
FONT_KPI_TITLE_SIZE = 10


class NoScrollComboBox(QComboBox):
    """QComboBox aendert per Default seinen Wert, sobald man mit dem
    Mausrad darueber scrollt - auch ohne vorherigen Klick. In einem
    scrollbaren Dialog (z.B. SettingsDialog) fuehrt normales Scrollen so
    versehentlich zum Verstellen von Einstellungen. Ignoriert
    Mausrad-Events, solange die Combobox keinen Fokus hat (das Event geht
    dann an den Eltern-Scrollbereich weiter) - nach einem Klick/Tab in die
    Combobox funktioniert Scrollen zum Werte-Wechsel wie gewohnt.
    StrongFocus statt des QComboBox-Defaults, damit ein voruebergehendes
    Ueberscrollen selbst nicht schon Fokus setzt."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)
