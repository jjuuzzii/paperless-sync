"""Workarounds fuer Bugs in der customtkinter-Bibliothek selbst (nicht in
unserem eigenen Code) - siehe jeweilige Klassen-Docstrings fuer Details.
Separates Modul, damit sowohl desktop_app.py als auch dialogs.py es nutzen
koennen, ohne dass beide Dateien sich gegenseitig importieren muessen."""
from __future__ import annotations

import customtkinter as ctk


class LeakSafeScrollableFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame registriert sein Mausrad/Tastatur-Handling global
    ueber bind_all() (auf dem 'all'-Bindtag, app-weit gueltig), entfernt
    diese Registrierungen in destroy() aber NIE wieder (Bug in
    customtkinter, Stand 5.2.x). Jeder neu erstellte Dialog mit eigener
    Scroll-Liste (Einstellungen, Dokumentsuche) haeuft dadurch ueber eine
    laengere Sitzung immer mehr tote globale Handler an - jeder davon wird
    bei JEDEM Mausrad-Ereignis IRGENDWO in der App durchlaufen (auch wenn
    das zugehoerige Fenster laengst geschlossen ist), was Scrollen spuerbar
    langsamer macht, je laenger die App laeuft (gemessen: ~2x Kosten pro
    Scroll-Event nach ca. 100 akkumulierten toten Handlern).

    Faengt bind_all()-Aufrufe waehrend __init__ ab, um die von Tcl
    zurueckgegebenen Funcids zu merken, und entfernt beim destroy() gezielt
    nur diese eigenen Eintraege wieder aus dem globalen Bindtag - der Rest
    der App (z.B. die permanenten Karten-Listen im Hauptfenster) bleibt
    unangetastet."""

    def __init__(self, *args, **kwargs):
        self._leak_safe_funcids: list[tuple[str, str]] = []
        real_bind_all = self.bind_all

        def capturing_bind_all(sequence=None, func=None, add=None):
            funcid = real_bind_all(sequence, func, add)
            if sequence is not None and func is not None:
                self._leak_safe_funcids.append((sequence, funcid))
            return funcid

        self.bind_all = capturing_bind_all
        try:
            super().__init__(*args, **kwargs)
        finally:
            del self.bind_all

    def destroy(self):
        for sequence, funcid in self._leak_safe_funcids:
            try:
                current = self.tk.call("bind", "all", sequence)
            except Exception:
                current = ""
            if current and funcid in current:
                kept = [ln for ln in current.split("\n") if funcid not in ln]
                try:
                    self.tk.call("bind", "all", sequence, "\n".join(kept))
                except Exception:
                    pass
        super().destroy()
