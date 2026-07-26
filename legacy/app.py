"""Paperless Sync - automatischer Abgleich zwischen Paperless-ngx und einem
Bank-CSV-Kontoauszug fuer eine luecken lose Steuer-Beleg-Zuordnung.

ARCHIVIERT: alte Streamlit-Oberflaeche, nicht mehr aktiv gepflegt - siehe
legacy/README.md. Aktuelle Oberflaeche: desktop_app_qt.py im Repo-Root.

Start (Quellcode):  streamlit run legacy/app.py
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Die geteilten Backend-Module (config_manager, csv_utils, ...) liegen
# (noch) im Repo-Root, dieses Skript aber in legacy/ - Root-Verzeichnis
# muss deshalb explizit auf den Suchpfad, sonst schlagen die folgenden
# Imports fehl.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config_manager import (
    get_base_dir,
    get_resource_dir,
    seed_default_files,
    load_env,
    load_config,
    save_config,
    save_env,
    is_configured,
    csv_signature,
    PLACEHOLDER_TOKEN,
)
from csv_utils import read_csv_raw, detect_encoding_and_delimiter
from paperless_client import PaperlessClient
from matcher import build_transactions, fetch_and_prepare_paperless_docs, match_transactions
from exporter import generate_export
from session_store import save_session, load_session
from backup import create_backup, restore_backup, backup_filename

BASE_DIR = get_base_dir()
seed_default_files(BASE_DIR, get_resource_dir())

CONFIG_PATH = BASE_DIR / "config.json"
INPUT_DIR = BASE_DIR / "input"
EXPORT_DIR = BASE_DIR / "export"
EXAMPLE_CSV_PATH = INPUT_DIR / "beispiel_kontoauszug.csv"
INPUT_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

ENV = load_env(BASE_DIR)

st.set_page_config(page_title="Paperless Sync", page_icon="📎", layout="wide")


# ---------------------------------------------------------------------------
# Session state initialisieren
# ---------------------------------------------------------------------------
def init_state():
    defaults = {
        "config": load_config(CONFIG_PATH),
        "csv_signature": None,
        "csv_columns": [],
        "mapping_confirmed": False,
        "pending_mapping": {"date_column": None, "amount_column": None, "purpose_column": None},
        "transactions": [],
        "custom_fields": None,
        "matched_once": False,
        "setup_mode": not is_configured(ENV),
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # Einmal pro Browser-/App-Sitzung versuchen, den zuletzt gespeicherten
    # Arbeitsstand wiederherzustellen (CSV + alle Matches/Tags/Uploads) -
    # damit beim Schliessen der App nichts verloren geht, auch wenn noch
    # nicht exportiert wurde.
    if "session_restore_attempted" not in st.session_state:
        st.session_state.session_restore_attempted = True
        if not st.session_state.setup_mode:
            restored = load_session(BASE_DIR)
            if restored:
                st.session_state.csv_signature = restored.get("csv_signature")
                st.session_state.csv_columns = restored.get("csv_columns") or []
                st.session_state.csv_delimiter = restored.get("csv_delimiter")
                st.session_state.csv_encoding = restored.get("csv_encoding")
                st.session_state.csv_df = restored.get("csv_df")
                st.session_state.mapping_confirmed = restored.get("mapping_confirmed", False)
                st.session_state.pending_mapping = restored.get("pending_mapping") or st.session_state.pending_mapping
                st.session_state.matched_once = restored.get("matched_once", False)
                st.session_state.transactions = restored.get("transactions") or []


init_state()
config = st.session_state.config


# ---------------------------------------------------------------------------
# Wiederverwendbare UI-Bausteine (im Setup-Screen UND in den
# Sidebar-Einstellungen genutzt, um die Logik nicht zu duplizieren)
# ---------------------------------------------------------------------------
def render_mapping_selectors(columns: list[str], key_prefix: str, current: dict | None = None):
    """Zeigt die drei Spalten-Selectboxen und gibt das Mapping-Dict zurueck,
    sobald alle drei belegt sind - sonst None."""
    current = current or {}

    def _idx(colname):
        return columns.index(colname) + 1 if colname in columns else 0

    date_col = st.selectbox("Spalte fuer Datum", ["-"] + columns, index=_idx(current.get("date_column")), key=f"{key_prefix}_date")
    amount_col = st.selectbox("Spalte fuer Betrag", ["-"] + columns, index=_idx(current.get("amount_column")), key=f"{key_prefix}_amount")
    purpose_col = st.selectbox("Spalte fuer Verwendungszweck", ["-"] + columns, index=_idx(current.get("purpose_column")), key=f"{key_prefix}_purpose")

    if "-" in (date_col, amount_col, purpose_col):
        return None
    return {"date_column": date_col, "amount_column": amount_col, "purpose_column": purpose_col}


def render_amount_detection_editor(client, current_detection: dict, key_prefix: str) -> dict:
    """Zeigt die Auswahl der Beleg-Erkennungs-Methode (Rechnungsformat) und
    gibt das (noch nicht gespeicherte) amount_detection-Dict zurueck."""
    method_label = st.radio(
        "Methode",
        ["Dateiname (Regex)", "Paperless Custom Field"],
        index=0 if current_detection.get("method", "filename_regex") == "filename_regex" else 1,
        key=f"{key_prefix}_method",
    )
    new_detection = dict(current_detection)
    if method_label == "Dateiname (Regex)":
        new_detection["method"] = "filename_regex"
        pattern = st.text_input(
            "Regex-Muster (1. Gruppe = Betrag)",
            value=current_detection.get("regex_pattern") or r"_EUR(\d+\.\d+)",
            key=f"{key_prefix}_pattern",
        )
        new_detection["regex_pattern"] = pattern
    else:
        new_detection["method"] = "custom_field"
        if not client:
            st.warning("Paperless-Zugangsdaten oben zuerst eingeben (und testen).")
        else:
            cache_key = f"{key_prefix}_custom_fields_cache"
            if cache_key not in st.session_state:
                try:
                    st.session_state[cache_key] = client.get_custom_fields()
                except Exception as exc:
                    st.error(f"Custom Fields konnten nicht geladen werden: {exc}")
                    st.session_state[cache_key] = []
            fields = st.session_state[cache_key] or []
            if fields:
                names = [f["name"] for f in fields]
                cur_name = current_detection.get("custom_field_name")
                idx = names.index(cur_name) if cur_name in names else 0
                selected_name = st.selectbox("Custom Field mit Rechnungsbetrag", names, index=idx, key=f"{key_prefix}_field")
                selected_field = next(f for f in fields if f["name"] == selected_name)
                new_detection["custom_field_id"] = selected_field["id"]
                new_detection["custom_field_name"] = selected_field["name"]
            else:
                st.info("Keine Custom Fields in Paperless gefunden.")
    return new_detection


def get_custom_field_data_type(client, field_id):
    """data_type (z.B. 'monetary', 'float', 'string') des Custom Fields, um
    beim Verknuepfen eines bestehenden Dokuments einen passenden
    Beispiel-Wert vorzubelegen (Paperless speichert monetaere Felder als
    waehrungspraefixierten String, z.B. 'EUR12.34')."""
    cache_key = "link_doc_custom_fields_cache"
    if cache_key not in st.session_state:
        try:
            st.session_state[cache_key] = client.get_custom_fields()
        except Exception:
            st.session_state[cache_key] = []
    for f in st.session_state[cache_key]:
        if f.get("id") == field_id:
            return f.get("data_type")
    return None


def load_example_dataframe():
    """Laedt die mitgelieferte Beispiel-CSV (input/beispiel_kontoauszug.csv)."""
    raw_bytes = EXAMPLE_CSV_PATH.read_bytes()
    text, encoding, delimiter = detect_encoding_and_delimiter(raw_bytes)
    df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("")
    return df, encoding, delimiter


# Feste Schnell-Tags fuer Buchungen ohne Beleg, die trotzdem erklaert/erledigt
# sind (kein "fehlender" Beleg im eigentlichen Sinn). "Sonstiges" ergaenzt das
# um frei vergebene, in config.json gelernte Tags.
BUILTIN_TAGS = ["PRIVAT", "EINZAHLUNG", "UMBUCHUNG"]
TAG_ICONS = {"PRIVAT": "🔒", "EINZAHLUNG": "💰", "UMBUCHUNG": "🔄"}


def amount_badge_html(tx: dict) -> str:
    """Farbiges Betrags-Badge, das klar zwischen Einnahme (gruen) und
    Ausgabe (rot) unterscheidet - der reine Absolutbetrag allein liess das
    bisher nicht erkennen."""
    if tx["amount_raw"] >= 0:
        return f'<span style="color:#2f9e44;font-weight:700;">▲ {tx["amount_abs"]:.2f} € · Einnahme</span>'
    return f'<span style="color:#e03131;font-weight:700;">▼ {tx["amount_abs"]:.2f} € · Ausgabe</span>'


def status_badge_html(tx: dict) -> str:
    if tx["status"] == "matched":
        return "🔗 Automatisch zugeordnet"
    if tx["status"] == "uploaded":
        return "📤 Hochgeladen"
    if tx["status"] == "tagged":
        tag = tx.get("tag") or "SONSTIGES"
        icon = TAG_ICONS.get(tag, "🏷️")
        return f"{icon} {tag.capitalize()}"
    return ""


def normalize_purpose(purpose: str) -> str:
    """Normalisiert einen Verwendungszweck fuer den Tag-Lern-Abgleich.

    Echte Bank-Verwendungszwecke betten pro Buchung fast immer einzigartige
    Ziffernfolgen ein (Datum, Uhrzeit, Betrag, Kartenterminal-/Referenz-
    nummern, z.B. 'REST MUENZEINZAHLUNG VOM 13.07.2024' oder 'GA
    70169450/00007635/...'). Ein Vergleich auf dem Rohtext wuerde bei einer
    wiederkehrenden Buchung (gleicher Haendler/Zweck, anderes Datum/Betrag)
    daher so gut wie nie erneut treffen. Ziffern werden deshalb entfernt und
    Whitespace normalisiert, bevor verglichen wird."""
    without_digits = re.sub(r"\d+", "", purpose)
    return re.sub(r"\s+", " ", without_digits).strip().upper()


def migrate_purpose_tag_memory(config: dict, config_path) -> None:
    """Alte purpose_tag_memory-Eintraege, die noch mit dem unnormalisierten
    Verwendungszweck als Schluessel gespeichert wurden, einmalig auf den
    normalisierten Schluessel umstellen (siehe normalize_purpose). Ohne das
    wuerden vor diesem Fix gelernte Zuordnungen nie wieder als Vorschlag
    auftauchen."""
    memory = config.get("purpose_tag_memory", {})
    if not memory:
        return
    migrated = {}
    changed = False
    for key, tag in memory.items():
        norm_key = normalize_purpose(key)
        if norm_key != key:
            changed = True
        migrated[norm_key] = tag
    if changed:
        config["purpose_tag_memory"] = migrated
        save_config(config_path, config)


def apply_tag(tx: dict, tag_name: str) -> None:
    tx["status"] = "tagged"
    tx["tag"] = tag_name
    tx["note"] = ""


def top_custom_tags(config: dict, limit: int = 3) -> list[str]:
    """Die 'limit' meistgenutzten eigenen Tags, absteigend nach
    Nutzungshaeufigkeit - werden neben Privat/Einzahlung/Umbuchung als
    Schnell-Buttons befoerdert."""
    counts = config.get("custom_tags", {})
    return [name for name, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def apply_tag_and_learn(tx: dict, tag_name: str, config: dict, config_path, all_transactions: list[dict] | None = None) -> None:
    """Wendet einen Tag an (Privat/Einzahlung/Umbuchung oder eigener Tag) und
    merkt sich zwei Dinge in config.json:
    1. Fuer eigene Tags: einen Nutzungszaehler, damit haeufig genutzte Tags
       nach oben in die Schnell-Buttons wandern (top_custom_tags).
    2. Fuer JEDEN Tag: die Zuordnung (normalisierter) Verwendungszweck -> Tag,
       damit bei kuenftigen, gleichartigen Buchungen (gleiches Muster, nur
       Datum/Betrag/Referenznummern unterscheiden sich) ein Vorschlag
       erscheint (siehe suggest_learned_tags, normalize_purpose).

    Scannt anschliessend sofort die uebrigen (noch unbearbeiteten)
    Transaktionen der aktuellen CSV neu - der haeufigste Fall ist eine
    wiederkehrende Buchung, die MEHRFACH im selben Kontoauszug auftaucht;
    ohne diesen Re-Scan wuerde der Vorschlag erst beim naechsten CSV-Import
    erscheinen."""
    apply_tag(tx, tag_name)
    tx["suggested_tag"] = None
    if tag_name not in BUILTIN_TAGS:
        counts = config.setdefault("custom_tags", {})
        counts[tag_name] = counts.get(tag_name, 0) + 1
    memory = config.setdefault("purpose_tag_memory", {})
    memory[normalize_purpose(tx["purpose"])] = tag_name
    save_config(config_path, config)
    if all_transactions is not None:
        suggest_learned_tags(all_transactions, config)


def suggest_learned_tags(transactions: list[dict], config: dict) -> int:
    """Markiert noch unbearbeitete Transaktionen (status None) mit einem Tag-
    VORSCHLAG (tx['suggested_tag']), wenn der normalisierte Verwendungszweck
    (siehe normalize_purpose) mit einer frueher getaggten Buchung
    uebereinstimmt. Der Status bleibt unveraendert -
    nichts wird automatisch als erledigt markiert, der Vorschlag muss im UI
    per Klick bestaetigt werden. Gibt die Anzahl Vorschlaege zurueck."""
    memory = config.get("purpose_tag_memory", {})
    if not memory:
        return 0
    suggested = 0
    for tx in transactions:
        if tx["status"] is None:
            learned_tag = memory.get(normalize_purpose(tx["purpose"]))
            if learned_tag:
                tx["suggested_tag"] = learned_tag
                suggested += 1
    return suggested


# ---------------------------------------------------------------------------
# Setup-Screen: einmalige Ersteinrichtung (Zugangsdaten, Beispiel-CSV-Mapping,
# Rechnungsformat) - laeuft auch in der kompilierten .exe, ohne dass jemand
# die .env von Hand editieren muss.
# ---------------------------------------------------------------------------
def render_setup_screen():
    st.title("📎 Paperless Sync – Einrichtung")
    st.caption(
        "Einmalige Einrichtung: Zugangsdaten, Beispiel-Kontoauszug und Rechnungsformat. "
        "Kann jederzeit ueber '⚙️ Setup erneut oeffnen' in der Sidebar wiederholt werden."
    )

    st.header("1️⃣ Zugangsdaten")
    company = st.text_input("Firmenname", value=ENV["COMPANY_NAME"], key="setup_company")
    url = st.text_input("Paperless-URL", value=ENV["PAPERLESS_URL"] or "http://localhost:8000", key="setup_url")
    existing_token = "" if ENV["PAPERLESS_TOKEN"] in ("", PLACEHOLDER_TOKEN) else ENV["PAPERLESS_TOKEN"]
    token = st.text_input(
        "Paperless-API-Token",
        value=existing_token,
        type="password",
        key="setup_token",
        help="Paperless-ngx -> Einstellungen -> Benutzer -> API-Token anzeigen/erstellen",
    )

    st.markdown("**🔒 Optional: Client-Zertifikat (mTLS, z. B. Cloudflare Access)**")
    st.caption(
        "Nur ausfuellen, wenn die Paperless-URL oeffentlich per Client-Zertifikat "
        "(PKCS#12 / .p12) abgesichert ist, z. B. hinter Cloudflare Access mit mTLS."
    )
    existing_cert_name = ENV.get("PAPERLESS_CLIENT_CERT_PATH") or ""
    existing_cert_full_path = BASE_DIR / existing_cert_name if existing_cert_name else None
    has_existing_cert = bool(existing_cert_full_path and existing_cert_full_path.exists())
    if has_existing_cert:
        st.caption(f"Aktuell hinterlegt: {existing_cert_full_path.name}")

    cert_file = st.file_uploader("Client-Zertifikat (.p12 / .pfx)", type=["p12", "pfx"], key="setup_cert_file")
    cert_password = st.text_input(
        "Zertifikat-Passwort (falls vorhanden)",
        value=ENV.get("PAPERLESS_CLIENT_CERT_PASSWORD") or "",
        type="password",
        key="setup_cert_password",
    )

    cert_bytes_for_client = cert_file.getvalue() if cert_file is not None else None
    cert_path_for_client = str(existing_cert_full_path) if (cert_bytes_for_client is None and has_existing_cert) else None

    temp_client = None
    temp_client_error = None
    if url and token:
        try:
            temp_client = PaperlessClient(
                url,
                token,
                client_cert_bytes=cert_bytes_for_client,
                client_cert_path=cert_path_for_client,
                client_cert_password=cert_password,
            )
        except Exception as exc:
            temp_client_error = str(exc)

    if temp_client_error:
        st.error(f"Client-Zertifikat konnte nicht geladen werden: {temp_client_error}")

    if st.button("🔌 Verbindung testen"):
        if not temp_client:
            st.warning("Bitte URL, Token (und ggf. ein gueltiges Zertifikat) eingeben.")
        elif temp_client.test_connection():
            st.success("Verbindung erfolgreich.")
        else:
            st.error("Verbindung fehlgeschlagen - bitte URL, Token und Zertifikat pruefen.")

    st.divider()
    st.header("2️⃣ Beispiel-Kontoauszug (CSV)")
    st.caption(
        "Ordnet die Spalten fuer Datum, Betrag und Verwendungszweck zu. Nutze die "
        "mitgelieferte Beispieldatei zum Ausprobieren oder lade direkt einen echten "
        "Auszug deiner Bank hoch."
    )

    example_available = EXAMPLE_CSV_PATH.exists()
    use_bundled = False
    if example_available:
        use_bundled = st.checkbox("Mitgelieferte Beispiel-CSV verwenden", value=True, key="setup_use_bundled")

    df_for_mapping = None
    columns_for_mapping: list[str] = []

    if use_bundled and example_available:
        df_for_mapping, encoding, delimiter = load_example_dataframe()
        st.caption(f"Beispieldatei erkannt: Encoding={encoding}, Trennzeichen='{delimiter}'")
    else:
        example_csv = st.file_uploader("CSV hochladen", type=["csv"], key="setup_example_csv")
        if example_csv is not None:
            df_for_mapping, encoding, delimiter, _ = read_csv_raw(example_csv)
            st.caption(f"Erkannt: Encoding={encoding}, Trennzeichen='{delimiter}'")

    mapping = None
    if df_for_mapping is not None:
        st.dataframe(df_for_mapping.head(5), width="stretch")
        columns_for_mapping = list(df_for_mapping.columns)
        mapping = render_mapping_selectors(columns_for_mapping, key_prefix="setup_mapping")
        if mapping is None:
            st.caption("Bitte alle drei Spalten auswaehlen, damit das Mapping gespeichert wird.")
    else:
        st.info("Optional - kann auch spaeter beim ersten echten CSV-Import konfiguriert werden.")

    st.divider()
    st.header("3️⃣ Rechnungsformat (Beleg-Erkennung)")
    st.caption("Legt fest, wie das Skript den Rechnungsbetrag eines Paperless-Dokuments ermittelt.")
    new_amount_detection = render_amount_detection_editor(temp_client, config["amount_detection"], key_prefix="setup_ad")

    st.divider()
    can_finish = bool(url) and bool(token) and token != PLACEHOLDER_TOKEN
    if not can_finish:
        st.warning("Paperless-URL und ein echter API-Token sind Pflicht, um fortzufahren.")

    if st.button("✅ Setup abschliessen & App starten", disabled=not can_finish, type="primary"):
        env_updates = {"PAPERLESS_URL": url.rstrip("/"), "PAPERLESS_TOKEN": token, "COMPANY_NAME": company}
        if cert_file is not None:
            cert_dest = BASE_DIR / "paperless_client_cert.p12"
            cert_dest.write_bytes(cert_file.getvalue())
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = cert_dest.name
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = cert_password
        elif has_existing_cert:
            env_updates["PAPERLESS_CLIENT_CERT_PATH"] = existing_cert_name
            env_updates["PAPERLESS_CLIENT_CERT_PASSWORD"] = cert_password
        save_env(BASE_DIR, env_updates)
        if mapping and columns_for_mapping:
            col_sig = csv_signature(columns_for_mapping)
            config["csv_mappings"][col_sig] = mapping
        config["amount_detection"] = new_amount_detection
        save_config(CONFIG_PATH, config)
        st.session_state.setup_mode = False
        st.rerun()


if st.session_state.setup_mode:
    render_setup_screen()
    st.stop()


# ---------------------------------------------------------------------------
# Normaler Betrieb
# ---------------------------------------------------------------------------
if "purpose_memory_migrated" not in st.session_state:
    st.session_state.purpose_memory_migrated = True
    migrate_purpose_tag_memory(config, CONFIG_PATH)

client = None
if ENV["PAPERLESS_URL"] and ENV["PAPERLESS_TOKEN"]:
    cert_path = None
    if ENV.get("PAPERLESS_CLIENT_CERT_PATH"):
        candidate = BASE_DIR / ENV["PAPERLESS_CLIENT_CERT_PATH"]
        if candidate.exists():
            cert_path = str(candidate)
    try:
        client = PaperlessClient(
            ENV["PAPERLESS_URL"],
            ENV["PAPERLESS_TOKEN"],
            client_cert_path=cert_path,
            client_cert_password=ENV.get("PAPERLESS_CLIENT_CERT_PASSWORD"),
        )
    except Exception as exc:
        st.sidebar.error(f"Paperless-Client konnte nicht initialisiert werden: {exc}")


with st.sidebar:
    st.title("📎 Paperless Sync")
    st.caption(ENV["COMPANY_NAME"] or "Beleg-Abgleich fuer den Steuerberater")
    if st.button("⚙️ Setup erneut oeffnen", width="stretch"):
        st.session_state.setup_mode = True
        st.rerun()

    with st.expander("💾 Datensicherung"):
        st.caption(
            "Sichert Einstellungen, gelernte Tags, Paperless-Zugangsdaten und "
            "den aktuellen Arbeitsstand als ZIP - z.B. vor einem Rechnerwechsel."
        )
        st.download_button(
            "⬇️ Backup erstellen",
            data=create_backup(BASE_DIR),
            file_name=backup_filename(),
            mime="application/zip",
            width="stretch",
        )

        st.divider()
        st.caption("Backup wiederherstellen (ueberschreibt Einstellungen, Zugangsdaten und Arbeitsstand):")
        restore_file = st.file_uploader("Backup-ZIP hochladen", type=["zip"], key="restore_upload")
        if restore_file is not None:
            st.warning("Achtung: ueberschreibt die aktuelle Konfiguration und den Arbeitsstand unwiderruflich.")
            if st.button("♻️ Backup wiederherstellen", type="primary", width="stretch"):
                try:
                    restored = restore_backup(BASE_DIR, restore_file.getvalue())
                    if restored:
                        st.success(f"Wiederhergestellt: {', '.join(restored)}. App wird neu geladen ...")
                        st.session_state.clear()
                        st.rerun()
                    else:
                        st.error("Das ZIP enthaelt keine bekannten Backup-Dateien (config.json / .env / session_state.json).")
                except Exception as exc:
                    st.error(f"Wiederherstellung fehlgeschlagen: {exc}")

    st.divider()
    uploaded_csv = st.file_uploader("Bank-Kontoauszug (CSV)", type=["csv"])

    if uploaded_csv is not None:
        sig = f"{uploaded_csv.name}_{uploaded_csv.size}"
        if sig != st.session_state.csv_signature:
            df, encoding, delimiter, _raw_bytes = read_csv_raw(uploaded_csv)
            st.session_state.csv_df = df
            st.session_state.csv_columns = list(df.columns)
            st.session_state.csv_signature = sig
            st.session_state.csv_encoding = encoding
            st.session_state.csv_delimiter = delimiter
            st.session_state.transactions = []
            st.session_state.matched_once = False

            col_sig = csv_signature(df.columns)
            saved_mapping = config["csv_mappings"].get(col_sig)
            if saved_mapping:
                st.session_state.pending_mapping = saved_mapping
                st.session_state.mapping_confirmed = True
                st.session_state.transactions = build_transactions(df, saved_mapping)
                suggest_learned_tags(st.session_state.transactions, config)
            else:
                st.session_state.pending_mapping = {"date_column": None, "amount_column": None, "purpose_column": None}
                st.session_state.mapping_confirmed = False

    with st.expander("⚙️ Einstellungen", expanded=not st.session_state.mapping_confirmed):
        st.subheader("CSV-Spalten-Mapping")
        if not st.session_state.csv_columns:
            st.caption("CSV hochladen, um das Mapping zu konfigurieren.")
        else:
            cols = st.session_state.csv_columns
            cur = st.session_state.pending_mapping
            mapping_candidate = render_mapping_selectors(cols, key_prefix="sidebar_mapping", current=cur)

            if st.button("Mapping bestaetigen & speichern"):
                if mapping_candidate is None:
                    st.error("Bitte alle drei Spalten auswaehlen.")
                else:
                    st.session_state.pending_mapping = mapping_candidate
                    st.session_state.mapping_confirmed = True
                    col_sig = csv_signature(cols)
                    config["csv_mappings"][col_sig] = mapping_candidate
                    save_config(CONFIG_PATH, config)
                    st.session_state.transactions = build_transactions(st.session_state.csv_df, mapping_candidate)
                    suggest_learned_tags(st.session_state.transactions, config)
                    st.session_state.matched_once = False
                    st.success("Mapping gespeichert.")
                    st.rerun()

        st.divider()
        st.subheader("Beleg-Erkennung (Paperless)")
        new_amount_detection = render_amount_detection_editor(client, config["amount_detection"], key_prefix="sidebar_ad")

        if st.button("Einstellungen speichern"):
            config["amount_detection"] = new_amount_detection
            save_config(CONFIG_PATH, config)
            st.session_state.matched_once = False
            st.success("Einstellungen gespeichert.")
            st.rerun()

    st.divider()
    if client:
        connected = client.test_connection()
        st.write("🟢 Paperless verbunden" if connected else "🔴 Paperless nicht erreichbar")
    else:
        st.write("⚪ Paperless nicht konfiguriert")

    can_match = bool(st.session_state.transactions) and client is not None
    if st.button("🔍 Mit Paperless abgleichen", disabled=not can_match):
        with st.spinner("Lade Paperless-Dokumente und gleiche ab..."):
            try:
                docs = fetch_and_prepare_paperless_docs(client, config["amount_detection"])
                match_transactions(st.session_state.transactions, docs)
                st.session_state.matched_once = True
                st.session_state.paperless_docs_raw = docs
                st.success(f"{len(docs)} Paperless-Dokumente geladen, Abgleich abgeschlossen.")
            except Exception as exc:
                st.error(f"Abgleich fehlgeschlagen: {exc}")

    months = sorted({tx["date"].strftime("%Y-%m") for tx in st.session_state.transactions})
    selected_month = st.selectbox("Monat (fuer Export)", months) if months else None

    st.divider()
    if st.button("📁 Ordner jetzt generieren", disabled=not (selected_month and st.session_state.transactions)):
        try:
            export_path = generate_export(
                BASE_DIR,
                selected_month,
                st.session_state.transactions,
                st.session_state.csv_df,
                st.session_state.csv_delimiter,
                client,
            )
            st.success(f"Export erstellt: {export_path}")
        except Exception as exc:
            st.error(f"Export fehlgeschlagen: {exc}")


# ---------------------------------------------------------------------------
# Hauptbereich
# ---------------------------------------------------------------------------
transactions = st.session_state.transactions

SUCCESS_STATUSES = ("matched", "tagged", "uploaded")
ACTION_STATUSES = (None, "missing", "unclear")

success_count = sum(1 for t in transactions if t["status"] in SUCCESS_STATUSES)
action_count = sum(1 for t in transactions if t["status"] in ACTION_STATUSES)

col1, col2 = st.columns(2)
with col1:
    st.markdown(
        f"""
        <div style="background-color:#1e5631;border:1px solid #2f9e44;border-radius:10px;padding:22px;text-align:center;">
            <div style="font-size:14px;color:#d3f9d8;">✅ Belege gefunden/zugeordnet</div>
            <div style="font-size:36px;font-weight:700;color:#ffffff;">{success_count}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        f"""
        <div style="background-color:#7d1f2b;border:1px solid #e03131;border-radius:10px;padding:22px;text-align:center;">
            <div style="font-size:14px;color:#ffe3e3;">⚠️ Aktion erforderlich</div>
            <div style="font-size:36px;font-weight:700;color:#ffffff;">{action_count}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.write("")

if not transactions:
    st.info("Bitte in der Sidebar eine Bank-CSV hochladen, um zu starten.")
else:
    tab_ok, tab_action = st.tabs(["✅ Erfolgreich", "⚠️ Fehlt / Unklar"])

    with tab_ok:
        ok_txs = [t for t in transactions if t["status"] in SUCCESS_STATUSES]
        if not ok_txs:
            st.info("Noch keine zugeordneten Belege.")
        for tx in ok_txs:
            with st.container(border=True):
                c = st.columns([1, 2, 3, 4, 3])
                c[0].markdown(f"**{tx['id']}**")
                c[1].write(tx["date"].strftime("%d.%m.%Y"))
                c[2].markdown(amount_badge_html(tx), unsafe_allow_html=True)
                c[3].write(tx["purpose"])
                c[4].write(status_badge_html(tx))

    with tab_action:
        action_txs = [t for t in transactions if t["status"] in ACTION_STATUSES]
        if not action_txs:
            st.success("Alles zugeordnet! 🎉")
        for tx in action_txs:
            with st.container(border=True):
                st.markdown(
                    f"**ID {tx['id']}** &nbsp;|&nbsp; {tx['date'].strftime('%d.%m.%Y')} "
                    f"&nbsp;|&nbsp; {amount_badge_html(tx)} &nbsp;|&nbsp; {tx['purpose']}",
                    unsafe_allow_html=True,
                )
                if tx["status"] == "unclear":
                    st.warning(tx["note"])
                elif not st.session_state.matched_once:
                    st.caption("Noch nicht mit Paperless abgeglichen.")

                if tx.get("suggested_tag"):
                    sugg = tx["suggested_tag"]
                    icon = TAG_ICONS.get(sugg, "🏷️")
                    label = sugg.capitalize() if sugg in BUILTIN_TAGS else sugg
                    sc1, sc2 = st.columns([4, 1])
                    sc1.info(f"💡 Vorschlag: {icon} **{label}** - aehnliche Buchung (gleiches Muster) bereits so getaggt.")
                    if sc2.button("Übernehmen", key=f"confirm_suggestion_{tx['id']}"):
                        apply_tag_and_learn(tx, sugg, config, CONFIG_PATH, transactions)
                        st.rerun()

                pdf_file = st.file_uploader(
                    "PDF hochladen",
                    type=["pdf"],
                    key=f"upload_{tx['id']}",
                    disabled=client is None,
                    help=None if client else "Paperless nicht konfiguriert",
                )
                if pdf_file is not None and client is not None:
                    try:
                        file_bytes = pdf_file.getvalue()
                        client.upload_document(file_bytes, pdf_file.name)
                        tx["status"] = "uploaded"
                        tx["uploaded_bytes"] = file_bytes
                        tx["uploaded_name"] = pdf_file.name
                        tx["note"] = ""
                        st.success(f"{pdf_file.name} an Paperless gesendet.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Upload fehlgeschlagen: {exc}")

                if client is not None and config["amount_detection"].get("method") == "custom_field":
                    with st.expander("🔗 Vorhandenes Paperless-Dokument verknuepfen (Betrag nachtragen)"):
                        docs_raw = st.session_state.get("paperless_docs_raw")
                        if not docs_raw:
                            st.caption("Bitte zuerst '🔍 Mit Paperless abgleichen' klicken, um die Dokumentliste zu laden.")
                        else:
                            doc_options = {
                                f"#{d['id']} - {d['title'] or d['original_file_name'] or 'ohne Titel'} "
                                f"· {d.get('correspondent_name') or 'kein Korrespondent'} "
                                f"· {d['date'].strftime('%d.%m.%Y') if d['date'] else 'kein Datum'} "
                                f"(aktuell: {d['amount'] if d['amount'] is not None else 'leer'})": d
                                for d in docs_raw
                            }
                            chosen_label = st.selectbox(
                                "Dokument (in Paperless bereits vorhanden, Betrag dort noch nicht gesetzt)",
                                ["-"] + list(doc_options.keys()),
                                key=f"linkdoc_select_{tx['id']}",
                            )
                            if chosen_label != "-":
                                chosen_doc = doc_options[chosen_label]
                                field_id = config["amount_detection"].get("custom_field_id")
                                data_type = get_custom_field_data_type(client, field_id)
                                default_value = (
                                    f"EUR{tx['amount_abs']:.2f}" if data_type == "monetary" else f"{tx['amount_abs']:.2f}"
                                )
                                entered_value = st.text_input(
                                    "Wert fuer das Custom Field",
                                    value=default_value,
                                    key=f"linkdoc_value_{tx['id']}",
                                    help="Format pruefen: an einem bereits befuellten Paperless-Dokument orientieren, "
                                    "falls das Format hier nicht passt (z.B. anderes Waehrungskuerzel).",
                                )
                                if st.button("Betrag setzen & verknuepfen", key=f"linkdoc_apply_{tx['id']}"):
                                    try:
                                        client.update_custom_field_value(chosen_doc["id"], field_id, entered_value)
                                        tx["status"] = "matched"
                                        tx["matched_doc"] = dict(chosen_doc)
                                        tx["note"] = ""
                                        st.success(f"Custom Field gesetzt und Dokument #{chosen_doc['id']} verknuepft.")
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Fehlgeschlagen: {exc}")

                st.caption("Kein Beleg noetig - als was einordnen?")
                promoted_tags = top_custom_tags(config, limit=3)
                quick_tags = BUILTIN_TAGS + promoted_tags
                tag_cols = st.columns(len(quick_tags))
                for i, tag_name in enumerate(quick_tags):
                    is_builtin = tag_name in BUILTIN_TAGS
                    icon = TAG_ICONS.get(tag_name, "🏷️")
                    label = f"{icon} {tag_name.capitalize()}" if is_builtin else f"{icon} {tag_name}"
                    if tag_cols[i].button(label, key=f"tag_{tag_name}_{tx['id']}"):
                        apply_tag_and_learn(tx, tag_name, config, CONFIG_PATH, transactions)
                        st.rerun()

                custom_tags_counts = config.get("custom_tags", {})
                other_custom_tags = [t for t in custom_tags_counts if t not in promoted_tags]
                with st.expander("🏷️ Sonstiges (weiterer Tag)"):
                    options = other_custom_tags + ["+ neuer Tag"]
                    chosen = st.selectbox("Tag", options, key=f"customtag_select_{tx['id']}")
                    if chosen == "+ neuer Tag":
                        new_tag = st.text_input("Neuer Tag-Name", key=f"customtag_new_{tx['id']}")
                        if st.button("Anwenden", key=f"customtag_apply_new_{tx['id']}"):
                            tag_clean = new_tag.strip()
                            if not tag_clean:
                                st.warning("Bitte einen Tag-Namen eingeben.")
                            else:
                                apply_tag_and_learn(tx, tag_clean.upper(), config, CONFIG_PATH, transactions)
                                st.rerun()
                    else:
                        if st.button("Anwenden", key=f"customtag_apply_existing_{tx['id']}"):
                            apply_tag_and_learn(tx, chosen, config, CONFIG_PATH, transactions)
                            st.rerun()


# ---------------------------------------------------------------------------
# Arbeitsstand sichern (bei jedem Skriptlauf) - damit beim Schliessen der App
# nichts verloren geht, auch wenn noch nicht exportiert wurde.
# ---------------------------------------------------------------------------
if st.session_state.get("transactions"):
    save_session(
        BASE_DIR,
        {
            "csv_signature": st.session_state.get("csv_signature"),
            "csv_columns": st.session_state.get("csv_columns"),
            "csv_delimiter": st.session_state.get("csv_delimiter"),
            "csv_encoding": st.session_state.get("csv_encoding"),
            "csv_records": (
                st.session_state.csv_df.to_dict("records") if st.session_state.get("csv_df") is not None else None
            ),
            "mapping_confirmed": st.session_state.get("mapping_confirmed"),
            "pending_mapping": st.session_state.get("pending_mapping"),
            "matched_once": st.session_state.get("matched_once"),
            "transactions": st.session_state.transactions,
        },
    )
