"""Zentrale Verwaltung aller sensiblen Werte (Paperless-API-Token,
mTLS-Client-Zertifikat-Passwort, Verschluesselungsschluessel fuer
session_state.json) - entscheidet pro System, ob das OS-Keyring
(credential_store.py) oder der Passphrasen-Fallback (encrypted_fallback.py)
genutzt wird, und ist die EINZIGE Stelle, die das entscheidet. Es wird NIE
stillschweigend auf Klartext zurueckgefallen: ist kein Keyring verfuegbar
und keine Passphrase gegeben, bleibt ein Secret schlicht gesperrt
(get_secret gibt None zurueck, set_secret wirft SecretsLockedError) - die
UI-Schicht muss in diesem Fall eine Passphrase abfragen.

Fallback-Speicherort: dieselbe .env-Datei wie die uebrigen
Laufzeit-Einstellungen (siehe config_manager.py), als eigene
<NAME>_ENC/<NAME>_SALT-Schluesselpaare (base64) - bewusst NICHT ueber
config_manager.save_env(), um keine zirkulaere Abhaengigkeit zu erzeugen;
dieses Modul verwaltet seinen eigenen kleinen Ausschnitt der .env-Datei
komplett selbst.
"""
from __future__ import annotations

import base64
from pathlib import Path

from dotenv import dotenv_values

from . import credential_store, encrypted_fallback

SECRET_TOKEN = "paperless_token"
SECRET_CERT_PASSWORD = "paperless_client_cert_password"
SECRET_SESSION_KEY = "session_state_key"


class SecretsLockedError(Exception):
    """Kein Keyring verfuegbar UND keine Passphrase gegeben - ein Secret
    kann weder gelesen noch geschrieben werden, ohne dass der Nutzer eine
    Passphrase eingibt."""


def is_keyring_available() -> bool:
    return credential_store.is_keyring_available()


def _env_path(base_dir: Path) -> Path:
    return base_dir / ".env"


def _enc_key(name: str) -> str:
    return f"{name.upper()}_ENC"


def _salt_key(name: str) -> str:
    return f"{name.upper()}_SALT"


def _read_fallback_entry(base_dir: Path, name: str) -> tuple[str, bytes] | None:
    env_path = _env_path(base_dir)
    if not env_path.exists():
        return None
    values = dotenv_values(env_path)
    enc = values.get(_enc_key(name))
    salt_b64 = values.get(_salt_key(name))
    if not enc or not salt_b64:
        return None
    try:
        salt = base64.urlsafe_b64decode(salt_b64)
    except (ValueError, TypeError):
        return None
    return enc, salt


def _write_fallback_entry(base_dir: Path, name: str, ciphertext: str, salt: bytes) -> None:
    """Aktualisiert nur die beiden <NAME>_ENC/<NAME>_SALT-Zeilen dieses
    Secrets in .env, alle anderen Zeilen (auch anderer Secrets) bleiben
    unangetastet - gleiches Grundmuster wie config_manager.save_env
    (lesen, im Dict aktualisieren, komplett neu schreiben)."""
    env_path = _env_path(base_dir)
    existing = dict(dotenv_values(env_path)) if env_path.exists() else {}
    existing[_enc_key(name)] = ciphertext
    existing[_salt_key(name)] = base64.urlsafe_b64encode(salt).decode("ascii")
    with open(env_path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(f"{key}={value}\n")


def _delete_fallback_entry(base_dir: Path, name: str) -> None:
    env_path = _env_path(base_dir)
    if not env_path.exists():
        return
    existing = dict(dotenv_values(env_path))
    existing.pop(_enc_key(name), None)
    existing.pop(_salt_key(name), None)
    with open(env_path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(f"{key}={value}\n")


def has_locked_secrets(base_dir: Path) -> bool:
    """True, wenn KEIN Keyring verfuegbar ist UND mindestens ein Secret
    bereits im Passphrasen-Fallback abgelegt wurde - die UI muss dann vor
    dem eigentlichen App-Start eine Passphrase abfragen, um es zu
    entschluesseln (siehe get_secret(..., passphrase=...))."""
    if is_keyring_available():
        return False
    return any(
        _read_fallback_entry(base_dir, name) is not None
        for name in (SECRET_TOKEN, SECRET_CERT_PASSWORD, SECRET_SESSION_KEY)
    )


def get_secret(base_dir: Path, name: str, passphrase: str | None = None) -> str | None:
    """None bedeutet: Secret existiert nicht ODER (im Fallback-Fall) ist
    gesperrt/die Passphrase war falsch - beide Faelle sind fuer Aufrufer
    ('kein gueltiger Wert vorhanden') gleich zu behandeln."""
    if is_keyring_available():
        return credential_store.get_secret(name)

    entry = _read_fallback_entry(base_dir, name)
    if entry is None:
        return None
    if not passphrase:
        return None
    ciphertext, salt = entry
    return encrypted_fallback.decrypt(ciphertext, passphrase, salt)


def set_secret(base_dir: Path, name: str, value: str, passphrase: str | None = None) -> None:
    if is_keyring_available():
        credential_store.set_secret(name, value)
        return
    if not passphrase:
        raise SecretsLockedError(
            f"Kein OS-Keyring verfuegbar - '{name}' kann nur mit einer Passphrase gespeichert werden."
        )
    salt = encrypted_fallback.generate_salt()
    ciphertext = encrypted_fallback.encrypt(value, passphrase, salt)
    _write_fallback_entry(base_dir, name, ciphertext, salt)


def delete_secret(base_dir: Path, name: str) -> None:
    if is_keyring_available():
        credential_store.delete_secret(name)
        return
    _delete_fallback_entry(base_dir, name)


def get_or_create_session_key(base_dir: Path, passphrase: str | None = None) -> bytes | None:
    """Fernet-Schluessel fuer session_state.json (siehe session_store.py) -
    ein rein interner, dem Nutzer nie angezeigter Schluessel (kein
    Passwort), wird beim allerersten Bedarf zufaellig erzeugt und wie die
    uebrigen Secrets abgelegt. None, wenn der Fallback-Speicher gesperrt
    ist (kein Keyring, keine/falsche Passphrase) - der Aufrufer darf dann
    NICHT auf Klartext ausweichen, sondern muss die Passphrase-Abfrage der
    UI abwarten."""
    existing = get_secret(base_dir, SECRET_SESSION_KEY, passphrase=passphrase)
    if existing is not None:
        return existing.encode("ascii")

    if not is_keyring_available() and not passphrase:
        return None

    new_key = encrypted_fallback.generate_fernet_key()
    set_secret(base_dir, SECRET_SESSION_KEY, new_key.decode("ascii"), passphrase=passphrase)
    return new_key
