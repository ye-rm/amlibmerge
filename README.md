# amlibmerge

**English** | [简体中文](README.zh-CN.md)

> **Apple Music Library Split-Album Scanner, Backup & Merger CLI**  
> A cross-platform Python CLI & TUI tool to scan an Apple Music cloud library for split albums, safely back up playlists and ratings, automatically delete & re-import canonical albums, and generate an actionable report of auto-merged vs. manual-action albums.

> [!IMPORTANT]
> **Community Testing Welcome**: The current merge logic cannot automatically handle all split-album cases (such as mixed multi-edition recordings, region-delisted tracks, or albums whose upstream Apple Music catalog metadata is itself broken). You are warmly welcome to test with your own library (start with read-only `scan` and Dry-Run previews) and share findings via Issues or Pull Requests to help improve the merge logic!

---

## Why `amlibmerge`?

Apple Music libraries frequently suffer from **split albums**—where tracks from a single release splinter across multiple incomplete album entries (e.g., 14 tracks + 1 track, or 35 tracks + 9 tracks). Because the public Apple Music API does not expose endpoints to delete library albums or edit album metadata, fixing them one by one in the UI is tedious and risks losing playlist memberships.

`amlibmerge` automates this workflow safely via the Apple Music web API (`amp-api.music.apple.com`):

- **Zero Developer Account · Browser Popup Login**: Automatically launches your local Edge / Chrome / Brave browser to `music.apple.com`, supports full Apple ID + 2FA login, captures `media-user-token` via localhost CDP, auto-fetches Apple's public Web Developer JWT, and closes the window cleanly—no $99/year Apple Developer Program required.
- **Two-Tier Detection + Strict Safety Guardrails**:
  - **Tier 1 (High Confidence)**: Multiple library albums pointing to the exact same Apple Music Catalog Album ID.
  - **Tier 2 (Medium Confidence)**: Normalized `(album, artist)` duplicates across different Catalog IDs.
  - **Built-in Protections**: Automatically blocks **local uploads / region-delisted tracks** (missing Catalog IDs), **mixed-edition / multi-recording groups** (e.g., distinct classical recordings sharing a title), and **albums delisted from your current storefront**.
- **Pre-Merge Backup, WAL Journal & Idempotent Restore**:
  - Automatically snapshots all editable user playlists and song ratings (`backups/`) before any destructive write.
  - Appends a Write-Ahead Log (`journals/merge_*.jsonl`) for every step (`planned` → `deleted` → `readded` → `restored`).
  - Automatically restores tracks removed from playlists by deletion, plus a standalone `restore` command for idempotent recovery.
- **Bilingual UI (English & Simplified Chinese)**: Defaults to English and automatically displays Simplified Chinese on Chinese OS locales; switch anytime via `--lang en|zh|auto`, `amlibmerge lang <value>`, `AMLIBMERGE_LANG`, or pressing `G` live inside the TUI.
- **Interactive Terminal UI (TUI)**: Launch `amlibmerge --tui` (or `amlibmerge tui`) for a full keyboard + mouse interactive dashboard to sign in, scan, inspect per-album tracks & warnings side-by-side, check/uncheck split groups, run Dry-Run previews, confirm & execute merges, and restore playlists without memorizing CLI flags.
- **Smart Fallback & Categorized Report**:
  - Falls back to group-wide song-level deletion if Apple Music leaves ghost album shells cached after album-level `DELETE`.
  - Supports `--all` batch processing and outputs both a terminal summary and a JSON report (`reports/merge_report_<timestamp>.json`) clearly separating **Auto-Merged Albums** from **Albums Requiring Manual Action**.

---

## Requirements

- Python **3.11+**
- An active Apple Music subscription
- A Chromium-based browser installed locally (Edge / Chrome / Brave) for popup login (or use `--manual` in headless/SSH environments)

---

## Installation

```bash
git clone git@github.com:ye-rm/amlibmerge.git
cd amlibmerge

python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
```

---

## Quick Start

### Interactive TUI Mode (Recommended for First-Time Users)

```bash
# Launch the interactive TUI (auto-loads the latest scan report & backup if present)
amlibmerge --tui
# Equivalent subcommand (supports pre-loading a specific scan report):
amlibmerge tui --report reports/scan_20261124_154538.json
```

Key bindings inside the TUI: `L` Login · `S` Scan library · `B` Backup playlists · `Space` Toggle selection · `A` Select all safe groups · `N` Clear selection · `D` Dry-Run preview · `M` Confirm & Execute merge · `R` Compare/Restore playlists · `G` Switch EN/中文 · `Q` Quit.

### Display Language

`amlibmerge` defaults to **English**, automatically uses **Simplified Chinese** on Chinese OS environments, and lets you override it at any time:

```bash
# View current language & source
amlibmerge lang

# Persist language preference (en | zh | auto)
amlibmerge lang en
amlibmerge lang zh
amlibmerge lang auto

# One-off override per command or via environment variable
amlibmerge --lang en scan
AMLIBMERGE_LANG=zh amlibmerge --tui
```

### 1. Sign in to Apple Music

```bash
# Open a browser popup to sign in and auto-capture media-user-token
amlibmerge auth login

# Start with a clean temporary browser profile (useful when switching Apple IDs)
amlibmerge auth login --fresh

# Headless/manual fallback: paste media-user-token directly
amlibmerge auth login --manual

# Verify saved credentials and storefront
amlibmerge auth status --verify
```

> Credentials are stored outside the repository at `~/.amlibmerge/credentials.json` (or overridden via `AM_MEDIA_USER_TOKEN`).

