"""Duenner Wrapper um die Paperless-ngx REST-API.

Deckt ab, was fuer den Abgleich noetig ist: Dokumente + Custom Fields lesen,
ein Dokument herunterladen (fuer den Steuer-Export), ein neues Dokument
hochladen (fuer fehlende Belege) sowie den Custom-Field-Wert eines bereits
vorhandenen Dokuments nachtragen (falls der Beleg schon in Paperless liegt,
aber der Betrag dort noch fehlt).
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import requests

try:
    from requests_pkcs12 import Pkcs12Adapter
except ImportError:  # optional dependency, nur noetig bei mTLS-geschuetzten Instanzen
    Pkcs12Adapter = None


class PaperlessClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        client_cert_path: str | None = None,
        client_cert_bytes: bytes | None = None,
        client_cert_password: str | None = None,
    ):
        """client_cert_path/client_cert_bytes + client_cert_password: optionales
        PKCS#12-Client-Zertifikat (.p12/.pfx) fuer mTLS, z.B. wenn die
        Paperless-URL oeffentlich per Cloudflare Access mit Client-
        Zertifikatspflicht abgesichert ist. Das Zertifikat wirkt auf TLS-Ebene
        und ist unabhaengig vom Token, das weiterhin fuer die Paperless-API-
        Authentifizierung noetig ist. client_cert_bytes hat Vorrang vor
        client_cert_path (z.B. um ein frisch hochgeladenes Zertifikat vor dem
        Speichern auf die Platte testen zu koennen)."""
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}"})

        if client_cert_bytes or client_cert_path:
            if Pkcs12Adapter is None:
                raise RuntimeError(
                    "Client-Zertifikat konfiguriert, aber 'requests-pkcs12' ist nicht "
                    "installiert. Bitte `pip install requests-pkcs12` ausfuehren."
                )
            if client_cert_bytes:
                adapter = Pkcs12Adapter(pkcs12_data=client_cert_bytes, pkcs12_password=client_cert_password or None)
            else:
                adapter = Pkcs12Adapter(pkcs12_filename=client_cert_path, pkcs12_password=client_cert_password or None)
            self.session.mount("https://", adapter)

    def _same_origin(self, url: str) -> str:
        """Ersetzt Schema+Host+Port einer (moeglichst von Paperless selbst
        gelieferten, z.B. DRF-Pagination-'next') URL immer durch die
        konfigurierte base_url - nur Pfad+Query bleiben erhalten.

        Noetig, weil Paperless hinter einem Reverse-Proxy/Tunnel (z.B.
        Cloudflare) 'next'-Links mit dem intern von Paperless gesehenen
        Host/Port erzeugen kann (z.B. Port 7070 statt der oeffentlichen
        HTTPS-URL), falls PAPERLESS_URL/X-Forwarded-Host dort nicht korrekt
        gesetzt sind - dieser Port ist von aussen nicht erreichbar und ein
        Request dorthin haengt bis zum Timeout, was die UI einfrieren laesst."""
        parts = urlsplit(url)
        base_parts = urlsplit(self.base_url)
        return urlunsplit((base_parts.scheme, base_parts.netloc, parts.path, parts.query, ""))

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/api/documents/?page_size=1", timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_all_documents(self) -> list[dict]:
        """Liest alle Dokumente ueber alle Seiten hinweg."""
        docs = []
        url = f"{self.base_url}/api/documents/?page_size=200"
        while url:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            docs.extend(data.get("results", []))
            next_url = data.get("next")
            url = self._same_origin(next_url) if next_url else None
        return docs

    def get_custom_fields(self) -> list[dict]:
        r = self.session.get(f"{self.base_url}/api/custom_fields/?page_size=200", timeout=15)
        r.raise_for_status()
        return r.json().get("results", [])

    def get_correspondents(self) -> list[dict]:
        """Paperless liefert im Dokument nur die Korrespondent-ID, nicht den
        Namen - diese Liste wird gebraucht, um IDs zu Namen aufzuloesen."""
        docs = []
        url = f"{self.base_url}/api/correspondents/?page_size=200"
        while url:
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            docs.extend(data.get("results", []))
            next_url = data.get("next")
            url = self._same_origin(next_url) if next_url else None
        return docs

    def download_document(self, doc_id: int) -> bytes:
        r = self.session.get(f"{self.base_url}/api/documents/{doc_id}/download/", timeout=60)
        r.raise_for_status()
        return r.content

    def upload_document(self, file_bytes: bytes, filename: str) -> str:
        """POST /api/documents/post_document/ - landet in Paperless als
        neues Dokument in der Inbox (Verarbeitung erfolgt asynchron)."""
        files = {"document": (filename, file_bytes, "application/pdf")}
        r = self.session.post(f"{self.base_url}/api/documents/post_document/", files=files, timeout=60)
        r.raise_for_status()
        return r.text

    def get_document(self, doc_id: int) -> dict:
        r = self.session.get(f"{self.base_url}/api/documents/{doc_id}/", timeout=15)
        r.raise_for_status()
        return r.json()

    def get_or_create_tag(self, name: str) -> int:
        """Liefert die ID des Tags mit diesem Namen, legt ihn in Paperless
        an, falls er noch nicht existiert (z.B. beim allerersten Einsatz von
        'Abgeglichen')."""
        r = self.session.get(f"{self.base_url}/api/tags/", params={"name__iexact": name}, timeout=15)
        r.raise_for_status()
        for tag in r.json().get("results", []):
            if tag.get("name", "").lower() == name.lower():
                return tag["id"]
        r = self.session.post(f"{self.base_url}/api/tags/", json={"name": name}, timeout=15)
        r.raise_for_status()
        return r.json()["id"]

    def add_tag_to_document(self, doc_id: int, tag_id: int) -> dict:
        """Ergaenzt EINEN Tag zu einem Dokument, ohne bereits vorhandene Tags
        zu entfernen (PATCH ersetzt die komplette tags-Liste, daher zuerst
        den aktuellen Stand lesen - gleiches Muster wie
        update_custom_field_value)."""
        doc = self.get_document(doc_id)
        tags = list(doc.get("tags") or [])
        if tag_id not in tags:
            tags.append(tag_id)
            r = self.session.patch(f"{self.base_url}/api/documents/{doc_id}/", json={"tags": tags}, timeout=30)
            r.raise_for_status()
            return r.json()
        return doc

    def remove_tag_from_document(self, doc_id: int, tag_id: int) -> dict:
        """Symmetrisch zu add_tag_to_document: entfernt EINEN Tag von einem
        Dokument, laesst alle anderen Tags unangetastet."""
        doc = self.get_document(doc_id)
        tags = list(doc.get("tags") or [])
        if tag_id in tags:
            tags.remove(tag_id)
            r = self.session.patch(f"{self.base_url}/api/documents/{doc_id}/", json={"tags": tags}, timeout=30)
            r.raise_for_status()
            return r.json()
        return doc

    def update_custom_field_value(self, doc_id: int, field_id: int, value) -> dict:
        """Setzt/aktualisiert den Wert EINES Custom Fields eines bereits
        bestehenden Dokuments - z.B. wenn ein Beleg schon in Paperless liegt,
        das Betrags-Custom-Field dort aber noch leer ist.

        PATCH /api/documents/{id}/ ersetzt die komplette custom_fields-Liste,
        daher wird zuerst der aktuelle Stand gelesen und nur der betroffene
        Eintrag ergaenzt/ueberschrieben, damit andere Custom Fields des
        Dokuments erhalten bleiben."""
        doc = self.get_document(doc_id)
        custom_fields = [dict(cf) for cf in (doc.get("custom_fields") or [])]
        for cf in custom_fields:
            if cf.get("field") == field_id:
                cf["value"] = value
                break
        else:
            custom_fields.append({"field": field_id, "value": value})

        r = self.session.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            json={"custom_fields": custom_fields},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
