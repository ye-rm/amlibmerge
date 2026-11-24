"""Typer CLI entry point for amlibmerge."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from amlibmerge import __version__
from amlibmerge.backup import create_library_backup, load_backup, save_backup
from amlibmerge.browser_auth import BrowserAuthError, capture_token_via_browser
from amlibmerge.client import (
    AppleMusicAPIError,
    AppleMusicAuthError,
    AppleMusicClient,
    fetch_web_developer_token,
)
from amlibmerge.config import (
    clear_credentials,
    get_credentials_path,
    get_jwt_exp,
    is_jwt_valid,
    load_credentials,
    load_language_preference,
    mask_token,
    save_credentials,
    save_language_preference,
)
from amlibmerge.detector import evaluate_group_safety
from amlibmerge.i18n import (
    get_language,
    normalize_language,
    resolve_language,
    set_language,
    t,
)
from amlibmerge.merger import (
    build_merge_report,
    find_latest_file,
    load_scan_report,
    make_skipped_result,
    merge_split_group,
    restore_from_backup,
    save_merge_report,
)
from amlibmerge.models import (
    Credentials,
    LibraryBackup,
    MergeReport,
    MergeResult,
    ScanReport,
    SplitGroup,
    SplitTier,
)
from amlibmerge.scanner import scan_library

app = typer.Typer(
    name="amlibmerge",
    help=t("cli.app_help"),
    no_args_is_help=False,
)
auth_app = typer.Typer(help=t("cli.auth_help"))
app.add_typer(auth_app, name="auth")

console = Console()


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _format_exp(exp: int | None) -> str:
    if exp is None:
        return t("common.unknown")
    dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _require_client() -> tuple[AppleMusicClient, Credentials]:
    creds = load_credentials()
    if not creds or not creds.media_user_token:
        console.print(t("cli.err.no_token"))
        raise typer.Exit(code=1)
    client = AppleMusicClient(
        media_user_token=creds.media_user_token,
        developer_token=creds.developer_token,
    )
    return client, creds


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help=t("cli.opt.version"), is_eager=True
    ),
    lang: Optional[str] = typer.Option(
        None, "--lang", help=t("cli.opt.lang")
    ),
    tui: bool = typer.Option(
        False, "--tui", help=t("cli.opt.tui")
    ),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        "-r",
        help=t("cli.opt.tui_report"),
    ),
) -> None:
    if lang is not None:
        norm = normalize_language(lang)
        if norm is None:
            console.print(f"[red]{t('common.invalid_lang', lang=lang)}[/red]")
            raise typer.Exit(code=1)
        set_language(norm)

    if version:
        console.print(f"amlibmerge {__version__}")
        raise typer.Exit()
    if tui:
        if ctx.invoked_subcommand is not None:
            console.print(f"[red]{t('cli.err.tui_with_subcommand')}[/red]")
            raise typer.Exit(code=1)
        from amlibmerge.tui import run_tui

        run_tui(report_path=report)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command("tui", help=t("cli.cmd.tui.help"))
def cmd_tui(
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        "-r",
        help=t("cli.cmd.tui.opt_report"),
    ),
) -> None:
    from amlibmerge.tui import run_tui

    run_tui(report_path=report)


@app.command("lang", help=t("cli.cmd.lang.help"))
def cmd_lang(
    value: Optional[str] = typer.Argument(
        None,
        help=t("cli.cmd.lang.arg"),
    ),
) -> None:
    if value is None:
        active, source = resolve_language(cli_lang=None, argv=[])
        saved = load_language_preference() or "auto"
        console.print(t("cli.cmd.lang.current", lang=active, source=source, saved=saved))
        return

    norm = normalize_language(value)
    if norm is None:
        console.print(f"[red]{t('common.invalid_lang', lang=value)}[/red]")
        raise typer.Exit(code=1)

    save_language_preference(norm)
    active = set_language(norm)
    console.print(t("cli.cmd.lang.updated", pref=norm, active=active))


@auth_app.command("login", help=t("cli.auth.login.help"))
def auth_login(
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help=t("cli.auth.login.opt_token"),
    ),
    manual: bool = typer.Option(
        False,
        "--manual",
        help=t("cli.auth.login.opt_manual"),
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help=t("cli.auth.login.opt_fresh"),
    ),
    browser: Optional[str] = typer.Option(
        None,
        "--browser",
        help=t("cli.auth.login.opt_browser"),
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help=t("cli.auth.login.opt_timeout"),
    ),
    dev_token: Optional[str] = typer.Option(
        None,
        "--dev-token",
        help=t("cli.auth.login.opt_dev_token"),
    ),
) -> None:
    storefront: str
    dev_exp: int | None

    if not token and not manual:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(t("cli.auth.login.preparing"), total=None)
            try:
                token, storefront, dev_token, dev_exp = capture_token_via_browser(
                    browser_path=browser,
                    dev_token=dev_token,
                    fresh_profile=fresh,
                    timeout=float(timeout),
                    on_status=lambda msg: progress.update(task, description=msg),
                )
            except BrowserAuthError as exc:
                progress.stop()
                console.print(t("cli.auth.login.browser_failed", exc=exc))
                console.print(t("cli.auth.login.manual_hint"))
                raise typer.Exit(code=1) from exc
            except Exception as exc:
                progress.stop()
                console.print(t("cli.auth.login.error", exc=exc))
                raise typer.Exit(code=1) from exc
    else:
        if not token:
            console.print(
                Panel(
                    t("cli.auth.login.manual_panel_body"),
                    title=t("cli.auth.login.manual_panel_title"),
                    border_style="cyan",
                )
            )
            token = typer.prompt(t("cli.auth.login.manual_prompt"), hide_input=True).strip()

        if not token:
            console.print(t("cli.auth.login.empty_token"))
            raise typer.Exit(code=1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            if not dev_token:
                task = progress.add_task(t("cli.auth.login.fetching_jwt"), total=None)
                try:
                    dev_token, dev_exp = fetch_web_developer_token()
                except Exception as exc:
                    console.print(t("cli.auth.login.jwt_failed", exc=exc))
                    raise typer.Exit(code=1) from exc
                progress.remove_task(task)
            else:
                dev_exp = get_jwt_exp(dev_token)

            task2 = progress.add_task(t("cli.auth.login.verifying"), total=None)
            try:
                with AppleMusicClient(media_user_token=token, developer_token=dev_token) as client:
                    storefront = client.get_storefront()
                    dev_token = client.developer_token
                    dev_exp = client.developer_token_exp
            except AppleMusicAuthError as exc:
                console.print(t("cli.err.auth_failed", exc=exc))
                raise typer.Exit(code=1) from exc
            except AppleMusicAPIError as exc:
                console.print(t("cli.err.api_error", exc=exc))
                raise typer.Exit(code=1) from exc
            progress.remove_task(task2)

    creds = Credentials(
        media_user_token=token,
        developer_token=dev_token,
        developer_token_exp=dev_exp,
        storefront=storefront,
    )
    saved_path = save_credentials(creds)
    console.print(
        Panel(
            t(
                "cli.auth.login.success",
                storefront=storefront,
                token=mask_token(token),
                exp=_format_exp(dev_exp),
                path=saved_path,
            ),
            title="amlibmerge auth",
            border_style="green",
        )
    )


@auth_app.command("status", help=t("cli.auth.status.help"))
def auth_status(
    verify: bool = typer.Option(False, "--verify", help=t("cli.auth.status.opt_verify")),
) -> None:
    creds = load_credentials()
    if not creds:
        console.print(t("cli.auth.status.not_found", path=get_credentials_path()))
        raise typer.Exit(code=1)

    jwt_ok = is_jwt_valid(creds.developer_token, creds.developer_token_exp)
    table = Table(title=t("cli.auth.status.title"), show_header=False)
    table.add_row(t("cli.auth.status.row_file"), str(get_credentials_path()))
    table.add_row("User Token", mask_token(creds.media_user_token))
    table.add_row("Web JWT", mask_token(creds.developer_token))
    table.add_row(
        t("cli.auth.status.row_jwt_state"),
        t("cli.auth.status.jwt_valid") if jwt_ok else t("cli.auth.status.jwt_refresh"),
    )
    table.add_row(t("cli.auth.status.row_jwt_exp"), _format_exp(creds.developer_token_exp))
    table.add_row(t("cli.auth.status.row_storefront"), creds.storefront or t("common.unknown"))

    if verify:
        try:
            with AppleMusicClient(
                media_user_token=creds.media_user_token,
                developer_token=creds.developer_token,
            ) as client:
                sf = client.get_storefront()
                if client.developer_token != creds.developer_token or sf != creds.storefront:
                    creds.developer_token = client.developer_token
                    creds.developer_token_exp = client.developer_token_exp
                    creds.storefront = sf
                    save_credentials(creds)
                table.add_row(t("cli.auth.status.row_verify"), t("cli.auth.status.verify_ok", sf=sf))
        except Exception as exc:
            table.add_row(
                t("cli.auth.status.row_verify"),
                t("cli.auth.status.verify_fail", exc=exc),
            )
            console.print(table)
            raise typer.Exit(code=1) from exc

    console.print(table)


@auth_app.command("logout", help=t("cli.auth.logout.help"))
def auth_logout() -> None:
    if clear_credentials():
        console.print(t("cli.auth.logout.deleted"))
    else:
        console.print(t("cli.auth.logout.none"))


def _render_scan_report(report: ScanReport) -> None:
    stats = report.stats
    console.print(
        Panel(
            t(
                "cli.scan.summary_body",
                storefront=report.storefront,
                total=stats.total_library_albums,
                linked=stats.catalog_linked_albums,
                local=stats.local_only_albums,
                high=stats.high_confidence_groups,
                medium=stats.medium_confidence_groups,
                safe=stats.safe_to_merge_groups,
                blocked=stats.blocked_groups,
            ),
            title=t("cli.scan.summary_title"),
            border_style="cyan",
        )
    )

    if not report.split_groups:
        console.print(t("cli.scan.no_splits"))
        return

    table = Table(title=t("cli.scan.table_title", count=len(report.split_groups)), show_lines=True)
    table.add_column(t("cli.scan.col_gid"), style="bold cyan", no_wrap=True)
    table.add_column(t("cli.scan.col_tier"), no_wrap=True)
    table.add_column(t("cli.scan.col_album"))
    table.add_column(t("cli.scan.col_splits"), justify="right", no_wrap=True)
    table.add_column(t("cli.scan.col_tracks"), no_wrap=True)
    table.add_column(t("cli.scan.col_cid"), no_wrap=True)
    table.add_column(t("cli.scan.col_status"))

    for g in report.split_groups:
        tier_label = (
            t("cli.scan.tier_high")
            if g.tier == SplitTier.HIGH_SAME_CATALOG_ID
            else t("cli.scan.tier_medium")
        )
        counts_str = " + ".join(str(a.track_count) for a in g.albums)
        if g.target_catalog_track_count:
            counts_str += t("cli.scan.full_album_suffix", count=g.target_catalog_track_count)
        target_str = g.target_catalog_album_id or "-"
        if g.target_catalog_available is True:
            target_str = f"[green]{target_str}[/green]"
        elif g.target_catalog_available is False:
            target_str = f"[red]{target_str}{t('cli.scan.delisted_tag')}[/red]"

        status_parts = [t("cli.scan.status_safe") if g.safe_to_merge else t("cli.scan.status_blocked")]
        if g.warnings:
            status_parts.extend(f"[yellow]• {w}[/yellow]" for w in g.warnings)

        table.add_row(
            g.group_id,
            tier_label,
            f"[bold]{g.canonical_name}[/bold]\n[dim]{g.canonical_artist}[/dim]",
            str(g.split_count),
            counts_str,
            target_str,
            "\n".join(status_parts),
        )

    console.print(table)


@app.command("scan", help=t("cli.scan.help"))
def cmd_scan(
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help=t("cli.scan.opt_out"),
    ),
    include_weak: bool = typer.Option(
        True,
        "--include-weak/--no-weak",
        help=t("cli.scan.opt_weak"),
    ),
    inspect_tracks: bool = typer.Option(
        True,
        "--inspect-tracks/--no-inspect-tracks",
        help=t("cli.scan.opt_tracks"),
    ),
    dump_sample: bool = typer.Option(
        False,
        "--dump-sample",
        help=t("cli.scan.opt_sample"),
    ),
) -> None:
    client, creds = _require_client()
    raw_samples: list[dict] | None = [] if dump_sample else None

    try:
        with client:
            storefront = creds.storefront or client.get_storefront()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                album_task = progress.add_task(t("cli.scan.fetching"), total=None)
                enrich_task = None

                def on_page(page_num: int, total_albums: int) -> None:
                    progress.update(
                        album_task,
                        description=t("cli.scan.fetching_page", page=page_num, total=total_albums),
                    )

                def on_enrich(idx: int, total: int, group: SplitGroup) -> None:
                    nonlocal enrich_task
                    if enrich_task is None:
                        progress.update(album_task, description=t("cli.scan.fetched_done"))
                        enrich_task = progress.add_task(
                            t("cli.scan.enrich_start", total=total), total=total
                        )
                    progress.update(
                        enrich_task,
                        completed=idx,
                        description=t(
                            "cli.scan.enrich_item", idx=idx, total=total, name=group.canonical_name
                        ),
                    )

                report = scan_library(
                    client,
                    storefront,
                    include_weak=include_weak,
                    inspect_tracks=inspect_tracks,
                    on_album_page=on_page,
                    on_enrich_group=on_enrich,
                    raw_sample_sink=raw_samples,
                )
    except AppleMusicAuthError as exc:
        console.print(t("cli.err.auth_failed", exc=exc))
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(t("cli.scan.failed", exc=exc))
        raise typer.Exit(code=1) from exc

    output_path = out or Path("reports") / f"scan_{_timestamp_slug()}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    _render_scan_report(report)
    console.print(t("cli.scan.saved", path=output_path))

    if dump_sample and raw_samples is not None:
        sample_path = Path("reports") / "sample_raw_albums.json"
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(
            json.dumps(raw_samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        console.print(t("cli.scan.sample_saved", path=sample_path))


@app.command("backup", help=t("cli.backup.help"))
def cmd_backup(
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help=t("cli.backup.opt_out"),
    ),
    all_playlists: bool = typer.Option(
        False,
        "--all-playlists",
        help=t("cli.backup.opt_all_pl"),
    ),
    with_ratings: bool = typer.Option(
        True,
        "--with-ratings/--no-ratings",
        help=t("cli.backup.opt_ratings"),
    ),
) -> None:
    client, creds = _require_client()

    try:
        with client:
            storefront = creds.storefront or client.get_storefront()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(t("cli.backup.fetching"), total=None)

                def on_pl(idx: int, total: int, name: str) -> None:
                    progress.update(
                        task,
                        total=total,
                        completed=idx,
                        description=t("cli.backup.progress", idx=idx, total=total, name=name),
                    )

                backup = create_library_backup(
                    client,
                    storefront,
                    include_non_editable_playlists=all_playlists,
                    include_ratings=with_ratings,
                    on_playlist=on_pl,
                )
    except AppleMusicAuthError as exc:
        console.print(t("cli.err.auth_failed", exc=exc))
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(t("cli.backup.failed", exc=exc))
        raise typer.Exit(code=1) from exc

    output_path = out or Path("backups") / f"backup_{_timestamp_slug()}.json"
    save_backup(backup, output_path)
    meta = backup.metadata
    console.print(
        Panel(
            t(
                "cli.backup.done",
                backed=meta.get("playlists_backed_up", 0),
                seen=meta.get("total_playlists_seen", 0),
                tracks=meta.get("total_playlist_tracks", 0),
                ratings=meta.get("ratings_backed_up", 0),
                path=output_path,
            ),
            title="amlibmerge backup",
            border_style="green",
        )
    )


def _format_album_cell(r: MergeResult) -> str:
    if r.artist_name:
        return f"[bold]{r.canonical_name}[/bold]\n[dim]{r.artist_name}[/dim]"
    return f"[bold]{r.canonical_name}[/bold]"


def _render_merge_report(merge_report: MergeReport) -> None:
    if merge_report.dry_run:
        summary = t(
            "cli.merge.summary_dry",
            storefront=merge_report.storefront,
            ready=len(merge_report.dry_run_ready),
            manual=len(merge_report.manual_required),
        )
        border = "cyan"
    else:
        summary = t(
            "cli.merge.summary_exec",
            storefront=merge_report.storefront,
            ok=len(merge_report.succeeded),
            manual=len(merge_report.manual_required),
        )
        border = "green" if not merge_report.manual_required else "yellow"

    console.print(Panel(summary, title=t("cli.merge.report_title"), border_style=border))

    if merge_report.succeeded:
        ok_table = Table(
            title=t("cli.merge.table_ok_title", count=len(merge_report.succeeded)),
            show_lines=True,
            border_style="green",
        )
        ok_table.add_column(t("cli.scan.col_gid"), style="bold cyan", no_wrap=True)
        ok_table.add_column(t("cli.scan.col_album"))
        ok_table.add_column(t("cli.scan.col_cid"), no_wrap=True)
        ok_table.add_column(t("cli.merge.col_new_id"), no_wrap=True)
        ok_table.add_column(t("cli.merge.col_details"))

        for r in merge_report.succeeded:
            details: list[str] = []
            if r.deleted_album_statuses:
                details.append(t("cli.merge.detail_deleted_alb", statuses=r.deleted_album_statuses))
            if r.deleted_song_statuses:
                details.append(t("cli.merge.detail_deleted_songs", count=len(r.deleted_song_statuses)))
            if r.readd_status is not None:
                details.append(t("cli.merge.detail_readd", status=r.readd_status))
            if r.restored_playlist_tracks:
                pl_txt = ", ".join(
                    t("cli.merge.detail_pl_item", pid=pid, cnt=cnt)
                    for pid, cnt in r.restored_playlist_tracks.items()
                )
                details.append(t("cli.merge.detail_pl_restored", info=pl_txt))
            if r.restored_ratings:
                details.append(t("cli.merge.detail_ratings", count=r.restored_ratings))
            ok_table.add_row(
                r.group_id,
                _format_album_cell(r),
                r.target_catalog_album_id or "-",
                ", ".join(r.new_library_album_ids) or "-",
                "\n".join(details) if details else "-",
            )
        console.print(ok_table)

    if merge_report.dry_run_ready:
        dry_table = Table(
            title=t("cli.merge.table_dry_title", count=len(merge_report.dry_run_ready)),
            show_lines=True,
            border_style="cyan",
        )
        dry_table.add_column(t("cli.scan.col_gid"), style="bold cyan", no_wrap=True)
        dry_table.add_column(t("cli.scan.col_album"))
        dry_table.add_column(t("cli.scan.col_cid"), no_wrap=True)
        dry_table.add_column(t("cli.merge.col_affected"))

        for r in merge_report.dry_run_ready:
            details = []
            if r.affected_playlists:
                pl_summary = ", ".join(
                    t("cli.merge.dry_pl_item", pid=pid, cnt=len(cids))
                    for pid, cids in r.affected_playlists.items()
                )
                details.append(t("cli.merge.dry_pl_note", info=pl_summary))
            for w in r.warnings:
                details.append(f"[yellow]• {w}[/yellow]")
            dry_table.add_row(
                r.group_id,
                _format_album_cell(r),
                r.target_catalog_album_id or "-",
                "\n".join(details) if details else t("cli.merge.dry_no_pl"),
            )
        console.print(dry_table)

    if merge_report.manual_required:
        man_table = Table(
            title=t("cli.merge.table_manual_title", count=len(merge_report.manual_required)),
            show_lines=True,
            border_style="yellow",
        )
        man_table.add_column(t("cli.scan.col_gid"), style="bold cyan", no_wrap=True)
        man_table.add_column(t("cli.scan.col_album"))
        man_table.add_column(t("cli.merge.col_category"), no_wrap=True)
        man_table.add_column(t("cli.merge.col_reason_hint"))

        stage_labels = {
            "still_split": t("cli.merge.stage_still_split"),
            "skipped": t("cli.merge.stage_skipped"),
            "failed": t("cli.merge.stage_failed"),
        }
        for r in merge_report.manual_required:
            notes: list[str] = []
            if r.error:
                notes.append(t("cli.merge.label_reason", reason=r.error))
            for w in r.warnings:
                notes.append(f"[yellow]• {w}[/yellow]")
            if r.manual_action_hint:
                notes.append(t("cli.merge.label_advice", hint=r.manual_action_hint))
            man_table.add_row(
                r.group_id,
                _format_album_cell(r),
                stage_labels.get(r.stage, r.stage),
                "\n".join(notes) if notes else "-",
            )
        console.print(man_table)


@app.command("merge", help=t("cli.merge.help"))
def cmd_merge(
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        "-r",
        help=t("cli.merge.opt_report"),
    ),
    group: Optional[list[str]] = typer.Option(
        None,
        "--group",
        "-g",
        help=t("cli.merge.opt_group"),
    ),
    merge_all: bool = typer.Option(
        False,
        "--all",
        help=t("cli.merge.opt_all"),
    ),
    catalog_id: Optional[str] = typer.Option(
        None,
        "--catalog-id",
        help=t("cli.merge.opt_cid"),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=t("cli.merge.opt_force"),
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help=t("cli.merge.opt_execute"),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=t("cli.merge.opt_yes"),
    ),
    backup_file: Optional[Path] = typer.Option(
        None,
        "--backup-file",
        "-b",
        help=t("cli.merge.opt_backup"),
    ),
    report_out: Optional[Path] = typer.Option(
        None,
        "--report-out",
        help=t("cli.merge.opt_report_out"),
    ),
) -> None:
    if not group and not merge_all:
        console.print(t("cli.merge.err_no_target"))
        raise typer.Exit(code=1)

    if catalog_id and (merge_all or not group or len(group) != 1):
        console.print(t("cli.merge.err_cid_multi"))
        raise typer.Exit(code=1)

    report_path = report or find_latest_file(Path("reports"), "scan_*.json")
    if not report_path or not report_path.exists():
        console.print(t("cli.merge.err_no_report"))
        raise typer.Exit(code=1)

    scan_report = load_scan_report(report_path)
    groups_by_id: dict[str, SplitGroup] = {}
    for g in scan_report.split_groups:
        evaluate_group_safety(g)
        groups_by_id[g.group_id.upper()] = g

    selected: list[SplitGroup] = []
    skipped_blocked: list[SplitGroup] = []
    if merge_all:
        for g in scan_report.split_groups:
            if g.safe_to_merge or force:
                selected.append(g)
            else:
                skipped_blocked.append(g)
    else:
        for gid in group or []:
            key = gid.strip().upper()
            if key not in groups_by_id:
                console.print(t("cli.merge.err_missing_group", path=report_path, gid=gid))
                raise typer.Exit(code=1)
            selected.append(groups_by_id[key])

    if not selected and not skipped_blocked:
        console.print(t("cli.merge.nothing_to_do"))
        raise typer.Exit(code=0)

    results: list[MergeResult] = []
    journal_path: Path | None = None
    storefront = scan_report.storefront

    if selected:
        client, creds = _require_client()
        active_backup: LibraryBackup | None = None
        if backup_file:
            if not backup_file.exists():
                console.print(t("cli.merge.err_backup_missing", path=backup_file))
                raise typer.Exit(code=1)
            active_backup = load_backup(backup_file)
        elif not execute:
            latest_bk = find_latest_file(Path("backups"), "backup_*.json")
            if latest_bk:
                active_backup = load_backup(latest_bk)
                console.print(t("cli.merge.dry_using_backup", path=latest_bk))

        try:
            with client:
                storefront = creds.storefront or scan_report.storefront or client.get_storefront()

                if execute:
                    skip_note = (
                        t("cli.merge.confirm_skip_note", count=len(skipped_blocked))
                        if skipped_blocked
                        else ""
                    )
                    items_text = "\n".join(
                        t(
                            "cli.merge.confirm_item",
                            gid=g.group_id,
                            name=g.canonical_name,
                            old_ids=", ".join(a.id for a in g.albums),
                            cid=catalog_id or g.target_catalog_album_id,
                        )
                        for g in selected
                    )
                    console.print(
                        Panel(
                            t(
                                "cli.merge.confirm_body",
                                count=len(selected),
                                items=items_text,
                                skip_note=skip_note,
                            ),
                            title=t("cli.merge.confirm_title"),
                            border_style="red",
                        )
                    )
                    if not yes:
                        confirmed = typer.confirm(t("cli.merge.confirm_prompt"), default=False)
                        if not confirmed:
                            console.print(t("cli.merge.cancelled"))
                            raise typer.Exit(code=0)

                    if active_backup is None:
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            console=console,
                            transient=True,
                        ) as progress:
                            btask = progress.add_task(
                                t("cli.merge.pre_backup_progress"), total=None
                            )
                            active_backup = create_library_backup(client, storefront)
                            bk_path = Path("backups") / f"backup_pre_merge_{_timestamp_slug()}.json"
                            save_backup(active_backup, bk_path)
                            progress.remove_task(btask)
                        console.print(t("cli.merge.pre_backup_saved", path=bk_path))

                journal_path = (
                    Path("journals") / f"merge_{_timestamp_slug()}.jsonl" if execute else None
                )

                for g in selected:
                    console.print(
                        t("cli.merge.processing_group", gid=g.group_id, name=g.canonical_name)
                    )
                    res = merge_split_group(
                        client,
                        storefront,
                        g,
                        backup=active_backup,
                        dry_run=not execute,
                        force=force,
                        override_catalog_id=catalog_id,
                        journal_path=journal_path,
                        on_step=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
                    )
                    results.append(res)

        except AppleMusicAuthError as exc:
            console.print(t("cli.err.auth_failed", exc=exc))
            raise typer.Exit(code=1) from exc
        except AppleMusicAPIError as exc:
            console.print(t("cli.err.api_error", exc=exc))
            raise typer.Exit(code=1) from exc

    for bg in skipped_blocked:
        results.append(
            make_skipped_result(
                bg,
                dry_run=not execute,
                reason=t("merger.reason_auto_skipped"),
            )
        )

    merge_report = build_merge_report(
        results,
        storefront=storefront,
        dry_run=not execute,
        source_scan_report=report_path,
        journal_path=journal_path if (journal_path and journal_path.exists()) else None,
    )

    console.print()
    _render_merge_report(merge_report)

    out_path = report_out or (
        Path("reports") / f"merge_report_{_timestamp_slug()}.json" if execute else None
    )
    if out_path is not None:
        save_merge_report(merge_report, out_path)
        console.print(t("cli.merge.report_saved", path=out_path))

    if not execute:
        if merge_report.dry_run_ready:
            cmd_hint = (
                "amlibmerge merge --all --execute"
                if merge_all
                else f"amlibmerge merge --group {merge_report.dry_run_ready[0].group_id} --execute"
            )
            console.print(t("cli.merge.dry_footer", cmd=cmd_hint))
        else:
            console.print(t("cli.merge.dry_none_auto"))
    elif journal_path and journal_path.exists():
        console.print(t("cli.merge.wal_saved", path=journal_path))


@app.command("restore", help=t("cli.restore.help"))
def cmd_restore(
    backup_file: Optional[Path] = typer.Option(
        None,
        "--backup",
        "-b",
        help=t("cli.restore.opt_backup"),
    ),
    playlist: Optional[str] = typer.Option(
        None,
        "--playlist",
        help=t("cli.restore.opt_playlist"),
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help=t("cli.restore.opt_execute"),
    ),
    no_ratings: bool = typer.Option(
        False,
        "--no-ratings",
        help=t("cli.restore.opt_no_ratings"),
    ),
) -> None:
    bk_path = backup_file or find_latest_file(Path("backups"), "backup_*.json")
    if not bk_path or not bk_path.exists():
        console.print(t("cli.restore.err_no_backup"))
        raise typer.Exit(code=1)

    backup = load_backup(bk_path)
    client, _ = _require_client()

    try:
        with client:
            summary = restore_from_backup(
                client,
                backup,
                dry_run=not execute,
                playlist_id=playlist,
                restore_ratings=not no_ratings,
                on_step=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
            )
    except AppleMusicAuthError as exc:
        console.print(t("cli.err.auth_failed", exc=exc))
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(t("cli.restore.failed", exc=exc))
        raise typer.Exit(code=1) from exc

    table = Table(
        title=t("cli.restore.title_exec") if execute else t("cli.restore.title_dry"),
        show_lines=True,
    )
    table.add_column(t("cli.restore.col_pid"), style="cyan", no_wrap=True)
    table.add_column(t("cli.restore.col_name"))
    table.add_column(t("cli.restore.col_bk"), justify="right")
    table.add_column(t("cli.restore.col_miss"), justify="right")
    table.add_column(t("cli.restore.col_done"), justify="right")

    for row in summary["playlists"]:  # type: ignore[index]
        table.add_row(
            str(row["playlist_id"]),
            str(row["name"]),
            str(row["backup_tracks"]),
            str(row["missing_tracks"]),
            str(row["restored_tracks"]),
        )
    console.print(table)
    if not execute:
        console.print(t("cli.restore.dry_footer", path=bk_path))


if __name__ == "__main__":
    app()