### 2. Scan Your Library (Read-Only)

```bash
# Scan all library albums and verify storefront availability (saves reports/scan_<timestamp>.json)
amlibmerge scan

# Only detect Tier 1 (same Catalog ID) splits
amlibmerge scan --no-weak
```

### 3. Back Up Editable Playlists & Ratings (Optional — Also Runs Automatically Before `--execute`)

```bash
amlibmerge backup
```

### 4. Preview & Merge Split Albums

`merge` defaults strictly to **Dry-Run preview mode** and makes zero cloud modifications unless `--execute` is explicitly provided.

```bash
# Dry-run preview for a single split group
amlibmerge merge --group G004

# Execute merge for a single split group
amlibmerge merge --group G004 --execute

# Dry-run preview across all split groups (shows auto-mergeable vs. manual-action groups)
amlibmerge merge --all

# Execute all safe merges and output the categorized Succeeded / Manual-Required report
amlibmerge merge --all --execute

# Override a safety-blocked group after manual review (still enforces storefront availability check)
amlibmerge merge --group G006 --catalog-id 1440743071 --force --execute
```

### 5. Idempotent Playlist Restore

```bash
# Preview missing playlist tracks compared to the latest backup snapshot
amlibmerge restore

# Re-add any missing tracks back to playlists (deduplicated / idempotent)
amlibmerge restore --execute
```

---

## Manual Merging (When the CLI Cannot Auto-Merge)

Some split groups are intentionally skipped by safety guardrails (such as mixed multi-edition recordings, albums containing region-delisted or local tracks, or albums flagged in the post-merge report) or fail to resolve automatically. For these albums, you can merge them manually in Apple Music:

1. **Check the Report & Back Up First**
   - Review the **Manual Action Required** section of your `amlibmerge merge --all` report to note the album title, artist, and target Catalog Album ID.
   - Make sure you have a fresh playlist snapshot (`amlibmerge backup`) if any tracks from the album belong to your custom playlists.
   - *(If the group mixes multiple recordings/editions—common in classical music—verify which edition(s) you want to keep before deleting.)*
2. **Locate All Split Entries in Your Library**
   - Open the Apple Music app (macOS, Windows, iOS/iPadOS, or `https://music.apple.com`), navigate to **Library → Albums**, and search for the album to find **every split entry** belonging to it.
3. **Delete Every Split Entry from Your Library**
   - For **each** split album entry, click `…` → **Delete from Library** until **all** split fragments of that album are completely removed from your library.
   - Wait a few seconds for iCloud Library sync to settle so no stale album shells remain.
4. **Re-Add the Canonical Album to Your Library**
   - Search for the album in the Apple Music catalog (or open its catalog page directly at `https://music.apple.com/<storefront>/album/<catalog_id>` using the Catalog ID from the scan report) and click **`+ Add`** to re-import the complete album cleanly as a single entry.
5. **Restore Affected Playlist Tracks (If Needed)**
   - Because deleting an album from your Apple Music library also removes its tracks from custom playlists, run:
     ```bash
     amlibmerge restore --execute
     ```
     to automatically and idempotently add any removed tracks back into your playlists.

> **Tip — What if an album still splits even after manual delete & re-add?**  
> A small number of albums have inconsistent metadata inside Apple Music's own upstream catalog, so even a brand-new add splits immediately (the CLI flags these as `still_split`). For those specific albums, open the desktop Apple Music app, select all tracks across the split entries, open **Get Info** (`Cmd+I` / `Ctrl+I`), and set an identical **Album** and **Album Artist** across all tracks (temporarily appending a suffix, saving, and removing it forces Apple Music to regroup them).

---

## Known Limitations

1. **Play Counts & Date Added**: Deleting and re-adding an album resets play counts, skip counts, and original Date Added timestamps; downloaded offline files must be re-downloaded.
2. **Playlist Track Ordering**: Apple Music's playlist API appends restored tracks to the end of a playlist rather than inserting them at their exact original indices.
3. **Upstream Catalog Bugs**: Albums whose current Apple Music catalog metadata is itself broken cannot be fixed by delete-and-reimport alone and will be reported as `still_split`.

---

## Project Layout

```text
src/amlibmerge/
├── cli.py           # Typer + Rich CLI entry point (auth / scan / backup / merge / restore / tui / lang)
├── tui.py           # Textual interactive TUI dashboard (scan, track inspector, confirm modals, reports)
├── i18n.py          # Bilingual EN/zh-CN catalog, OS locale detection & runtime language switching
├── browser_auth.py  # Zero-dependency localhost CDP WebSocket client & browser popup login
├── client.py        # amp-api.music.apple.com HTTP/2 client (auto Web JWT, pagination, retries)
├── config.py        # Credential persistence, env overrides & stdlib JWT exp decoding
├── models.py        # Pydantic v2 models (albums, split groups, backups, WAL entries, merge reports)
├── detector.py      # Tier 1 / Tier 2 split detection & safety guardrail evaluation
├── scanner.py       # Paginated library scan, track enrichment & storefront availability checks
├── backup.py        # Editable playlist & song rating snapshot export/import
└── merger.py        # Delete (with group-wide song fallback) -> poll -> re-add -> restore & reporting
```

---

## Development & Tests

```bash
pytest -v
```

---

## Disclaimer

This tool is intended for personal management of your own Apple Music library. It interacts with Apple Music's undocumented web-player API (`amp-api.music.apple.com`) and is not affiliated with or endorsed by Apple Inc. API behavior may change without notice—always run Dry-Run previews and keep backups before executing cloud mutations.

---

## License

This project is licensed under the [MIT License](LICENSE).
