"""Lightweight bilingual (English / Simplified Chinese) internationalization support.

Default language is English (`en`). On Chinese OS environments, Simplified Chinese (`zh`)
is selected automatically unless overridden via `--lang`, `AMLIBMERGE_LANG`, or saved
preference in `~/.amlibmerge/settings.json`.
"""

from __future__ import annotations

import ctypes
import locale
import os
import sys
from typing import Any

from amlibmerge.config import load_language_preference

SUPPORTED_LANGUAGES = ("en", "zh")
DEFAULT_LANGUAGE = "en"

_ACTIVE_LANGUAGE: str | None = None


def normalize_language(raw: str | None) -> str | None:
    """Normalize a language string into 'en', 'zh', 'auto', or None if unrecognized."""
    if not raw:
        return None
    val = raw.strip().lower().replace("_", "-")
    if val in ("auto", "default", "system"):
        return "auto"
    if val.startswith("zh") or val in ("chinese", "chs", "cht", "cn"):
        return "zh"
    if val.startswith("en") or val in ("english", "us", "gb"):
        return "en"
    return None


def _is_chinese_locale_name(name: str | None) -> bool:
    if not name:
        return False
    low = name.strip().lower().replace("_", "-")
    return low.startswith("zh") or "chinese" in low or low.startswith("chs") or low.startswith("cht")


def detect_system_language() -> str:
    """Detect OS display language: returns 'zh' on Chinese systems, otherwise 'en'."""
    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            if _is_chinese_locale_name(env_val):
                return "zh"
            if env_val.lower() not in ("c", "posix", "c.utf-8", "c.utf8"):
                return "en"

    if sys.platform == "win32":
        try:
            lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined]
            # Primary language ID 0x04 is LANG_CHINESE (covers zh-CN, zh-TW, zh-HK, zh-SG)
            if (lang_id & 0x3FF) == 0x04:
                return "zh"
            return "en"
        except Exception:
            pass

    try:
        loc_name, _ = locale.getlocale()
        if _is_chinese_locale_name(loc_name):
            return "zh"
    except Exception:
        pass

    return DEFAULT_LANGUAGE


def _extract_lang_from_argv(argv: list[str] | None = None) -> str | None:
    args = sys.argv[1:] if argv is None else argv
    for i, arg in enumerate(args):
        if arg == "--lang" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


def resolve_language(
    cli_lang: str | None = None,
    *,
    argv: list[str] | None = None,
) -> tuple[str, str]:
    """Resolve effective language and its source label ('cli' | 'env' | 'config' | 'system')."""
    raw_cli = cli_lang if cli_lang is not None else _extract_lang_from_argv(argv)
    norm_cli = normalize_language(raw_cli)
    if norm_cli in SUPPORTED_LANGUAGES:
        return norm_cli, "cli"
    if norm_cli == "auto":
        return detect_system_language(), "system"

    raw_env = os.environ.get("AMLIBMERGE_LANG")
    norm_env = normalize_language(raw_env)
    if norm_env in SUPPORTED_LANGUAGES:
        return norm_env, "env"
    if norm_env == "auto":
        return detect_system_language(), "system"

    raw_cfg = load_language_preference()
    norm_cfg = normalize_language(raw_cfg)
    if norm_cfg in SUPPORTED_LANGUAGES:
        return norm_cfg, "config"

    return detect_system_language(), "system"


def get_language() -> str:
    global _ACTIVE_LANGUAGE
    if _ACTIVE_LANGUAGE is None:
        _ACTIVE_LANGUAGE, _ = resolve_language()
    return _ACTIVE_LANGUAGE


def set_language(lang: str | None) -> str:
    """Set active runtime language ('en', 'zh', 'auto', or None to re-resolve)."""
    global _ACTIVE_LANGUAGE
    norm = normalize_language(lang)
    if norm in SUPPORTED_LANGUAGES:
        _ACTIVE_LANGUAGE = norm
    elif norm == "auto":
        _ACTIVE_LANGUAGE = detect_system_language()
    else:
        _ACTIVE_LANGUAGE, _ = resolve_language()
    return _ACTIVE_LANGUAGE


