"""Interactive Textual TUI for amlibmerge."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

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
    get_jwt_exp,
    load_credentials,
    mask_token,
    save_credentials,
)
from amlibmerge.detector import evaluate_group_safety
from amlibmerge.merger import (
    build_merge_report,
    compute_affected_items,
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


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class AuthChoiceModal(ModalScreen[str | None]):
    """Choose between browser popup login, fresh profile login, or manual token paste."""

    CSS = """
    AuthChoiceModal {
        align: center middle;
    }
    #auth-dialog {
        width: 68;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #auth-dialog Label {
        margin-bottom: 1;
    }
    #auth-buttons {
        height: auto;
        margin-top: 1;
    }
    #auth-buttons Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-dialog"):
            yield Label("[bold cyan]Apple Music 登录方式[/bold cyan]")
            yield Label(
                "• [bold]浏览器弹窗登录[/bold]：打开本机 Edge / Chrome 登录 music.apple.com 并自动抓取 Token\n"
                "• [bold]全新配置登录[/bold]：使用全新临时浏览器配置（适合切换 Apple ID）\n"
                "• [bold]手动粘贴 Token[/bold]：适合无图形界面或已有 media-user-token 的环境"
            )
            with Horizontal(id="auth-buttons"):
                yield Button("浏览器登录", variant="primary", id="btn-auth-browser")
                yield Button("全新配置登录", variant="default", id="btn-auth-fresh")
                yield Button("手动输入", variant="warning", id="btn-auth-manual")
                yield Button("取消", id="btn-auth-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-auth-browser": "browser",
            "btn-auth-fresh": "fresh",
            "btn-auth-manual": "manual",
            "btn-auth-cancel": None,
        }
        self.dismiss(mapping.get(event.button.id))


class ManualTokenModal(ModalScreen[str | None]):
    """Password-masked prompt to enter media-user-token manually."""

    CSS = """
    ManualTokenModal {
        align: center middle;
    }
    #manual-token-dialog {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #manual-token-dialog Input {
        margin: 1 0;
    }
    #manual-token-buttons {
        height: auto;
        align-horizontal: right;
    }
    #manual-token-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="manual-token-dialog"):
            yield Label("[bold yellow]手动输入 media-user-token[/bold yellow]")
            yield Label(
                "在浏览器登录 https://music.apple.com → F12 → Application → Cookies，"
                "复制 media-user-token 粘贴到下方（输入已隐藏）："
            )
            yield Input(placeholder="粘贴 media-user-token...", password=True, id="input-token")
            with Horizontal(id="manual-token-buttons"):
                yield Button("取消", id="btn-token-cancel")
                yield Button("校验并保存", variant="primary", id="btn-token-ok")

    def on_mount(self) -> None:
        self.query_one("#input-token", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-token-ok":
            value = self.query_one("#input-token", Input).value.strip()
            self.dismiss(value if value else None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)


class ConfirmMergeModal(ModalScreen[dict[str, Any] | None]):
    """Explicit confirmation dialog before cloud DELETE + re-import execution."""

    CSS = """
    ConfirmMergeModal {
        align: center middle;
    }
    #confirm-merge-dialog {
        width: 82;
        max-height: 85%;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #confirm-merge-list {
        max-height: 12;
        margin: 1 0;
        padding: 0 1;
        border: tall $panel;
    }
    #confirm-merge-error {
        color: $error;
        margin-top: 1;
    }
    #confirm-merge-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #confirm-merge-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, groups: list[SplitGroup]) -> None:
        super().__init__()
        self.groups = groups
        self.blocked_groups = [g for g in groups if not g.safe_to_merge]

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-merge-dialog"):
            yield Label(
                f"[bold red]确认执行云端合并（共 {len(self.groups)} 组）[/bold red]"
            )
            lines: list[str] = []
            for g in self.groups:
                old_ids = ", ".join(a.id for a in g.albums)
                tag = "[green]安全[/green]" if g.safe_to_merge else "[red]受保护拦截[/red]"
                lines.append(
                    f"• [{g.group_id}] {escape(g.canonical_name)} ({tag})\n"
                    f"    删除: {old_ids}  →  重导整专: {g.target_catalog_album_id or '无'}"
                )
            with VerticalScroll(id="confirm-merge-list"):
                yield Static("\n".join(lines))

            yield Label(
                "[yellow]注意：执行前会自动拉取最新歌单备份并写入 WAL 日志；"
                "删除重导会重置受影响专辑的播放次数与原始添加日期，受影响自建歌单曲目会自动补回。[/yellow]"
            )

            if self.blocked_groups:
                blocked_ids = ", ".join(g.group_id for g in self.blocked_groups)
                yield Checkbox(
                    f"我已核对受拦截分组（{blocked_ids}）并允许强制合并（仍严格校验官方曲库在架，404 绝不删除）",
                    value=False,
                    id="chk-force-blocked",
                )

            if len(self.groups) == 1:
                yield Input(
                    value=self.groups[0].target_catalog_album_id or "",
                    placeholder="可选：覆盖目标 Catalog Album ID",
                    id="input-override-cid",
                )

            yield Label("", id="confirm-merge-error")

            with Horizontal(id="confirm-merge-buttons"):
                yield Button("取消", id="btn-merge-cancel")
                yield Button(
                    "确认执行合并",
                    variant="error",
                    id="btn-merge-confirm",
                    disabled=bool(self.blocked_groups),
                )

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "chk-force-blocked":
            self.query_one("#btn-merge-confirm", Button).disabled = not event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn-merge-confirm":
            self.dismiss(None)
            return

        force = False
        if self.blocked_groups:
            chk = self.query_one("#chk-force-blocked", Checkbox)
            if not chk.value:
                self.query_one("#confirm-merge-error", Label).update(
                    "请先勾选上方强制确认选项后再执行。"
                )
                return
            force = True

        override_cid: str | None = None
        if len(self.groups) == 1:
            raw_cid = self.query_one("#input-override-cid", Input).value.strip()
            if raw_cid and raw_cid != (self.groups[0].target_catalog_album_id or ""):
                override_cid = raw_cid

        self.dismiss({"force": force, "catalog_id": override_cid})


