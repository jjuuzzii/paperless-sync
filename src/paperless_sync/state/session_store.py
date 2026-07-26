"""Persistiert den aktuellen Arbeitsstand (hochgeladene CSV + Transaktionen
mit allen Zuordnungen/Tags/Uploads) verschluesselt neben der .exe/App.

Zweck: Schliesst der Nutzer die App (oder die .exe), bevor der Export-Ordner
generiert wurde, darf keine bereits geleistete Arbeit (Matches, Tags,
Uploads) verloren gehen. Beim naechsten Start wird der Stand automatisch
wiederhergestellt, ohne dass die CSV erneut hochgeladen werden muss.

Enthaelt Kontobewegungen (Datum/Betrag/Verwendungszweck/Gegenpartei) und
hochgeladene Beleg-PDFs - deshalb per Fernet (AES) verschluesselt, mit
einem ueber secrets_manager.get_or_create_session_key() verwalteten
Schluessel (OS-Keyring oder Passphrasen-Fallback, siehe dort). fernet_key
ist bewusst optional (None): call sites, die keinen Schluessel bekommen
konnten (Fallback-Speicher gesperrt, siehe secrets_manager), sollen NICHT
persistieren/laden statt (unsicher) auf Klartext auszuweichen - das
entscheiden sie selbst, dieses Modul erzwingt es nicht.

Rueckwaertskompatibel: eine session_state.json von vor dieser Umstellung
war reines Klartext-JSON. load_session versucht bei gegebenem fernet_key
zuerst zu entschluesseln: schlaegt das fehl, wird als Fallback auf
Klartext-JSON geprueft - gelingt DAS, gilt die Datei als Alt-Sitzung und
wird sofort verschluesselt neu geschrieben (keine manuelle Migration
noetig, kein Datenverlust bei bereits bestehenden Sitzungen)."""
from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

import pandas as pd
from cryptography.fernet import Fernet, InvalidToken

SESSION_FILENAME = "session_state.json"


def _to_jsonable(obj):
    if isinstance(obj, date):
        return {"__date__": obj.isoformat()}
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, str):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return obj
    return obj


def _from_jsonable(obj):
    if isinstance(obj, dict):
        if set(obj.keys()) == {"__date__"}:
            return date.fromisoformat(obj["__date__"])
        if set(obj.keys()) == {"__bytes__"}:
            return base64.b64decode(obj["__bytes__"])
        return {k: _from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_jsonable(v) for v in obj]
    return obj


def save_session(base_dir: Path, state: dict, fernet_key: bytes | None = None) -> None:
    """state-Keys: csv_signature, csv_columns, csv_delimiter, csv_encoding,
    csv_records (df.to_dict('records')), mapping_confirmed, pending_mapping,
    matched_once, transactions. Schreibt atomar (erst .tmp, dann rename),
    damit ein Absturz waehrend des Schreibens keine kaputte Datei hinterlaesst.
    fernet_key gegeben -> verschluesselt, sonst (nur fuer den
    Rueckwaertskompatibilitaets-Pfad in load_session gedacht) Klartext-JSON."""
    path = base_dir / SESSION_FILENAME
    payload = _to_jsonable(state)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if fernet_key is not None:
        raw = Fernet(fernet_key).encrypt(raw)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "wb") as f:
        f.write(raw)
    tmp_path.replace(path)


def load_session(base_dir: Path, fernet_key: bytes | None = None) -> dict | None:
    path = base_dir / SESSION_FILENAME
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    needs_migration = False
    parsed = None
    if fernet_key is not None:
        try:
            parsed = json.loads(Fernet(fernet_key).decrypt(raw).decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            # Evtl. eine Alt-Sitzung von vor der Verschluesselung (reines
            # Klartext-JSON) - bevor aufgegeben wird, das noch versuchen.
            try:
                parsed = json.loads(raw.decode("utf-8"))
                needs_migration = True
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
    else:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    payload = _from_jsonable(parsed)

    if needs_migration:
        # Sofort verschluesselt neu schreiben, statt weiter im Klartext
        # liegen zu bleiben - payload hat an dieser Stelle noch KEIN
        # 'csv_df' (siehe unten), passt also zum von save_session
        # erwarteten Schema.
        save_session(base_dir, payload, fernet_key=fernet_key)

    csv_records = payload.get("csv_records")
    payload["csv_df"] = pd.DataFrame(csv_records) if csv_records is not None else None
    return payload


def clear_session(base_dir: Path) -> None:
    path = base_dir / SESSION_FILENAME
    if path.exists():
        path.unlink()
