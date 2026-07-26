"""Laden/Speichern von Laufzeit-Konfiguration (.env und config.json).

Alle Pfade werden relativ zu get_base_dir() aufgeloest.

Als installierte Anwendung liegt das Programm selbst in einem meist nicht
ohne erhoehte Rechte beschreibbaren, generell nicht fuer Nutzerdaten
vorgesehenen Ordner (z.B. Program Files unter Windows). Nutzerdaten (.env,
config.json, session_state.json, input/, export/, Client-Zertifikat) leben
deshalb im plattformueblichen Ort fuer Pro-Nutzer-Anwendungsdaten (siehe
get_base_dir(), platformdirs) - Windows: %APPDATA%\\PaperlessSync, macOS:
~/Library/Application Support/PaperlessSync, Linux: ~/.config/PaperlessSync.
Im Quellcode-Betrieb (Entwicklung) bleibt es weiterhin der Projektordner -
einfacher zum Testen, keine Berechtigungs-Fallstricke.

Bei PyInstaller (onedir, ab v6) landen gebuendelte Datendateien in einem
'_internal'-Unterordner (sys._MEIPASS) neben der .exe. Das sind die
mitgelieferten DEFAULT-Vorlagen (leeres config.json/.env, Beispiel-CSV,
icon.ico) - seed_default_files() kopiert sie beim allerersten Start nach
get_base_dir(), falls dort noch nichts existiert.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values
from platformdirs import user_data_dir

PLACEHOLDER_TOKEN = "your_paperless_api_token_here"

DEFAULT_CONFIG = {
    "csv_mappings": {},
    "amount_detection": {
        "method": "filename_regex",
        "regex_pattern": r"_EUR(\d+\.\d+)",
        "custom_field_id": None,
        "custom_field_name": None,
    },
    # Frei vergebene "Sonstiges"-Tags (z.B. "Spende", "Darlehen") werden hier
    # als {name: Nutzungszaehler} gelernt/gemerkt, sobald sie einmal verwendet
    # wurden. Der Zaehler entscheidet, welche Tags oben als Schnell-Buttons
    # neben Privat/Einzahlung/Umbuchung befoerdert werden.
    "custom_tags": {},
    # Merkt sich {Verwendungszweck: Tag}, sobald eine Transaktion getaggt
    # wird. Kuenftige Buchungen mit EXAKT demselben Verwendungszweck werden
    # beim naechsten CSV-Import automatisch mit demselben Tag versehen.
    "purpose_tag_memory": {},
    # Frei editierbare Liste von Textbausteinen, die aus der angezeigten
    # (nicht der exportierten/rohen) Verwendungszweck-Zeile entfernt werden -
    # fuer bankspezifisches Rauschen, das keine feste Struktur wie IBAN/BIC
    # hat (die werden immer per Regex entfernt, siehe desktop_app._IBAN_RE).
    "purpose_noise_terms": ["SecureGo plus"],
    # Frei waehlbarer Zielordner fuer "ORDNER JETZT GENERIEREN" (z.B. ein
    # geteilter OneDrive-/Steuerberater-Ordner). None = Standard verwenden
    # (get_base_dir()/export, siehe desktop_state.AppState.get_export_base_dir).
    "export_dir": None,
    # Setzt in Paperless selbst einen Tag auf Dokumente, die erfolgreich
    # einer Buchung zugeordnet wurden (automatisch gematcht, manuell
    # verknuepft oder per Mehrfach-Match aufgeloest) - damit der
    # Abgleichsstatus auch direkt in Paperless sichtbar/filterbar ist. Gilt
    # NICHT fuer frisch hochgeladene PDFs (siehe TxStatus - MATCHED, aber
    # ohne matched_docs): Paperless verarbeitet Uploads asynchron, eine
    # Dokument-ID liegt dafuer noch nicht vor. Der Tag wird in Paperless
    # automatisch angelegt, falls er noch nicht existiert.
    "paperless_success_tag_enabled": True,
    "paperless_success_tag_name": "Abgeglichen",
    # Oberflaechen-Sprache (siehe i18n.py) - "de" oder "en". Wirkt erst nach
    # einem Neustart der App.
    "language": "de",
    # Optionales, ueber die Einstellungen hochgeladenes Firmenlogo - Dateiname
    # relativ zu get_base_dir() (z.B. "company_icon.png"), ersetzt dort das
    # Standard-Icon in der Sidebar und als Fenstersymbol. None = kein Logo.
    "company_icon_path": None,
}


def get_base_dir() -> Path:
    """Basisordner fuer Nutzerdaten. Als installierte Anwendung: der
    plattformuebliche Ort fuer Pro-Nutzer-Anwendungsdaten (platformdirs) -
    Windows: %APPDATA%\\PaperlessSync (roaming=True, wie zuvor hartcodiert;
    appauthor=False vermeidet den sonst ueblichen zusaetzlichen
    Herausgeber-Unterordner), macOS: ~/Library/Application
    Support/PaperlessSync, Linux: ~/.config/PaperlessSync. Der
    Installationsordner selbst (Program Files/Applications/...) ist i.d.R.
    nicht ohne erhoehte Rechte beschreibbar und nicht der vorgesehene Ort
    fuer Nutzerdaten. Im Quellcode-Betrieb: Projektordner (Repo-Root, NICHT
    dieser core/-Ordner - diese Datei liegt unter src/paperless_sync/core/,
    parents[3] geht core/ -> paperless_sync/ -> src/ -> Repo-Root)."""
    if getattr(sys, "frozen", False):
        base = Path(user_data_dir("PaperlessSync", appauthor=False, roaming=True))
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).resolve().parents[3]


def get_resource_dir() -> Path:
    """Ordner mit den zur Build-Zeit gebuendelten Default-Dateien. Bei
    PyInstaller (onedir) ist das sys._MEIPASS (der '_internal'-Ordner), im
    Quellcode-Betrieb identisch mit get_base_dir()."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return get_base_dir()


