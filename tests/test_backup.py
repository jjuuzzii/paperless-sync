"""Tests fuer core/backup.py - Datensicherung (siehe CLAUDE.md/Prompt 8,
Sicherheits-Checkliste Punkt 6): Backup mit/ohne Passwort, Restore mit
falschem Passwort muss sauber scheitern statt beschaedigte Daten zu laden.

WICHTIG: backup._extension_dir() ruft config_manager.get_enable_banking_
key_path() OHNE Umweg ueber get_base_dir() auf - das loest IMMER den
echten plattformueblichen Pfad auf (siehe dortige Docstring), unabhaengig
von base_dir hier. Ohne das Monkeypatching unten wuerden diese Tests den
echten %APPDATA%\\PaperlessSync\\enable_banking\\-Ordner des Test-Rechners
anfassen - deshalb patch_enable_banking_key_path als autouse-Fixture."""
from __future__ import annotations

import json

import pytest

import paperless_sync.core.backup as backup_module
import paperless_sync.core.credential_store as credential_store
import paperless_sync.core.secrets_manager as secrets_manager
from paperless_sync.core.backup import WrongBackupPasswordError, create_backup, restore_backup


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    fake_store: dict = {}
    monkeypatch.setattr(credential_store, "is_keyring_available", lambda: True)
    monkeypatch.setattr(credential_store, "set_secret", lambda name, value: fake_store.__setitem__(name, value))
    monkeypatch.setattr(credential_store, "get_secret", lambda name: fake_store.get(name))
    monkeypatch.setattr(credential_store, "delete_secret", lambda name: fake_store.pop(name, None))


@pytest.fixture(autouse=True)
def patch_enable_banking_key_path(tmp_path, monkeypatch):
    fake_ext_dir = tmp_path / "_enable_banking_extension_dir"
    monkeypatch.setattr(backup_module, "get_enable_banking_key_path", lambda: fake_ext_dir / "application.pem")


def test_create_backup_without_password_is_plain_zip_readable(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    zip_bytes = create_backup(tmp_path, password=None)

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "config.json" in zf.namelist()
        assert json.loads(zf.read("config.json")) == {"a": 1}


def test_create_backup_with_password_is_encrypted(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    zip_bytes = create_backup(tmp_path, password="geheim123")

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        with pytest.raises(RuntimeError):
            zf.read("config.json")  # normales zipfile kennt kein AES-Passwort -> scheitert


def test_backup_restore_roundtrip_no_password(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.json").write_text(json.dumps({"language": "de"}), encoding="utf-8")
    (src_dir / ".env").write_text("PAPERLESS_URL=https://example.test", encoding="utf-8")

    zip_bytes = create_backup(src_dir, password=None)

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    restored = restore_backup(dst_dir, zip_bytes, password=None)

    assert "config.json" in restored
    assert ".env" in restored
    assert "session_state.json" not in restored  # existierte im Quellordner nicht
    assert json.loads((dst_dir / "config.json").read_text(encoding="utf-8")) == {"language": "de"}
    assert (dst_dir / ".env").read_text(encoding="utf-8") == "PAPERLESS_URL=https://example.test"


def test_backup_restore_roundtrip_with_password(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")

    zip_bytes = create_backup(src_dir, password="korrektesPasswort")

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    restored = restore_backup(dst_dir, zip_bytes, password="korrektesPasswort")

    assert "config.json" in restored
    assert json.loads((dst_dir / "config.json").read_text(encoding="utf-8")) == {"language": "en"}


def test_restore_with_wrong_password_fails_cleanly_no_partial_write(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.json").write_text(json.dumps({"language": "de"}), encoding="utf-8")
    zip_bytes = create_backup(src_dir, password="richtig")

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    with pytest.raises(WrongBackupPasswordError):
        restore_backup(dst_dir, zip_bytes, password="falsch")

    # Keine beschaedigte/halb geschriebene Datei im Zielordner
    assert not (dst_dir / "config.json").exists()


def test_restore_encrypted_backup_without_password_fails_cleanly(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.json").write_text("{}", encoding="utf-8")
    zip_bytes = create_backup(src_dir, password="geheim")

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    with pytest.raises(WrongBackupPasswordError):
        restore_backup(dst_dir, zip_bytes, password=None)
    assert not (dst_dir / "config.json").exists()


def test_backup_includes_secrets_when_present(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    secrets_manager.set_secret(src_dir, secrets_manager.SECRET_TOKEN, "mein-token-123")

    zip_bytes = create_backup(src_dir, password=None)

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "secrets.json" in zf.namelist()
        secrets = json.loads(zf.read("secrets.json"))
        assert secrets[secrets_manager.SECRET_TOKEN] == "mein-token-123"


def test_restore_secrets_lands_in_keyring_on_this_machine(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    secrets_manager.set_secret(src_dir, secrets_manager.SECRET_TOKEN, "mein-token-123")
    zip_bytes = create_backup(src_dir, password=None)

    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    restored = restore_backup(dst_dir, zip_bytes, password=None)

    assert "secrets.json" in restored
    assert secrets_manager.get_secret(dst_dir, secrets_manager.SECRET_TOKEN) == "mein-token-123"


def test_backup_without_any_existing_files_still_produces_valid_zip(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    zip_bytes = create_backup(empty_dir, password=None)

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert zf.namelist() == []  # nichts vorhanden, aber kein Fehler


def test_restore_ignores_path_traversal_in_extension_files(tmp_path):
    # Verteidigung gegen ein manipuliertes ZIP mit "extensions/../../evil.txt"
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("extensions/../evil.txt", "boese")
        zf.writestr("extensions/legit.pem", "harmlos")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    restore_backup(dst_dir, buf.getvalue(), password=None)

    assert not (tmp_path / "evil.txt").exists()
    assert not (dst_dir.parent / "evil.txt").exists()
