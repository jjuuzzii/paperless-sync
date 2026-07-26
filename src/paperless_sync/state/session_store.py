"""Persistiert den aktuellen Arbeitsstand (hochgeladene CSV + Transaktionen
mit allen Zuordnungen/Tags/Uploads) in einer JSON-Datei neben der .exe/App.

Zweck: Schliesst der Nutzer die App (oder die .exe), bevor der Export-Ordner
generiert wurde, darf keine bereits geleistete Arbeit (Matches, Tags,
Uploads) verloren gehen. Beim naechsten Start wird der Stand automatisch
wiederhergestellt, ohne dass die CSV erneut hochgeladen werden muss.
"""
from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

import pandas as pd

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


def save_session(base_dir: Path, state: dict) -> None:
    """state-Keys: csv_signature, csv_columns, csv_delimiter, csv_encoding,
    csv_records (df.to_dict('records')), mapping_confirmed, pending_mapping,
    matched_once, transactions. Schreibt atomar (erst .tmp, dann rename),
    damit ein Absturz waehrend des Schreibens keine kaputte Datei hinterlaesst."""
    path = base_dir / SESSION_FILENAME
    payload = _to_jsonable(state)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    tmp_path.replace(path)


def load_session(base_dir: Path) -> dict | None:
    path = base_dir / SESSION_FILENAME
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    payload = _from_jsonable(raw)
    csv_records = payload.get("csv_records")
    payload["csv_df"] = pd.DataFrame(csv_records) if csv_records is not None else None
    return payload


def clear_session(base_dir: Path) -> None:
    path = base_dir / SESSION_FILENAME
    if path.exists():
        path.unlink()