def reset_language_cache() -> None:
    global _ACTIVE_LANGUAGE
    _ACTIVE_LANGUAGE = None


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # General / Common
        "common.unknown": "Unknown",
        "common.none": "None",
        "common.cancel": "Cancel",
        "common.confirm": "Confirm",
        "common.invalid_lang": "Unsupported language '{lang}'. Use 'en', 'zh', or 'auto'.",
        # CLI Root & Subcommand Help
        "cli.app_help": "Scan, back up, and merge split albums in an Apple Music library.",
        "cli.auth_help": "Manage Apple Music web authentication credentials.",
        "cli.opt.version": "Show version and exit.",
        "cli.opt.lang": "Display language: en, zh, or auto (default: Chinese on Chinese OS, else English).",
        "cli.opt.tui": "Launch the full interactive terminal UI (TUI).",
        "cli.opt.tui_report": "Used with --tui: pre-load a specific scan report path.",
        "cli.err.tui_with_subcommand": "--tui cannot be combined with another subcommand.",
        "cli.cmd.tui.help": "Launch the interactive terminal UI (TUI).",
        "cli.cmd.tui.opt_report": "Scan report path to pre-load on startup (defaults to latest in reports/).",
        "cli.cmd.lang.help": "View or set the persistent display language (en / zh / auto).",
        "cli.cmd.lang.arg": "Language to save: en, zh, or auto (omit to view current setting).",
        "cli.cmd.lang.current": "Active language: [bold cyan]{lang}[/bold cyan] (source: {source}) | Saved preference: [dim]{saved}[/dim]",
        "cli.cmd.lang.updated": "Saved display language preference: [bold cyan]{pref}[/bold cyan] (active: [bold green]{active}[/bold green]).",
        "cli.err.no_token": "[red]No media-user-token configured.[/red] Run [bold]amlibmerge auth login[/bold] or set [bold]AM_MEDIA_USER_TOKEN[/bold].",
        "cli.err.auth_failed": "[red]Authentication failed:[/red] {exc}",
        "cli.err.api_error": "[red]API error:[/red] {exc}",
        # Auth commands
        "cli.auth.login.help": "Sign in to Apple Music via browser popup to capture media-user-token (or use --manual).",
        "cli.auth.login.opt_token": "Pass media-user-token directly (skips browser popup).",
        "cli.auth.login.opt_manual": "Use manual cookie paste mode (for headless/SSH terminals).",
        "cli.auth.login.opt_fresh": "Launch browser with a fresh temporary profile (useful to switch Apple IDs).",
        "cli.auth.login.opt_browser": "Explicit path to Chrome / Edge / Brave executable.",
        "cli.auth.login.opt_timeout": "Timeout in seconds waiting for browser sign-in.",
        "cli.auth.login.opt_dev_token": "Optional: manually provide Web Developer JWT (auto-extracted by default).",
        "cli.auth.login.preparing": "Preparing browser login window...",
        "cli.auth.login.browser_failed": "[red]Browser login failed:[/red] {exc}",
        "cli.auth.login.manual_hint": "[dim]Tip: you can also run [bold]amlibmerge auth login --manual[/bold] to paste media-user-token manually.[/dim]",
        "cli.auth.login.error": "[red]Login error:[/red] {exc}",
        "cli.auth.login.manual_panel_title": "Get media-user-token",
        "cli.auth.login.manual_panel_body": (
            "Sign in at [bold]https://music.apple.com[/bold], open DevTools (F12) ->\n"
            "[bold]Application -> Cookies -> https://music.apple.com[/bold],\n"
            "and copy the value of the [cyan]media-user-token[/cyan] cookie."
        ),
        "cli.auth.login.manual_prompt": "Enter media-user-token (hidden input)",
        "cli.auth.login.empty_token": "[red]media-user-token cannot be empty.[/red]",
        "cli.auth.login.fetching_jwt": "Extracting public Web Developer JWT from music.apple.com...",
        "cli.auth.login.jwt_failed": "[red]Failed to extract Web Developer JWT:[/red] {exc}",
        "cli.auth.login.verifying": "Verifying credentials via /v1/me/storefront...",
        "cli.auth.login.success": (
            "[green]Login verified![/green]\n"
            "Storefront: [bold cyan]{storefront}[/bold cyan]\n"
            "User Token: [dim]{token}[/dim]\n"
            "Web JWT Expires: [dim]{exp}[/dim]\n"
            "Credentials Path: [dim]{path}[/dim]"
        ),
        "cli.auth.status.help": "Display current local authentication status.",
        "cli.auth.status.opt_verify": "Run a live read-only request to verify the token is still valid.",
        "cli.auth.status.not_found": "[yellow]No saved credentials found[/yellow] (path: {path}).",
        "cli.auth.status.title": "Authentication Status",
        "cli.auth.status.row_file": "Credentials File",
        "cli.auth.status.row_jwt_state": "Web JWT Status",
        "cli.auth.status.jwt_valid": "[green]Valid[/green]",
        "cli.auth.status.jwt_refresh": "[yellow]Needs refresh[/yellow]",
        "cli.auth.status.row_jwt_exp": "Web JWT Expires",
        "cli.auth.status.row_storefront": "Saved Storefront",
        "cli.auth.status.row_verify": "Live Check",
        "cli.auth.status.verify_ok": "[green]Passed (storefront={sf})[/green]",
        "cli.auth.status.verify_fail": "[red]Failed: {exc}[/red]",
        "cli.auth.logout.help": "Delete locally saved credentials.",
        "cli.auth.logout.deleted": "[green]Deleted local credentials file.[/green]",
        "cli.auth.logout.none": "[yellow]No local credentials file to delete.[/yellow]",
        # Scan command
        "cli.scan.help": "[Read-Only] Scan library for split albums and verify storefront availability.",
        "cli.scan.opt_out": "Output path for scan report JSON (defaults to reports/scan_<timestamp>.json).",
        "cli.scan.opt_weak": "Include medium-confidence (same name + artist across Catalog IDs) split detection.",
        "cli.scan.opt_tracks": "Fetch tracks of split candidates to detect local uploads or mixed editions.",
        "cli.scan.opt_sample": "Also save first 3 raw album payloads to reports/sample_raw_albums.json for debugging.",
        "cli.scan.fetching": "Fetching library albums...",
        "cli.scan.fetching_page": "Fetching library albums (page {page}, {total} albums loaded)...",
        "cli.scan.fetched_done": "Finished fetching library albums.",
        "cli.scan.enrich_start": "Validating split candidates (0/{total})...",
        "cli.scan.enrich_item": "Validating split candidates ({idx}/{total}): {name}",
        "cli.scan.failed": "[red]Scan failed:[/red] {exc}",
        "cli.scan.summary_title": "Scan Overview",
        "cli.scan.summary_body": (
            "Storefront: [cyan]{storefront}[/cyan]   "
            "Library Albums: [bold]{total}[/bold]   "
            "Catalog Linked: [green]{linked}[/green]   "
            "Local Only (Protected): [yellow]{local}[/yellow]\n"
            "High Confidence (Same ID): [bold magenta]{high}[/bold magenta]   "
            "Medium Confidence (Same Name): [bold blue]{medium}[/bold blue]   "
            "Safe to Merge: [bold green]{safe}[/bold green]   "
            "Blocked: [bold red]{blocked}[/bold red]"
        ),
        "cli.scan.no_splits": "[green]No split albums detected![/green]",
        "cli.scan.table_title": "Detected {count} Split Album Candidate(s)",
        "cli.scan.col_gid": "Group",
        "cli.scan.col_tier": "Confidence",
        "cli.scan.col_album": "Album / Artist",
        "cli.scan.col_splits": "Splits",
        "cli.scan.col_tracks": "Track Counts",
        "cli.scan.col_cid": "Target Catalog ID",
        "cli.scan.col_status": "Status & Notes",
        "cli.scan.tier_high": "[magenta]High (Same ID)[/magenta]",
        "cli.scan.tier_medium": "[blue]Medium (Name)[/blue]",
        "cli.scan.full_album_suffix": " / full {count}",
        "cli.scan.delisted_tag": " (delisted)",
        "cli.scan.status_safe": "[green]Safe to merge[/green]",
        "cli.scan.status_blocked": "[red]Protected[/red]",
        "cli.scan.saved": "\n[green]Scan report saved to:[/green] {path}",
        "cli.scan.sample_saved": "[dim]Raw sample saved to: {path}[/dim]",
        # Backup command
        "cli.backup.help": "[Read-Only] Snapshot editable playlists and song ratings before deletions.",
        "cli.backup.opt_out": "Output path for backup JSON (defaults to backups/backup_<timestamp>.json).",
        "cli.backup.opt_all_pl": "Include non-editable subscribed playlists (defaults to editable user playlists only).",
        "cli.backup.opt_ratings": "Also back up song ratings/favorites for playlist tracks.",
        "cli.backup.fetching": "Fetching library playlists...",
        "cli.backup.progress": "Backing up playlists ({idx}/{total}): {name}",
        "cli.backup.failed": "[red]Backup failed:[/red] {exc}",
        "cli.backup.done": (
            "[green]Backup completed![/green]\n"
            "Playlists backed up: [bold]{backed}[/bold] / {seen}\n"
            "Track entries: [bold]{tracks}[/bold]\n"
            "Ratings saved: [bold]{ratings}[/bold]\n"
            "Snapshot path: [cyan]{path}[/cyan]"
        ),
        # Merge command
        "cli.merge.help": "Merge split albums: supports --all batching and outputs a Succeeded / Manual-Action report.",
        "cli.merge.opt_report": "Scan report JSON path (defaults to latest reports/scan_*.json).",
        "cli.merge.opt_group": "Group ID(s) to merge, e.g. --group G004 (repeatable).",
        "cli.merge.opt_all": "Process all safe split groups and list remaining manual-action albums in the report.",
        "cli.merge.opt_cid": "Optional: override target Catalog Album ID (single --group only).",
        "cli.merge.opt_force": "Allow merging safety-blocked groups (still enforces storefront availability check).",
        "cli.merge.opt_execute": "Execute cloud DELETE and re-import (defaults strictly to Dry-Run preview).",
        "cli.merge.opt_yes": "Skip confirmation prompt (only applies with --execute).",
        "cli.merge.opt_backup": "Use an existing backup file (by default takes a fresh backup before --execute).",
        "cli.merge.opt_report_out": "Merge report JSON path (defaults to reports/merge_report_<ts>.json on --execute).",
        "cli.merge.err_no_target": "[red]Specify target group(s):[/red] use [bold]--all[/bold] or [bold]--group G004[/bold].",
        "cli.merge.err_cid_multi": "[red]--catalog-id can only be used with a single --group.[/red]",
        "cli.merge.err_no_report": "[red]No scan report found.[/red] Run [bold]amlibmerge scan[/bold] first.",
        "cli.merge.err_missing_group": "[red]Group {gid} not found in report {path}.[/red]",
        "cli.merge.nothing_to_do": "[green]No split albums to process in scan report![/green]",
        "cli.merge.err_backup_missing": "[red]Backup file does not exist: {path}[/red]",
        "cli.merge.dry_using_backup": "[dim]Dry-Run estimating affected playlists from backup: {path}[/dim]",
        "cli.merge.confirm_skip_note": "\n[yellow]{count}[/yellow] safety-blocked group(s) will be skipped and listed in the Manual Action Required report.",
        "cli.merge.confirm_body": (
            "About to execute [bold red]cloud DELETE + full-album re-import[/bold red] for [bold]{count}[/bold] group(s):\n"
            "{items}{skip_note}\n\n"
            "[yellow]Note: deleting and re-importing resets play counts and original Date Added timestamps. "
            "Affected custom playlist tracks will be backed up and restored automatically.[/yellow]"
        ),
        "cli.merge.confirm_item": "  • [cyan]{gid}[/cyan] {name} (delete {old_ids} -> re-add {cid})",
        "cli.merge.confirm_title": "Confirm Execution",
        "cli.merge.confirm_prompt": "Proceed with cloud merge?",
        "cli.merge.cancelled": "[yellow]Cancelled. No cloud changes were made.[/yellow]",
        "cli.merge.pre_backup_progress": "Taking pre-merge playlist & rating backup...",
        "cli.merge.pre_backup_saved": "[green]Pre-merge safety backup saved:[/green] {path}",
        "cli.merge.processing_group": "\n[bold]>>> Processing {gid}: {name}[/bold]",
        "cli.merge.report_title": "Merge Summary Report",
        "cli.merge.summary_dry": (
            "Mode: [cyan]Dry-Run Preview (no cloud changes)[/cyan]   Storefront: [cyan]{storefront}[/cyan]\n"
            "Ready to Auto-Merge: [bold green]{ready}[/bold green]   "
            "Skipped (Manual Action): [bold yellow]{manual}[/bold yellow]"
        ),
        "cli.merge.summary_exec": (
            "Mode: [bold]Executed[/bold]   Storefront: [cyan]{storefront}[/cyan]\n"
            "Auto-Merged: [bold green]{ok}[/bold green]   "
            "Manual Action Required: [bold yellow]{manual}[/bold yellow]"
        ),
        "cli.merge.table_ok_title": "Auto-Merged Albums ({count})",
        "cli.merge.col_new_id": "New Library ID",
        "cli.merge.col_details": "Execution Details",
        "cli.merge.detail_deleted_alb": "Deleted albums HTTP: {statuses}",
        "cli.merge.detail_deleted_songs": "Song-level fallback deletions: {count}",
        "cli.merge.detail_readd": "Re-add HTTP: {status}",
        "cli.merge.detail_pl_restored": "Restored playlists: {info}",
        "cli.merge.detail_pl_item": "{pid} (+{cnt})",
        "cli.merge.detail_ratings": "Restored ratings: {count}",
        "cli.merge.table_dry_title": "Dry-Run Preview: Auto-Mergeable Albums ({count})",
        "cli.merge.col_affected": "Affected Playlists & Notes",
        "cli.merge.dry_pl_note": "Will back up & restore playlists: {info}",
        "cli.merge.dry_pl_item": "{pid} ({cnt} tracks)",
        "cli.merge.dry_no_pl": "[green]No playlist impact; ready to merge[/green]",
        "cli.merge.table_manual_title": "Manual Action Required ({count})",
        "cli.merge.col_category": "Category",
        "cli.merge.col_reason_hint": "Reason & Manual Action Advice",
        "cli.merge.stage_still_split": "[yellow]Still Split\n(Catalog Bug)[/yellow]",
        "cli.merge.stage_skipped": "[yellow]Blocked / Skipped[/yellow]",
        "cli.merge.stage_failed": "[red]Failed[/red]",
        "cli.merge.label_reason": "[red]Reason: {reason}[/red]",
        "cli.merge.label_advice": "[cyan]Advice: {hint}[/cyan]",
        "cli.merge.report_saved": "\n[green]Merge report saved to:[/green] {path}",
        "cli.merge.dry_footer": "\n[cyan]Dry-Run preview only — no cloud changes were made.[/cyan] To execute, run:\n  [bold]{cmd}[/bold]",
        "cli.merge.dry_none_auto": "\n[yellow]No albums are eligible for safe auto-merge; all remaining groups are listed above under Manual Action Required.[/yellow]",
        "cli.merge.wal_saved": "[dim]WAL journal written to: {path}[/dim]",
        # Restore command
        "cli.restore.help": "Idempotently restore missing playlist tracks and ratings from a backup snapshot.",
        "cli.restore.opt_backup": "Backup snapshot JSON path (defaults to latest backups/backup_*.json).",
        "cli.restore.opt_playlist": "Only restore a specific playlist ID (defaults to all editable playlists in backup).",
        "cli.restore.opt_execute": "Execute track/rating restoration (defaults to Dry-Run diff preview).",
        "cli.restore.opt_no_ratings": "Skip rating/favorite restoration.",
        "cli.restore.err_no_backup": "[red]No backup snapshot found.[/red] Run [bold]amlibmerge backup[/bold] first.",
        "cli.restore.failed": "[red]Restore failed:[/red] {exc}",
        "cli.restore.title_exec": "Playlist Restore Result",
        "cli.restore.title_dry": "Playlist Diff Preview (Dry-Run)",
        "cli.restore.col_pid": "Playlist ID",
        "cli.restore.col_name": "Playlist Name",
        "cli.restore.col_bk": "Backed Up",
        "cli.restore.col_miss": "Missing",
        "cli.restore.col_done": "Restored",
        "cli.restore.dry_footer": "\n[cyan]Diff preview using {path}.[/cyan] Pass [bold]--execute[/bold] to restore missing tracks.",
        # Detector warnings
        "detector.warn_local_album": "Contains an album without an Apple Music Catalog link (local upload or delisted); auto-delete blocked.",
        "detector.warn_local_tracks": "Album {album_id} contains {count} track(s) without a Catalog link (local upload or delisted); auto-delete blocked.",
        "detector.warn_mixed_tracks": "Group contains {distinct} distinct catalog tracks, exceeding target album track count ({target}); likely mixes multiple recordings/editions. Auto-merge blocked to prevent data loss.",
        "detector.warn_no_target_cid": "No valid target Catalog Album ID found.",
        "detector.warn_delisted_target": "Target Catalog album {cid} is delisted or unavailable in current storefront; deletion blocked.",
        "detector.warn_cross_version": "Cross-version split ({count} distinct Catalog IDs: {cids}); verify target edition manually (auto-merge blocked).",
        "detector.warn_all_full": "Each entry already matches full album track count; may be intentionally distinct editions (e.g. different recordings/deluxe).",
        # Merger steps, reasons & manual hints
        "merger.hint_still_split": "Apple Music's upstream catalog metadata for this album splits even on a fresh add; select all tracks in the desktop Music app -> Get Info and unify Album & Album Artist (temporarily append a suffix, save, then remove it).",
        "merger.hint_delisted": "Target album is unavailable in your current storefront—DO NOT delete it from your library; edit metadata manually in the desktop Music app instead.",
        "merger.hint_local": "Contains tracks without a Catalog link (local uploads or region-delisted tracks); deleting would lose those tracks. Unify metadata manually in the desktop Music app.",
        "merger.hint_mixed": "Multiple recordings/editions are mixed in this group; merging to one Catalog ID would drop the other edition. Review manually, or run `--group <ID> --catalog-id <TARGET_ID> --force --execute` after confirming.",
        "merger.hint_failed": "A network or API error occurred; inspect the WAL journal and run `amlibmerge restore --execute` if needed before retrying.",
        "merger.hint_generic": "Inspect this album manually and merge it in the Apple Music app.",
        "merger.reason_no_cid": "Missing target Catalog Album ID.",
        "merger.reason_blocked": "Blocked by safety guardrails (not auto-deleted).",
        "merger.reason_auto_skipped": "Blocked by safety guardrails (auto-skipped for manual review).",
        "merger.reason_auto_skipped_dry": "Blocked by safety guardrails (requires manual review).",
        "merger.reason_404": "Target Catalog album {cid} is unavailable (404) in storefront {storefront}; refusing to delete!",
        "merger.step_check_catalog": "Verifying target album {cid} availability in storefront {storefront}...",
        "merger.step_delete_album": "Deleting old library album {aid} ({count} tracks)...",
        "merger.step_delete_album_fallback": "Album delete failed for {aid} ({exc}); falling back to song-level deletion...",
        "merger.step_poll_delete": "Waiting for cloud deletion sync ({attempt}/{total}), remaining: {remaining}...",
        "merger.step_song_cleanup": "Album shell(s) {remaining} still cached in cloud; deleting all {count} group song(s)...",
        "merger.step_poll_song_delete": "Waiting for sync after song-level deletion ({attempt}/{total}), remaining: {remaining}...",
        "merger.step_still_present_warn": "Warning: old shell(s) {remaining} still visible before re-add; continuing re-import...",
        "merger.step_settle": "Old album shells cleared; waiting {sec}s for cloud shard sync before re-adding...",
        "merger.step_readd": "Re-importing full catalog album {cid} to library...",
        "merger.step_poll_readd": "Waiting for new album sync ({attempt}/{total}), linked IDs: {ids}...",
        "merger.warn_still_split": "Cloud still returned {count} library album IDs ({ids}) after clean re-import; upstream Apple Music catalog metadata itself splits this release (requires manual Get Info merge in desktop Music app).",
        "merger.step_restore_pl": "Restoring {count} missing track(s) to playlist {pid}...",
        "merger.step_compare_pl": "Comparing playlist: {name} ({pid})...",
        "merger.step_restore_pl_named": "Restoring {count} missing track(s) to playlist {name}...",
        # Browser auth messages
        "browser.err_explicit_missing": "Specified browser executable not found: {path}",
        "browser.err_none_found": "No Chromium-based browser (Chrome / Edge / Brave) found. Use --browser <path> or `amlibmerge auth login --manual`.",
        "browser.err_ws_closed": "CDP WebSocket handshake closed unexpectedly.",
        "browser.err_ws_handshake": "CDP WebSocket handshake failed: {status}",
        "browser.err_ws_accept": "CDP WebSocket Sec-WebSocket-Accept verification failed.",
        "browser.err_cdp_call": "CDP call {method} failed: {err}",
        "browser.err_early_exit": "Browser process exited prematurely before CDP port became ready.",
        "browser.err_cdp_timeout": "Timed out waiting for browser debugging port.",
        "browser.status_jwt": "Fetching public Web Developer JWT from music.apple.com...",
        "browser.status_launch": "Launching browser login window ({name})...",
        "browser.status_wait_login": "Sign in with your Apple ID in the browser window (it will close automatically once verified)...",
        "browser.err_window_closed": "Browser window was closed before a valid Apple Music session was detected.",
        "browser.status_verifying": "Detected media-user-token; verifying session...",
        "browser.status_verified": "Login verified (storefront={sf}); closing browser window...",
        "browser.status_stale_cookie": "Detected an expired session cookie; please sign in again in the browser window...",
        "browser.err_timeout": "Timed out waiting for browser login (exceeded {sec}s).",
        # TUI labels & modals
        "tui.subtitle": "Apple Music Split Album Interactive Manager",
        "tui.bind.login": "Login",
        "tui.bind.scan": "Scan",
        "tui.bind.backup": "Backup",
        "tui.bind.toggle": "Select",
        "tui.bind.select_safe": "All Safe",
        "tui.bind.clear": "Clear",
        "tui.bind.dry_run": "Dry-Run",
        "tui.bind.merge": "Merge",
        "tui.bind.restore": "Restore",
        "tui.bind.lang": "EN/中文",
        "tui.bind.quit": "Quit",
        "tui.btn.login": "Login (L)",
        "tui.btn.scan": "Scan (S)",
        "tui.btn.backup": "Backup (B)",
        "tui.btn.select_safe": "All Safe (A)",
        "tui.btn.dry_run": "Dry-Run (D)",
        "tui.btn.merge": "Merge (M)",
        "tui.btn.restore": "Restore (R)",
        "tui.btn.lang": "中文 (G)",
        "tui.tab.groups": "Split Albums",
        "tui.tab.report": "Merge Report",
        "tui.tab.restore": "Playlist Restore",
        "tui.btn.restore_diff": "Compare Live Diff",
        "tui.btn.restore_exec": "Restore Missing Tracks",
        "tui.empty_report": "[dim]No merge preview or execution yet. Select groups and press [bold]D[/bold] for Dry-Run or [bold]M[/bold] to merge.[/dim]",
        "tui.empty_detail": "[dim]Move the cursor in the left table to inspect album shells, track lists, warnings, and affected playlists.[/dim]",
        "tui.empty_detail_none": "[dim]No split groups loaded. Press [bold]S[/bold] to scan your library.[/dim]",
        "tui.welcome": "[cyan]Welcome to amlibmerge TUI! Press [bold]S[/bold] to scan, [bold]Space[/bold] to select, [bold]D[/bold] for Dry-Run, [bold]M[/bold] to merge, [bold]G[/bold] to toggle language.[/cyan]",
        "tui.col.sel": "Sel",
        "tui.col.gid": "Group",
        "tui.col.tier": "Tier",
        "tui.col.album": "Album / Artist",
        "tui.col.tracks": "Tracks",
        "tui.col.cid": "Catalog ID",
        "tui.col.status": "Status",
        "tui.col.pid": "Playlist ID",
        "tui.col.pname": "Playlist Name",
        "tui.col.pbk": "Backed Up",
        "tui.col.pmiss": "Missing",
        "tui.col.pdone": "Restored",
        "tui.status.logged_in": "[green]Signed In[/green] Storefront:[bold cyan]{sf}[/bold cyan] Token:[dim]{tok}[/dim]",
        "tui.status.logged_out": "[yellow]Not Signed In (press L)[/yellow]",
        "tui.status.report": "Report:[cyan]{name}[/cyan]",
        "tui.status.report_none": "Report:[dim]None (press S)[/dim]",
        "tui.status.backup": "Backup:[green]{name}[/green]",
        "tui.status.backup_none": "Backup:[dim]None (press B)[/dim]",
        "tui.status.selected": "Selected:[bold magenta]{count}[/bold magenta]",
        "tui.tier_high": "High (ID)",
        "tui.tier_medium": "Med (Name)",
        "tui.status_safe": "Safe",
        "tui.status_blocked": "Protected",
        "tui.detail.badge_safe": "[bold green]Safe to Merge[/bold green]",
        "tui.detail.badge_blocked": "[bold red]Protected (Blocked by Default)[/bold red]",
        "tui.detail.avail_ok": "[green]Available ✓[/green]",
        "tui.detail.avail_404": "[red]Delisted / 404 ✗[/red]",
        "tui.detail.avail_unk": "[dim]Not checked[/dim]",
        "tui.detail.tier_line": "Confidence: {tier}",
        "tui.detail.tier_high": "High (Same Catalog ID)",
        "tui.detail.tier_med": "Medium (Same Name & Artist)",
        "tui.detail.target_cid": "Target Catalog ID: [bold]{cid}[/bold] ({avail})",
        "tui.detail.target_tc": "Target Full Track Count: {count}",
        "tui.detail.affected_pl": "Affected Custom Playlists: [yellow]{desc}[/yellow]",
        "tui.detail.affected_pl_none": "Affected Custom Playlists: [green]None[/green]",
        "tui.detail.affected_ratings": "Affected Ratings: {count}",
        "tui.detail.warnings_hdr": "\n[bold yellow]Safety Warnings / Block Reasons[/bold yellow]",
        "tui.detail.shells_hdr": "\n[bold]Split Album Shells ({count})[/bold]",
        "tui.detail.shell_line": "\n[cyan]▸ {aid}[/cyan] ({tc} tracks | cid={cid} | added {date})",
        "tui.detail.no_catalog": "NoCatalog",
        "tui.detail.unknown_date": "unknown date",
        "tui.detail.empty_shell": "    [dim](Empty shell or tracks not loaded; tracks may live on sibling shell)[/dim]",
        "tui.detail.more_tracks": "    [dim]... {count} more track(s) omitted[/dim]",
        "tui.report.mode_dry": "[cyan]Dry-Run Preview (no cloud changes)[/cyan]",
        "tui.report.mode_exec": "[bold green]Execution Completed[/bold green]",
        "tui.report.header": "Mode: {mode}   Storefront: [cyan]{sf}[/cyan]\nAuto-Merged: [bold green]{ok}[/bold green]   Dry-Run Ready: [bold cyan]{dry}[/bold cyan]   Manual Action Required: [bold yellow]{manual}[/bold yellow]",
        "tui.report.sec_ok": "\n[bold green]Auto-Merged Albums[/bold green]",
        "tui.report.sec_dry": "\n[bold cyan]Dry-Run Preview: Auto-Mergeable Albums[/bold cyan]",
        "tui.report.sec_manual": "\n[bold yellow]Manual Action Required[/bold yellow]",
        "tui.report.item_ok": "  • [cyan]{gid}[/cyan] [bold]{name}[/bold] -> New ID: [green]{new_ids}[/green] (Catalog {cid}){pl_info}",
        "tui.report.item_ok_pl": " | Playlists restored: {info}",
        "tui.report.item_dry": "  • [cyan]{gid}[/cyan] [bold]{name}[/bold] -> Target {cid} {pl_note}",
        "tui.report.item_dry_pl": "(will back up & restore {cnt} playlist tracks)",
        "tui.report.item_dry_no_pl": "(no playlist impact)",
        "tui.report.stage_still_split": "Still Split (Upstream Catalog Bug)",
        "tui.report.stage_skipped": "Blocked / Skipped",
        "tui.report.stage_failed": "Failed",
        "tui.report.item_reason": "      [red]Reason: {reason}[/red]",
        "tui.report.item_advice": "      [cyan]Advice: {hint}[/cyan]",
        "tui.report.wal": "\n[dim]WAL Journal: {path}[/dim]",
        "tui.report.panel_title": "Merge Report",
        "tui.log.loaded_report": "[green]Loaded scan report:[/green] {path} ({count} split candidate(s))",
        "tui.log.load_report_err": "[yellow]Failed to load scan report: {err}[/yellow]",
        "tui.log.load_backup_err": "[yellow]Failed to load latest backup (ignored): {err}[/yellow]",
        "tui.log.need_login": "[red]Not signed in to Apple Music.[/red] Press [bold]L[/bold] to sign in.",
        "tui.log.need_scan": "[yellow]No scan report loaded. Press S to scan your library first.[/yellow]",
        "tui.log.selected_safe": "[cyan]Selected all {count} safe-to-merge group(s).[/cyan]",
        "tui.log.lang_switched": "[cyan]Display language switched to English (saved to settings).[/cyan]",
        "tui.log.login_ok": "[green]Signed in! Storefront={sf}, Token={tok}[/green]",
        "tui.log.login_fail": "[red]Browser login failed: {err}[/red]",
        "tui.log.login_err": "[red]Login error: {err}[/red]",
        "tui.log.manual_ok": "[green]Token verified! Storefront={sf}, Token={tok}[/green]",
        "tui.log.manual_fail": "[red]Token verification failed: {err}[/red]",
        "tui.log.scan_start": "[cyan]Scanning Apple Music library (read-only)...[/cyan]",
        "tui.log.scan_page": "[dim]Loaded page {page} ({total} albums)...[/dim]",
        "tui.log.scan_enrich": "[dim]Validating candidate ({idx}/{total}): {name}[/dim]",
        "tui.log.scan_done": "[green]Scan complete! {total} albums, {splits} split candidate(s) ({safe} safe). Saved to {path}[/green]",
        "tui.log.scan_fail": "[red]Scan failed: {err}[/red]",
        "tui.log.backup_start": "[cyan]Backing up editable playlists & song ratings...[/cyan]",
        "tui.log.backup_pl": "[dim]Backing up playlist ({idx}/{total}): {name}[/dim]",
        "tui.log.backup_done": "[green]Backup complete! {pls} playlist(s), {tracks} track(s) -> {path}[/green]",
        "tui.log.backup_fail": "[red]Backup failed: {err}[/red]",
        "tui.log.dry_default_all": "[dim]No rows checked; previewing all groups in report...[/dim]",
        "tui.log.select_before_merge": "[yellow]Select at least one group with Space (or press A for all safe groups) before merging.[/yellow]",
        "tui.log.merge_cancelled": "[dim]Cloud merge cancelled; no changes were made.[/dim]",
        "tui.log.pre_backup_start": "[cyan]Taking pre-merge safety backup of playlists & ratings...[/cyan]",
        "tui.log.pre_backup_done": "[green]Pre-merge backup saved: {path}[/green]",
        "tui.log.merge_group": "[bold]>>> Processing {gid}: {name}[/bold]",
        "tui.log.merge_done": "[green]Merge finished! Succeeded: {ok}, Manual Action Required: {manual}. Report saved: {path}[/green]",
        "tui.log.dry_done": "[cyan]Dry-Run preview finished: {ready} auto-mergeable, {manual} manual-action (no cloud writes).[/cyan]",
        "tui.log.merge_err": "[red]Merge error: {err}[/red]",
        "tui.log.need_backup": "[yellow]No local backup found. Press B to create a backup first.[/yellow]",
        "tui.log.restore_cancelled": "[dim]Playlist restore cancelled.[/dim]",
        "tui.log.restore_done": "[green]Restore complete: added {tracks} track(s) and {ratings} rating(s).[/green]",
        "tui.log.restore_dry_done": "[cyan]Diff check complete: {missing} missing track(s) found (Dry-Run).[/cyan]",
        "tui.log.restore_err": "[red]Restore error: {err}[/red]",
        "tui.busy.login": "Waiting for browser sign-in...",
        "tui.busy.manual": "Verifying media-user-token...",
        "tui.busy.scan": "Scanning library...",
        "tui.busy.backup": "Backing up playlists...",
        "tui.busy.merge_exec": "Executing cloud merge...",
        "tui.busy.merge_dry": "Running Dry-Run preview...",
        "tui.busy.restore_exec": "Restoring missing playlist tracks...",
        "tui.busy.restore_dry": "Comparing live playlists...",
        # Modals
        "tui.modal.auth.title": "[bold cyan]Apple Music Sign-In Method[/bold cyan]",
        "tui.modal.auth.desc": (
            "• [bold]Browser Popup[/bold]: Open local Edge / Chrome to music.apple.com and auto-capture token\n"
            "• [bold]Fresh Profile[/bold]: Launch a clean temporary browser profile (switch Apple ID)\n"
            "• [bold]Manual Paste[/bold]: Paste media-user-token directly (headless/SSH)"
        ),
        "tui.modal.auth.btn_browser": "Browser Login",
        "tui.modal.auth.btn_fresh": "Fresh Profile",
        "tui.modal.auth.btn_manual": "Manual Paste",
        "tui.modal.token.title": "[bold yellow]Enter media-user-token Manually[/bold yellow]",
        "tui.modal.token.desc": "Sign in at https://music.apple.com -> F12 -> Application -> Cookies, copy media-user-token and paste below (hidden):",
        "tui.modal.token.placeholder": "Paste media-user-token...",
        "tui.modal.token.btn_ok": "Verify & Save",
        "tui.modal.merge.title": "[bold red]Confirm Cloud Merge ({count} group(s))[/bold red]",
        "tui.modal.merge.item": "• [{gid}] {name} ({tag})\n    Delete: {old_ids}  ->  Re-import full album: {cid}",
        "tui.modal.merge.tag_safe": "[green]Safe[/green]",
        "tui.modal.merge.tag_blocked": "[red]Protected[/red]",
        "tui.modal.merge.note": "[yellow]Note: a fresh playlist backup and WAL journal will be saved automatically. Deleting & re-importing resets play counts and Date Added; affected custom playlist tracks are restored automatically.[/yellow]",
        "tui.modal.merge.force_chk": "I reviewed protected group(s) ({ids}) and allow forced merge (404 delisted albums are still strictly blocked)",
        "tui.modal.merge.cid_placeholder": "Optional: override target Catalog Album ID",
        "tui.modal.merge.err_need_force": "Check the force confirmation box above before executing.",
        "tui.modal.merge.btn_confirm": "Execute Merge",
        "tui.modal.restore.title": "[bold cyan]Confirm Playlist & Rating Restore[/bold cyan]",
        "tui.modal.restore.body": "Backup snapshot: {path}\nMissing tracks to add back: [bold]{missing}[/bold] (idempotent, deduplicated)\nRatings to restore: [bold]{ratings}[/bold]",
        "tui.modal.restore.btn_confirm": "Restore Now",
    },
    "zh": {
        # General / Common
        "common.unknown": "未知",
        "common.none": "无",
        "common.cancel": "取消",
        "common.confirm": "确认",
        "common.invalid_lang": "不支持的语言 '{lang}'，请使用 'en'、'zh' 或 'auto'。",
        # CLI Root & Subcommand Help
        "cli.app_help": "Apple Music 资料库分裂专辑扫描、备份与合并工具",
        "cli.auth_help": "管理 Apple Music 网页端鉴权凭证",
        "cli.opt.version": "显示版本号并退出",
        "cli.opt.lang": "显示语言：en、zh 或 auto（默认英文，中文系统自动显示中文）",
        "cli.opt.tui": "启动全功能交互式终端界面（TUI）",
        "cli.opt.tui_report": "配合 --tui 使用：启动时预加载指定的扫描报告路径",
        "cli.err.tui_with_subcommand": "--tui 不能与其他子命令同时使用。",
        "cli.cmd.tui.help": "启动全功能交互式终端界面（TUI）。",
        "cli.cmd.tui.opt_report": "启动时预加载的扫描报告路径（默认自动加载 reports/ 下最新报告）",
        "cli.cmd.lang.help": "查看或设置持久化显示语言（en / zh / auto）。",
        "cli.cmd.lang.arg": "要保存的语言：en、zh 或 auto（不传则查看当前语言设置）",
        "cli.cmd.lang.current": "当前显示语言: [bold cyan]{lang}[/bold cyan]（来源: {source}）| 本地保存偏好: [dim]{saved}[/dim]",
        "cli.cmd.lang.updated": "已保存语言偏好：[bold cyan]{pref}[/bold cyan]（当前生效：[bold green]{active}[/bold green]）。",
        "cli.err.no_token": "[red]尚未配置 media-user-token。[/red]请先运行 [bold]amlibmerge auth login[/bold] 或设置环境变量 [bold]AM_MEDIA_USER_TOKEN[/bold]。",
        "cli.err.auth_failed": "[red]鉴权失败：[/red]{exc}",
        "cli.err.api_error": "[red]接口异常：[/red]{exc}",
        # Auth commands
        "cli.auth.login.help": "弹出浏览器窗口登录 Apple Music 并自动抓取 media-user-token（也支持 --manual 手动输入）。",
        "cli.auth.login.opt_token": "直接传入 media-user-token（跳过浏览器窗口）",
        "cli.auth.login.opt_manual": "使用手动粘贴 Cookie 模式（适合无图形界面的终端环境）",
        "cli.auth.login.opt_fresh": "使用全新临时浏览器配置启动登录（适合切换 Apple ID）",
        "cli.auth.login.opt_browser": "手动指定 Chrome / Edge / Brave 可执行文件路径",
        "cli.auth.login.opt_timeout": "等待浏览器登录完成的超时秒数",
        "cli.auth.login.opt_dev_token": "可选：手动指定 Web Developer JWT（默认自动从 music.apple.com 提取）",
        "cli.auth.login.preparing": "正在准备浏览器登录窗口...",
        "cli.auth.login.browser_failed": "[red]浏览器自动登录失败：[/red]{exc}",
        "cli.auth.login.manual_hint": "[dim]提示：也可使用 [bold]amlibmerge auth login --manual[/bold] 手动粘贴 media-user-token。[/dim]",
        "cli.auth.login.error": "[red]登录过程异常：[/red]{exc}",
        "cli.auth.login.manual_panel_title": "获取 media-user-token",
        "cli.auth.login.manual_panel_body": (
            "请在浏览器登录 [bold]https://music.apple.com[/bold]，打开 F12 开发者工具 →\n"
            "[bold]Application → Cookies → https://music.apple.com[/bold]，\n"
            "复制名为 [cyan]media-user-token[/cyan] 的 Cookie 值。"
        ),
        "cli.auth.login.manual_prompt": "请输入 media-user-token（输入已隐藏）",
        "cli.auth.login.empty_token": "[red]media-user-token 不能为空。[/red]",
        "cli.auth.login.fetching_jwt": "正在从 music.apple.com 提取公开 Web Developer JWT...",
        "cli.auth.login.jwt_failed": "[red]提取 Web Developer JWT 失败：[/red]{exc}",
        "cli.auth.login.verifying": "正在调用 /v1/me/storefront 校验账号凭证...",
        "cli.auth.login.success": (
            "[green]登录验证成功！[/green]\n"
            "账号区服 (Storefront): [bold cyan]{storefront}[/bold cyan]\n"
            "User Token: [dim]{token}[/dim]\n"
            "Web JWT 过期时间: [dim]{exp}[/dim]\n"
            "凭证存储路径: [dim]{path}[/dim]"
        ),
        "cli.auth.status.help": "查看当前本地鉴权状态。",
        "cli.auth.status.opt_verify": "发起一次真实只读请求验证 Token 是否仍有效",
        "cli.auth.status.not_found": "[yellow]未找到已保存的凭证[/yellow]（路径：{path}）。",
        "cli.auth.status.title": "鉴权状态",
        "cli.auth.status.row_file": "凭证文件",
        "cli.auth.status.row_jwt_state": "Web JWT 状态",
        "cli.auth.status.jwt_valid": "[green]有效[/green]",
        "cli.auth.status.jwt_refresh": "[yellow]需刷新[/yellow]",
        "cli.auth.status.row_jwt_exp": "Web JWT 过期时间",
        "cli.auth.status.row_storefront": "记录的区服",
        "cli.auth.status.row_verify": "在线校验",
        "cli.auth.status.verify_ok": "[green]通过 (storefront={sf})[/green]",
        "cli.auth.status.verify_fail": "[red]失败：{exc}[/red]",
        "cli.auth.logout.help": "清除本地保存的凭证文件。",
        "cli.auth.logout.deleted": "[green]已删除本地凭证文件。[/green]",
        "cli.auth.logout.none": "[yellow]本地没有已保存的凭证文件。[/yellow]",
        # Scan command
        "cli.scan.help": "[只读] 扫描资料库，识别分裂专辑并校验官方曲库在架状态。",
        "cli.scan.opt_out": "扫描报告 JSON 输出路径（默认 reports/scan_<timestamp>.json）",
        "cli.scan.opt_weak": "是否包含中置信度（同名同艺人跨 Catalog ID）分裂检测",
        "cli.scan.opt_tracks": "是否进一步拉取候选分裂专辑曲目，检查是否存在本地自传或混版",
        "cli.scan.opt_sample": "额外保存前 3 条原始专辑 JSON 到 reports/sample_raw_albums.json 便于调试字段结构",
        "cli.scan.fetching": "正在分页拉取资料库专辑...",
        "cli.scan.fetching_page": "正在拉取资料库专辑（第 {page} 页，已读取 {total} 张）...",
        "cli.scan.fetched_done": "资料库专辑拉取完成。",
        "cli.scan.enrich_start": "正在校验分裂候选（0/{total}）...",
        "cli.scan.enrich_item": "正在校验分裂候选（{idx}/{total}）：{name}",
        "cli.scan.failed": "[red]扫描失败：[/red]{exc}",
        "cli.scan.summary_title": "扫描概览",
        "cli.scan.summary_body": (
            "区服: [cyan]{storefront}[/cyan]   "
            "资料库专辑总数: [bold]{total}[/bold]   "
            "官方曲库关联: [green]{linked}[/green]   "
            "纯本地上传(自动保护): [yellow]{local}[/yellow]\n"
            "高置信度分裂(同Catalog ID): [bold magenta]{high}[/bold magenta]   "
            "中置信度分裂(同名同艺人): [bold blue]{medium}[/bold blue]   "
            "可安全合并: [bold green]{safe}[/bold green]   "
            "受保护拦截: [bold red]{blocked}[/bold red]"
        ),
        "cli.scan.no_splits": "[green]未发现分裂专辑！[/green]",
        "cli.scan.table_title": "检测到 {count} 组疑似分裂专辑",
        "cli.scan.col_gid": "组ID",
        "cli.scan.col_tier": "置信度",
        "cli.scan.col_album": "专辑 / 艺人",
        "cli.scan.col_splits": "分裂份数",
        "cli.scan.col_tracks": "各份曲目数",
        "cli.scan.col_cid": "目标 Catalog ID",
        "cli.scan.col_status": "状态与提示",
        "cli.scan.tier_high": "[magenta]高 (同ID)[/magenta]",
        "cli.scan.tier_medium": "[blue]中 (同名)[/blue]",
        "cli.scan.full_album_suffix": " / 整专{count}",
        "cli.scan.delisted_tag": " (已下架)",
        "cli.scan.status_safe": "[green]可安全合并[/green]",
        "cli.scan.status_blocked": "[red]拦截保护[/red]",
        "cli.scan.saved": "\n[green]扫描报告已保存至：[/green]{path}",
        "cli.scan.sample_saved": "[dim]原始样本已保存至：{path}[/dim]",
        # Backup command
        "cli.backup.help": "[只读] 全量备份自建歌单曲目与喜爱状态，防止后续删除专辑导致歌单丢失。",
        "cli.backup.opt_out": "备份快照 JSON 输出路径（默认 backups/backup_<timestamp>.json）",
        "cli.backup.opt_all_pl": "是否包含不可编辑的订阅歌单（默认仅备份可编辑的自建歌单）",
        "cli.backup.opt_ratings": "是否同时备份歌单曲目的喜爱/评分状态",
        "cli.backup.fetching": "正在拉取歌单列表...",
        "cli.backup.progress": "正在备份歌单（{idx}/{total}）：{name}",
        "cli.backup.failed": "[red]备份失败：[/red]{exc}",
        "cli.backup.done": (
            "[green]备份完成！[/green]\n"
            "备份歌单数: [bold]{backed}[/bold] / {seen}\n"
            "备份曲目条目: [bold]{tracks}[/bold]\n"
            "备份评分记录: [bold]{ratings}[/bold]\n"
            "快照路径: [cyan]{path}[/cyan]"
        ),
        # Merge command
        "cli.merge.help": "合并分裂专辑：支持 --all 一键批处理并输出「成功 / 需手动合并」完整报告。",
        "cli.merge.opt_report": "扫描报告路径（默认自动选用 reports/ 下最新的 scan_*.json）",
        "cli.merge.opt_group": "指定要合并的分裂组 ID，例如 --group G004（可多次指定）",
        "cli.merge.opt_all": "一键处理报告中所有可安全合并的分裂组，并在报告中完整列出需手动处理的专辑",
        "cli.merge.opt_cid": "可选：手动覆盖目标 Catalog Album ID（仅支持配合单个 --group 使用）",
        "cli.merge.opt_force": "强制允许处理受保护拦截的分组（仍强制校验目标 Catalog 在架，绝不绕过下架保护）",
        "cli.merge.opt_execute": "真正执行云端删除与重导（不传此参数时严格为 Dry-Run 预览模式）",
        "cli.merge.opt_yes": "跳过交互式确认提示（仅在 --execute 时生效）",
        "cli.merge.opt_backup": "指定已有歌单备份文件（执行模式下默认自动现场拉取最新备份）",
        "cli.merge.opt_report_out": "合并结果报告 JSON 输出路径（执行模式默认保存至 reports/merge_report_<ts>.json）",
        "cli.merge.err_no_target": "[red]请指定要处理的分裂组[/red]：使用 [bold]--all[/bold] 处理全部，或 [bold]--group G004[/bold] 单张处理。",
        "cli.merge.err_cid_multi": "[red]--catalog-id 仅能配合单个 --group 使用。[/red]",
        "cli.merge.err_no_report": "[red]未找到扫描报告。[/red]请先运行 [bold]amlibmerge scan[/bold]。",
        "cli.merge.err_missing_group": "[red]报告 {path} 中不存在分组 {gid}。[/red]",
        "cli.merge.nothing_to_do": "[green]扫描报告中没有任何分裂专辑需要处理！[/green]",
        "cli.merge.err_backup_missing": "[red]指定的备份文件不存在：{path}[/red]",
        "cli.merge.dry_using_backup": "[dim]Dry-Run 使用已有备份快照估算受影响歌单：{path}[/dim]",
        "cli.merge.confirm_skip_note": "\n另有 [yellow]{count}[/yellow] 组触发安全保护将自动跳过，并汇总到末尾的「需手动合并」报告中。",
        "cli.merge.confirm_body": (
            "即将对 [bold]{count}[/bold] 组分裂专辑执行 [bold red]云端删除 + 整专重导[/bold red]：\n"
            "{items}{skip_note}\n\n"
            "[yellow]注意：删除并重导会重置受影响专辑的播放次数与原始添加日期，受影响自建歌单曲目将自动备份并补回。[/yellow]"
        ),
        "cli.merge.confirm_item": "  • [cyan]{gid}[/cyan] {name} (删除 {old_ids} → 重导 {cid})",
        "cli.merge.confirm_title": "执行前确认",
        "cli.merge.confirm_prompt": "确认继续执行？",
        "cli.merge.cancelled": "[yellow]已取消，未执行任何修改。[/yellow]",
        "cli.merge.pre_backup_progress": "执行前自动备份自建歌单与评分...",
        "cli.merge.pre_backup_saved": "[green]前置安全备份已保存：[/green]{path}",
        "cli.merge.processing_group": "\n[bold]>>> 处理 {gid}: {name}[/bold]",
        "cli.merge.report_title": "合并结果总览报告",
        "cli.merge.summary_dry": (
            "模式: [cyan]Dry-Run 预览（未修改云端资料库）[/cyan]   区服: [cyan]{storefront}[/cyan]\n"
            "可自动合并: [bold green]{ready}[/bold green]   "
            "将跳过（需手动处理）: [bold yellow]{manual}[/bold yellow]"
        ),
        "cli.merge.summary_exec": (
            "模式: [bold]正式执行[/bold]   区服: [cyan]{storefront}[/cyan]\n"
            "自动合并成功: [bold green]{ok}[/bold green]   "
            "需手动操作合并: [bold yellow]{manual}[/bold yellow]"
        ),
        "cli.merge.table_ok_title": "✅ 自动合并成功的专辑（{count} 组）",
        "cli.merge.col_new_id": "新资料库 ID",
        "cli.merge.col_details": "执行详情",
        "cli.merge.detail_deleted_alb": "删除旧专辑: {statuses}",
        "cli.merge.detail_deleted_songs": "降级逐首删歌: {count} 首",
        "cli.merge.detail_readd": "整专重导 HTTP: {status}",
        "cli.merge.detail_pl_restored": "歌单恢复: {info}",
        "cli.merge.detail_pl_item": "{pid}(补回{cnt}首)",
        "cli.merge.detail_ratings": "恢复评分: {count}",
        "cli.merge.table_dry_title": "🔍 预览：可自动合并的专辑（{count} 组）",
        "cli.merge.col_affected": "受影响歌单与提示",
        "cli.merge.dry_pl_note": "将自动备份并补回歌单: {info}",
        "cli.merge.dry_pl_item": "{pid}({cnt}首)",
        "cli.merge.dry_no_pl": "[green]无歌单影响，可直接合并[/green]",
        "cli.merge.table_manual_title": "⚠️ 需要手动操作合并的专辑（{count} 组）",
        "cli.merge.col_category": "原因分类",
        "cli.merge.col_reason_hint": "详细原因与手动处理建议",
        "cli.merge.stage_still_split": "[yellow]重导后仍分裂\n(上游曲库异常)[/yellow]",
        "cli.merge.stage_skipped": "[yellow]安全拦截跳过[/yellow]",
        "cli.merge.stage_failed": "[red]执行失败[/red]",
        "cli.merge.label_reason": "[red]原因: {reason}[/red]",
        "cli.merge.label_advice": "[cyan]建议: {hint}[/cyan]",
        "cli.merge.report_saved": "\n[green]合并结果报告已保存至：[/green]{path}",
        "cli.merge.dry_footer": "\n[cyan]以上为 Dry-Run 预览，未对资料库做任何改动。[/cyan]如确认无误，可执行：\n  [bold]{cmd}[/bold]",
        "cli.merge.dry_none_auto": "\n[yellow]当前没有可自动安全合并的专辑，所有剩余分组均已归入上方「需手动操作合并」清单。[/yellow]",
        "cli.merge.wal_saved": "[dim]操作日志 (WAL) 已写入：{path}[/dim]",
        # Restore command
        "cli.restore.help": "根据备份快照幂等补回缺失的歌单曲目与喜爱状态。",
        "cli.restore.opt_backup": "备份快照 JSON 路径（默认选用 backups/ 下最新备份）",
        "cli.restore.opt_playlist": "仅恢复指定歌单 ID（默认检查备份中的所有可编辑歌单）",
        "cli.restore.opt_execute": "真正执行歌单曲目补回与评分恢复（不传此参数时为 Dry-Run 比对模式）",
        "cli.restore.opt_no_ratings": "跳过评分/喜爱状态恢复",
        "cli.restore.err_no_backup": "[red]未找到备份快照文件。[/red]请先运行 [bold]amlibmerge backup[/bold]。",
        "cli.restore.failed": "[red]恢复失败：[/red]{exc}",
        "cli.restore.title_exec": "歌单恢复结果",
        "cli.restore.title_dry": "歌单差异预览 (Dry-Run)",
        "cli.restore.col_pid": "歌单 ID",
        "cli.restore.col_name": "歌单名称",
        "cli.restore.col_bk": "备份曲目数",
        "cli.restore.col_miss": "当前缺失",
        "cli.restore.col_done": "已补回",
        "cli.restore.dry_footer": "\n[cyan]以上为比对预览（使用快照 {path}）。[/cyan]如需补回缺失曲目，请加 [bold]--execute[/bold]。",
        # Detector warnings
        "detector.warn_local_album": "包含无 Catalog 映射的专辑（可能是本地自传或本区已下架），默认禁止自动删除",
        "detector.warn_local_tracks": "专辑 {album_id} 包含 {count} 首无 Catalog 映射的曲目（本地自传或本区已下架），默认禁止自动删除",
        "detector.warn_mixed_tracks": "组内包含 {distinct} 首不同曲库曲目，超过目标整专曲目数 ({target})，疑似混入了多个不同录音/版本；合并到单一版本可能丢失其余录音，默认拦截保护",
        "detector.warn_no_target_cid": "缺少可用的目标 Catalog Album ID",
        "detector.warn_delisted_target": "目标 Catalog 专辑 {cid} 在当前区服已下架或不可用，禁止删除",
        "detector.warn_cross_version": "跨版本分裂（发现 {count} 个不同 Catalog ID：{cids}），请人工核对目标版本，默认拦截保护",
        "detector.warn_all_full": "各条目曲目数均已达到完整专辑长度，可能是刻意保留的不同版本（如不同年份录音/豪华版）",
        # Merger steps, reasons & manual hints
        "merger.hint_still_split": "Apple Music 官方曲库当前元数据自身存在分裂，删后重导无法合并；请在桌面端 Music 应用选中该专辑全部曲目 →「显示简介」，统一「专辑」与「专辑艺人」（可先临时加后缀再改回触发重新归组）。",
        "merger.hint_delisted": "目标专辑在当前区服已下架或不可用，切勿从资料库删除；仅可在桌面端 Music 应用手动编辑元数据合并。",
        "merger.hint_local": "包含无 Catalog 映射的曲目（本地自传或本区已下架），删除会导致这些曲目丢失；建议在桌面端 Music 应用手动统一专辑元数据。",
        "merger.hint_mixed": "组内混入了多个不同录音/版本，直接合并到单一版本会丢失其余录音；请在客户端人工核对要保留的版本，或确认后用 `--group <ID> --catalog-id <目标ID> --force --execute` 指定版本合并。",
        "merger.hint_failed": "执行期间发生网络或接口异常；请检查 WAL 操作日志，按需运行 `amlibmerge restore --execute` 恢复歌单后重试。",
        "merger.hint_generic": "请人工核对该专辑信息并在桌面端 Music 应用手动合并。",
        "merger.reason_no_cid": "缺少目标 Catalog Album ID",
        "merger.reason_blocked": "该分裂组触发安全保护拦截（未自动删除）",
        "merger.reason_auto_skipped": "触发安全护栏（自动跳过，需手动核对处理）",
        "merger.reason_auto_skipped_dry": "触发安全护栏（未自动合并，需手动核对）",
        "merger.reason_404": "目标 Catalog 专辑 {cid} 在区服 {storefront} 不可用（404），拒绝删除！",
        "merger.step_check_catalog": "校验目标专辑 {cid} 在区服 {storefront} 的在架状态...",
        "merger.step_delete_album": "删除旧资料库专辑 {aid}（{count} 首）...",
        "merger.step_delete_album_fallback": "专辑级删除 {aid} 返回异常（{exc}），降级为逐首曲目删除...",
        "merger.step_poll_delete": "等待云端同步删除（{attempt}/{total}），剩余: {remaining}...",
        "merger.step_song_cleanup": "专辑 {remaining} 仍在云端缓存中，降级逐首清理整组共 {count} 首曲目...",
        "merger.step_poll_song_delete": "逐首删歌后等待云端同步（{attempt}/{total}），剩余: {remaining}...",
        "merger.step_still_present_warn": "警告：旧专辑 {remaining} 在重导前仍未完全消失，继续尝试重导...",
        "merger.step_settle": "旧专辑已从云端清除，等待 {sec}s 让云端分片同步稳定后再重导...",
        "merger.step_readd": "正在重新导入完整官方专辑 {cid} 至资料库...",
        "merger.step_poll_readd": "等待新专辑入库同步（{attempt}/{total}），当前关联 ID: {ids}...",
        "merger.warn_still_split": "整专重导后云端仍返回 {count} 个资料库专辑 ID（{ids}），说明该专辑当前 Apple Music 官方曲库自身元数据即存在分裂（删后重导无法根治，需在桌面端 Music 应用手动统一专辑信息）",
        "merger.step_restore_pl": "恢复歌单 {pid} 中缺失的 {count} 首曲目...",
        "merger.step_compare_pl": "比对歌单：{name} ({pid})...",
        "merger.step_restore_pl_named": "向歌单 {name} 补回 {count} 首缺失曲目...",
        # Browser auth messages
        "browser.err_explicit_missing": "指定的浏览器路径不存在：{path}",
        "browser.err_none_found": "未检测到可用的 Chromium 浏览器（Chrome / Edge / Brave）。请使用 --browser 指定浏览器路径，或使用 `amlibmerge auth login --manual` 手动输入 Token。",
        "browser.err_ws_closed": "CDP WebSocket 握手意外断开。",
        "browser.err_ws_handshake": "CDP WebSocket 握手失败：{status}",
        "browser.err_ws_accept": "CDP WebSocket Sec-WebSocket-Accept 校验失败。",
        "browser.err_cdp_call": "CDP 调用 {method} 失败：{err}",
        "browser.err_early_exit": "浏览器进程启动后提前退出，无法连接调试接口。",
        "browser.err_cdp_timeout": "等待浏览器调试端口就绪超时。",
        "browser.status_jwt": "正在从 music.apple.com 获取公开 Web Developer JWT...",
        "browser.status_launch": "正在启动浏览器登录窗口（{name}）...",
        "browser.status_wait_login": "请在弹出的浏览器窗口中点击「登录」并完成 Apple ID 登录（登录成功后将自动关闭窗口）...",
        "browser.err_window_closed": "浏览器窗口已关闭，未检测到有效的 Apple Music 登录会话。",
        "browser.status_verifying": "检测到 media-user-token，正在校验登录有效性...",
        "browser.status_verified": "登录验证成功（storefront={sf}），正在关闭浏览器窗口...",
        "browser.status_stale_cookie": "检测到旧的已失效会话 Cookie，请在浏览器窗口中重新登录 Apple ID...",
        "browser.err_timeout": "等待浏览器登录超时（超过 {sec} 秒）。",
        # TUI labels & modals
        "tui.subtitle": "Apple Music 分裂专辑交互式管理终端",
        "tui.bind.login": "登录",
        "tui.bind.scan": "扫描",
        "tui.bind.backup": "备份",
        "tui.bind.toggle": "勾选",
        "tui.bind.select_safe": "全选安全",
        "tui.bind.clear": "清空勾选",
        "tui.bind.dry_run": "预览",
        "tui.bind.merge": "合并",
        "tui.bind.restore": "歌单恢复",
        "tui.bind.lang": "EN/中文",
        "tui.bind.quit": "退出",
        "tui.btn.login": "登录 (L)",
        "tui.btn.scan": "扫描 (S)",
        "tui.btn.backup": "备份 (B)",
        "tui.btn.select_safe": "全选安全 (A)",
        "tui.btn.dry_run": "预览 (D)",
        "tui.btn.merge": "执行合并 (M)",
        "tui.btn.restore": "歌单恢复 (R)",
        "tui.btn.lang": "EN (G)",
        "tui.tab.groups": "分裂专辑列表",
        "tui.tab.report": "合并报告",
        "tui.tab.restore": "歌单恢复",
        "tui.btn.restore_diff": "比对线上差异",
        "tui.btn.restore_exec": "执行补回缺失曲目",
        "tui.empty_report": "[dim]尚未运行合并预览或执行。请勾选分组后按 [bold]D[/bold] 预览或 [bold]M[/bold] 执行。[/dim]",
        "tui.empty_detail": "[dim]在左侧列表移动光标以查看分裂专辑明细、曲目列表、拦截原因与受影响歌单。[/dim]",
        "tui.empty_detail_none": "[dim]当前没有可检视的分裂组。按 [bold]S[/bold] 扫描资料库后可在此查看详细信息。[/dim]",
        "tui.welcome": "[cyan]欢迎使用 amlibmerge TUI！按 [bold]S[/bold] 扫描资料库，[bold]Space[/bold] 勾选，[bold]D[/bold] 预览，[bold]M[/bold] 执行合并，[bold]G[/bold] 切换中英文。[/cyan]",
        "tui.col.sel": "选",
        "tui.col.gid": "组ID",
        "tui.col.tier": "置信度",
        "tui.col.album": "专辑 / 艺人",
        "tui.col.tracks": "曲目分布",
        "tui.col.cid": "Catalog ID",
        "tui.col.status": "状态",
        "tui.col.pid": "歌单 ID",
        "tui.col.pname": "歌单名称",
        "tui.col.pbk": "备份曲目",
        "tui.col.pmiss": "线上缺失",
        "tui.col.pdone": "已补回",
        "tui.status.logged_in": "[green]已登录[/green] 区服:[bold cyan]{sf}[/bold cyan] Token:[dim]{tok}[/dim]",
        "tui.status.logged_out": "[yellow]未登录（按 L 登录）[/yellow]",
        "tui.status.report": "报告:[cyan]{name}[/cyan]",
        "tui.status.report_none": "报告:[dim]无（按 S 扫描）[/dim]",
        "tui.status.backup": "备份:[green]{name}[/green]",
        "tui.status.backup_none": "备份:[dim]无（按 B 备份）[/dim]",
        "tui.status.selected": "已勾选:[bold magenta]{count}[/bold magenta]",
        "tui.tier_high": "高 (同ID)",
        "tui.tier_medium": "中 (同名)",
        "tui.status_safe": "可合并",
        "tui.status_blocked": "拦截保护",
        "tui.detail.badge_safe": "[bold green]可安全合并[/bold green]",
        "tui.detail.badge_blocked": "[bold red]受保护拦截（默认不自动合并）[/bold red]",
        "tui.detail.avail_ok": "[green]在架 ✓[/green]",
        "tui.detail.avail_404": "[red]已下架 / 404 ✗[/red]",
        "tui.detail.avail_unk": "[dim]未校验[/dim]",
        "tui.detail.tier_line": "置信度: {tier}",
        "tui.detail.tier_high": "高 (同 Catalog ID)",
        "tui.detail.tier_med": "中 (同名同艺人)",
        "tui.detail.target_cid": "目标 Catalog ID: [bold]{cid}[/bold] ({avail})",
        "tui.detail.target_tc": "目标整专曲目数: {count}",
        "tui.detail.affected_pl": "受影响自建歌单: [yellow]{desc}[/yellow]",
        "tui.detail.affected_pl_none": "受影响自建歌单: [green]无[/green]",
        "tui.detail.affected_ratings": "受影响评分: {count} 条",
        "tui.detail.warnings_hdr": "\n[bold yellow]安全提示 / 拦截原因[/bold yellow]",
        "tui.detail.shells_hdr": "\n[bold]分裂专辑壳明细（共 {count} 份）[/bold]",
        "tui.detail.shell_line": "\n[cyan]▸ {aid}[/cyan] ({tc} 首 | cid={cid} | 添加于 {date})",
        "tui.detail.no_catalog": "无Catalog",
        "tui.detail.unknown_date": "未知日期",
        "tui.detail.empty_shell": "    [dim](空壳或未加载曲目，实际曲目可能挂在同组另一条目下)[/dim]",
        "tui.detail.more_tracks": "    [dim]... 其余 {count} 首已省略[/dim]",
        "tui.report.mode_dry": "[cyan]Dry-Run 预览（未修改云端资料库）[/cyan]",
        "tui.report.mode_exec": "[bold green]正式执行完成[/bold green]",
        "tui.report.header": "模式: {mode}   区服: [cyan]{sf}[/cyan]\n自动合并成功: [bold green]{ok}[/bold green]   预览就绪: [bold cyan]{dry}[/bold cyan]   需手动操作合并: [bold yellow]{manual}[/bold yellow]",
        "tui.report.sec_ok": "\n[bold green]✅ 自动合并成功的专辑[/bold green]",
        "tui.report.sec_dry": "\n[bold cyan]🔍 Dry-Run 预览：可自动合并的专辑[/bold cyan]",
        "tui.report.sec_manual": "\n[bold yellow]⚠️ 需要手动操作合并的专辑[/bold yellow]",
        "tui.report.item_ok": "  • [cyan]{gid}[/cyan] [bold]{name}[/bold] → 新ID: [green]{new_ids}[/green] (Catalog {cid}){pl_info}",
        "tui.report.item_ok_pl": " | 歌单补回: {info}",
        "tui.report.item_dry": "  • [cyan]{gid}[/cyan] [bold]{name}[/bold] → 目标整专 {cid} {pl_note}",
        "tui.report.item_dry_pl": "（将自动备份并补回 {cnt} 首自建歌单曲目）",
        "tui.report.item_dry_no_pl": "（无歌单影响）",
        "tui.report.stage_still_split": "重导后仍分裂（上游曲库异常）",
        "tui.report.stage_skipped": "安全拦截跳过",
        "tui.report.stage_failed": "执行失败",
        "tui.report.item_reason": "      [red]原因: {reason}[/red]",
        "tui.report.item_advice": "      [cyan]建议: {hint}[/cyan]",
        "tui.report.wal": "\n[dim]WAL 日志: {path}[/dim]",
        "tui.report.panel_title": "合并结果报告",
        "tui.log.loaded_report": "[green]已自动加载扫描报告：[/green]{path}（共 {count} 组分裂候选）",
        "tui.log.load_report_err": "[yellow]读取扫描报告失败：{err}[/yellow]",
        "tui.log.load_backup_err": "[yellow]读取最新备份失败（已忽略）：{err}[/yellow]",
        "tui.log.need_login": "[red]尚未登录 Apple Music。[/red]请先按 [bold]L[/bold] 登录账号。",
        "tui.log.need_scan": "[yellow]尚未加载扫描报告，请先按 S 扫描资料库。[/yellow]",
        "tui.log.selected_safe": "[cyan]已自动勾选全部 {count} 组可安全合并的专辑。[/cyan]",
        "tui.log.lang_switched": "[cyan]显示语言已切换为简体中文（已保存到本地设置）。[/cyan]",
        "tui.log.login_ok": "[green]登录成功！区服={sf}，Token={tok}[/green]",
        "tui.log.login_fail": "[red]浏览器登录失败：{err}[/red]",
        "tui.log.login_err": "[red]登录异常：{err}[/red]",
        "tui.log.manual_ok": "[green]手动 Token 校验成功！区服={sf}，Token={tok}[/green]",
        "tui.log.manual_fail": "[red]Token 校验失败：{err}[/red]",
        "tui.log.scan_start": "[cyan]开始扫描 Apple Music 资料库（只读）...[/cyan]",
        "tui.log.scan_page": "[dim]已拉取第 {page} 页，共 {total} 张专辑...[/dim]",
        "tui.log.scan_enrich": "[dim]校验候选 ({idx}/{total})：{name}[/dim]",
        "tui.log.scan_done": "[green]扫描完成！共 {total} 张专辑，发现 {splits} 组分裂候选（可安全合并 {safe} 组）。报告已保存至 {path}[/green]",
        "tui.log.scan_fail": "[red]扫描失败：{err}[/red]",
        "tui.log.backup_start": "[cyan]开始备份可编辑自建歌单与喜爱评分...[/cyan]",
        "tui.log.backup_pl": "[dim]备份歌单 ({idx}/{total})：{name}[/dim]",
        "tui.log.backup_done": "[green]备份完成！共 {pls} 个歌单、{tracks} 条曲目 → {path}[/green]",
        "tui.log.backup_fail": "[red]备份失败：{err}[/red]",
        "tui.log.dry_default_all": "[dim]未单独勾选分组，默认对报告中全部组进行 Dry-Run 预览...[/dim]",
        "tui.log.select_before_merge": "[yellow]请先用 Space 勾选至少一个分裂组（或按 A 全选安全组）后再执行合并。[/yellow]",
        "tui.log.merge_cancelled": "[dim]已取消云端合并，未执行任何修改。[/dim]",
        "tui.log.pre_backup_start": "[cyan]执行前自动拉取最新自建歌单与评分安全备份...[/cyan]",
        "tui.log.pre_backup_done": "[green]前置安全备份已保存：{path}[/green]",
        "tui.log.merge_group": "[bold]>>> 处理 {gid}: {name}[/bold]",
        "tui.log.merge_done": "[green]合并完成！成功 {ok} 组，需手动处理 {manual} 组。报告已保存：{path}[/green]",
        "tui.log.dry_done": "[cyan]Dry-Run 预览完成：可自动合并 {ready} 组，需手动处理 {manual} 组（未做任何云端写入）。[/cyan]",
        "tui.log.merge_err": "[red]合并流程异常：{err}[/red]",
        "tui.log.need_backup": "[yellow]未找到本地备份快照，请先按 B 创建备份。[/yellow]",
        "tui.log.restore_cancelled": "[dim]已取消歌单补回。[/dim]",
        "tui.log.restore_done": "[green]歌单恢复完成：已补回 {tracks} 首曲目，恢复 {ratings} 条评分。[/green]",
        "tui.log.restore_dry_done": "[cyan]歌单差异比对完成：共发现 {missing} 首缺失曲目（Dry-Run 未写入）。[/cyan]",
        "tui.log.restore_err": "[red]歌单恢复异常：{err}[/red]",
        "tui.busy.login": "等待浏览器登录...",
        "tui.busy.manual": "校验 media-user-token...",
        "tui.busy.scan": "正在扫描资料库...",
        "tui.busy.backup": "正在备份自建歌单...",
        "tui.busy.merge_exec": "正在执行云端合并...",
        "tui.busy.merge_dry": "正在运行 Dry-Run 预览...",
        "tui.busy.restore_exec": "正在补回缺失歌单曲目...",
        "tui.busy.restore_dry": "正在比对线上歌单差异...",
        # Modals
        "tui.modal.auth.title": "[bold cyan]Apple Music 登录方式[/bold cyan]",
        "tui.modal.auth.desc": (
            "• [bold]浏览器弹窗登录[/bold]：打开本机 Edge / Chrome 登录 music.apple.com 并自动抓取 Token\n"
            "• [bold]全新配置登录[/bold]：使用全新临时浏览器配置（适合切换 Apple ID）\n"
            "• [bold]手动粘贴 Token[/bold]：适合无图形界面或已有 media-user-token 的环境"
        ),
        "tui.modal.auth.btn_browser": "浏览器登录",
        "tui.modal.auth.btn_fresh": "全新配置登录",
        "tui.modal.auth.btn_manual": "手动输入",
        "tui.modal.token.title": "[bold yellow]手动输入 media-user-token[/bold yellow]",
        "tui.modal.token.desc": "在浏览器登录 https://music.apple.com → F12 → Application → Cookies，复制 media-user-token 粘贴到下方（输入已隐藏）：",
        "tui.modal.token.placeholder": "粘贴 media-user-token...",
        "tui.modal.token.btn_ok": "校验并保存",
        "tui.modal.merge.title": "[bold red]确认执行云端合并（共 {count} 组）[/bold red]",
        "tui.modal.merge.item": "• [{gid}] {name} ({tag})\n    删除: {old_ids}  →  重导整专: {cid}",
        "tui.modal.merge.tag_safe": "[green]安全[/green]",
        "tui.modal.merge.tag_blocked": "[red]受保护拦截[/red]",
        "tui.modal.merge.note": "[yellow]注意：执行前会自动拉取最新歌单备份并写入 WAL 日志；删除重导会重置受影响专辑的播放次数与原始添加日期，受影响自建歌单曲目会自动补回。[/yellow]",
        "tui.modal.merge.force_chk": "我已核对受拦截分组（{ids}）并允许强制合并（仍严格校验官方曲库在架，404 绝不删除）",
        "tui.modal.merge.cid_placeholder": "可选：覆盖目标 Catalog Album ID",
        "tui.modal.merge.err_need_force": "请先勾选上方强制确认选项后再执行。",
        "tui.modal.merge.btn_confirm": "确认执行合并",
        "tui.modal.restore.title": "[bold cyan]确认执行歌单与评分恢复[/bold cyan]",
        "tui.modal.restore.body": "备份快照：{path}\n待补回缺失曲目：[bold]{missing}[/bold] 首（幂等去重，不会重复添加）\n待恢复评分记录：[bold]{ratings}[/bold] 条",
        "tui.modal.restore.btn_confirm": "确认补回",
    },
}


def t(key: str, **kwargs: Any) -> str:
    """Translate message key in active language, falling back to English."""
    lang = get_language()
    template = TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]).get(
        key, TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)
    )
    if kwargs:
        return template.format(**kwargs)
    return template


# Initialize early so Typer `--help` strings honor `--lang`, AMLIBMERGE_LANG, settings, or OS locale.
get_language()
