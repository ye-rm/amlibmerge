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
    mask_token,
    save_credentials,
)
from amlibmerge.detector import evaluate_group_safety
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
    help="Apple Music 资料库分裂专辑扫描、备份与合并工具",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="管理 Apple Music 网页端鉴权凭证")
app.add_typer(auth_app, name="auth")

console = Console()


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _format_exp(exp: int | None) -> str:
    if exp is None:
        return "未知"
    dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _require_client() -> tuple[AppleMusicClient, Credentials]:
    creds = load_credentials()
    if not creds or not creds.media_user_token:
        console.print(
            "[red]尚未配置 media-user-token。[/red]请先运行 [bold]amlibmerge auth login[/bold] "
            "或设置环境变量 [bold]AM_MEDIA_USER_TOKEN[/bold]。"
        )
        raise typer.Exit(code=1)
    client = AppleMusicClient(
        media_user_token=creds.media_user_token,
        developer_token=creds.developer_token,
    )
    return client, creds


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False, "--version", "-V", help="显示版本号并退出", is_eager=True
    ),
) -> None:
    if version:
        console.print(f"amlibmerge {__version__}")
        raise typer.Exit()


@auth_app.command("login")
def auth_login(
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="直接传入 media-user-token（跳过浏览器窗口）",
    ),
    manual: bool = typer.Option(
        False,
        "--manual",
        help="使用手动粘贴 Cookie 模式（适合无图形界面的终端环境）",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="使用全新临时浏览器配置启动登录（适合切换 Apple ID）",
    ),
    browser: Optional[str] = typer.Option(
        None,
        "--browser",
        help="手动指定 Chrome / Edge / Brave 可执行文件路径",
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help="等待浏览器登录完成的超时秒数",
    ),
    dev_token: Optional[str] = typer.Option(
        None,
        "--dev-token",
        help="可选：手动指定 Web Developer JWT（默认自动从 music.apple.com 提取）",
    ),
) -> None:
    """弹出浏览器窗口登录 Apple Music 并自动抓取 media-user-token（也支持 --manual 手动输入）。"""
    storefront: str
    dev_exp: int | None

    if not token and not manual:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("正在准备浏览器登录窗口...", total=None)
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
                console.print(f"[red]浏览器自动登录失败：[/red]{exc}")
                console.print(
                    "[dim]提示：也可使用 [bold]amlibmerge auth login --manual[/bold] 手动粘贴 media-user-token。[/dim]"
                )
                raise typer.Exit(code=1) from exc
            except Exception as exc:
                progress.stop()
                console.print(f"[red]登录过程异常：[/red]{exc}")
                raise typer.Exit(code=1) from exc
    else:
        if not token:
            console.print(
                Panel(
                    "请在浏览器登录 [bold]https://music.apple.com[/bold]，打开 F12 开发者工具 →\n"
                    "[bold]Application → Cookies → https://music.apple.com[/bold]，\n"
                    "复制名为 [cyan]media-user-token[/cyan] 的 Cookie 值。",
                    title="获取 media-user-token",
                    border_style="cyan",
                )
            )
            token = typer.prompt("请输入 media-user-token（输入已隐藏）", hide_input=True).strip()

        if not token:
            console.print("[red]media-user-token 不能为空。[/red]")
            raise typer.Exit(code=1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            if not dev_token:
                task = progress.add_task("正在从 music.apple.com 提取公开 Web Developer JWT...", total=None)
                try:
                    dev_token, dev_exp = fetch_web_developer_token()
                except Exception as exc:
                    console.print(f"[red]提取 Web Developer JWT 失败：[/red]{exc}")
                    raise typer.Exit(code=1) from exc
                progress.remove_task(task)
            else:
                dev_exp = get_jwt_exp(dev_token)

            task2 = progress.add_task("正在调用 /v1/me/storefront 校验账号凭证...", total=None)
            try:
                with AppleMusicClient(media_user_token=token, developer_token=dev_token) as client:
                    storefront = client.get_storefront()
                    dev_token = client.developer_token
                    dev_exp = client.developer_token_exp
            except AppleMusicAuthError as exc:
                console.print(f"[red]鉴权失败：[/red]{exc}")
                raise typer.Exit(code=1) from exc
            except AppleMusicAPIError as exc:
                console.print(f"[red]接口调用异常：[/red]{exc}")
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
            f"[green]登录验证成功！[/green]\n"
            f"账号区服 (Storefront): [bold cyan]{storefront}[/bold cyan]\n"
            f"User Token: [dim]{mask_token(token)}[/dim]\n"
            f"Web JWT 过期时间: [dim]{_format_exp(dev_exp)}[/dim]\n"
            f"凭证存储路径: [dim]{saved_path}[/dim]",
            title="amlibmerge auth",
            border_style="green",
        )
    )


