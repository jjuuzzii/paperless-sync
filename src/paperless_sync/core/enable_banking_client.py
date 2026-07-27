"""Enable Banking API Client - generischer Bank-Import ueber die Enable
Banking Open-Banking-API (https://enablebanking.com). JEDER Nutzer bringt
seine eigene Registrierung mit (eigene Application-ID, eigener privater
Schluessel, eigenes Konto) - siehe config_manager.py, Abschnitt
"enable_banking". Dieses Modul selbst liest keine Config und kennt keine
Zugangsdaten von sich aus: application_id/key_path/redirect_url kommen
immer vom Aufrufer (gleiche Trennung wie bei paperless_client.PaperlessClient).

Ablauf pro Import - bewusst KEIN Session-Caching, jeder Aufruf von
authorize() bis get_transactions() ist ein vollstaendiger, unabhaengiger
Durchlauf: viele Banken unterstuetzen die PSD2-Ausnahme fuer verlaengerte
Session-Gueltigkeit nicht und verlangen bei jedem Datenabruf eine komplett
neue Autorisierung - das ist Normalfall, kein Fehlerzustand.

1. get_aspsps(client, country) - verfuegbare Banken fuer ein Land
2. authorize(client, aspsp_name, aspsp_country, redirect_url) - oeffnet den
   Bank-Login im Standardbrowser, startet einen lokalen HTTP-Listener und
   wartet auf den Redirect-Callback, gibt den Autorisierungs-Code zurueck
3. client.create_session(code) - tauscht den Code gegen eine Session mit
   der Liste autorisierter Konten
4. client.get_transactions(account_uid, date_from, date_to) - Kontobewegungen

Enable Banking verlangt in Production zwingend HTTPS-Redirect-URLs, auch
fuer localhost - deshalb der empfohlene Umweg ueber redirectmeto.com (ein
kostenloser, oeffentlicher Dienst, der HTTPS auf einen lokalen HTTP-
Listener weiterleitet, siehe config_manager.DEFAULT_CONFIG). Der
Autorisierungscode selbst ist ohne den privaten Schluessel wertlos - der
Redirect ueber einen Drittanbieter ist damit unkritisch.

authorize()/_wait_for_callback() blockieren synchron, bis der Callback
eintrifft oder das Timeout erreicht ist (bis zu 5 Minuten, solange der
Nutzer den Bank-Login im Browser durchfuehrt) - dieses Modul ist bewusst
framework-agnostisch (kein Qt-Import). Die aufrufende UI-Schicht MUSS
authorize() in einem Hintergrund-Thread ausfuehren, sonst friert das
Hauptfenster fuer die Dauer des Logins ein (siehe ui_qt/desktop_app_qt.py)."""
from __future__ import annotations

import base64
import http.server
import json
import time
import uuid
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

API_BASE_URL = "https://api.enablebanking.com"

# Muss exakt der beim Enable-Banking-Control-Panel fuer die eigene
# Anwendung hinterlegten Redirect-URL entsprechen (siehe Modul-Docstring) -
# nur als Vorbelegung fuer die Einstellungen gedacht, nicht hier aktiv
# verwendet (die tatsaechliche URL kommt immer vom Aufrufer).
DEFAULT_REDIRECT_URL = "https://redirectmeto.com/http://localhost:8765/callback"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
AUTHORIZATION_TIMEOUT_SECONDS = 300  # 5 Minuten

# Spalten-Mapping fuer matcher.build_transactions() - identisch zu dem, was
# fuer eine hochgeladene CSV in MappingDialog bestaetigt wuerde, damit
# matcher.py/exporter.py unveraendert funktionieren (siehe
# transactions_to_dataframe unten).
ENABLE_BANKING_MAPPING = {
    "date_column": "Datum",
    "amount_column": "Betrag",
    "purpose_column": "Verwendungszweck",
    "counterparty_column": "Sender",
}


