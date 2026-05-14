# Crm-Actual-Photo

Polling automation for the **Actual Photo** request workflow in Zoho Creator:
the script watches the encoding-requests report, finds the matching
photo/video files in Zoho WorkDrive, uploads them to the corresponding
Creator record, and marks the request as done.

## Requirements

- Python 3.10+ (3.12 recommended)
- A Zoho account with Creator + WorkDrive access and an OAuth app
  (`Client ID`, `Client Secret`, `Refresh Token`)

## Installation

```bash
# 1. Clone
git clone https://github.com/homecartelmarketing2-bit/Crm-Actual-Photo.git
cd Crm-Actual-Photo

# 2. Create a virtualenv
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in your Zoho credentials plus the
WorkDrive folder IDs to scan:

```bash
# Windows: copy .env.example .env
cp .env.example .env
```

Required keys:

- `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`
- `ZOHO_WORKDRIVE_PARENT_FOLDER_IDS` (comma-separated WorkDrive folder IDs
  the script will recursively search for matching media)

Everything else has sensible defaults in `actual_photo_automation/config.py`
and is only needed if your Creator form uses non-default field names or you
want to tweak polling/throttling. See the comments in `.env.example` for the
full list.

## Usage

All commands assume the virtualenv is active and you are in the repo root.

```bash
# Show all available flags
python -m actual_photo_automation --help

# Dry run: fetch and log actions without uploading anything
python -m actual_photo_automation --dry-run

# Process one eligible record and exit (useful for testing)
python -m actual_photo_automation --test-one

# Filter by product name
python -m actual_photo_automation --product "Svana Cirque"

# Print Creator field metadata then exit (debug)
python -m actual_photo_automation --debug-fields

# Run the polling loop forever (Ctrl+C to stop)
python -m actual_photo_automation
```

There is also a small built-in WorkDrive video browser:

```bash
python -m actual_photo_automation --video-browser
# defaults: http://127.0.0.1:8787
```

## How it works

1. Loads `.env` and the config defaults in
   `actual_photo_automation/config.py`.
2. Polls the Zoho Creator report `All_Encoding_Requests` for rows with
   `Type_of_Request = "Actual Photo"` and `Request_Status` in
   `Pending` / `In progress`.
3. For each pending record, searches the configured WorkDrive parent
   folders (recursively) for files whose name matches the product.
4. Downloads matching media, uploads it to the Creator record's image and
   video fields, sets remarks, and marks the record as `Done`.
5. Persists processed record IDs in `processed_records.json` so the same
   record is not re-processed after a restart.

## Project layout

```
actual_photo_automation/   the only Python package in this repo
  __main__.py              CLI entry point (python -m actual_photo_automation)
  automation.py            main polling / upload pipeline
  config.py                env-driven config + validation
  auth.py                  Zoho OAuth helper
  creator.py               Zoho Creator REST client
  workdrive.py             Zoho WorkDrive REST client
  helpers.py               product-name matching, filename heuristics, ...
  akeneo.py                optional Akeneo PIM client
  upload_to_akeneo.py      one-shot script for pushing media to Akeneo
  video_browser.py         optional local web UI for browsing WorkDrive videos
  test_automation_rules.py unit tests for the matching rules
processed_records.json     persisted state (auto-managed by the script)
fields.json                sample Creator field/record dump (from --debug-fields)
```
