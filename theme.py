"""Gemeinsame Farb-/Font-Konstanten fuer Hauptfenster UND alle Dialoge
(dialogs.py). Eigenes Modul statt in desktop_app.py, weil dialogs.py sonst
desktop_app.py importieren muesste, das seinerseits dialogs.py importiert -
ein Zirkel."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Theme: weiches, dunkles Anthrazit statt hartem Schwarz/Grau (kein Tailwind
# verfuegbar in Tkinter, aber dieselbe Farb-/Abstands-Philosophie: dezente
# Flaechen, satte Akzentfarben nur fuer Zahlen/Status, viel Weissraum).
# ---------------------------------------------------------------------------
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
    "PRIVAT": ("amber", COLORS["amber"], COLORS["amber_dim"]),
    "EINZAHLUNG": ("green", COLORS["green"], COLORS["green_dim"]),
    "UMBUCHUNG": ("blue", COLORS["blue"], COLORS["blue_dim"]),
}
DEFAULT_TAG_COLOR = (COLORS["purple"], COLORS["purple_dim"])

# Eigene ("Sonstiges"-)Tags teilten sich bisher alle dieselbe lila Farbe und
# wirkten dadurch austauschbar. Stabile (Namens-basierte, kein Zufall) Farbe
# je Tag aus einer kleinen Palette, damit z.B. "Darlehen" und
# "Kontofuehrung" auf einen Blick unterscheidbar sind.
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


FONT_TITLE = ("", 18, "bold")
FONT_SUBTITLE = ("", 13, "bold")
FONT_SMALL = ("", 11)
FONT_PURPOSE = ("", 16, "bold")
FONT_PURPOSE_SECONDARY = ("", 13)
FONT_AMOUNT = ("", 28, "bold")
FONT_KPI_NUMBER = ("", 40, "bold")
FONT_KPI_TITLE = ("", 13, "bold")
