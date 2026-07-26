# Paperless Sync

A Windows desktop tool that reconciles a bank statement CSV against documents stored in [Paperless-ngx](https://docs.paperless-ngx.com/), so every transaction ends up with a matching receipt — built for gap-free bookkeeping records for tax purposes (accountant-ready exports included).

![platform](https://img.shields.io/badge/platform-Windows-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![python](https://img.shields.io/badge/python-3.12-blue)

## What it does

1. **Import** a bank statement CSV (any delimiter/encoding, auto-detected).
2. **Match** each transaction against your Paperless-ngx documents by amount — either parsed from the filename via a configurable regex (e.g. `_EUR12.34.pdf`) or read from a Paperless custom field.
3. **Resolve** what's left by hand: upload a PDF (drag & drop or file picker), pick an existing Paperless document, or tag the transaction (Private / Deposit / Transfer / your own custom tags) when no receipt is needed. Multiple documents can be linked to one transaction (e.g. a combined charge with several individual invoices).
4. **Export** a clean, numbered folder per month — matched PDFs, tag notes for untagged bookings, a filtered copy of the original CSV, a list of unresolved transactions, and a separate list of all "Deposit"-tagged bookings — ready to hand to an accountant.

The app learns as you go: once you tag a recurring transaction (e.g. a subscription with a varying reference number), it suggests the same tag next time a similar booking shows up.

## Features

- **Automatic CSV parsing** — encoding/delimiter/date/amount format detection, works with most European bank export formats.
- **Two receipt-detection methods** — filename regex or a Paperless custom field holding the invoice amount.
- **Learned tag suggestions** — recurring bookings (same purpose text, ignoring dates/reference numbers) get a one-click tag suggestion.
- **Multi-document matching** — link several Paperless documents to a single transaction (e.g. a marketplace payout covering multiple invoices).
- **mTLS client certificate support** — for Paperless instances behind something like Cloudflare Access with a PKCS#12 client certificate.
- **Backup & restore** — one ZIP with settings, learned tags, credentials, and current work state; restorable on a new machine.
- **Custom company logo** — replace the default icon with your own, shown in the sidebar and as the window/taskbar icon.
- **German / English UI** — switchable in Settings (restart required).
- **Paperless success tag** — optionally tags matched documents back in Paperless itself, so the match status is visible/filterable there too.

## Screenshots

*(add screenshots to `docs/` and reference them here)*

## Installation

### Windows (recommended)

Download the latest installer from the [Releases](../../releases) page and run it. `Paperless Sync` will appear in your Start menu.

### From source

Requires Python 3.12+.

```bash
git clone <this-repo-url>
cd paperless-sync
pip install -r requirements.txt
python desktop_app_qt.py
```

On first launch you'll be guided through a short setup: your Paperless-ngx URL and API token, and how invoice amounts should be detected.

## Building the Windows executable

```bash
pip install -r requirements-build.txt
python build.py
```

This builds `dist/PaperlessSyncQt/PaperlessSyncQt.exe` via PyInstaller, and — if [Inno Setup](https://jrsoftware.org/isinfo.php) is installed — a Windows installer into `installer_output/`.

## Configuration

All settings are reachable from the "⚙️ Settings" button in the app — no manual file editing needed:

- **Paperless URL + API token** (and optional client certificate for mTLS-protected instances)
- **Export folder** — where finished monthly folders are written (e.g. a shared OneDrive/accountant folder)
- **Receipt amount detection** — filename regex or custom field
- **CSV column mapping** — which columns hold date/amount/purpose/sender
- **Custom tags, noise terms, backup/restore, language, company logo**

User data (config, credentials, session state) lives in `%APPDATA%\PaperlessSync` when running the installed `.exe`, and next to the source files when run from source.

## Architecture

The UI (PySide6/Qt, `desktop_app_qt.py` + `dialogs_qt.py`) is a thin layer over a framework-agnostic backend:

| Module | Responsibility |
|---|---|
| `desktop_state.py` | Application state (replaces a web framework's session state) |
| `desktop_controller.py` | User actions → state changes |
| `matcher.py` | Building transactions from the CSV, matching against Paperless documents |
| `paperless_client.py` | Thin Paperless-ngx REST API wrapper |
| `exporter.py` | Generates the final per-month export folder |
| `csv_utils.py` | Encoding/delimiter/amount/date parsing |
| `config_manager.py` | `.env` / `config.json` loading and persistence |
| `backup.py` | ZIP backup/restore of all user data |
| `i18n.py` | Minimal DE/EN translation layer |

An older CustomTkinter UI (`desktop_app.py`) and an even older Streamlit web UI (`app.py`) are kept in the repo as fallbacks but are no longer the primary target.

## License

MIT — see [LICENSE](LICENSE).
