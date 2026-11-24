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
    save_language_preference,
)
from amlibmerge.detector import evaluate_group_safety
from amlibmerge.i18n import get_language, set_language, t
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
        width: 72;
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
            yield Label(t("tui.modal.auth.title"))
            yield Label(t("tui.modal.auth.desc"))
            with Horizontal(id="auth-buttons"):
                yield Button(t("tui.modal.auth.btn_browser"), variant="primary", id="btn-auth-browser")
                yield Button(t("tui.modal.auth.btn_fresh"), variant="default", id="btn-auth-fresh")
                yield Button(t("tui.modal.auth.btn_manual"), variant="warning", id="btn-auth-manual")
                yield Button(t("common.cancel"), id="btn-auth-cancel")

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
        width: 74;
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
            yield Label(t("tui.modal.token.title"))
            yield Label(t("tui.modal.token.desc"))
            yield Input(placeholder=t("tui.modal.token.placeholder"), password=True, id="input-token")
            with Horizontal(id="manual-token-buttons"):
                yield Button(t("common.cancel"), id="btn-token-cancel")
                yield Button(t("tui.modal.token.btn_ok"), variant="primary", id="btn-token-ok")

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
        width: 84;
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
            yield Label(t("tui.modal.merge.title", count=len(self.groups)))
            lines: list[str] = []
            for g in self.groups:
                old_ids = ", ".join(a.id for a in g.albums)
                tag = t("tui.modal.merge.tag_safe") if g.safe_to_merge else t("tui.modal.merge.tag_blocked")
                lines.append(
                    t(
                        "tui.modal.merge.item",
                        gid=g.group_id,
                        name=escape(g.canonical_name),
                        tag=tag,
                        old_ids=old_ids,
                        cid=g.target_catalog_album_id or t("common.none"),
                    )
                )
            with VerticalScroll(id="confirm-merge-list"):
                yield Static("\n".join(lines))

            yield Label(t("tui.modal.merge.note"))

            if self.blocked_groups:
                blocked_ids = ", ".join(g.group_id for g in self.blocked_groups)
                yield Checkbox(
                    t("tui.modal.merge.force_chk", ids=blocked_ids),
                    value=False,
                    id="chk-force-blocked",
                )

            if len(self.groups) == 1:
                yield Input(
                    value=self.groups[0].target_catalog_album_id or "",
                    placeholder=t("tui.modal.merge.cid_placeholder"),
                    id="input-override-cid",
                )

            yield Label("", id="confirm-merge-error")

            with Horizontal(id="confirm-merge-buttons"):
                yield Button(t("common.cancel"), id="btn-merge-cancel")
                yield Button(
                    t("tui.modal.merge.btn_confirm"),
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
                    t("tui.modal.merge.err_need_force")
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
            yield Label(t("tui.modal.restore.title"))
            yield Label(
                t(
                    "tui.modal.restore.body",
                    path=escape(str(self.backup_path)),
                    missing=self.missing_tracks,
                    ratings=self.ratings_count,
                )
            )
            with Horizontal(id="confirm-restore-buttons"):
                yield Button(t("common.cancel"), id="btn-restore-cancel")
                yield Button(
                    t("tui.modal.restore.btn_confirm"),
                    variant="primary",
                    id="btn-restore-confirm",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-restore-confirm")


class AmlibmergeApp(App[None]):
    """Interactive Textual TUI for Apple Music split album scanning, backup, merging, and restore."""

    TITLE = f"amlibmerge {__version__}"
    SUB_TITLE = t("tui.subtitle")

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
        Binding("l", "login", t("tui.bind.login")),
        Binding("s", "scan", t("tui.bind.scan")),
        Binding("b", "backup", t("tui.bind.backup")),
        Binding("space", "toggle_select", t("tui.bind.toggle")),
        Binding("a", "select_safe", t("tui.bind.select_safe")),
        Binding("n", "clear_selection", t("tui.bind.clear")),
        Binding("d", "dry_run", t("tui.bind.dry_run")),
        Binding("m", "execute_merge", t("tui.bind.merge")),
        Binding("r", "restore_preview", t("tui.bind.restore")),
        Binding("g", "toggle_language", t("tui.bind.lang")),
        Binding("q", "quit", t("tui.bind.quit")),
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
        self.sub_title = t("tui.subtitle")
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
            yield Button(t("tui.btn.login"), id="btn-login", variant="default")
            yield Button(t("tui.btn.scan"), id="btn-scan", variant="primary")
            yield Button(t("tui.btn.backup"), id="btn-backup", variant="default")
            yield Button(t("tui.btn.select_safe"), id="btn-select-safe", variant="default")
            yield Button(t("tui.btn.dry_run"), id="btn-dry-run", variant="warning")
            yield Button(t("tui.btn.merge"), id="btn-merge", variant="error")
            yield Button(t("tui.btn.restore"), id="btn-restore", variant="default")
            yield Button(t("tui.btn.lang"), id="btn-lang", variant="default")

        with Horizontal(id="main-split"):
            with Vertical(id="left-pane"):
                with TabbedContent(initial="tab-groups", id="tabs"):
                    with TabPane(t("tui.tab.groups"), id="tab-groups"):
                        yield DataTable(id="groups-table", cursor_type="row")
                    with TabPane(t("tui.tab.report"), id="tab-report"):
                        with VerticalScroll(id="report-scroll"):
                            yield Static(t("tui.empty_report"), id="report-view")
                    with TabPane(t("tui.tab.restore"), id="tab-restore"):
                        with Vertical(id="restore-pane-inner"):
                            with Horizontal(id="restore-actions"):
                                yield Button(
                                    t("tui.btn.restore_diff"),
                                    id="btn-restore-diff",
                                    variant="primary",
                                )
                                yield Button(
                                    t("tui.btn.restore_exec"),
                                    id="btn-restore-exec",
                                    variant="warning",
                                )
                            yield DataTable(id="restore-table", cursor_type="row")

            with VerticalScroll(id="right-pane"):
                yield Static(t("tui.empty_detail"), id="detail-view")

        yield RichLog(id="log-panel", markup=True, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._init_tables()
        self._load_local_state()
        self._refresh_status_bar()
        self._populate_groups_table()
        self.query_one("#groups-table", DataTable).focus()
        self._log(t("tui.welcome"))

    def _init_tables(self) -> None:
        gt = self.query_one("#groups-table", DataTable)
        gt.clear(columns=True)
        gt.add_column(t("tui.col.sel"), key="sel", width=4)
        gt.add_column(t("tui.col.gid"), key="gid", width=6)
        gt.add_column(t("tui.col.tier"), key="tier", width=11)
        gt.add_column(t("tui.col.album"), key="album")
        gt.add_column(t("tui.col.tracks"), key="tracks", width=16)
        gt.add_column(t("tui.col.cid"), key="cid", width=12)
        gt.add_column(t("tui.col.status"), key="status", width=10)

        rt = self.query_one("#restore-table", DataTable)
        rt.clear(columns=True)
        rt.add_column(t("tui.col.pid"), key="pid", width=18)
        rt.add_column(t("tui.col.pname"), key="name")
        rt.add_column(t("tui.col.pbk"), key="bk", width=10)
        rt.add_column(t("tui.col.pmiss"), key="miss", width=10)
        rt.add_column(t("tui.col.pdone"), key="done", width=10)

    def _refresh_all_i18n_widgets(self) -> None:
        self.sub_title = t("tui.subtitle")
        btn_labels = {
            "#btn-login": t("tui.btn.login"),
            "#btn-scan": t("tui.btn.scan"),
            "#btn-backup": t("tui.btn.backup"),
            "#btn-select-safe": t("tui.btn.select_safe"),
            "#btn-dry-run": t("tui.btn.dry_run"),
            "#btn-merge": t("tui.btn.merge"),
            "#btn-restore": t("tui.btn.restore"),
            "#btn-lang": t("tui.btn.lang"),
            "#btn-restore-diff": t("tui.btn.restore_diff"),
            "#btn-restore-exec": t("tui.btn.restore_exec"),
        }
        for selector, label in btn_labels.items():
            self.query_one(selector, Button).label = label

        tabs = self.query_one("#tabs", TabbedContent)
        for tab_id, key in (
            ("tab-groups", "tui.tab.groups"),
            ("tab-report", "tui.tab.report"),
            ("tab-restore", "tui.tab.restore"),
        ):
            try:
                tabs.get_tab(tab_id).label = t(key)
            except Exception:
                pass

        if self.scan_report:
            for g in self.scan_report.split_groups:
                evaluate_group_safety(g)

        prev_highlighted = self.highlighted_group_id
        self._init_tables()
        self._populate_groups_table(preserve_highlight=prev_highlighted)
        if self.last_merge_report:
            self._render_merge_report_tab(self.last_merge_report)
        else:
            self.query_one("#report-view", Static).update(t("tui.empty_report"))
        if self.last_restore_summary:
            self._render_restore_table(self.last_restore_summary)
        self._refresh_status_bar()

    def _load_local_state(self) -> None:
        self.credentials = load_credentials()

        bk_path = find_latest_file(self.backups_dir, "backup_*.json")
        if bk_path and bk_path.exists():
            try:
                self.backup = load_backup(bk_path)
                self.backup_path = bk_path
            except Exception as exc:
                self._log(t("tui.log.load_backup_err", err=escape(str(exc))))

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
                    t(
                        "tui.log.loaded_report",
                        path=escape(str(rpt_path)),
                        count=len(report.split_groups),
                    )
                )
            except Exception as exc:
                self._log(t("tui.log.load_report_err", err=escape(str(exc))))

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
            sf = self.credentials.storefront or t("common.unknown")
            auth_part = t(
                "tui.status.logged_in",
                sf=escape(sf),
                tok=escape(mask_token(self.credentials.media_user_token)),
            )
        else:
            auth_part = t("tui.status.logged_out")

        rpt_part = (
            t("tui.status.report", name=escape(self.scan_report_path.name))
            if self.scan_report_path
            else t("tui.status.report_none")
        )
        bk_part = (
            t("tui.status.backup", name=escape(self.backup_path.name))
            if self.backup_path
            else t("tui.status.backup_none")
        )
        sel_part = t("tui.status.selected", count=len(self.selected_group_ids))
        busy_part = f"   [bold yellow]⏳ {escape(busy_note)}[/bold yellow]" if busy_note else ""

        self.query_one("#status-bar", Static).update(
            f"{auth_part}  │  {rpt_part}  │  {bk_part}  │  {sel_part}{busy_part}"
        )

    def _populate_groups_table(self, *, preserve_highlight: str | None = None) -> None:
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
                Text(t("tui.tier_high"), style="magenta")
                if g.tier == SplitTier.HIGH_SAME_CATALOG_ID
                else Text(t("tui.tier_medium"), style="blue")
            )
            album_label = g.canonical_name
            if g.canonical_artist:
                album_label = f"{g.canonical_name} — {g.canonical_artist}"
            counts = "+".join(str(a.track_count) for a in g.albums)
            if g.target_catalog_track_count:
                counts += f"/{g.target_catalog_track_count}"
            status_text = (
                Text(t("tui.status_safe"), style="bold green")
                if g.safe_to_merge
                else Text(t("tui.status_blocked"), style="bold red")
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

        target_hl = (
            preserve_highlight
            if preserve_highlight and preserve_highlight in self.groups_by_id
            else self.scan_report.split_groups[0].group_id
        )
        self.highlighted_group_id = target_hl
        self._render_detail_panel(self.groups_by_id.get(target_hl))
        self._refresh_status_bar()

    def _render_detail_panel(self, group: SplitGroup | None) -> None:
        view = self.query_one("#detail-view", Static)
        if group is None:
            view.update(t("tui.empty_detail_none"))
            return

        status_badge = (
            t("tui.detail.badge_safe") if group.safe_to_merge else t("tui.detail.badge_blocked")
        )
        avail_str = (
            t("tui.detail.avail_ok")
            if group.target_catalog_available is True
            else t("tui.detail.avail_404")
            if group.target_catalog_available is False
            else t("tui.detail.avail_unk")
        )
        tier_label = (
            t("tui.detail.tier_high")
            if group.tier == SplitTier.HIGH_SAME_CATALOG_ID
            else t("tui.detail.tier_med")
        )

        lines = [
            f"[bold cyan]{group.group_id}[/bold cyan]  {status_badge}",
            f"[bold]{escape(group.canonical_name)}[/bold]",
            f"[dim]{escape(group.canonical_artist)}[/dim]",
            t("tui.detail.tier_line", tier=tier_label),
            t(
                "tui.detail.target_cid",
                cid=escape(group.target_catalog_album_id or "-"),
                avail=avail_str,
            ),
            t("tui.detail.target_tc", count=group.target_catalog_track_count or t("common.unknown")),
        ]

        affected_playlists, affected_ratings = compute_affected_items(group, self.backup)
        if affected_playlists:
            pl_names = {p.id: p.name for p in (self.backup.playlists if self.backup else [])}
            pl_desc = ", ".join(
                f"{escape(pl_names.get(pid, pid))}({len(cids)})"
                for pid, cids in affected_playlists.items()
            )
            lines.append(t("tui.detail.affected_pl", desc=pl_desc))
        else:
            lines.append(t("tui.detail.affected_pl_none"))
        if affected_ratings:
            lines.append(t("tui.detail.affected_ratings", count=len(affected_ratings)))

        if group.warnings:
            lines.append(t("tui.detail.warnings_hdr"))
            for w in group.warnings:
                lines.append(f"  [yellow]• {escape(w)}[/yellow]")

        lines.append(t("tui.detail.shells_hdr", count=len(group.albums)))
        for album in group.albums:
            date_str = (album.date_added or "")[:10] or t("tui.detail.unknown_date")
            cid_str = album.catalog_id or t("tui.detail.no_catalog")
            lines.append(
                t(
                    "tui.detail.shell_line",
                    aid=escape(album.id),
                    tc=album.track_count,
                    cid=escape(cid_str),
                    date=escape(date_str),
                )
            )
            if not album.tracks:
                lines.append(t("tui.detail.empty_shell"))
            else:
                for track in album.tracks[:25]:
                    dnum = f"D{track.disc_number}" if track.disc_number else ""
                    tnum = f"T{track.track_number:02d}" if track.track_number else ""
                    pos = f"{dnum}{tnum}".strip() or "--"
                    if track.catalog_id:
                        tcid = f"[dim][{escape(track.catalog_id)}][/dim]"
                    else:
                        tcid = f"[bold yellow][{escape(t('tui.detail.no_catalog'))}][/bold yellow]"
                    lines.append(f"    {pos} {escape(track.name)} {tcid}")
                if len(album.tracks) > 25:
                    lines.append(t("tui.detail.more_tracks", count=len(album.tracks) - 25))

        view.update("\n".join(lines))

    def _render_merge_report_tab(self, report: MergeReport) -> None:
        mode_str = t("tui.report.mode_dry") if report.dry_run else t("tui.report.mode_exec")
        parts: list[str] = [
            t(
                "tui.report.header",
                mode=mode_str,
                sf=escape(report.storefront),
                ok=len(report.succeeded),
                dry=len(report.dry_run_ready),
                manual=len(report.manual_required),
            )
        ]

        if report.succeeded:
            parts.append(t("tui.report.sec_ok"))
            for r in report.succeeded:
                new_ids = ", ".join(r.new_library_album_ids) or "-"
                pl_info = (
                    t("tui.report.item_ok_pl", info=r.restored_playlist_tracks)
                    if r.restored_playlist_tracks
                    else ""
                )
                parts.append(
                    t(
                        "tui.report.item_ok",
                        gid=r.group_id,
                        name=escape(r.canonical_name),
                        new_ids=escape(new_ids),
                        cid=escape(r.target_catalog_album_id),
                        pl_info=escape(pl_info),
                    )
                )

        if report.dry_run_ready:
            parts.append(t("tui.report.sec_dry"))
            for r in report.dry_run_ready:
                pl_cnt = sum(len(v) for v in r.affected_playlists.values())
                pl_note = (
                    t("tui.report.item_dry_pl", cnt=pl_cnt)
                    if pl_cnt
                    else t("tui.report.item_dry_no_pl")
                )
                parts.append(
                    t(
                        "tui.report.item_dry",
                        gid=r.group_id,
                        name=escape(r.canonical_name),
                        cid=escape(r.target_catalog_album_id),
                        pl_note=pl_note,
                    )
                )

        if report.manual_required:
            parts.append(t("tui.report.sec_manual"))
            stage_map = {
                "still_split": t("tui.report.stage_still_split"),
                "skipped": t("tui.report.stage_skipped"),
                "failed": t("tui.report.stage_failed"),
            }
            for r in report.manual_required:
                stage_label = stage_map.get(r.stage, r.stage)
                parts.append(
                    f"  • [cyan]{r.group_id}[/cyan] [bold]{escape(r.canonical_name)}[/bold] [{stage_label}]"
                )
                if r.error:
                    parts.append(t("tui.report.item_reason", reason=escape(r.error)))
                if r.manual_action_hint:
                    parts.append(t("tui.report.item_advice", hint=escape(r.manual_action_hint)))

        if report.journal_path:
            parts.append(t("tui.report.wal", path=escape(report.journal_path)))

        self.query_one("#report-view", Static).update(
            Panel("\n".join(parts), title=t("tui.report.panel_title"), border_style="cyan")
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
            self._log(t("tui.log.need_login"))
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
            "btn-lang": self.action_toggle_language,
            "btn-restore-diff": self.action_restore_preview,
            "btn-restore-exec": self.action_restore_execute,
        }
        fn = handlers.get(event.button.id or "")
        if fn:
            fn()

    # --- Language & Selection Actions ---

    def action_toggle_language(self) -> None:
        next_lang = "en" if get_language() == "zh" else "zh"
        set_language(next_lang)
        save_language_preference(next_lang)
        self._refresh_all_i18n_widgets()
        self._log(t("tui.log.lang_switched"))

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
            self._log(t("tui.log.need_scan"))
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
        self._log(t("tui.log.selected_safe", count=len(self.selected_group_ids)))

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
        self.call_from_thread(self._set_busy, True, t("tui.busy.login"))
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
                t("tui.log.login_ok", sf=escape(sf), tok=escape(mask_token(tok))),
            )
        except BrowserAuthError as exc:
            self.call_from_thread(self._log, t("tui.log.login_fail", err=escape(str(exc))))
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.login_err", err=escape(str(exc))))
        finally:
            self.call_from_thread(self._set_busy, False)

    @work(thread=True, exclusive=True)
    def _run_manual_login(self, token: str) -> None:
        self.call_from_thread(self._set_busy, True, t("tui.busy.manual"))
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
                dev_exp = getattr(client, "developer_token_exp", dev_exp) or get_jwt_exp(
                    dev_tok or ""
                )
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
                t("tui.log.manual_ok", sf=escape(sf), tok=escape(mask_token(token))),
            )
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.manual_fail", err=escape(str(exc))))
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
        self.call_from_thread(self._set_busy, True, t("tui.busy.scan"))
        self.call_from_thread(self._log, t("tui.log.scan_start"))
        try:
            with self._client_factory(creds) as client:
                sf = creds.storefront or client.get_storefront()

                def on_page(page_num: int, total_albums: int) -> None:
                    self.call_from_thread(
                        self._log, t("tui.log.scan_page", page=page_num, total=total_albums)
                    )

                def on_enrich(idx: int, total: int, group: SplitGroup) -> None:
                    self.call_from_thread(
                        self._log,
                        t(
                            "tui.log.scan_enrich",
                            idx=idx,
                            total=total,
                            name=escape(group.canonical_name),
                        ),
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
                t(
                    "tui.log.scan_done",
                    total=report.stats.total_library_albums,
                    splits=len(report.split_groups),
                    safe=report.stats.safe_to_merge_groups,
                    path=escape(str(out_path)),
                ),
            )
        except (AppleMusicAuthError, AppleMusicAPIError) as exc:
            self.call_from_thread(self._log, t("tui.log.scan_fail", err=escape(str(exc))))
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.scan_fail", err=escape(str(exc))))
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
        self.call_from_thread(self._set_busy, True, t("tui.busy.backup"))
        self.call_from_thread(self._log, t("tui.log.backup_start"))
        try:
            with self._client_factory(creds) as client:
                sf = creds.storefront or client.get_storefront()
                bk = create_library_backup(
                    client,
                    sf,
                    on_playlist=lambda i, n, name: self.call_from_thread(
                        self._log, t("tui.log.backup_pl", idx=i, total=n, name=escape(name))
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
                t(
                    "tui.log.backup_done",
                    pls=len(bk.playlists),
                    tracks=bk.metadata.get("total_playlist_tracks", 0),
                    path=escape(str(out_path)),
                ),
            )
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.backup_fail", err=escape(str(exc))))
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
            self._log(t("tui.log.need_scan"))
            return
        creds = self._require_credentials()
        if creds is None:
            return
        selected = self._get_selected_groups_ordered()
        if not selected:
            selected = list(self.scan_report.split_groups)
            self._log(t("tui.log.dry_default_all"))
        self._run_merge_worker(creds, selected, execute=False, force=False, override_cid=None)

    def action_execute_merge(self) -> None:
        if self.busy:
            return
        if not self.scan_report:
            self._log(t("tui.log.need_scan"))
            return
        creds = self._require_credentials()
        if creds is None:
            return
        selected = self._get_selected_groups_ordered()
        if not selected:
            self._log(t("tui.log.select_before_merge"))
            return

        def on_confirm(decision: dict[str, Any] | None) -> None:
            if decision is None:
                self._log(t("tui.log.merge_cancelled"))
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
        mode_label = t("tui.busy.merge_exec") if execute else t("tui.busy.merge_dry")
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

                if execute:
                    self.call_from_thread(self._log, t("tui.log.pre_backup_start"))
                    active_backup = create_library_backup(client, storefront)
                    bk_path = self.backups_dir / f"backup_pre_merge_{_timestamp_slug()}.json"
                    save_backup(active_backup, bk_path)
                    self.backup = active_backup
                    self.backup_path = bk_path
                    self.call_from_thread(
                        self._log, t("tui.log.pre_backup_done", path=escape(str(bk_path)))
                    )

                for g in selected:
                    if not g.safe_to_merge and not force:
                        results.append(
                            make_skipped_result(
                                g,
                                dry_run=not execute,
                                reason=t("merger.reason_auto_skipped"),
                            )
                        )
                        continue

                    self.call_from_thread(
                        self._log,
                        t("tui.log.merge_group", gid=g.group_id, name=escape(g.canonical_name)),
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

            if self.scan_report and not execute:
                processed_ids = {r.group_id for r in results}
                for g in self.scan_report.split_groups:
                    if g.group_id not in processed_ids and not g.safe_to_merge:
                        results.append(
                            make_skipped_result(
                                g,
                                dry_run=True,
                                reason=t("merger.reason_auto_skipped_dry"),
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
                    t(
                        "tui.log.merge_done",
                        ok=len(report.succeeded),
                        manual=len(report.manual_required),
                        path=escape(str(out_path)),
                    ),
                )
            else:
                self.call_from_thread(
                    self._log,
                    t(
                        "tui.log.dry_done",
                        ready=len(report.dry_run_ready),
                        manual=len(report.manual_required),
                    ),
                )

            def show_report() -> None:
                self._render_merge_report_tab(report)
                self.query_one("#tabs", TabbedContent).active = "tab-report"

            self.call_from_thread(show_report)
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.merge_err", err=escape(str(exc))))
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
            self._log(t("tui.log.need_backup"))
            return
        self._run_restore_worker(creds, self.backup, execute=False)

    def action_restore_execute(self) -> None:
        if self.busy:
            return
        creds = self._require_credentials()
        if creds is None:
            return
        if not self.backup or not self.backup_path:
            self._log(t("tui.log.need_backup"))
            return

        missing = int((self.last_restore_summary or {}).get("total_missing_tracks", 0))
        ratings = len([r for r in self.backup.ratings if r.catalog_id])

        def on_confirm(ok: bool) -> None:
            if not ok:
                self._log(t("tui.log.restore_cancelled"))
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
            self._set_busy, True, t("tui.busy.restore_exec") if execute else t("tui.busy.restore_dry")
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
                    t(
                        "tui.log.restore_done",
                        tracks=summary["total_restored_tracks"],
                        ratings=summary["restored_ratings"],
                    ),
                )
            else:
                self.call_from_thread(
                    self._log,
                    t("tui.log.restore_dry_done", missing=summary["total_missing_tracks"]),
                )
        except Exception as exc:
            self.call_from_thread(self._log, t("tui.log.restore_err", err=escape(str(exc))))
        finally:
            self.call_from_thread(self._set_busy, False)


def run_tui(report_path: Path | None = None) -> None:
    """Launch the interactive Textual TUI application."""
    tui_app = AmlibmergeApp(report_path=report_path)
    tui_app.run()
