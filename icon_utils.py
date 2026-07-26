"""Gemeinsame Fenster-Icon-Logik fuer das Hauptfenster UND alle Toplevel-
Dialoge (Einstellungen, CSV-Mapping, Dokumentsuche, 'Neuer Tag'-Eingabe).

Toplevel-Fenster erben iconbitmap()/iconphoto() in Tkinter NICHT automatisch
vom Hauptfenster - jedes einzelne muss sein Icon selbst gesetzt bekommen,
sonst zeigt Windows dort ein Standard-Icon.

Tk's iconbitmap()/iconphoto() sind fuer eine scharfe, korrekt gross
dargestellte Windows-Taskleisten-Ikone bekanntermassen unzuverlaessig (laden
oft nur eine feste Groesse, die dann verwaschen hoch- oder klein
herunterskaliert angezeigt wird). Auf Windows wird deshalb zusaetzlich die
native WM_SETICON-Botschaft per ctypes gesendet, mit HICON-Handles, die
Windows selbst per LoadImageW aus der .ico waehlt (nimmt zuverlaessig die am
besten passende eingebettete Groesse) - das ist der uebliche robuste Weg,
dieses Tk-Problem auf Windows zu umgehen.
"""
from __future__ import annotations

import sys

from PIL import Image, ImageTk

from paperless_sync.core.config_manager import get_resource_dir

# PhotoImage-Objekte werden von Tk nur per Referenz gehalten - ohne eine
# Python-seitige Referenz wuerden sie vom Garbage Collector eingesammelt und
# das Icon verschwaende wieder. Modulweit am Leben halten.
_icon_photo_keepalive: list = []


def apply_window_icon(window) -> None:
    icon_path = get_resource_dir() / "icon.ico"
    if not icon_path.exists():
        return

    try:
        window.iconbitmap(str(icon_path))
    except Exception:
        pass

    try:
        base = Image.open(icon_path)
        sizes = sorted(set(base.info.get("sizes") or [base.size]))
        photos = []
        for size in sizes:
            frame = Image.open(icon_path)
            frame.size = size
            frame.load()
            photos.append(ImageTk.PhotoImage(frame.convert("RGBA")))
        if photos:
            window.iconphoto(True, *photos)
            _icon_photo_keepalive.extend(photos)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            window.update_idletasks()
            _set_win32_taskbar_icon(window, icon_path)
        except Exception:
            pass


def _set_win32_taskbar_icon(window, icon_path) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040
    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1
    GA_ROOT = 2

    user32.LoadImageW.restype = ctypes.c_void_p
    user32.GetAncestor.restype = wintypes.HWND

    hwnd = window.winfo_id()
    root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd

    small = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
    big = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
    if small:
        user32.SendMessageW(root_hwnd, WM_SETICON, ICON_SMALL, small)
    if big:
        user32.SendMessageW(root_hwnd, WM_SETICON, ICON_BIG, big)
