"""Datensicherung: sichert config.json, .env (inkl. Paperless-Zugangsdaten)
und den Arbeitsstand (session_state.json) als ZIP und stellt sie wieder her.

Noetig, weil im installierten Betrieb alle Nutzerdaten ausserhalb des
Programmordners in get_base_dir() liegen (plattformueblicher Ort fuer
Pro-Nutzer-Anwendungsdaten, siehe config_manager.py) und bei einem
Rechnerwechsel oder Datenverlust sonst verloren waeren - inklusive
gelernter Tags/Zuordnungen (config.json) und ggf. noch nicht exportierter
Zuordnungsarbeit (session_state.json)."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

BACKUP_FILES = ["config.json", ".env", "session_state.json"]


def create_backup(base_dir: Path) -> bytes:
    """Packt alle vorhandenen BACKUP_FILES in ein ZIP (im Speicher) und gibt
    dessen Bytes zurueck. Fehlende Dateien (z.B. noch kein Arbeitsstand
    gespeichert) werden stillschweigend ausgelassen."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in BACKUP_FILES:
            path = base_dir / name
            if path.exists():
                zf.write(path, arcname=name)
    return buf.getvalue()


def backup_filename() -> str:
    return f"paperless_sync_backup_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"


def restore_backup(base_dir: Path, zip_bytes: bytes) -> list[str]:
    """Ueberschreibt config.json/.env/session_state.json aus dem Backup-ZIP.
    Dateien, die im ZIP fehlen, bleiben unangetastet. Gibt die Liste der
    tatsaechlich wiederhergestellten Dateien zurueck."""
    restored = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        for name in BACKUP_FILES:
            if name in names:
                (base_dir / name).write_bytes(zf.read(name))
                restored.append(name)
    return restored
