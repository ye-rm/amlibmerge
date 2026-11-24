# AGENTS.md

Guidance for AI coding agents working on the `amlibmerge` repository.

## Project Overview

`amlibmerge` is a cross-platform Python CLI tool that scans an Apple Music cloud library for split albums, backs up editable playlists and song ratings, deletes split library album shells, re-imports the canonical full catalog album, restores affected playlist memberships/ratings, and produces a structured report separating **auto-merged albums** from **albums requiring manual action**.

- **Language / Runtime**: Python 3.11+ (developed and verified on Python 3.14 on Windows)
- **Core Dependencies**: `httpx[http2]`, `pydantic`, `typer`, `rich`, `textual` (plus `pytest` for dev)
- **Entry Point**: `amlibmerge = "amlibmerge.cli:app"` (defined in `pyproject.toml`)

## Architecture & Module Map

| File | Responsibility |
| :--- | :--- |
| `src/amlibmerge/cli.py` | Typer CLI commands (`auth login/status/logout`, `scan`, `backup`, `merge`, `restore`, `tui`, plus `--tui` root option) and Rich terminal tables/panels. |
| `src/amlibmerge/tui.py` | Textual interactive TUI (`AmlibmergeApp`) wrapping auth, scan, side-by-side split/track inspection, multi-select merge with modal confirmation (`ConfirmMergeModal`), merge report viewing, and playlist restore. Supports dependency injection (`client_factory`, `browser_login_fn`) for offline headless `Pilot` tests. |
| `src/amlibmerge/browser_auth.py` | Zero-dependency RFC 6455 localhost WebSocket client (`_CDPWebSocket`) that launches Edge/Chrome/Brave with `--remote-debugging-port`, polls `Storage.getCookies` for `media-user-token`, verifies `/v1/me/storefront`, and closes the browser cleanly via `Browser.close`. |
| `src/amlibmerge/client.py` | HTTP/2 client for `amp-api.music.apple.com`. Dynamically scrapes Apple's public Web Developer JWT from `music.apple.com` JS bundles, handles pagination (`next` URL query-param preservation), pacing jitter, and `429`/`5xx` exponential backoff. |
| `src/amlibmerge/config.py` | Credential persistence under `~/.amlibmerge/credentials.json` (overridable via `AMLIBMERGE_HOME`, `AM_MEDIA_USER_TOKEN`, `AM_DEVELOPER_TOKEN`), token masking, and stdlib JWT `exp` decoding. |
| `src/amlibmerge/models.py` | Pydantic v2 models (`Credentials`, `LibraryTrack`, `LibraryAlbum`, `SplitGroup`, `ScanReport`, `LibraryBackup`, `MergeJournalEntry`, `MergeResult`, `MergeReport`). |
| `src/amlibmerge/detector.py` | Two-tier split detection (`HIGH_SAME_CATALOG_ID`, `MEDIUM_SAME_NAME_ARTIST`) and safety guardrail evaluation (`has_local_uploads`, `has_mixed_editions`, `target_catalog_available`, `safe_to_merge`). |
| `src/amlibmerge/scanner.py` | Paginates `/v1/me/library/albums?include=catalog`, enriches candidate split groups with track lists and storefront catalog availability checks. |
| `src/amlibmerge/backup.py` | Snapshots editable user playlists (`canEdit == True`) and batched song ratings (`library-songs`) to JSON atomically. |
| `src/amlibmerge/merger.py` | Merge state machine (`planned` -> `deleted` -> `readded` -> `restored` / `still_split` / `failed`), WAL journaling (`journals/*.jsonl`), group-wide song-level delete fallback, idempotent playlist restoration, and `MergeReport` generation. |

## Hard Safety Invariants (DO NOT WEAKEN)

1. **Never bypass storefront availability check**: Before any `DELETE`, `client.get_catalog_album(storefront, target_cid)` MUST return a valid catalog album. Even `--force` must never delete an album whose target catalog ID returns 404.
2. **Protect local uploads & region-delisted tracks**: Any album or track missing a `catalog_id` sets `has_local_uploads = True` and blocks automatic deletion (`safe_to_merge = False`).
3. **Protect mixed-edition/multi-recording groups**: Classical and multi-edition libraries frequently have distinct recordings under the same title/artist or scrambled across album shells (`len(distinct_track_cids) > target_catalog_track_count`). Always flag `has_mixed_editions = True` and set `safe_to_merge = False` by default.
4. **Dry-run by default**: `merge` and `restore` MUST default to non-mutating Dry-Run mode and require explicit `--execute` for cloud writes.
5. **Pre-merge backup & WAL**: Executing `merge --execute` must take a fresh playlist/rating backup (unless `--backup-file` is provided) and append WAL entries before/during mutations.
6. **Secret & user-data hygiene**: Never log or print full `media-user-token` or JWT strings (always use `mask_token()`). Keep `reports/`, `backups/`, `journals/`, `.env`, and `credentials.json` gitignored.

## Apple Music `amp-api` Real-World Quirks

Keep these empirically verified behaviors in mind when modifying `client.py`, `detector.py`, or `merger.py`:

- **Headers**: `amp-api.music.apple.com` requires `Authorization: Bearer <web_jwt>`, `media-user-token: <user_token>`, `Origin: https://music.apple.com`, and `Referer: https://music.apple.com/`. Do **not** send `x-apple-client-version` (causes 500 errors).
- **Pagination**: `next` URLs in paginated responses may omit `include=catalog`; `_merge_query_params()` preserves it across pages.
- **404 on empty sub-resources**: Empty playlists (`/tracks`) and unrated batches (`/v1/me/ratings/...`) return HTTP 404 rather than empty arrays; always pass `allow_404=True`.
- **Ghost split shells on delete**: A split album shell can report `trackCount > 0` yet return 404 (0 tracks) on `/tracks` while its sibling shell lists all combined tracks. If album-level `DELETE` leaves any old shell alive after polling, the fallback in `merger.py` MUST delete all song IDs across the **entire group**, not just tracks attached to the surviving shell.
- **Upstream catalog-bug splits**: Some albums split immediately upon a clean fresh `POST /v1/me/library?ids[albums]=...` due to broken metadata in Apple's own catalog. Polling after re-add must NOT exit early on multi-ID responses; if `len(new_lib_ids) > 1`, classify as `stage="still_split"` so the user gets manual-merge instructions.
- **Localhost CDP & proxies**: `_wait_for_cdp_ws_url` MUST use `httpx.Client(trust_env=False)` so system HTTP proxies do not intercept `127.0.0.1:<port>`.

## Development & Verification Commands

```powershell
# Activate virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Editable install with dev dependencies
pip install -e ".[dev]"

# Run full unit test suite (all tests are offline/mocked and fast)
pytest -v

# Check CLI help & subcommands
amlibmerge --help
amlibmerge auth login --help
amlibmerge merge --help
```

When adding or modifying detection, merge, backup, or CDP auth logic, add or update corresponding offline unit tests in `tests/` and run `pytest -v` before concluding.