@auth_app.command("status")
def auth_status(
    verify: bool = typer.Option(False, "--verify", help="发起一次真实只读请求验证 Token 是否仍有效"),
) -> None:
    """查看当前本地鉴权状态。"""
    creds = load_credentials()
    if not creds:
        console.print(f"[yellow]未找到已保存的凭证[/yellow]（路径：{get_credentials_path()}）。")
        raise typer.Exit(code=1)

    jwt_ok = is_jwt_valid(creds.developer_token, creds.developer_token_exp)
    table = Table(title="鉴权状态", show_header=False)
    table.add_row("凭证文件", str(get_credentials_path()))
    table.add_row("User Token", mask_token(creds.media_user_token))
    table.add_row("Web JWT", mask_token(creds.developer_token))
    table.add_row("Web JWT 状态", "[green]有效[/green]" if jwt_ok else "[yellow]需刷新[/yellow]")
    table.add_row("Web JWT 过期时间", _format_exp(creds.developer_token_exp))
    table.add_row("记录的区服", creds.storefront or "未知")

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
                table.add_row("在线校验", f"[green]通过 (storefront={sf})[/green]")
        except Exception as exc:
            table.add_row("在线校验", f"[red]失败：{exc}[/red]")
            console.print(table)
            raise typer.Exit(code=1) from exc

    console.print(table)


@auth_app.command("logout")
def auth_logout() -> None:
    """清除本地保存的凭证文件。"""
    if clear_credentials():
        console.print("[green]已删除本地凭证文件。[/green]")
    else:
        console.print("[yellow]本地没有已保存的凭证文件。[/yellow]")


def _render_scan_report(report: ScanReport) -> None:
    stats = report.stats
    console.print(
        Panel(
            f"区服: [cyan]{report.storefront}[/cyan]   "
            f"资料库专辑总数: [bold]{stats.total_library_albums}[/bold]   "
            f"官方曲库关联: [green]{stats.catalog_linked_albums}[/green]   "
            f"纯本地上传(自动保护): [yellow]{stats.local_only_albums}[/yellow]\n"
            f"高置信度分裂(同Catalog ID): [bold magenta]{stats.high_confidence_groups}[/bold magenta]   "
            f"中置信度分裂(同名同艺人): [bold blue]{stats.medium_confidence_groups}[/bold blue]   "
            f"可安全合并: [bold green]{stats.safe_to_merge_groups}[/bold green]   "
            f"受保护拦截: [bold red]{stats.blocked_groups}[/bold red]",
            title="扫描概览",
            border_style="cyan",
        )
    )

    if not report.split_groups:
        console.print("[green]未发现分裂专辑！[/green]")
        return

    table = Table(title=f"检测到 {len(report.split_groups)} 组疑似分裂专辑", show_lines=True)
    table.add_column("组ID", style="bold cyan", no_wrap=True)
    table.add_column("置信度", no_wrap=True)
    table.add_column("专辑 / 艺人")
    table.add_column("分裂份数", justify="right", no_wrap=True)
    table.add_column("各份曲目数", no_wrap=True)
    table.add_column("目标 Catalog ID", no_wrap=True)
    table.add_column("状态与提示")

    for g in report.split_groups:
        tier_label = (
            "[magenta]高 (同ID)[/magenta]"
            if g.tier == SplitTier.HIGH_SAME_CATALOG_ID
            else "[blue]中 (同名)[/blue]"
        )
        counts_str = " + ".join(str(a.track_count) for a in g.albums)
        if g.target_catalog_track_count:
            counts_str += f" / 整专{g.target_catalog_track_count}"
        target_str = g.target_catalog_album_id or "-"
        if g.target_catalog_available is True:
            target_str = f"[green]{target_str}[/green]"
        elif g.target_catalog_available is False:
            target_str = f"[red]{target_str} (已下架)[/red]"

        status_parts = ["[green]可安全合并[/green]" if g.safe_to_merge else "[red]拦截保护[/red]"]
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


