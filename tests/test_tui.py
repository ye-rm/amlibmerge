"""Offline headless Textual Pilot tests for the interactive TUI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterator

from textual.widgets import Button, Checkbox, DataTable, Static, TabbedContent
from typer.testing import CliRunner

from amlibmerge.cli import app as cli_app
from amlibmerge.config import save_credentials
from amlibmerge.models import (
    Credentials,
    LibraryAlbum,
    LibraryBackup,
    LibraryTrack,
    PlaylistSnapshot,
    PlaylistTrackEntry,
    ScanReport,
    ScanStats,
    SplitGroup,
    SplitTier,
)
from amlibmerge.tui import AmlibmergeApp, ConfirmMergeModal


class FakeTUIClient:
    """Offline mock AppleMusicClient supporting scan, backup, merge, and restore."""

    def __init__(self, *, catalog_404_ids: set[str] | None = None) -> None:
        self.catalog_404_ids = catalog_404_ids or set()
        self.existing_albums: set[str] = {"l.g1_a", "l.g1_b", "l.g2_a", "l.g2_b"}
        self.deleted_albums: list[str] = []
        self.added_albums: list[str] = []
        self.live_playlist_tracks: dict[str, list[str]] = {"p.pl1": ["9999"]}

    def __enter__(self) -> FakeTUIClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_storefront(self) -> str:
        return "cn"

    def get_catalog_album(self, storefront: str, catalog_album_id: str) -> dict[str, Any] | None:
        if catalog_album_id in self.catalog_404_ids:
            return None
        return {"id": catalog_album_id, "attributes": {"name": "Mock Album", "trackCount": 4}}

    def get_library_album(self, library_album_id: str) -> dict[str, Any] | None:
        return {"id": library_album_id} if library_album_id in self.existing_albums else None

    def delete_library_album(self, library_album_id: str) -> int:
        self.deleted_albums.append(library_album_id)
        self.existing_albums.discard(library_album_id)
        return 204

    def delete_library_song(self, library_song_id: str) -> int:
        return 204

    def add_catalog_album_to_library(self, catalog_album_id: str) -> int:
        self.added_albums.append(catalog_album_id)
        return 202

    def check_catalog_album_in_library(self, storefront: str, catalog_album_id: str) -> list[str]:
        if catalog_album_id in self.added_albums:
            return ["l.merged_new"]
        return []

    def iter_library_playlist_pages(self, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        yield {
            "data": [
                {
                    "id": "p.pl1",
                    "type": "library-playlists",
                    "attributes": {"name": "Classical Mix", "canEdit": True, "isPublic": False},
                }
            ]
        }

    def iter_playlist_tracks_pages(
        self, playlist_id: str, *, limit: int = 100, include: str = "catalog"
    ) -> Iterator[dict[str, Any]]:
        cids = self.live_playlist_tracks.get(playlist_id, [])
        yield {
            "data": [
                {
                    "id": f"i.live_{cid}",
                    "type": "library-songs",
                    "attributes": {"name": f"Song {cid}", "playParams": {"catalogId": cid}},
                }
                for cid in cids
            ]
        }

    def get_ratings(self, resource_type: str, resource_ids: list[str]) -> list[dict[str, Any]]:
        return []

    def add_tracks_to_playlist(self, playlist_id: str, catalog_song_ids: list[str]) -> list[int]:
        self.live_playlist_tracks.setdefault(playlist_id, []).extend(catalog_song_ids)
        return [204]

    def set_song_rating(self, catalog_song_id: str, value: int) -> int:
        return 200


def _seed_workspace(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    home_dir = tmp_path / "home"
    monkeypatch.setenv("AMLIBMERGE_HOME", str(home_dir))
    monkeypatch.delenv("AM_MEDIA_USER_TOKEN", raising=False)
    save_credentials(Credentials(media_user_token="0.fake_user_token_1234567890", storefront="cn"))

    reports_dir = tmp_path / "reports"
    backups_dir = tmp_path / "backups"
    journals_dir = tmp_path / "journals"
    reports_dir.mkdir()
    backups_dir.mkdir()

    safe_group = SplitGroup(
        group_id="G001",
        tier=SplitTier.HIGH_SAME_CATALOG_ID,
        canonical_name="Mendelssohn: A Midsummer Night's Dream",
        canonical_artist="Boston Symphony Orchestra",
        target_catalog_album_id="1440722842",
        target_catalog_track_count=4,
        target_catalog_available=True,
        safe_to_merge=True,
        albums=[
            LibraryAlbum(
                id="l.g1_a",
                name="Mendelssohn: A Midsummer Night's Dream",
                track_count=3,
                catalog_id="1440722842",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.1", name="Overture", track_number=1, disc_number=1, catalog_id="101"),
                    LibraryTrack(id="i.2", name="Scherzo", track_number=2, disc_number=1, catalog_id="102"),
                    LibraryTrack(id="i.3", name="Intermezzo", track_number=3, disc_number=1, catalog_id="103"),
                ],
            ),
            LibraryAlbum(
                id="l.g1_b",
                name="Mendelssohn: A Midsummer Night's Dream",
                track_count=1,
                catalog_id="1440722842",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.4", name="Nocturne", track_number=4, disc_number=1, catalog_id="104"),
                ],
            ),
        ],
    )
    blocked_group = SplitGroup(
        group_id="G002",
        tier=SplitTier.HIGH_SAME_CATALOG_ID,
        canonical_name="Beethoven: Symphony No. 9",
        canonical_artist="Berlin Phil",
        target_catalog_album_id="1552919202",
        target_catalog_track_count=2,
        target_catalog_available=True,
        has_mixed_editions=True,
        safe_to_merge=False,
        albums=[
            LibraryAlbum(
                id="l.g2_a",
                name="Beethoven: Symphony No. 9",
                track_count=2,
                catalog_id="1552919202",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.21", name="Mvt 1", catalog_id="201"),
                    LibraryTrack(id="i.22", name="Mvt 2", catalog_id="202"),
                ],
            ),
            LibraryAlbum(
                id="l.g2_b",
                name="Beethoven: Symphony No. 9",
                track_count=2,
                catalog_id="1552919202",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.23", name="Mvt 3", catalog_id="203"),
                    LibraryTrack(id="i.24", name="Mvt 4", catalog_id="204"),
                ],
            ),
        ],
    )

    scan_report = ScanReport(
        storefront="cn",
        stats=ScanStats(
            total_library_albums=10,
            catalog_linked_albums=10,
            local_only_albums=0,
            high_confidence_groups=2,
            medium_confidence_groups=0,
            safe_to_merge_groups=1,
            blocked_groups=1,
        ),
        split_groups=[safe_group, blocked_group],
    )
    (reports_dir / "scan_20261124_120000.json").write_text(
        scan_report.model_dump_json(indent=2), encoding="utf-8"
    )

    backup = LibraryBackup(
        storefront="cn",
        playlists=[
            PlaylistSnapshot(
                id="p.pl1",
                name="Classical Mix",
                can_edit=True,
                tracks=[
                    PlaylistTrackEntry(position=0, library_song_id="i.other", catalog_song_id="9999"),
                    PlaylistTrackEntry(position=1, library_song_id="i.1", catalog_song_id="101"),
                ],
            )
        ],
    )
    (backups_dir / "backup_20261124_120000.json").write_text(
        backup.model_dump_json(indent=2), encoding="utf-8"
    )
    return reports_dir, backups_dir, journals_dir


def test_tui_loads_report_selects_safe_and_dry_runs_without_writes(tmp_path: Path, monkeypatch) -> None:
    reports_dir, backups_dir, journals_dir = _seed_workspace(tmp_path, monkeypatch)
    fake_client = FakeTUIClient()

    async def scenario() -> None:
        app = AmlibmergeApp(
            reports_dir=reports_dir,
            backups_dir=backups_dir,
            journals_dir=journals_dir,
            client_factory=lambda _: fake_client,
            poll_interval=0.0,
            settle_delay=0.0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#groups-table", DataTable)
            assert table.row_count == 2
            assert app.highlighted_group_id == "G001"

            # Select all safe groups -> only G001 should be checked, G002 stays unchecked
            await pilot.press("a")
            assert app.selected_group_ids == {"G001"}

            # Dry-run preview switches to Report tab and makes zero writes
            await pilot.press("d")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert fake_client.deleted_albums == []
            assert fake_client.added_albums == []
            assert app.last_merge_report is not None
            assert app.last_merge_report.dry_run is True
            assert [r.group_id for r in app.last_merge_report.dry_run_ready] == ["G001"]
            assert [r.group_id for r in app.last_merge_report.manual_required] == ["G002"]
            assert app.query_one("#tabs", TabbedContent).active == "tab-report"

    asyncio.run(scenario())


def test_tui_confirm_modal_executes_merge_and_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    reports_dir, backups_dir, journals_dir = _seed_workspace(tmp_path, monkeypatch)
    fake_client = FakeTUIClient()

    async def scenario() -> None:
        app = AmlibmergeApp(
            reports_dir=reports_dir,
            backups_dir=backups_dir,
            journals_dir=journals_dir,
            client_factory=lambda _: fake_client,
            poll_interval=0.0,
            settle_delay=0.0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmMergeModal)

            # Cancel first -> zero cloud writes
            await pilot.click("#btn-merge-cancel")
            await pilot.pause()
            assert fake_client.deleted_albums == []

            # Re-open modal and confirm execution
            await pilot.press("m")
            await pilot.pause()
            await pilot.click("#btn-merge-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert fake_client.deleted_albums == ["l.g1_a", "l.g1_b"]
            assert fake_client.added_albums == ["1440722842"]
            assert app.last_merge_report is not None
            assert app.last_merge_report.dry_run is False
            assert [r.group_id for r in app.last_merge_report.succeeded] == ["G001"]
            assert list(journals_dir.glob("merge_*.jsonl"))
            assert list(reports_dir.glob("merge_report_*.json"))
            assert list(backups_dir.glob("backup_pre_merge_*.json"))

    asyncio.run(scenario())


def test_tui_blocked_group_requires_explicit_force_checkbox_and_respects_404(
    tmp_path: Path, monkeypatch
) -> None:
    reports_dir, backups_dir, journals_dir = _seed_workspace(tmp_path, monkeypatch)
    # Mark G002's catalog album unavailable (404) to verify hard safety invariant even with force
    fake_client = FakeTUIClient(catalog_404_ids={"1552919202"})

    async def scenario() -> None:
        app = AmlibmergeApp(
            reports_dir=reports_dir,
            backups_dir=backups_dir,
            journals_dir=journals_dir,
            client_factory=lambda _: fake_client,
            poll_interval=0.0,
            settle_delay=0.0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            # Move cursor to G002 and toggle selection
            await pilot.press("down")
            await pilot.press("space")
            assert app.selected_group_ids == {"G002"}

            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmMergeModal)

            # Confirm button MUST be disabled until user checks force checkbox
            confirm_btn = app.screen.query_one("#btn-merge-confirm", Button)
            assert confirm_btn.disabled is True

            await pilot.click("#chk-force-blocked")
            await pilot.pause()
            assert confirm_btn.disabled is False

            await pilot.click("#btn-merge-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()

            # Hard guardrail: 404 catalog album MUST NEVER be deleted even with force=True
            assert fake_client.deleted_albums == []
            assert app.last_merge_report is not None
            assert [r.group_id for r in app.last_merge_report.manual_required] == ["G002"]
            assert "404" in (app.last_merge_report.manual_required[0].error or "")

    asyncio.run(scenario())


def test_tui_restore_preview_and_confirm_execute(tmp_path: Path, monkeypatch) -> None:
    reports_dir, backups_dir, journals_dir = _seed_workspace(tmp_path, monkeypatch)
    fake_client = FakeTUIClient()

    async def scenario() -> None:
        app = AmlibmergeApp(
            reports_dir=reports_dir,
            backups_dir=backups_dir,
            journals_dir=journals_dir,
            client_factory=lambda _: fake_client,
            poll_interval=0.0,
            settle_delay=0.0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            # Preview restore diff -> finds 1 missing track (101)
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.query_one("#tabs", TabbedContent).active == "tab-restore"
            assert app.last_restore_summary is not None
            assert app.last_restore_summary["total_missing_tracks"] == 1
            assert "101" not in fake_client.live_playlist_tracks["p.pl1"]

            # Click execute button -> confirm modal -> restores track 101
            await pilot.click("#btn-restore-exec")
            await pilot.pause()
            await pilot.click("#btn-restore-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "101" in fake_client.live_playlist_tracks["p.pl1"]
            assert app.last_restore_summary["total_restored_tracks"] == 1

    asyncio.run(scenario())


def test_cli_exposes_tui_flag_and_subcommand() -> None:
    runner = CliRunner()
    res = runner.invoke(cli_app, ["--help"])
    assert res.exit_code == 0
    assert "--tui" in res.stdout
    assert "tui" in res.stdout
