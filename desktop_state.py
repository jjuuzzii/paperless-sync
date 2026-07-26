"""Zentraler Anwendungszustand fuer die Desktop-UI - Framework-unabhaengig.

Ersetzt Streamlits st.session_state durch ein einfaches Objekt. Nutzt
ausschliesslich die bereits bestehenden, framework-agnostischen
Backend-Module (config_manager, session_store, paperless_client)."""
from __future__ import annotations

from pathlib import Path

from config_manager import (
    get_base_dir,
    get_resource_dir,
    seed_default_files,
    load_env,
    load_config,
    save_config,
    save_env,
    is_configured,
    csv_signature as compute_csv_signature,
)
from session_store import save_session, load_session
from paperless_client import PaperlessClient

SUCCESS_STATUSES = ("matched", "tagged", "uploaded")


class AppState:
    def __init__(self):
        self.base_dir = get_base_dir()
        seed_default_files(self.base_dir, get_resource_dir())

        self.config_path = self.base_dir / "config.json"
        self.input_dir = self.base_dir / "input"
        self.export_dir = self.base_dir / "export"
        self.example_csv_path = self.input_dir / "beispiel_kontoauszug.csv"
        self.input_dir.mkdir(exist_ok=True)
        self.export_dir.mkdir(exist_ok=True)

        self.env = load_env(self.base_dir)
        self.config = load_config(self.config_path)

        self.csv_df = None
        self.csv_columns: list[str] = []
        self.csv_delimiter = None
        self.csv_encoding = None
        self.csv_signature = None
        self.mapping_confirmed = False
        self.pending_mapping = {"date_column": None, "amount_column": None, "purpose_column": None}
        self.matched_once = False
        self.transactions: list[dict] = []
        self.paperless_docs_raw: list[dict] = []
        # Von der UI gesetzt (Sidebar "Monat"). Filtert NUR die Anzeige
        # (KPIs/Karten) - Matching/Export bleiben unabhaengig davon korrekt.
        # Wichtig fuer die Performance: ohne diesen Filter wuerden bei einer
        # CSV, die mehrere Monate/Jahre umfasst, alle Transaktionen
        # gleichzeitig als Karten gerendert (bei 200+ Buchungen spuerbar
        # langsam in Tkinter/CustomTkinter).
        self.selected_month: str | None = None

        # Jede Karte besteht aus 20-50+ Tk-Widgets (Tags, Drop-Zone, Buttons
        # mit abgerundeten Ecken/Rahmen sind in CustomTkinter deutlich
        # teurer als einfache Labels). Bei Monaten mit vielen offenen
        # Buchungen (30+) summiert sich das spuerbar beim Scrollen. Wie bei
        # selected_month oben: nur die ANZEIGE wird begrenzt, Matching/
        # Export/KPI-Zaehler sehen weiterhin alle Transaktionen. "Weitere
        # anzeigen" erhoeht die Werte bei Bedarf (siehe DesktopApp).
        self.DEFAULT_REVEAL_COUNT = 20
        self.success_reveal_count = self.DEFAULT_REVEAL_COUNT
        self.action_reveal_count = self.DEFAULT_REVEAL_COUNT

        self.client = self._build_client()
        self._restore_session()

    # --- Zugangsdaten / Client ------------------------------------------------
    def is_configured(self) -> bool:
        return is_configured(self.env)

    def _build_client(self):
        if not (self.env.get("PAPERLESS_URL") and self.env.get("PAPERLESS_TOKEN")):
            return None
        cert_path = None
        if self.env.get("PAPERLESS_CLIENT_CERT_PATH"):
            candidate = self.base_dir / self.env["PAPERLESS_CLIENT_CERT_PATH"]
            if candidate.exists():
                cert_path = str(candidate)
        try:
            return PaperlessClient(
                self.env["PAPERLESS_URL"],
                self.env["PAPERLESS_TOKEN"],
                client_cert_path=cert_path,
                client_cert_password=self.env.get("PAPERLESS_CLIENT_CERT_PASSWORD"),
            )
        except Exception:
            return None

    def reload_env_and_client(self):
        self.env = load_env(self.base_dir)
        self.client = self._build_client()

    def save_config(self):
        save_config(self.config_path, self.config)

    def get_export_base_dir(self) -> Path:
        """Zielordner fuer 'ORDNER JETZT GENERIEREN' - der frei waehlbare
        Pfad aus den Einstellungen (z.B. ein geteilter OneDrive-/
        Steuerberater-Ordner), falls gesetzt, sonst der Standard-Exportordner
        neben den uebrigen Nutzerdaten."""
        custom = self.config.get("export_dir")
        if custom:
            path = Path(custom)
            if path.exists() and path.is_dir():
                return path
        return self.export_dir

    def save_env(self, values: dict):
        save_env(self.base_dir, values)
        self.reload_env_and_client()

    # --- Sitzung wiederherstellen/speichern ------------------------------------------------
    def _restore_session(self):
        if not self.is_configured():
            return
        restored = load_session(self.base_dir)
        if not restored:
            return
        self.csv_signature = restored.get("csv_signature")
        self.csv_columns = restored.get("csv_columns") or []
        self.csv_delimiter = restored.get("csv_delimiter")
        self.csv_encoding = restored.get("csv_encoding")
        self.csv_df = restored.get("csv_df")
        self.mapping_confirmed = restored.get("mapping_confirmed", False)
        self.pending_mapping = restored.get("pending_mapping") or self.pending_mapping
        self.matched_once = restored.get("matched_once", False)
        self.transactions = restored.get("transactions") or []
        self._backfill_display_numbers()
        self._backfill_matched_docs()
        self.reapply_counterparty_mapping()

    def _backfill_display_numbers(self) -> None:
        """Aeltere Sitzungen (vor Einfuehrung der Pro-Monat-Nummerierung)
        kennen tx['display_number'] noch nicht. Laesst sich - anders als
        counterparty - direkt aus dem bereits vorhandenen Datum ableiten,
        keine Roh-CSV noetig. Tastet Status/Tags/Matches nicht an."""
        if not self.transactions or all(t.get("display_number") for t in self.transactions):
            return
        by_month: dict[str, list[dict]] = {}
        for t in self.transactions:
            by_month.setdefault(t["date"].strftime("%Y-%m"), []).append(t)
        changed = False
        for month_txs in by_month.values():
            month_txs.sort(key=lambda t: (t["date"], t["id"]))
            for i, t in enumerate(month_txs, start=1):
                new_val = f"{i:03d}"
                if t.get("display_number") != new_val:
                    t["display_number"] = new_val
                    changed = True
        if changed:
            self.persist_session()

    def _backfill_matched_docs(self) -> None:
        """Aeltere Sitzungen kennen noch tx['matched_doc'] (Singular, ein
        einzelnes Dokument oder None) statt tx['matched_docs'] (Liste, fuer
        Mehrfach-Beleg-Zuordnung z.B. bei Sammelabbuchungen). Migriert
        verlustfrei, tastet Status/Tags/Matches sonst nicht an."""
        changed = False
        for t in self.transactions:
            if "matched_docs" in t:
                continue
            legacy = t.pop("matched_doc", None)
            t["matched_docs"] = [legacy] if legacy else []
            changed = True
        if changed:
            self.persist_session()

    def reapply_counterparty_mapping(self) -> None:
        """Ergaenzt tx['counterparty'] aus der (mit-persistierten) Roh-CSV
        gemaess der aktuell hinterlegten Spalten-Zuordnung, ohne bereits
        erledigte Zuordnungen/Tags/Status anzutasten. Noetig, weil (a)
        Sitzungen, die vor Einfuehrung des Feldes gespeichert wurden, es noch
        nicht kennen, und (b) das Mapping nachtraeglich in den Einstellungen
        geaendert werden kann, ohne dass die CSV neu importiert wird."""
        if self.csv_df is None or not self.transactions:
            return
        mapping = self.config.get("csv_mappings", {}).get(compute_csv_signature(self.csv_columns)) or self.pending_mapping
        counterparty_col = (mapping or {}).get("counterparty_column")
        if not counterparty_col or counterparty_col not in self.csv_df.columns:
            return
        changed = False
        for tx in self.transactions:
            row_index = tx.get("row_index")
            if row_index is None or row_index not in self.csv_df.index:
                continue
            value = str(self.csv_df.loc[row_index, counterparty_col] or "").strip()
            if value != (tx.get("counterparty") or ""):
                tx["counterparty"] = value
                changed = True
        if changed:
            self.persist_session()

    def persist_session(self) -> None:
        if not self.transactions:
            return
        save_session(
            self.base_dir,
            {
                "csv_signature": self.csv_signature,
                "csv_columns": self.csv_columns,
                "csv_delimiter": self.csv_delimiter,
                "csv_encoding": self.csv_encoding,
                "csv_records": self.csv_df.to_dict("records") if self.csv_df is not None else None,
                "mapping_confirmed": self.mapping_confirmed,
                "pending_mapping": self.pending_mapping,
                "matched_once": self.matched_once,
                "transactions": self.transactions,
            },
        )

    # --- Abgeleitete Ansichten fuer die UI ------------------------------------------------
    @property
    def months(self) -> list[str]:
        """Immer ALLE Monate der geladenen CSV (fuer das Monats-Dropdown),
        unabhaengig vom aktuell gewaehlten Monat."""
        return sorted({t["date"].strftime("%Y-%m") for t in self.transactions})

    @property
    def visible_transactions(self) -> list[dict]:
        """Transaktionen des aktuell gewaehlten Monats - das ist die Menge,
        die in KPIs/Karten angezeigt wird. Ohne Monatsauswahl (z.B. direkt
        nach dem Import) werden alle angezeigt."""
        if not self.selected_month:
            return self.transactions
        return [t for t in self.transactions if t["date"].strftime("%Y-%m") == self.selected_month]

    @property
    def success_transactions(self) -> list[dict]:
        return [t for t in self.visible_transactions if t["status"] in SUCCESS_STATUSES]

    @property
    def missing_transactions(self) -> list[dict]:
        """Rote Karten: kein Beleg gefunden (oder noch nicht abgeglichen)."""
        return [t for t in self.visible_transactions if t["status"] in (None, "missing")]

    @property
    def unclear_transactions(self) -> list[dict]:
        """Gelbe Karten: Betrag tritt mehrfach auf, manuelle Auswahl noetig."""
        return [t for t in self.visible_transactions if t["status"] == "unclear"]

    @property
    def action_transactions(self) -> list[dict]:
        return self.missing_transactions + self.unclear_transactions