@app.command("scan")
def cmd_scan(
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help="扫描报告 JSON 输出路径（默认 reports/scan_<timestamp>.json）",
    ),
    include_weak: bool = typer.Option(
        True,
        "--include-weak/--no-weak",
        help="是否包含中置信度（同名同艺人跨 Catalog ID）分裂检测",
    ),
    inspect_tracks: bool = typer.Option(
        True,
        "--inspect-tracks/--no-inspect-tracks",
        help="是否进一步拉取候选分裂专辑曲目，检查是否存在本地自传混入",
    ),
    dump_sample: bool = typer.Option(
        False,
        "--dump-sample",
        help="额外保存前 3 条原始专辑 JSON 到 reports/sample_raw_albums.json 便于调试字段结构",
    ),
) -> None:
    """[阶段一·只读] 扫描资料库，识别分裂专辑并校验官方曲库在架状态。"""
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
                album_task = progress.add_task("正在分页拉取资料库专辑...", total=None)
                enrich_task = None

                def on_page(page_num: int, total_albums: int) -> None:
                    progress.update(
                        album_task,
                        description=f"正在拉取资料库专辑（第 {page_num} 页，已读取 {total_albums} 张）...",
                    )

                def on_enrich(idx: int, total: int, group) -> None:
                    nonlocal enrich_task
                    if enrich_task is None:
                        progress.update(album_task, description="资料库专辑拉取完成。")
                        enrich_task = progress.add_task(
                            f"正在校验分裂候选（0/{total}）...", total=total
                        )
                    progress.update(
                        enrich_task,
                        completed=idx,
                        description=f"正在校验分裂候选（{idx}/{total}）：{group.canonical_name}",
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
        console.print(f"[red]鉴权失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(f"[red]扫描失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    output_path = out or Path("reports") / f"scan_{_timestamp_slug()}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    _render_scan_report(report)
    console.print(f"\n[green]扫描报告已保存至：[/green]{output_path}")

    if dump_sample and raw_samples is not None:
        sample_path = Path("reports") / "sample_raw_albums.json"
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(
            json.dumps(raw_samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        console.print(f"[dim]原始样本已保存至：{sample_path}[/dim]")


@app.command("backup")
def cmd_backup(
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help="备份快照 JSON 输出路径（默认 backups/backup_<timestamp>.json）",
    ),
    all_playlists: bool = typer.Option(
        False,
        "--all-playlists",
        help="是否包含不可编辑的订阅歌单（默认仅备份可编辑的自建歌单）",
    ),
    with_ratings: bool = typer.Option(
        True,
        "--with-ratings/--no-ratings",
        help="是否同时备份歌单曲目的喜爱/评分状态",
    ),
) -> None:
    """[阶段一·只读] 全量备份自建歌单曲目与喜爱状态，防止后续删除专辑导致歌单丢失。"""
    client, creds = _require_client()

    try:
        with client:
            storefront = creds.storefront or client.get_storefront()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("正在拉取歌单列表...", total=None)

                def on_pl(idx: int, total: int, name: str) -> None:
                    progress.update(
                        task,
                        total=total,
                        completed=idx,
                        description=f"正在备份歌单（{idx}/{total}）：{name}",
                    )

                backup = create_library_backup(
                    client,
                    storefront,
                    include_non_editable_playlists=all_playlists,
                    include_ratings=with_ratings,
                    on_playlist=on_pl,
                )
    except AppleMusicAuthError as exc:
        console.print(f"[red]鉴权失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(f"[red]备份失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    output_path = out or Path("backups") / f"backup_{_timestamp_slug()}.json"
    save_backup(backup, output_path)
    meta = backup.metadata
    console.print(
        Panel(
            f"[green]备份完成！[/green]\n"
            f"备份歌单数: [bold]{meta.get('playlists_backed_up', 0)}[/bold] / {meta.get('total_playlists_seen', 0)}\n"
            f"备份曲目条目: [bold]{meta.get('total_playlist_tracks', 0)}[/bold]\n"
            f"备份评分记录: [bold]{meta.get('ratings_backed_up', 0)}[/bold]\n"
            f"快照路径: [cyan]{output_path}[/cyan]",
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
        summary = (
            f"模式: [cyan]Dry-Run 预览（未修改云端资料库）[/cyan]   区服: [cyan]{merge_report.storefront}[/cyan]\n"
            f"可自动合并: [bold green]{len(merge_report.dry_run_ready)}[/bold green]   "
            f"将跳过（需手动处理）: [bold yellow]{len(merge_report.manual_required)}[/bold yellow]"
        )
        border = "cyan"
    else:
        summary = (
            f"模式: [bold]正式执行[/bold]   区服: [cyan]{merge_report.storefront}[/cyan]\n"
            f"自动合并成功: [bold green]{len(merge_report.succeeded)}[/bold green]   "
            f"需手动操作合并: [bold yellow]{len(merge_report.manual_required)}[/bold yellow]"
        )
        border = "green" if not merge_report.manual_required else "yellow"

    console.print(Panel(summary, title="合并结果总览报告", border_style=border))

    # 1. Succeeded table
    if merge_report.succeeded:
        ok_table = Table(
            title=f"✅ 自动合并成功的专辑（{len(merge_report.succeeded)} 组）",
            show_lines=True,
            border_style="green",
        )
        ok_table.add_column("组ID", style="bold cyan", no_wrap=True)
        ok_table.add_column("专辑 / 艺人")
        ok_table.add_column("目标 Catalog ID", no_wrap=True)
        ok_table.add_column("新资料库 ID", no_wrap=True)
        ok_table.add_column("执行详情")

        for r in merge_report.succeeded:
            details: list[str] = []
            if r.deleted_album_statuses:
                details.append(f"删除旧专辑: {r.deleted_album_statuses}")
            if r.deleted_song_statuses:
                details.append(f"降级逐首删歌: {len(r.deleted_song_statuses)} 首")
            if r.readd_status is not None:
                details.append(f"整专重导 HTTP: {r.readd_status}")
            if r.restored_playlist_tracks:
                pl_txt = ", ".join(
                    f"{pid}(补回{cnt}首)" for pid, cnt in r.restored_playlist_tracks.items()
                )
                details.append(f"歌单恢复: {pl_txt}")
            if r.restored_ratings:
                details.append(f"恢复评分: {r.restored_ratings}")
            ok_table.add_row(
                r.group_id,
                _format_album_cell(r),
                r.target_catalog_album_id or "-",
                ", ".join(r.new_library_album_ids) or "-",
                "\n".join(details) if details else "-",
            )
        console.print(ok_table)

    # 2. Dry-run ready table
    if merge_report.dry_run_ready:
        dry_table = Table(
            title=f"🔍 预览：可自动合并的专辑（{len(merge_report.dry_run_ready)} 组）",
            show_lines=True,
            border_style="cyan",
        )
        dry_table.add_column("组ID", style="bold cyan", no_wrap=True)
        dry_table.add_column("专辑 / 艺人")
        dry_table.add_column("目标 Catalog ID", no_wrap=True)
        dry_table.add_column("受影响歌单与提示")

        for r in merge_report.dry_run_ready:
            details = []
            if r.affected_playlists:
                pl_summary = ", ".join(
                    f"{pid}({len(cids)}首)" for pid, cids in r.affected_playlists.items()
                )
                details.append(f"将自动备份并补回歌单: {pl_summary}")
            for w in r.warnings:
                details.append(f"[yellow]• {w}[/yellow]")
            dry_table.add_row(
                r.group_id,
                _format_album_cell(r),
                r.target_catalog_album_id or "-",
                "\n".join(details) if details else "[green]无歌单影响，可直接合并[/green]",
            )
        console.print(dry_table)

    # 3. Manual action required table
    if merge_report.manual_required:
        man_table = Table(
            title=f"⚠️ 需要手动操作合并的专辑（{len(merge_report.manual_required)} 组）",
            show_lines=True,
            border_style="yellow",
        )
        man_table.add_column("组ID", style="bold cyan", no_wrap=True)
        man_table.add_column("专辑 / 艺人")
        man_table.add_column("原因分类", no_wrap=True)
        man_table.add_column("详细原因与手动处理建议")

        stage_labels = {
            "still_split": "[yellow]重导后仍分裂\n(上游曲库异常)[/yellow]",
            "skipped": "[yellow]安全拦截跳过[/yellow]",
            "failed": "[red]执行失败[/red]",
        }
        for r in merge_report.manual_required:
            notes: list[str] = []
            if r.error:
                notes.append(f"[red]原因: {r.error}[/red]")
            for w in r.warnings:
                notes.append(f"[yellow]• {w}[/yellow]")
            if r.manual_action_hint:
                notes.append(f"[cyan]建议: {r.manual_action_hint}[/cyan]")
            man_table.add_row(
                r.group_id,
                _format_album_cell(r),
                stage_labels.get(r.stage, r.stage),
                "\n".join(notes) if notes else "-",
            )
        console.print(man_table)


@app.command("merge")
def cmd_merge(
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        "-r",
        help="扫描报告路径（默认自动选用 reports/ 下最新的 scan_*.json）",
    ),
    group: Optional[list[str]] = typer.Option(
        None,
        "--group",
        "-g",
        help="指定要合并的分裂组 ID，例如 --group G004（可多次指定）",
    ),
    merge_all: bool = typer.Option(
        False,
        "--all",
        help="一键处理报告中所有可安全合并的分裂组，并在报告中完整列出需手动处理的专辑",
    ),
    catalog_id: Optional[str] = typer.Option(
        None,
        "--catalog-id",
        help="可选：手动覆盖目标 Catalog Album ID（仅支持配合单个 --group 使用）",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="强制允许处理受保护拦截的分组（仍强制校验目标 Catalog 在架，绝不绕过下架保护）",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="真正执行云端删除与重导（不传此参数时严格为 Dry-Run 预览模式）",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="跳过交互式确认提示（仅在 --execute 时生效）",
    ),
    backup_file: Optional[Path] = typer.Option(
        None,
        "--backup-file",
        "-b",
        help="指定已有歌单备份文件（执行模式下默认自动现场拉取最新备份）",
    ),
    report_out: Optional[Path] = typer.Option(
        None,
        "--report-out",
        help="合并结果报告 JSON 输出路径（执行模式默认保存至 reports/merge_report_<ts>.json）",
    ),
) -> None:
    """[阶段二] 合并分裂专辑：支持 --all 一键批处理并输出「成功 / 需手动合并」完整报告。"""
    if not group and not merge_all:
        console.print(
            "[red]请指定要处理的分裂组[/red]：使用 [bold]--all[/bold] 处理全部，或 [bold]--group G004[/bold] 单张处理。"
        )
        raise typer.Exit(code=1)

    if catalog_id and (merge_all or not group or len(group) != 1):
        console.print("[red]--catalog-id 仅能配合单个 --group 使用。[/red]")
        raise typer.Exit(code=1)

    report_path = report or find_latest_file(Path("reports"), "scan_*.json")
    if not report_path or not report_path.exists():
        console.print("[red]未找到扫描报告。[/red]请先运行 [bold]amlibmerge scan[/bold]。")
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
                console.print(f"[red]报告 {report_path} 中不存在分组 {gid}。[/red]")
                raise typer.Exit(code=1)
            selected.append(groups_by_id[key])

    if not selected and not skipped_blocked:
        console.print("[green]扫描报告中没有任何分裂专辑需要处理！[/green]")
        raise typer.Exit(code=0)

    results: list[MergeResult] = []
    journal_path: Path | None = None
    storefront = scan_report.storefront

    if selected:
        client, creds = _require_client()
        active_backup: LibraryBackup | None = None
        if backup_file:
            if not backup_file.exists():
                console.print(f"[red]指定的备份文件不存在：{backup_file}[/red]")
                raise typer.Exit(code=1)
            active_backup = load_backup(backup_file)
        elif not execute:
            latest_bk = find_latest_file(Path("backups"), "backup_*.json")
            if latest_bk:
                active_backup = load_backup(latest_bk)
                console.print(f"[dim]Dry-Run 使用已有备份快照估算受影响歌单：{latest_bk}[/dim]")

        try:
            with client:
                storefront = creds.storefront or scan_report.storefront or client.get_storefront()

                if execute:
                    skip_note = (
                        f"\n另有 [yellow]{len(skipped_blocked)}[/yellow] 组触发安全保护将自动跳过，并汇总到末尾的「需手动合并」报告中。"
                        if skipped_blocked
                        else ""
                    )
                    console.print(
                        Panel(
                            f"即将对 [bold]{len(selected)}[/bold] 组分裂专辑执行 [bold red]云端删除 + 整专重导[/bold red]：\n"
                            + "\n".join(
                                f"  • [cyan]{g.group_id}[/cyan] {g.canonical_name} "
                                f"(删除 {', '.join(a.id for a in g.albums)} → 重导 {catalog_id or g.target_catalog_album_id})"
                                for g in selected
                            )
                            + skip_note
                            + "\n\n[yellow]注意：删除并重导会重置受影响专辑的播放次数与原始添加日期，受影响自建歌单曲目将自动备份并补回。[/yellow]",
                            title="执行前确认",
                            border_style="red",
                        )
                    )
                    if not yes:
                        confirmed = typer.confirm("确认继续执行？", default=False)
                        if not confirmed:
                            console.print("[yellow]已取消，未执行任何修改。[/yellow]")
                            raise typer.Exit(code=0)

                    if active_backup is None:
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            console=console,
                            transient=True,
                        ) as progress:
                            btask = progress.add_task("执行前自动备份自建歌单与评分...", total=None)
                            active_backup = create_library_backup(client, storefront)
                            bk_path = Path("backups") / f"backup_pre_merge_{_timestamp_slug()}.json"
                            save_backup(active_backup, bk_path)
                            progress.remove_task(btask)
                        console.print(f"[green]前置安全备份已保存：[/green]{bk_path}")

                journal_path = Path("journals") / f"merge_{_timestamp_slug()}.jsonl" if execute else None

                for g in selected:
                    console.print(f"\n[bold]>>> 处理 {g.group_id}: {g.canonical_name}[/bold]")
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
            console.print(f"[red]鉴权失败：[/red]{exc}")
            raise typer.Exit(code=1) from exc
        except AppleMusicAPIError as exc:
            console.print(f"[red]接口异常：[/red]{exc}")
            raise typer.Exit(code=1) from exc

    # Include all blocked groups from --all in the final report so the user has one unified manual-action list
    for bg in skipped_blocked:
        results.append(
            make_skipped_result(
                bg,
                dry_run=not execute,
                reason="触发安全护栏（自动跳过，需手动核对处理）",
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
        console.print(f"\n[green]合并结果报告已保存至：[/green]{out_path}")

    if not execute:
        if merge_report.dry_run_ready:
            cmd_hint = (
                "amlibmerge merge --all --execute"
                if merge_all
                else f"amlibmerge merge --group {merge_report.dry_run_ready[0].group_id} --execute"
            )
            console.print(
                f"\n[cyan]以上为 Dry-Run 预览，未对资料库做任何改动。[/cyan]"
                f"如确认无误，可执行：\n  [bold]{cmd_hint}[/bold]"
            )
        else:
            console.print(
                "\n[yellow]当前没有可自动安全合并的专辑，所有剩余分组均已归入上方「需手动操作合并」清单。[/yellow]"
            )
    elif journal_path and journal_path.exists():
        console.print(f"[dim]操作日志 (WAL) 已写入：{journal_path}[/dim]")


@app.command("restore")
def cmd_restore(
    backup_file: Optional[Path] = typer.Option(
        None,
        "--backup",
        "-b",
        help="备份快照 JSON 路径（默认选用 backups/ 下最新备份）",
    ),
    playlist: Optional[str] = typer.Option(
        None,
        "--playlist",
        help="仅恢复指定歌单 ID（默认检查备份中的所有可编辑歌单）",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="真正执行歌单曲目补回与评分恢复（不传此参数时为 Dry-Run 比对模式）",
    ),
    no_ratings: bool = typer.Option(
        False,
        "--no-ratings",
        help="跳过评分/喜爱状态恢复",
    ),
) -> None:
    """[阶段二] 根据备份快照幂等补回缺失的歌单曲目与喜爱状态。"""
    bk_path = backup_file or find_latest_file(Path("backups"), "backup_*.json")
    if not bk_path or not bk_path.exists():
        console.print("[red]未找到备份快照文件。[/red]请先运行 [bold]amlibmerge backup[/bold]。")
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
        console.print(f"[red]鉴权失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    except AppleMusicAPIError as exc:
        console.print(f"[red]恢复失败：[/red]{exc}")
        raise typer.Exit(code=1) from exc

    table = Table(
        title="歌单恢复结果" if execute else "歌单差异预览 (Dry-Run)",
        show_lines=True,
    )
    table.add_column("歌单 ID", style="cyan", no_wrap=True)
    table.add_column("歌单名称")
    table.add_column("备份曲目数", justify="right")
    table.add_column("当前缺失", justify="right")
    table.add_column("已补回", justify="right")

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
        console.print(
            f"\n[cyan]以上为比对预览（使用快照 {bk_path}）。[/cyan]"
            f"如需补回缺失曲目，请加 [bold]--execute[/bold]。"
        )


if __name__ == "__main__":
    app()