class EnableBankingError(Exception):
    """Fehler bei der Kommunikation mit der Enable Banking API, beim Laden
    des privaten Schluessels oder beim Autorisierungs-Flow (abgelaufener/
    ungueltiger Code, Timeout, vom Nutzer abgebrochen) - jeweils mit einer
    verstaendlichen deutschen Meldung statt eines rohen API-/Netzwerk-
    Fehlers."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_private_key(key_path: Path):
    """Laedt den privaten Application-Key aus der (vom Aufrufer
    uebergebenen) .pem-Datei - wird NIE geschrieben oder veraendert, nur
    gelesen."""
    if not key_path.exists():
        raise EnableBankingError(
            f"Kein Enable-Banking-Schluessel gefunden unter {key_path} - bitte die von Enable Banking "
            f"heruntergeladene .pem-Datei dort ablegen (siehe Einrichtungsassistent in den Einstellungen)."
        )
    key_bytes = key_path.read_bytes()
    try:
        return serialization.load_pem_private_key(key_bytes, password=None)
    except ValueError as exc:
        raise EnableBankingError(f"Schluessel unter {key_path} ist keine gueltige PEM-Datei: {exc}") from exc


class EnableBankingClient:
    def __init__(self, application_id: str, key_path: Path):
        """application_id/key_path kommen ausschliesslich vom Aufrufer
        (aus config_manager.py ueber die UI-Schicht) - nichts davon ist
        hier hartcodiert oder wird selbst aus einer Config gelesen."""
        if not application_id:
            raise EnableBankingError("Keine Application-ID konfiguriert - siehe Einstellungen > Bank-Import.")
        self.application_id = application_id
        self._private_key = load_private_key(key_path)
        self._session = requests.Session()

    def _create_jwt(self) -> str:
        """RS256-JWT nach Enable-Banking-Konvention: kid = application_id,
        signiert mit dem privaten Schluessel. Gueltigkeit bewusst kurz (1
        Stunde) - wird bei jedem Request neu erzeugt, nicht zwischen-
        gespeichert (siehe Modul-Docstring: kein Session-Caching)."""
        header = {"typ": "JWT", "alg": "RS256", "kid": self.application_id}
        now = int(time.time())
        payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600}
        header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = self._private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header_b64}.{payload_b64}.{_b64url(signature)}"

    def _request(self, method: str, path: str, **kwargs):
        url = f"{API_BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {self._create_jwt()}", "Content-Type": "application/json"}
        try:
            resp = self._session.request(method, url, headers=headers, timeout=30, **kwargs)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise EnableBankingError(f"Enable Banking API Fehler ({method} {path}): {exc}") from exc
        return resp.json()

    def get_aspsps(self, country: str) -> list[dict]:
        """Verfuegbare Banken (ASPSPs) fuer ein Land (ISO-3166-1 alpha-2,
        z.B. 'AT', 'DE')."""
        data = self._request("GET", "/aspsps", params={"country": country})
        return data.get("aspsps", [])

    def start_authorization(
        self, aspsp_name: str, aspsp_country: str, redirect_url: str, psu_type: str = "personal"
    ) -> str:
        """Startet die Autorisierung bei der Bank - gibt die URL zurueck, zu
        der der Nutzer zum Bank-Login weitergeleitet werden muss. Fuer den
        kompletten automatischen Flow siehe authorize() weiter unten."""
        valid_until = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": aspsp_country},
            "state": str(uuid.uuid4()),
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        }
        data = self._request("POST", "/auth", json=body)
        return data["url"]

    def create_session(self, code: str) -> dict:
        """Tauscht den beim Redirect erhaltenen 'code' gegen eine Session
        mit der Liste autorisierter Konten (data['accounts'])."""
        try:
            return self._request("POST", "/sessions", json={"code": code})
        except EnableBankingError as exc:
            raise EnableBankingError(
                f"Sitzung konnte nicht erstellt werden - der Autorisierungs-Code ist evtl. abgelaufen oder "
                f"bereits verwendet. Bitte den Import erneut starten. ({exc})"
            ) from exc

    def get_transactions(self, account_uid: str, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
        """Rohe Transaktions-Dicts der Enable-Banking-API - fuer die
        Weiterverarbeitung siehe transactions_to_dataframe() unten.

        Folgt automatisch einer evtl. vorhandenen Seitierung
        (continuation_key): bei einem laengeren angefragten Zeitraum als in
        eine Antwort passt, liefert Enable Banking nur eine erste Seite +
        einen continuation_key fuer die naechste. max_pages als
        Sicherheitsnetz gegen eine Endlosschleife bei unerwartetem API-
        Verhalten.

        Manche Banken stellen ueber die Schnittstelle nur eine begrenzte
        Historie bereit (haeufig ~90 Tage), unabhaengig vom angefragten
        date_from - das ist eine Bank-Beschraenkung, kein Fehler dieser
        Funktion; die aufrufende UI sollte das dem Nutzer erklaeren, statt
        eine leere/kuerzere Ergebnisliste als Bug wirken zu lassen."""
        params = {}
        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()

        all_transactions = []
        continuation_key = None
        max_pages = 50
        for _ in range(max_pages):
            request_params = dict(params)
            if continuation_key:
                request_params["continuation_key"] = continuation_key
            data = self._request("GET", f"/accounts/{account_uid}/transactions", params=request_params)
            all_transactions.extend(data.get("transactions", []))
            continuation_key = data.get("continuation_key")
            if not continuation_key:
                break
        return all_transactions


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Nimmt einen GET-Request auf CALLBACK_PATH entgegen, liest den
    'code'- bzw. 'error'-Query-Parameter aus und legt ihn am Server-Objekt
    ab (siehe _wait_for_callback). Zeigt in JEDEM Fall eine einfache
    Bestaetigungsseite im Browser an, egal ob Erfolg oder Fehler, damit der
    Nutzer nie auf einer leeren/kaputt wirkenden Seite landet."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        query = parse_qs(parsed.query)
        code = (query.get("code") or [None])[0]
        error = (query.get("error") or [None])[0]
        self.server.received_code = code
        self.server.received_error = error

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if code:
            body = (
                "<html><body style='font-family: sans-serif; text-align: center; margin-top: 15%;'>"
                "<h2>Autorisierung erfolgreich</h2><p>Dieses Fenster kann geschlossen werden.</p>"
                "</body></html>"
            )
        else:
            body = (
                "<html><body style='font-family: sans-serif; text-align: center; margin-top: 15%;'>"
                f"<h2>Autorisierung fehlgeschlagen</h2><p>{error or 'Unbekannter Fehler'}</p>"
                "</body></html>"
            )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # keine Konsolenausgabe pro Request - waere nur Rauschen


def _wait_for_callback(timeout_seconds: int = AUTHORIZATION_TIMEOUT_SECONDS) -> str:
    """Startet den lokalen HTTP-Listener (127.0.0.1:8765/callback), wartet
    auf GENAU EINEN eingehenden Callback (oder bis zum Timeout) und
    schliesst den Server danach automatisch wieder - lauscht also nur
    waehrend eines aktiven Autorisierungs-Vorgangs, nicht dauerhaft im
    Hintergrund."""
    try:
        server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
    except OSError as exc:
        raise EnableBankingError(
            f"Lokaler Port {CALLBACK_PORT} ist bereits belegt - laeuft evtl. schon ein anderer Import? ({exc})"
        ) from exc

    server.received_code = None
    server.received_error = None
    server.timeout = 1.0  # handle_request() blockiert hoechstens so lange - damit die Deadline unten greift

    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            server.handle_request()
            if server.received_code or server.received_error:
                break
        else:
            raise EnableBankingError(
                "Zeitueberschreitung: Es kam innerhalb von 5 Minuten keine Rueckmeldung von der Bank. "
                "Bitte den Import erneut starten."
            )
    finally:
        server.server_close()

    if server.received_error:
        raise EnableBankingError(f"Die Bank hat die Autorisierung abgelehnt oder abgebrochen: {server.received_error}")
    if not server.received_code:
        raise EnableBankingError("Kein Autorisierungs-Code erhalten.")
    return server.received_code


def authorize(client: EnableBankingClient, aspsp_name: str, aspsp_country: str, redirect_url: str, psu_type: str = "personal") -> str:
    """Kompletter automatischer Autorisierungs-Flow: startet die
    Autorisierung bei der Bank, oeffnet die Login-Seite im Standard-
    Browser, startet den lokalen HTTP-Listener und wartet auf den Redirect-
    Callback. Gibt den Autorisierungs-Code zurueck (fuer
    client.create_session()).

    redirect_url MUSS exakt der im Enable-Banking-Control-Panel fuer diese
    Anwendung hinterlegten Redirect-URL entsprechen - beim empfohlenen
    redirectmeto.com-Standardwert (siehe DEFAULT_REDIRECT_URL) zeigt sie
    auf genau den Listener, den diese Funktion selbst startet.

    BLOCKIERT synchron bis zu AUTHORIZATION_TIMEOUT_SECONDS lang (siehe
    Modul-Docstring) - von der aufrufenden UI-Schicht in einem Hintergrund-
    Thread auszufuehren."""
    auth_url = client.start_authorization(aspsp_name, aspsp_country, redirect_url, psu_type=psu_type)
    webbrowser.open(auth_url)
    return _wait_for_callback()


def _iso_date_to_ddmmyyyy(iso_date: str) -> str:
    """Enable Banking liefert Datumswerte als ISO YYYY-MM-DD (ggf. mit
    Zeitanteil, z.B. '2026-05-07T10:30:00Z'). csv_utils.parse_date() nutzt
    dayfirst=True - richtig fuer echte, mehrdeutige deutsche
    Bank-CSV-Formate ("07.05.2026" ohne fuehrende Jahreszahl), aber bei
    einem YYYY-MM-DD-String mit zwei Zahlen <=12 vertauscht dateutil damit
    Tag und Monat (aus 2026-05-07 wird faelschlich der 5. Juli statt 7.
    Mai - per Test bestaetigt). Deshalb hier VOR der Weitergabe explizit
    ins eindeutige deutsche Tag.Monat.Jahr-Format umwandeln, statt das
    ISO-Format unveraendert durchzureichen."""
    if not iso_date:
        return ""
    date_part = iso_date.split("T")[0]
    parts = date_part.split("-")
    if len(parts) != 3:
        return iso_date
    year, month, day = parts
    return f"{day}.{month}.{year}"


def _transaction_to_row(tx: dict) -> dict:
    """Eine einzelne Enable-Banking-Transaktion -> dieselben Spalten, die
    auch aus einer hochgeladenen Bank-CSV entstehen wuerden. Feldnamen
    gemaess Enable-Banking-API-Dokumentation
    (https://enablebanking.com/docs/api/reference/)."""
    booking_date = _iso_date_to_ddmmyyyy(tx.get("booking_date") or tx.get("value_date") or "")

    amount_info = tx.get("transaction_amount") or {}
    amount = str(amount_info.get("amount", "0"))
    indicator = tx.get("credit_debit_indicator", "DBIT")
    signed_amount = amount.lstrip("-") if indicator == "CRDT" else f"-{amount.lstrip('-')}"

    remittance = tx.get("remittance_information") or []
    purpose = " ".join(remittance) if isinstance(remittance, list) else str(remittance)

    # Gegenpartei: bei einer Abbuchung (DBIT) ist der Empfaenger der
    # Kreditor, bei einer Einzahlung (CRDT) der Schuldner.
    counterparty_info = tx.get("creditor") if indicator == "DBIT" else tx.get("debtor")
    counterparty = (counterparty_info or {}).get("name", "")

    return {
        "Datum": booking_date,
        "Betrag": signed_amount,
        "Verwendungszweck": purpose,
        "Sender": counterparty,
    }


def transactions_to_dataframe(transactions: list[dict]) -> pd.DataFrame:
    """Wandelt die von der Enable Banking API gelieferten Transaktionen in
    dieselbe Tabellenform um, die auch aus einer hochgeladenen CSV entsteht
    (Spalten: Datum, Betrag, Verwendungszweck, Sender - passend zu
    ENABLE_BANKING_MAPPING). Das Ergebnis kann direkt an
    matcher.build_transactions(df, ENABLE_BANKING_MAPPING) uebergeben
    werden - matcher.py/exporter.py brauchen dafuer keine Aenderung, der
    Import-Weg (CSV oder API) bleibt fuer den Rest der Pipeline unsichtbar."""
    return pd.DataFrame([_transaction_to_row(tx) for tx in transactions])