def seed_default_files(base_dir: Path, resource_dir: Path) -> None:
    """Kopiert gebuendelte Default-Dateien (.env, config.json, Beispiel-CSV)
    neben die .exe, falls dort noch keine vorhanden sind. Reines No-Op im
    Quellcode-Betrieb (dort ist base_dir == resource_dir)."""
    if base_dir == resource_dir:
        return

    for name in (".env", "config.json"):
        src = resource_dir / name
        dst = base_dir / name
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)

    example_src = resource_dir / "input" / "beispiel_kontoauszug.csv"
    example_dst = base_dir / "input" / "beispiel_kontoauszug.csv"
    if example_src.exists() and not example_dst.exists():
        example_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example_src, example_dst)


def load_env(base_dir: Path) -> dict:
    """Liest PAPERLESS_URL, PAPERLESS_TOKEN, COMPANY_NAME aus der .env.

    Fehlt die .env oder einzelne Werte, wird ein leerer String zurueckgegeben,
    damit die App auch ohne vollstaendige Konfiguration startet (nur mit
    eingeschraenkter Funktionalitaet, siehe README).
    """
    env_path = base_dir / ".env"
    values = dotenv_values(env_path) if env_path.exists() else {}

    def _get(key):
        return values.get(key) or os.environ.get(key) or ""

    return {
        "PAPERLESS_URL": _get("PAPERLESS_URL").rstrip("/"),
        "PAPERLESS_TOKEN": _get("PAPERLESS_TOKEN"),
        "COMPANY_NAME": _get("COMPANY_NAME"),
        # Optional: PKCS#12-Client-Zertifikat fuer mTLS-geschuetzte URLs (z.B.
        # Cloudflare Access). Pfad ist relativ zu get_base_dir() gespeichert,
        # damit er in Quellcode- und exe-Betrieb gleichermassen funktioniert.
        "PAPERLESS_CLIENT_CERT_PATH": _get("PAPERLESS_CLIENT_CERT_PATH"),
        "PAPERLESS_CLIENT_CERT_PASSWORD": _get("PAPERLESS_CLIENT_CERT_PASSWORD"),
    }


