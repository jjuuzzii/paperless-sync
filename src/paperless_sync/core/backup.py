"""Datensicherung: sichert config.json, .env, den Arbeitsstand
(session_state.json) UND die im OS-Keyring/Passphrasen-Fallback verwalteten
Zugangsdaten (Paperless-Token, Zertifikat-Passwort,
Session-Verschluesselungsschluessel - siehe secrets_manager.py) als ZIP und
stellt sie wieder her.

Seit der Umstellung auf secrets_manager (Keyring statt Klartext-.env)
enthaelt .env selbst keine Geheimnisse mehr - die stecken separat als
secrets.json innerhalb des Backups, damit ein wiederhergestelltes Backup
auf einem neuen Rechner tatsaechlich sofort nutzbar ist (nicht nur die
Einstellungen, ohne Zugangsdaten). Das Backup ist dadurch potenziell genauso
sensibel wie vorher die Klartext-.env und sollte deshalb passwortgeschuetzt
sein.

Passwort gesetzt -> AES-256-verschluesseltes ZIP (pyzipper, WZ_AES). Kein
Passwort -> normales, unverschluesseltes ZIP - die UI-Schicht MUSS in
diesem Fall deutlich warnen (siehe SettingsDialog), dieses Modul selbst
zeigt keine Dialoge und faellt nie stillschweigend zurueck.

Sichert zusaetzlich den kompletten Inhalt eines optionalen lokalen
Erweiterungsordners (siehe config_manager.get_enable_banking_key_path) -
bewusst generisch (keine einzelnen Dateinamen hartcodiert), damit dieses
oeffentliche Modul nicht wissen muss, was genau darin liegt."""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pyzipper

from . import secrets_manager
from .config_manager import get_enable_banking_key_path

BACKUP_FILES = ["config.json", ".env", "session_state.json"]
SECRETS_ARCNAME = "secrets.json"
EXTENSIONS_ARCPREFIX = "extensions/"


def _extension_dir() -> Path:
    """Optionaler lokaler Erweiterungsordner (siehe config_manager.
    get_enable_banking_key_path) - falls vorhanden, wird sein kompletter
    Inhalt mit gesichert/wiederhergestellt, OHNE dass dieses (oeffentliche)
    Modul wissen muss, was genau darin liegt oder wofuer es ist. Bewusst
    generisch statt einzelne Dateinamen hartzucodieren."""
    return get_enable_banking_key_path().parent


class WrongBackupPasswordError(Exception):
    """Passwort falsch, oder das ZIP ist verschluesselt, aber keins wurde
    angegeben."""


def _collect_secrets(base_dir: Path, secrets_passphrase: str | None) -> dict[str, str]:
    """Liest alle drei Secret-Slots aus (Keyring oder Passphrasen-Fallback) -
    ein leer gebliebener Slot (z.B. kein Client-Zertifikat konfiguriert)
    wird ausgelassen, kein leerer Eintrag im Backup."""
    secrets = {}
    for name in (secrets_manager.SECRET_TOKEN, secrets_manager.SECRET_CERT_PASSWORD, secrets_manager.SECRET_SESSION_KEY):
        value = secrets_manager.get_secret(base_dir, name, passphrase=secrets_passphrase)
        if value:
            secrets[name] = value
    return secrets


def create_backup(base_dir: Path, password: str | None = None, secrets_passphrase: str | None = None) -> bytes:
    """password: schuetzt das ZIP selbst (AES) - None = unverschluesseltes
    ZIP. secrets_passphrase: nur noetig, wenn secrets_manager auf diesem
    Rechner im Passphrasen-Fallback laeuft (kein Keyring), um die aktuell
    gespeicherten Secrets ueberhaupt auslesen zu koennen."""
    buf = io.BytesIO()
    secrets = _collect_secrets(base_dir, secrets_passphrase)

    kwargs = {"compression": pyzipper.ZIP_DEFLATED}
    if password:
        kwargs["encryption"] = pyzipper.WZ_AES
    with pyzipper.AESZipFile(buf, "w", **kwargs) as zf:
        if password:
            zf.setpassword(password.encode("utf-8"))
        for name in BACKUP_FILES:
            path = base_dir / name
            if path.exists():
                zf.write(path, arcname=name)
        if secrets:
            zf.writestr(SECRETS_ARCNAME, json.dumps(secrets))
        ext_dir = _extension_dir()
        if ext_dir.exists():
            for path in ext_dir.iterdir():
                if path.is_file():
                    zf.write(path, arcname=f"{EXTENSIONS_ARCPREFIX}{path.name}")
    return buf.getvalue()


def backup_filename() -> str:
    return f"paperless_sync_backup_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"


def restore_backup(
    base_dir: Path, zip_bytes: bytes, password: str | None = None, secrets_passphrase: str | None = None
) -> list[str]:
    """Stellt config.json/.env/session_state.json UND - falls im Backup
    enthalten - die Zugangsdaten wieder her (landen ueber secrets_manager
    im Keyring/Fallback DIESES Rechners, nicht als Datei). password: muss
    zum beim Erstellen verwendeten Passwort passen, sonst
    WrongBackupPasswordError. secrets_passphrase: nur noetig, wenn
    secrets_manager auf DIESEM Rechner im Passphrasen-Fallback laeuft, um
    die wiederhergestellten Secrets dort abzulegen.

    Gibt die Liste der tatsaechlich wiederhergestellten Eintraege zurueck
    (Dateien aus BACKUP_FILES, plus 'secrets.json', falls Zugangsdaten
    enthalten waren) - Eintraege, die im ZIP fehlen, bleiben unangetastet."""
    restored = []
    try:
        with pyzipper.AESZipFile(io.BytesIO(zip_bytes)) as zf:
            if password:
                zf.setpassword(password.encode("utf-8"))
            names = set(zf.namelist())
            for name in BACKUP_FILES:
                if name in names:
                    (base_dir / name).write_bytes(zf.read(name))
                    restored.append(name)
            if SECRETS_ARCNAME in names:
                secrets = json.loads(zf.read(SECRETS_ARCNAME).decode("utf-8"))
                for secret_name, value in secrets.items():
                    secrets_manager.set_secret(base_dir, secret_name, value, passphrase=secrets_passphrase)
                restored.append(SECRETS_ARCNAME)
            ext_names = [n for n in names if n.startswith(EXTENSIONS_ARCPREFIX) and n != EXTENSIONS_ARCPREFIX]
            if ext_names:
                ext_dir = _extension_dir()
                ext_dir.mkdir(parents=True, exist_ok=True)
                for name in ext_names:
                    filename = name[len(EXTENSIONS_ARCPREFIX):]
                    if not filename or "/" in filename or "\\" in filename:
                        continue  # keine verschachtelten Pfade/Path-Traversal aus dem ZIP uebernehmen
                    (ext_dir / filename).write_bytes(zf.read(name))
                restored.append("extensions")
    except RuntimeError as exc:
        # pyzipper/pycryptodomex wirft RuntimeError("Bad password for
        # file...") bei falschem oder fehlendem Passwort fuer ein
        # AES-verschluesseltes Archiv.
        raise WrongBackupPasswordError(str(exc)) from exc
    return restored
