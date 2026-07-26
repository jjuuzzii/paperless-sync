"""Duenner Wrapper um das keyring-Paket - nutzt plattformuebergreifend den
nativen Credential-Store des Betriebssystems (Windows Credential Manager,
macOS Keychain, Linux Secret Service). Weiss NICHTS von Passphrasen/
Verschluesselung - das ist Aufgabe von encrypted_fallback.py, orchestriert
von secrets_manager.py, das entscheidet, welches der beiden Backends genutzt
wird."""
from __future__ import annotations

import keyring
import keyring.errors
from keyring.backends.fail import Keyring as FailKeyring

SERVICE_NAME = "PaperlessSync"


def is_keyring_available() -> bool:
    """True, wenn ein echtes OS-Backend gefunden wurde. keyring faellt bei
    fehlendem Backend (z.B. manche minimalen/headless-Linux-Systeme ohne
    gnome-keyring/KWallet) intern auf ein 'fail'-Backend zurueck, das bei
    jedem Zugriff eine Exception wirft statt (unsicher) Klartext zu
    speichern - genau das nutzen wir hier als Erkennungsmerkmal."""
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return not isinstance(backend, FailKeyring)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE_NAME, name, value)


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, name)
    except keyring.errors.KeyringError:
        return None


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except keyring.errors.PasswordDeleteError:
        pass