class ConfirmRestoreModal(ModalScreen[bool]):
    """Confirmation dialog before executing idempotent playlist restore."""

    CSS = """
    ConfirmRestoreModal {
        align: center middle;
    }
    #confirm-restore-dialog {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #confirm-restore-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #confirm-restore-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, backup_path: Path, missing_tracks: int, ratings_count: int) -> None:
        super().__init__()
        self.backup_path = backup_path
        self.missing_tracks = missing_tracks
        self.ratings_count = ratings_count

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-restore-dialog"):
            yield Label("[bold cyan]确认执行歌单与评分恢复[/bold cyan]")
            yield Label(
                f"备份快照：{escape(str(self.backup_path))}\n"
                f"待补回缺失曲目：[bold]{self.missing_tracks}[/bold] 首（幂等去重，不会重复添加）\n"
                f"待恢复评分记录：[bold]{self.ratings_count}[/bold] 条"
            )
            with Horizontal(id="confirm-restore-buttons"):
                yield Button("取消", id="btn-restore-cancel")
                yield Button("确认补回", variant="primary", id="btn-restore-confirm")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-restore-confirm")


class AmlibmergeApp(App[None]):
    """Interactive Textual TUI for Apple Music split album scanning, backup, merging, and restore."""

    TITLE = f"amlibmerge {__version__}"
    SUB_TITLE = "Apple Music 分裂专辑交互式管理终端"

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        height: 3;
        padding: 0 1;
        border: round $primary;
        content-align: left middle;
    }
    #toolbar {
        height: 3;
        padding: 0 1;
        margin-bottom: 1;
    }
    #toolbar Button {
        margin-right: 1;
        min-width: 10;
    }
    #main-split {
        height: 1fr;
    }
    #left-pane {
        width: 3fr;
        min-width: 50;
        padding-right: 1;
    }
    #right-pane {
        width: 2fr;
        min-width: 36;
        border: round $secondary;
        padding: 0 1;
    }
    #groups-table {
        height: 1fr;
    }
    #report-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #restore-pane-inner {
        height: 1fr;
    }
    #restore-actions {
        height: 3;
        margin-bottom: 1;
    }
    #restore-actions Button {
        margin-right: 1;
    }
    #restore-table {
        height: 1fr;
    }
    #log-panel {
        height: 9;
        border: round $panel;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("l", "login", "登录"),
        Binding("s", "scan", "扫描"),
        Binding("b", "backup", "备份"),
        Binding("space", "toggle_select", "勾选"),
        Binding("a", "select_safe", "全选安全"),
        Binding("n", "clear_selection", "清空勾选"),
        Binding("d", "dry_run", "预览"),
        Binding("m", "execute_merge", "合并"),
        Binding("r", "restore_preview", "歌单恢复"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(
        self,
        *,
        report_path: Path | None = None,
        reports_dir: Path = Path("reports"),
        backups_dir: Path = Path("backups"),
        journals_dir: Path = Path("journals"),
        client_factory: Callable[[Credentials], Any] | None = None,
        browser_login_fn: Callable[..., tuple[str, str, str, int | None]] | None = None,
        poll_interval: float = 2.0,
        settle_delay: float = 3.0,
    ) -> None:
        super().__init__()
        self._initial_report_path = report_path
        self.reports_dir = reports_dir
        self.backups_dir = backups_dir
        self.journals_dir = journals_dir
        self._client_factory = client_factory or self._default_client_factory
        self._browser_login_fn = browser_login_fn or capture_token_via_browser
        self._poll_interval = poll_interval
        self._settle_delay = settle_delay

        self.credentials: Credentials | None = None
        self.scan_report: ScanReport | None = None
        self.scan_report_path: Path | None = None
        self.backup: LibraryBackup | None = None
        self.backup_path: Path | None = None
        self.last_merge_report: MergeReport | None = None
        self.last_restore_summary: dict[str, Any] | None = None

        self.groups_by_id: dict[str, SplitGroup] = {}
        self.selected_group_ids: set[str] = set()
        self.highlighted_group_id: str | None = None
        self.busy: bool = False

    @staticmethod
    def _default_client_factory(creds: Credentials) -> AppleMusicClient:
        return AppleMusicClient(
            media_user_token=creds.media_user_token,
            developer_token=creds.developer_token,
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status-bar")
        with Horizontal(id="toolbar"):
            yield Button("登录 [L]", id="btn-login", variant="default")
            yield Button("扫描 [S]", id="btn-scan", variant="primary")
            yield Button("备份 [B]", id="btn-backup", variant="default")
            yield Button("全选安全 [A]", id="btn-select-safe", variant="default")
            yield Button("预览 [D]", id="btn-dry-run", variant="warning")
            yield Button("执行合并 [M]", id="btn-merge", variant="error")
            yield Button("歌单恢复 [R]", id="btn-restore", variant="default")

        with Horizontal(id="main-split"):
            with Vertical(id="left-pane"):
                with TabbedContent(initial="tab-groups", id="tabs"):
                    with TabPane("分裂专辑列表", id="tab-groups"):
                        yield DataTable(id="groups-table", cursor_type="row")
                    with TabPane("合并报告", id="tab-report"):
                        with VerticalScroll(id="report-scroll"):
                            yield Static(
                                "[dim]尚未运行合并预览或执行。请勾选分组后按 [bold]D[/bold] 预览或 [bold]M[/bold] 执行。[/dim]",
                                id="report-view",
                            )
                    with TabPane("歌单恢复", id="tab-restore"):
                        with Vertical(id="restore-pane-inner"):
                            with Horizontal(id="restore-actions"):
                                yield Button("比对线上差异", id="btn-restore-diff", variant="primary")
                                yield Button("执行补回缺失曲目", id="btn-restore-exec", variant="warning")
                            yield DataTable(id="restore-table", cursor_type="row")

            with VerticalScroll(id="right-pane"):
                yield Static(
                    "[dim]在左侧列表移动光标以查看分裂专辑明细、曲目列表、拦截原因与受影响歌单。[/dim]",
                    id="detail-view",
                )

        yield RichLog(id="log-panel", markup=True, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._init_tables()
        self._load_local_state()
        self._refresh_status_bar()
        self._populate_groups_table()
        self.query_one("#groups-table", DataTable).focus()
        self._log("[cyan]欢迎使用 amlibmerge TUI！按 [bold]S[/bold] 扫描资料库，[bold]Space[/bold] 勾选，[bold]D[/bold] 预览，[bold]M[/bold] 执行合并。[/cyan]")

    def _init_tables(self) -> None:
        gt = self.query_one("#groups-table", DataTable)
        gt.add_column("选", key="sel", width=3)
        gt.add_column("组ID", key="gid", width=6)
        gt.add_column("置信度", key="tier", width=10)
        gt.add_column("专辑 / 艺人", key="album")
        gt.add_column("曲目分布", key="tracks", width=16)
        gt.add_column("Catalog ID", key="cid", width=12)
        gt.add_column("状态", key="status", width=10)

        rt = self.query_one("#restore-table", DataTable)
        rt.add_column("歌单 ID", key="pid", width=18)
        rt.add_column("歌单名称", key="name")
        rt.add_column("备份曲目", key="bk", width=10)
        rt.add_column("线上缺失", key="miss", width=10)
        rt.add_column("已补回", key="done", width=10)

    def _load_local_state(self) -> None:
        self.credentials = load_credentials()

        bk_path = find_latest_file(self.backups_dir, "backup_*.json")
        if bk_path and bk_path.exists():
            try:
                self.backup = load_backup(bk_path)
                self.backup_path = bk_path
            except Exception as exc:
                self._log(f"[yellow]读取最新备份失败（已忽略）：{escape(str(exc))}[/yellow]")

        rpt_path = self._initial_report_path or find_latest_file(self.reports_dir, "scan_*.json")
        if rpt_path and rpt_path.exists():
            try:
                report = load_scan_report(rpt_path)
                for g in report.split_groups:
                    evaluate_group_safety(g)
                self.scan_report = report
                self.scan_report_path = rpt_path
                self.groups_by_id = {g.group_id: g for g in report.split_groups}
                self._log(
                    f"[green]已自动加载扫描报告：[/green]{escape(str(rpt_path))} "
                    f"（共 {len(report.split_groups)} 组分裂候选）"
                )
            except Exception as exc:
                self._log(f"[yellow]读取扫描报告失败：{escape(str(exc))}[/yellow]")

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log-panel", RichLog).write(f"[dim]{ts}[/dim] {message}")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.busy = busy
        for btn_id in (
            "#btn-login",
            "#btn-scan",
            "#btn-backup",
            "#btn-dry-run",
            "#btn-merge",
            "#btn-restore",
            "#btn-restore-diff",
            "#btn-restore-exec",
        ):
            self.query_one(btn_id, Button).disabled = busy
        self._refresh_status_bar(busy_note=label if busy else "")

    def _refresh_status_bar(self, *, busy_note: str = "") -> None:
        if self.credentials and self.credentials.media_user_token:
            sf = self.credentials.storefront or "未知区服"
            auth_part = (
                f"[green]已登录[/green] 区服:[bold cyan]{escape(sf)}[/bold cyan] "
                f"Token:[dim]{escape(mask_token(self.credentials.media_user_token))}[/dim]"
            )
        else:
            auth_part = "[yellow]未登录（按 L 登录）[/yellow]"

        rpt_part = (
            f"报告:[cyan]{escape(self.scan_report_path.name)}[/cyan]"
            if self.scan_report_path
            else "报告:[dim]无（按 S 扫描）[/dim]"
        )
        bk_part = (
            f"备份:[green]{escape(self.backup_path.name)}[/green]"
            if self.backup_path
            else "备份:[dim]无（按 B 备份）[/dim]"
        )
        sel_part = f"已勾选:[bold magenta]{len(self.selected_group_ids)}[/bold magenta]"
        busy_part = f"   [bold yellow]⏳ {escape(busy_note)}[/bold yellow]" if busy_note else ""

        self.query_one("#status-bar", Static).update(
            f"{auth_part}  │  {rpt_part}  │  {bk_part}  │  {sel_part}{busy_part}"
        )

    def _populate_groups_table(self) -> None:
        table = self.query_one("#groups-table", DataTable)
        table.clear()
        self.selected_group_ids.intersection_update(self.groups_by_id.keys())

        if not self.scan_report or not self.scan_report.split_groups:
            self.highlighted_group_id = None
            self._render_detail_panel(None)
            self._refresh_status_bar()
            return

        for g in self.scan_report.split_groups:
            sel_mark = "☑" if g.group_id in self.selected_group_ids else "☐"
            tier_text = (
                Text("高 (同ID)", style="magenta")
                if g.tier == SplitTier.HIGH_SAME_CATALOG_ID
                else Text("中 (同名)", style="blue")
            )
            album_label = g.canonical_name
            if g.canonical_artist:
                album_label = f"{g.canonical_name} — {g.canonical_artist}"
            counts = "+".join(str(a.track_count) for a in g.albums)
            if g.target_catalog_track_count:
                counts += f"/{g.target_catalog_track_count}"
            status_text = (
                Text("可合并", style="bold green")
                if g.safe_to_merge
                else Text("拦截保护", style="bold red")
            )
            table.add_row(
                sel_mark,
                g.group_id,
                tier_text,
                album_label,
                counts,
                g.target_catalog_album_id or "-",
                status_text,
                key=g.group_id,
            )

        first_id = self.scan_report.split_groups[0].group_id
        self.highlighted_group_id = first_id
        self._render_detail_panel(self.groups_by_id.get(first_id))
        self._refresh_status_bar()

    def _render_detail_panel(self, group: SplitGroup | None) -> None:
        view = self.query_one("#detail-view", Static)
        if group is None:
            view.update(
                "[dim]当前没有可检视的分裂组。按 [bold]S[/bold] 扫描资料库后可在此查看详细信息。[/dim]"
            )
            return

        status_badge = (
            "[bold green]可安全合并[/bold green]"
            if group.safe_to_merge
            else "[bold red]受保护拦截（默认不自动合并）[/bold red]"
        )
        avail_str = (
            "[green]在架 ✓[/green]"
            if group.target_catalog_available is True
            else "[red]已下架 / 404 ✗[/red]"
            if group.target_catalog_available is False
            else "[dim]未校验[/dim]"
        )

        lines = [
            f"[bold cyan]{group.group_id}[/bold cyan]  {status_badge}",
            f"[bold]{escape(group.canonical_name)}[/bold]",
            f"[dim]{escape(group.canonical_artist)}[/dim]",
            f"置信度: {'高 (同 Catalog ID)' if group.tier == SplitTier.HIGH_SAME_CATALOG_ID else '中 (同名同艺人)'}",
            f"目标 Catalog ID: [bold]{escape(group.target_catalog_album_id or '-')}[/bold] ({avail_str})",
            f"目标整专曲目数: {group.target_catalog_track_count or '未知'}",
        ]

        affected_playlists, affected_ratings = compute_affected_items(group, self.backup)
        if affected_playlists:
            pl_names = {p.id: p.name for p in (self.backup.playlists if self.backup else [])}
            pl_desc = ", ".join(
                f"{escape(pl_names.get(pid, pid))}({len(cids)}首)"
                for pid, cids in affected_playlists.items()
            )
            lines.append(f"受影响自建歌单: [yellow]{pl_desc}[/yellow]")
        else:
            lines.append("受影响自建歌单: [green]无[/green]")
        if affected_ratings:
            lines.append(f"受影响评分: {len(affected_ratings)} 条")

        if group.warnings:
            lines.append("\n[bold yellow]安全提示 / 拦截原因[/bold yellow]")
            for w in group.warnings:
                lines.append(f"  [yellow]• {escape(w)}[/yellow]")

        lines.append(f"\n[bold]分裂专辑壳明细（共 {len(group.albums)} 份）[/bold]")
        for album in group.albums:
            date_str = (album.date_added or "")[:10] or "未知日期"
            cid_str = album.catalog_id or "无Catalog"
            lines.append(
                f"\n[cyan]▸ {escape(album.id)}[/cyan] "
                f"({album.track_count} 首 | cid={escape(cid_str)} | 添加于 {escape(date_str)})"
            )
            if not album.tracks:
                lines.append("    [dim](空壳或未加载曲目，实际曲目可能挂在同组另一条目下)[/dim]")
            else:
                for track in album.tracks[:25]:
                    dnum = f"D{track.disc_number}" if track.disc_number else ""
                    tnum = f"T{track.track_number:02d}" if track.track_number else ""
                    pos = f"{dnum}{tnum}".strip() or "--"
                    if track.catalog_id:
                        tcid = f"[dim][{escape(track.catalog_id)}][/dim]"
                    else:
                        tcid = "[bold yellow][无Catalog][/bold yellow]"
                    lines.append(f"    {pos} {escape(track.name)} {tcid}")
                if len(album.tracks) > 25:
                    lines.append(f"    [dim]... 其余 {len(album.tracks) - 25} 首已省略[/dim]")

        view.update("\n".join(lines))

    def _render_merge_report_tab(self, report: MergeReport) -> None:
        mode_str = (
            "[cyan]Dry-Run 预览（未修改云端资料库）[/cyan]"
            if report.dry_run
            else "[bold green]正式执行完成[/bold green]"
        )
        parts: list[str] = [
            f"模式: {mode_str}   区服: [cyan]{escape(report.storefront)}[/cyan]\n"
            f"自动合并成功: [bold green]{len(report.succeeded)}[/bold green]   "
            f"预览就绪: [bold cyan]{len(report.dry_run_ready)}[/bold cyan]   "
            f"需手动操作合并: [bold yellow]{len(report.manual_required)}[/bold yellow]"
        ]

        if report.succeeded:
            parts.append("\n[bold green]✅ 自动合并成功的专辑[/bold green]")
            for r in report.succeeded:
                new_ids = ", ".join(r.new_library_album_ids) or "-"
                pl_info = (
                    f" | 歌单补回: {r.restored_playlist_tracks}"
                    if r.restored_playlist_tracks
                    else ""
                )
                parts.append(
                    f"  • [cyan]{r.group_id}[/cyan] [bold]{escape(r.canonical_name)}[/bold] "
                    f"→ 新ID: [green]{escape(new_ids)}[/green] (Catalog {escape(r.target_catalog_album_id)}){escape(pl_info)}"
                )

        if report.dry_run_ready:
            parts.append("\n[bold cyan]🔍 Dry-Run 预览：可自动合并的专辑[/bold cyan]")
            for r in report.dry_run_ready:
                pl_cnt = sum(len(v) for v in r.affected_playlists.values())
                pl_note = f"（将自动备份并补回 {pl_cnt} 首自建歌单曲目）" if pl_cnt else "（无歌单影响）"
                parts.append(
                    f"  • [cyan]{r.group_id}[/cyan] [bold]{escape(r.canonical_name)}[/bold] "
                    f"→ 目标整专 {escape(r.target_catalog_album_id)} {pl_note}"
                )

        if report.manual_required:
            parts.append("\n[bold yellow]⚠️ 需要手动操作合并的专辑[/bold yellow]")
            stage_map = {
                "still_split": "重导后仍分裂（上游曲库异常）",
                "skipped": "安全拦截跳过",
                "failed": "执行失败",
            }
            for r in report.manual_required:
                stage_label = stage_map.get(r.stage, r.stage)
                parts.append(
                    f"  • [cyan]{r.group_id}[/cyan] [bold]{escape(r.canonical_name)}[/bold] "
                    f"[{stage_label}]"
                )
                if r.error:
                    parts.append(f"      [red]原因: {escape(r.error)}[/red]")
                if r.manual_action_hint:
                    parts.append(f"      [cyan]建议: {escape(r.manual_action_hint)}[/cyan]")

        if report.journal_path:
            parts.append(f"\n[dim]WAL 日志: {escape(report.journal_path)}[/dim]")

        self.query_one("#report-view", Static).update(
            Panel("\n".join(parts), title="合并结果报告", border_style="cyan")
        )

    def _render_restore_table(self, summary: dict[str, Any]) -> None:
        rt = self.query_one("#restore-table", DataTable)
        rt.clear()
        for row in summary.get("playlists") or []:
            rt.add_row(
                str(row["playlist_id"]),
                str(row["name"]),
                str(row["backup_tracks"]),
                str(row["missing_tracks"]),
                str(row["restored_tracks"]),
            )

    def _require_credentials(self) -> Credentials | None:
        if not self.credentials or not self.credentials.media_user_token:
            self._log("[red]尚未登录 Apple Music。[/red]请先按 [bold]L[/bold] 登录账号。")
            return None
        return self.credentials

    # --- UI Events ---

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "groups-table" or event.row_key is None:
            return
        gid = str(event.row_key.value)
        self.highlighted_group_id = gid
        self._render_detail_panel(self.groups_by_id.get(gid))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "groups-table" or event.row_key is None:
            return
        self._toggle_group_id(str(event.row_key.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "btn-login": self.action_login,
            "btn-scan": self.action_scan,
            "btn-backup": self.action_backup,
            "btn-select-safe": self.action_select_safe,
            "btn-dry-run": self.action_dry_run,
            "btn-merge": self.action_execute_merge,
            "btn-restore": self.action_restore_preview,
            "btn-restore-diff": self.action_restore_preview,
            "btn-restore-exec": self.action_restore_execute,
        }
        fn = handlers.get(event.button.id or "")
        if fn:
            fn()

    # --- Selection Actions ---

    def _toggle_group_id(self, gid: str) -> None:
        if gid not in self.groups_by_id:
            return
        table = self.query_one("#groups-table", DataTable)
        if gid in self.selected_group_ids:
            self.selected_group_ids.remove(gid)
            table.update_cell(gid, "sel", "☐")
        else:
            self.selected_group_ids.add(gid)
            table.update_cell(gid, "sel", "☑")
        self._refresh_status_bar()

    def action_toggle_select(self) -> None:
        if self.highlighted_group_id:
            self._toggle_group_id(self.highlighted_group_id)

    def action_select_safe(self) -> None:
        if not self.scan_report:
            self._log("[yellow]尚未加载扫描报告，请先按 S 扫描资料库。[/yellow]")
            return
        table = self.query_one("#groups-table", DataTable)
        self.selected_group_ids.clear()
        for g in self.scan_report.split_groups:
            if g.safe_to_merge:
                self.selected_group_ids.add(g.group_id)
                table.update_cell(g.group_id, "sel", "☑")
            else:
                table.update_cell(g.group_id, "sel", "☐")
        self._refresh_status_bar()
        self._log(f"[cyan]已自动勾选全部 {len(self.selected_group_ids)} 组可安全合并的专辑。[/cyan]")

    def action_clear_selection(self) -> None:
        if not self.scan_report:
            return
        table = self.query_one("#groups-table", DataTable)
        self.selected_group_ids.clear()
        for g in self.scan_report.split_groups:
            table.update_cell(g.group_id, "sel", "☐")
        self._refresh_status_bar()

    # --- Auth Flow ---

    def action_login(self) -> None:
        if self.busy:
            return
        self.push_screen(AuthChoiceModal(), self._handle_auth_choice)

    def _handle_auth_choice(self, choice: str | None) -> None:
        if not choice:
            return
        if choice == "manual":
            self.push_screen(ManualTokenModal(), self._handle_manual_token)
        else:
            self._run_browser_login(fresh_profile=(choice == "fresh"))

    def _handle_manual_token(self, token: str | None) -> None:
        if not token:
            return
        self._run_manual_login(token)

    @work(thread=True, exclusive=True)
    def _run_browser_login(self, fresh_profile: bool = False) -> None:
        self.call_from_thread(self._set_busy, True, "等待浏览器登录...")
        try:
            tok, sf, dev_tok, dev_exp = self._browser_login_fn(
                fresh_profile=fresh_profile,
                on_status=lambda msg: self.call_from_thread(self._log, f"[cyan]{escape(msg)}[/cyan]"),
            )
            creds = Credentials(
                media_user_token=tok,
                developer_token=dev_tok,
                developer_token_exp=dev_exp,
                storefront=sf,
            )
            save_credentials(creds)
            self.credentials = creds
            self.call_from_thread(
                self._log,
                f"[green]登录成功！区服={escape(sf)}，Token={escape(mask_token(tok))}[/green]",
            )
        except BrowserAuthError as exc:
            self.call_from_thread(self._log, f"[red]浏览器登录失败：{escape(str(exc))}[/red]")
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]登录异常：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)

    @work(thread=True, exclusive=True)
    def _run_manual_login(self, token: str) -> None:
        self.call_from_thread(self._set_busy, True, "校验 media-user-token...")
        try:
            dev_tok, dev_exp = fetch_web_developer_token()
            temp_creds = Credentials(
                media_user_token=token,
                developer_token=dev_tok,
                developer_token_exp=dev_exp,
            )
            with self._client_factory(temp_creds) as client:
                sf = client.get_storefront()
                dev_tok = getattr(client, "developer_token", dev_tok)
                dev_exp = getattr(client, "developer_token_exp", dev_exp) or get_jwt_exp(dev_tok or "")
            creds = Credentials(
                media_user_token=token,
                developer_token=dev_tok,
                developer_token_exp=dev_exp,
                storefront=sf,
            )
            save_credentials(creds)
            self.credentials = creds
            self.call_from_thread(
                self._log,
                f"[green]手动 Token 校验成功！区服={escape(sf)}，Token={escape(mask_token(token))}[/green]",
            )
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]Token 校验失败：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # --- Scan & Backup Flows ---

    def action_scan(self) -> None:
        if self.busy:
            return
        creds = self._require_credentials()
        if creds is None:
            return
        self._run_scan(creds)

    @work(thread=True, exclusive=True)
    def _run_scan(self, creds: Credentials) -> None:
        self.call_from_thread(self._set_busy, True, "正在扫描资料库...")
        self.call_from_thread(self._log, "[cyan]开始扫描 Apple Music 资料库（只读）...[/cyan]")
        try:
            with self._client_factory(creds) as client:
                sf = creds.storefront or client.get_storefront()

                def on_page(page_num: int, total_albums: int) -> None:
                    self.call_from_thread(
                        self._log, f"[dim]已拉取第 {page_num} 页，共 {total_albums} 张专辑...[/dim]"
                    )

                def on_enrich(idx: int, total: int, group: SplitGroup) -> None:
                    self.call_from_thread(
                        self._log,
                        f"[dim]校验候选 ({idx}/{total})：{escape(group.canonical_name)}[/dim]",
                    )

                report = scan_library(
                    client,
                    sf,
                    include_weak=True,
                    inspect_tracks=True,
                    on_album_page=on_page,
                    on_enrich_group=on_enrich,
                )

            out_path = self.reports_dir / f"scan_{_timestamp_slug()}.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

            self.scan_report = report
            self.scan_report_path = out_path
            self.groups_by_id = {g.group_id: g for g in report.split_groups}
            self.selected_group_ids.clear()

            self.call_from_thread(self._populate_groups_table)
            self.call_from_thread(
                self._log,
                f"[green]扫描完成！共 {report.stats.total_library_albums} 张专辑，"
                f"发现 {len(report.split_groups)} 组分裂候选（可安全合并 {report.stats.safe_to_merge_groups} 组）。"
                f"报告已保存至 {escape(str(out_path))}[/green]",
            )
        except (AppleMusicAuthError, AppleMusicAPIError) as exc:
            self.call_from_thread(self._log, f"[red]扫描失败：{escape(str(exc))}[/red]")
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]扫描异常：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)

    def action_backup(self) -> None:
        if self.busy:
            return
        creds = self._require_credentials()
        if creds is None:
            return
        self._run_backup(creds)

    @work(thread=True, exclusive=True)
    def _run_backup(self, creds: Credentials) -> None:
        self.call_from_thread(self._set_busy, True, "正在备份自建歌单...")
        self.call_from_thread(self._log, "[cyan]开始备份可编辑自建歌单与喜爱评分...[/cyan]")
        try:
            with self._client_factory(creds) as client:
                sf = creds.storefront or client.get_storefront()
                bk = create_library_backup(
                    client,
                    sf,
                    on_playlist=lambda i, n, name: self.call_from_thread(
                        self._log, f"[dim]备份歌单 ({i}/{n})：{escape(name)}[/dim]"
                    ),
                )
            out_path = self.backups_dir / f"backup_{_timestamp_slug()}.json"
            save_backup(bk, out_path)
            self.backup = bk
            self.backup_path = out_path
            self.call_from_thread(
                self._render_detail_panel, self.groups_by_id.get(self.highlighted_group_id or "")
            )
            self.call_from_thread(
                self._log,
                f"[green]备份完成！共 {len(bk.playlists)} 个歌单、"
                f"{bk.metadata.get('total_playlist_tracks', 0)} 条曲目 → {escape(str(out_path))}[/green]",
            )
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]备份失败：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # --- Merge Dry-Run & Execute Flows ---

    def _get_selected_groups_ordered(self) -> list[SplitGroup]:
        if not self.scan_report:
            return []
        return [g for g in self.scan_report.split_groups if g.group_id in self.selected_group_ids]

    def action_dry_run(self) -> None:
        if self.busy:
            return
        if not self.scan_report:
            self._log("[yellow]尚未加载扫描报告，请先按 S 扫描。[/yellow]")
            return
        creds = self._require_credentials()
        if creds is None:
            return
        selected = self._get_selected_groups_ordered()
        if not selected:
            # If user didn't check any rows yet, preview ALL groups just like `merge --all`!
            selected = list(self.scan_report.split_groups)
            self._log("[dim]未单独勾选分组，默认对报告中全部组进行 Dry-Run 预览...[/dim]")
        self._run_merge_worker(creds, selected, execute=False, force=False, override_cid=None)

    def action_execute_merge(self) -> None:
        if self.busy:
            return
        if not self.scan_report:
            self._log("[yellow]尚未加载扫描报告，请先按 S 扫描。[/yellow]")
            return
        creds = self._require_credentials()
        if creds is None:
            return
        selected = self._get_selected_groups_ordered()
        if not selected:
            self._log("[yellow]请先用 Space 勾选至少一个分裂组（或按 A 全选安全组）后再执行合并。[/yellow]")
            return

        def on_confirm(decision: dict[str, Any] | None) -> None:
            if decision is None:
                self._log("[dim]已取消云端合并，未执行任何修改。[/dim]")
                return
            self._run_merge_worker(
                creds,
                selected,
                execute=True,
                force=bool(decision.get("force")),
                override_cid=decision.get("catalog_id"),
            )

        self.push_screen(ConfirmMergeModal(selected), on_confirm)

    @work(thread=True, exclusive=True)
    def _run_merge_worker(
        self,
        creds: Credentials,
        selected: list[SplitGroup],
        *,
        execute: bool,
        force: bool,
        override_cid: str | None,
    ) -> None:
        mode_label = "正在执行云端合并..." if execute else "正在运行 Dry-Run 预览..."
        self.call_from_thread(self._set_busy, True, mode_label)
        results: list[MergeResult] = []
        journal_path = (
            self.journals_dir / f"merge_{_timestamp_slug()}.jsonl" if execute else None
        )
        storefront = creds.storefront or (self.scan_report.storefront if self.scan_report else "us")
        active_backup = self.backup

        try:
            with self._client_factory(creds) as client:
                storefront = creds.storefront or client.get_storefront()

                # Hard Safety Invariant #5: always take a fresh pre-merge backup before cloud writes
                if execute:
                    self.call_from_thread(
                        self._log, "[cyan]执行前自动拉取最新自建歌单与评分安全备份...[/cyan]"
                    )
                    active_backup = create_library_backup(client, storefront)
                    bk_path = self.backups_dir / f"backup_pre_merge_{_timestamp_slug()}.json"
                    save_backup(active_backup, bk_path)
                    self.backup = active_backup
                    self.backup_path = bk_path
                    self.call_from_thread(
                        self._log, f"[green]前置安全备份已保存：{escape(str(bk_path))}[/green]"
                    )

                for g in selected:
                    if not g.safe_to_merge and not force:
                        results.append(
                            make_skipped_result(
                                g,
                                dry_run=not execute,
                                reason="触发安全护栏（自动跳过，需手动核对处理）",
                            )
                        )
                        continue

                    self.call_from_thread(
                        self._log,
                        f"[bold]>>> 处理 {g.group_id}: {escape(g.canonical_name)}[/bold]",
                    )
                    res = merge_split_group(
                        client,
                        storefront,
                        g,
                        backup=active_backup,
                        dry_run=not execute,
                        force=force,
                        override_catalog_id=override_cid if len(selected) == 1 else None,
                        journal_path=journal_path,
                        poll_interval=self._poll_interval,
                        settle_delay=self._settle_delay,
                        on_step=lambda msg: self.call_from_thread(
                            self._log, f"  [dim]{escape(msg)}[/dim]"
                        ),
                    )
                    results.append(res)

            # Include unselected blocked groups when previewing/executing so manual list stays complete
            if self.scan_report and not execute:
                processed_ids = {r.group_id for r in results}
                for g in self.scan_report.split_groups:
                    if g.group_id not in processed_ids and not g.safe_to_merge:
                        results.append(
                            make_skipped_result(
                                g,
                                dry_run=True,
                                reason="触发安全护栏（未自动合并，需手动核对）",
                            )
                        )

            report = build_merge_report(
                results,
                storefront=storefront,
                dry_run=not execute,
                source_scan_report=self.scan_report_path or Path("reports/unknown.json"),
                journal_path=journal_path if (journal_path and journal_path.exists()) else None,
            )
            self.last_merge_report = report

            if execute:
                out_path = self.reports_dir / f"merge_report_{_timestamp_slug()}.json"
                save_merge_report(report, out_path)
                self.call_from_thread(
                    self._log,
                    f"[green]合并完成！成功 {len(report.succeeded)} 组，需手动处理 {len(report.manual_required)} 组。"
                    f"报告已保存：{escape(str(out_path))}[/green]",
                )
            else:
                self.call_from_thread(
                    self._log,
                    f"[cyan]Dry-Run 预览完成：可自动合并 {len(report.dry_run_ready)} 组，"
                    f"需手动处理 {len(report.manual_required)} 组（未做任何云端写入）。[/cyan]",
                )

            def show_report() -> None:
                self._render_merge_report_tab(report)
                self.query_one("#tabs", TabbedContent).active = "tab-report"

            self.call_from_thread(show_report)
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]合并流程异常：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)

    # --- Restore Flows ---

    def action_restore_preview(self) -> None:
        if self.busy:
            return
        creds = self._require_credentials()
        if creds is None:
            return
        if not self.backup:
            self._log("[yellow]未找到本地备份快照，请先按 B 创建备份。[/yellow]")
            return
        self._run_restore_worker(creds, self.backup, execute=False)

    def action_restore_execute(self) -> None:
        if self.busy:
            return
        creds = self._require_credentials()
        if creds is None:
            return
        if not self.backup or not self.backup_path:
            self._log("[yellow]未找到本地备份快照，请先按 B 创建备份。[/yellow]")
            return

        missing = int((self.last_restore_summary or {}).get("total_missing_tracks", 0))
        ratings = len([r for r in self.backup.ratings if r.catalog_id])

        def on_confirm(ok: bool) -> None:
            if not ok:
                self._log("[dim]已取消歌单补回。[/dim]")
                return
            self._run_restore_worker(creds, self.backup, execute=True)  # type: ignore[arg-type]

        self.push_screen(ConfirmRestoreModal(self.backup_path, missing, ratings), on_confirm)

    @work(thread=True, exclusive=True)
    def _run_restore_worker(
        self,
        creds: Credentials,
        backup: LibraryBackup,
        *,
        execute: bool,
    ) -> None:
        self.call_from_thread(
            self._set_busy, True, "正在补回缺失歌单曲目..." if execute else "正在比对线上歌单差异..."
        )
        try:
            with self._client_factory(creds) as client:
                summary = restore_from_backup(
                    client,
                    backup,
                    dry_run=not execute,
                    on_step=lambda msg: self.call_from_thread(
                        self._log, f"  [dim]{escape(msg)}[/dim]"
                    ),
                )
            self.last_restore_summary = summary

            def update_restore_ui() -> None:
                self._render_restore_table(summary)
                self.query_one("#tabs", TabbedContent).active = "tab-restore"

            self.call_from_thread(update_restore_ui)
            if execute:
                self.call_from_thread(
                    self._log,
                    f"[green]歌单恢复完成：已补回 {summary['total_restored_tracks']} 首曲目，"
                    f"恢复 {summary['restored_ratings']} 条评分。[/green]",
                )
            else:
                self.call_from_thread(
                    self._log,
                    f"[cyan]歌单差异比对完成：共发现 {summary['total_missing_tracks']} 首缺失曲目（Dry-Run 未写入）。[/cyan]",
                )
        except Exception as exc:
            self.call_from_thread(self._log, f"[red]歌单恢复异常：{escape(str(exc))}[/red]")
        finally:
            self.call_from_thread(self._set_busy, False)


def run_tui(report_path: Path | None = None) -> None:
    """Launch the interactive Textual TUI application."""
    tui_app = AmlibmergeApp(report_path=report_path)
    tui_app.run()