def save_env(base_dir: Path, values: dict) -> None:
    """Schreibt/aktualisiert Schluessel in der .env, ohne andere, dort bereits
    vorhandene Variablen zu verlieren. Wird vom Setup-Screen genutzt, damit
    Zugangsdaten direkt aus der App heraus (auch in der kompilierten .exe)
    gespeichert werden koennen, statt die Datei manuell editieren zu muessen.
    """
    env_path = base_dir / ".env"
    existing = dict(dotenv_values(env_path)) if env_path.exists() else {}
    for key, value in values.items():
        if value is not None:
            existing[key] = value
    with open(env_path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(f"{key}={value}\n")


def is_configured(env: dict) -> bool:
    """True, sobald eine Paperless-URL und ein echter (nicht der
    Platzhalter-)Token hinterlegt sind."""
    return bool(env.get("PAPERLESS_URL")) and bool(env.get("PAPERLESS_TOKEN")) and env["PAPERLESS_TOKEN"] != PLACEHOLDER_TOKEN


def _deep_merge_defaults(data: dict) -> dict:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged["csv_mappings"] = data.get("csv_mappings", {}) or {}
    merged["amount_detection"].update(data.get("amount_detection", {}) or {})

    raw_custom_tags = data.get("custom_tags", {})
    if isinstance(raw_custom_tags, list):
        # Migration vom alten Schema (reine Namensliste ohne Nutzungszaehler).
        merged["custom_tags"] = {name: 1 for name in raw_custom_tags}
    elif isinstance(raw_custom_tags, dict):
        merged["custom_tags"] = dict(raw_custom_tags)
    else:
        merged["custom_tags"] = {}

    merged["purpose_tag_memory"] = dict(data.get("purpose_tag_memory", {}) or {})
    if "purpose_noise_terms" in data and isinstance(data["purpose_noise_terms"], list):
        merged["purpose_noise_terms"] = [str(t) for t in data["purpose_noise_terms"]]
    if data.get("export_dir"):
        merged["export_dir"] = str(data["export_dir"])
    if "paperless_success_tag_enabled" in data:
        merged["paperless_success_tag_enabled"] = bool(data["paperless_success_tag_enabled"])
    if data.get("paperless_success_tag_name"):
        merged["paperless_success_tag_name"] = str(data["paperless_success_tag_name"])
    if data.get("language") in ("de", "en"):
        merged["language"] = data["language"]
    if data.get("company_icon_path"):
        merged["company_icon_path"] = str(data["company_icon_path"])

    _clean_custom_tags(merged)
    return merged


def _clean_custom_tags(merged: dict) -> None:
    """Selbstheilende Migration: das Sonstiges-Kombifeld in der Desktop-UI
    ist frei betippbar, daher konnten vor dem Fix in _on_sonstiges_apply
    verunreinigte Tag-Namen entstehen (z.B. '+ Darlehen' durch Nachahmen von
    '+ neuer Tag'). Bereinigt fuehrende '+'/Whitespace und fuehrt Tags, die
    sich nur durch Gross-/Kleinschreibung oder dieses Prefix unterscheiden,
    zusammen (Nutzungszaehler summiert), damit sie nicht laenger fragmentiert
    als mehrere getrennte Tags erscheinen."""
    cleaned_tags: dict[str, int] = {}
    rename_map: dict[str, str] = {}
    for name, count in merged["custom_tags"].items():
        clean = name.strip().lstrip("+").strip()
        if not clean:
            continue
        canonical = next((existing for existing in cleaned_tags if existing.lower() == clean.lower()), clean)
        cleaned_tags[canonical] = cleaned_tags.get(canonical, 0) + count
        rename_map[name] = canonical
    merged["custom_tags"] = cleaned_tags

    if rename_map:
        merged["purpose_tag_memory"] = {
            purpose: rename_map.get(tag, tag) for purpose, tag in merged["purpose_tag_memory"].items()
        }


def load_config(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _deep_merge_defaults(data)
        except (json.JSONDecodeError, OSError):
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(path: Path, config: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def csv_signature(columns) -> str:
    """Eindeutiger Schluessel fuer ein Spalten-Layout, um Mappings pro
    Bank-CSV-Format in config.json wiederzufinden."""
    return "|".join(sorted(str(c) for c in columns))
