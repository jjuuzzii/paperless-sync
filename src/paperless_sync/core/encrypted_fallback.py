"""Passphrasen-basierte Verschluesselung als Fallback, wenn kein
OS-Keyring verfuegbar ist (siehe credential_store.is_keyring_available -
kommt v.a. auf manchen minimalen/headless-Linux-Systemen vor). Nutzt
Fernet (AES-128-CBC + HMAC-SHA256, siehe cryptography-Paket) mit einem aus
der Nutzer-Passphrase abgeleiteten Schluessel (PBKDF2-HMAC-SHA256,
480.000 Iterationen - OWASP-Empfehlung Stand 2023 fuer PBKDF2-SHA256).

Der Salt ist NICHT geheim (bei PBKDF2 auch nicht noetig - er verhindert
nur vorberechnete Rainbow-Tables, nicht Brute-Force gegen eine bekannte
Passphrase) und liegt unverschluesselt neben dem verschluesselten Wert.

Reine, IO-freie Funktionen - wo/wie Ciphertext+Salt gespeichert werden,
entscheidet secrets_manager.py."""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 480_000
SALT_LENGTH = 16


def generate_salt() -> bytes:
    return Fernet.generate_key()[:SALT_LENGTH]


def generate_fernet_key() -> bytes:
    """Zufaelliger AES-Schluessel fuer direkte Datenverschluesselung (z.B.
    session_state.json, siehe secrets_manager.get_or_create_session_key) -
    im Unterschied zu _derive_key() hier KEIN aus einer Passphrase
    abgeleiteter Schluessel, sondern ein rein interner, dem Nutzer nie
    angezeigter Schluessel."""
    return Fernet.generate_key()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt(value: str, passphrase: str, salt: bytes) -> str:
    """Gibt den Ciphertext als ASCII-String zurueck (Fernet-Tokens sind
    bereits urlsafe-base64) - direkt als .env-Wert speicherbar."""
    key = _derive_key(passphrase, salt)
    return Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str, passphrase: str, salt: bytes) -> str | None:
    """None bei falscher Passphrase oder korrupten Daten (statt eine
    Exception durchzureichen) - die aufrufende UI-Schicht soll das als
    'Passphrase falsch, bitte erneut versuchen' behandeln koennen, ohne
    jede Fernet-Exception einzeln abfangen zu muessen."""
    key = _derive_key(passphrase, salt)
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
