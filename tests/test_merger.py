"""Unit tests for Phase 2 split album merger, WAL journal, and idempotent playlist restore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from amlibmerge.client import AppleMusicAPIError
from amlibmerge.merger import (
    build_merge_report,
    make_skipped_result,
    merge_split_group,
    restore_from_backup,
    save_merge_report,
)
from amlibmerge.models import (
    LibraryAlbum,
    LibraryBackup,
    LibraryTrack,
    PlaylistSnapshot,
    PlaylistTrackEntry,
    RatingEntry,
    SplitGroup,
    SplitTier,
)


class FakeMergeClient:
    def __init__(self, *, catalog_available: bool = True, fail_album_delete: bool = False) -> None:
        self.catalog_available = catalog_available
        self.fail_album_delete = fail_album_delete
        self.existing_library_albums: set[str] = {"l.part1", "l.part2"}
        self.deleted_albums: list[str] = []
        self.deleted_songs: list[str] = []
        self.added_catalog_albums: list[str] = []
        self.live_playlist_tracks: dict[str, list[str]] = {"p.pl1": ["9999"]}
        self.set_ratings: list[tuple[str, int]] = []

    def get_catalog_album(self, storefront: str, catalog_album_id: str) -> dict[str, Any] | None:
        if not self.catalog_available:
            return None
        return {"id": catalog_album_id, "attributes": {"name": "Mahler 4", "trackCount": 4}}

    def delete_library_album(self, library_album_id: str) -> int:
        if self.fail_album_delete:
            raise AppleMusicAPIError(405, "Method Not Allowed", f"/v1/me/library/albums/{library_album_id}")
        self.deleted_albums.append(library_album_id)
        self.existing_library_albums.discard(library_album_id)
        return 204

    def delete_library_song(self, library_song_id: str) -> int:
        self.deleted_songs.append(library_song_id)
        if len(self.deleted_songs) >= 4:
            self.existing_library_albums.clear()
        return 204

    def get_library_album(self, library_album_id: str) -> dict[str, Any] | None:
        if library_album_id in self.existing_library_albums:
            return {"id": library_album_id}
        return None

    def add_catalog_album_to_library(self, catalog_album_id: str) -> int:
        self.added_catalog_albums.append(catalog_album_id)
        self.existing_library_albums.add("l.fresh_merged")
        return 202

    def check_catalog_album_in_library(self, storefront: str, catalog_album_id: str) -> list[str]:
        if catalog_album_id in self.added_catalog_albums:
            return ["l.fresh_merged"]
        return []

    def iter_playlist_tracks_pages(
        self, playlist_id: str, *, limit: int = 100, include: str = "catalog"
    ) -> Iterator[dict[str, Any]]:
        cids = self.live_playlist_tracks.get(playlist_id, [])
        yield {
            "data": [
                {
                    "id": f"i.live_{cid}",
                    "type": "library-songs",
                    "attributes": {"playParams": {"catalogId": cid}},
                }
                for cid in cids
            ]
        }

    def add_tracks_to_playlist(self, playlist_id: str, catalog_song_ids: list[str]) -> list[int]:
        self.live_playlist_tracks.setdefault(playlist_id, []).extend(catalog_song_ids)
        return [204]

    def set_song_rating(self, catalog_song_id: str, value: int) -> int:
        self.set_ratings.append((catalog_song_id, value))
        return 200


def _make_group() -> SplitGroup:
    return SplitGroup(
        group_id="G004",
        tier=SplitTier.HIGH_SAME_CATALOG_ID,
        canonical_name="Mahler: Symphony No. 4",
        canonical_artist="Berlin Phil",
        target_catalog_album_id="1571313404",
        target_catalog_track_count=4,
        target_catalog_available=True,
        safe_to_merge=True,
        albums=[
            LibraryAlbum(
                id="l.part1",
                name="Mahler: Symphony No. 4",
                track_count=2,
                catalog_id="1571313404",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.t1", name="I", catalog_id="1571313408"),
                    LibraryTrack(id="i.t4", name="IV", catalog_id="1571313420"),
                ],
            ),
            LibraryAlbum(
                id="l.part2",
                name="Mahler: Symphony No. 4",
                track_count=2,
                catalog_id="1571313404",
                tracks_loaded=True,
                tracks=[
                    LibraryTrack(id="i.t2", name="II", catalog_id="1571313411"),
                    LibraryTrack(id="i.t3", name="III", catalog_id="1571313414"),
                ],
            ),
        ],
    )


def _make_backup() -> LibraryBackup:
    return LibraryBackup(
        storefront="cn",
        playlists=[
            PlaylistSnapshot(
                id="p.pl1",
                name="Classical Mix",
                can_edit=True,
                tracks=[
                    PlaylistTrackEntry(position=0, library_song_id="i.other", catalog_song_id="9999"),
                    PlaylistTrackEntry(position=1, library_song_id="i.t1", catalog_song_id="1571313408"),
                    PlaylistTrackEntry(position=2, library_song_id="i.t2", catalog_song_id="1571313411"),
                ],
            )
        ],
        ratings=[
            RatingEntry(
                resource_type="library-songs",
                resource_id="i.t1",
                catalog_id="1571313408",
                value=1,
            )
        ],
    )


def test_merge_dry_run_makes_no_writes() -> None:
    client = FakeMergeClient()
    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        backup=_make_backup(),
        dry_run=True,
    )
    assert res.stage == "dry_run"
    assert res.affected_playlists == {"p.pl1": ["1571313408", "1571313411"]}
    assert client.deleted_albums == []
    assert client.added_catalog_albums == []


def test_merge_execute_full_pipeline_and_wal(tmp_path: Path) -> None:
    client = FakeMergeClient()
    journal = tmp_path / "merge.jsonl"

    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        backup=_make_backup(),
        dry_run=False,
        journal_path=journal,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )

    assert res.stage == "restored"
    assert client.deleted_albums == ["l.part1", "l.part2"]
    assert client.added_catalog_albums == ["1571313404"]
    assert res.new_library_album_ids == ["l.fresh_merged"]
    assert res.restored_playlist_tracks == {"p.pl1": 2}
    assert set(client.live_playlist_tracks["p.pl1"]) == {"9999", "1571313408", "1571313411"}
    assert client.set_ratings == [("1571313408", 1)]

    lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [entry["stage"] for entry in lines] == ["planned", "deleted", "readded", "restored"]


def test_merge_falls_back_to_song_delete_when_album_delete_fails() -> None:
    client = FakeMergeClient(fail_album_delete=True)
    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        dry_run=False,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )
    assert res.stage == "restored"
    assert set(client.deleted_songs) == {"i.t1", "i.t2", "i.t3", "i.t4"}
    assert client.added_catalog_albums == ["1571313404"]


def test_merge_cleans_empty_track_shell_via_group_wide_song_delete() -> None:
    """Reproduces G001 where sibling shell l.part2 has 0 tracks loaded and survives album delete."""

    class StickyShellClient(FakeMergeClient):
        def delete_library_album(self, library_album_id: str) -> int:
            self.deleted_albums.append(library_album_id)
            if library_album_id == "l.part1":
                self.existing_library_albums.discard("l.part1")
            # l.part2 stays alive until group-wide song delete runs!
            return 204

    group = _make_group()
    # Put all 4 tracks on l.part1 and leave l.part2.tracks empty (exact G001 shape)
    group.albums[0].tracks.extend(group.albums[1].tracks)
    group.albums[1].tracks = []

    client = StickyShellClient()
    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        group,
        dry_run=False,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )
    assert res.stage == "restored"
    assert set(client.deleted_songs) == {"i.t1", "i.t2", "i.t3", "i.t4"}
    assert res.new_library_album_ids == ["l.fresh_merged"]


def test_merge_reports_still_split_when_catalog_itself_splits() -> None:
    class StillSplitClient(FakeMergeClient):
        def check_catalog_album_in_library(self, storefront: str, catalog_album_id: str) -> list[str]:
            return ["l.part1", "l.part2"]

    client = StillSplitClient()
    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        dry_run=False,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )
    assert res.stage == "still_split"
    assert any(
        ("官方曲库自身元数据即存在分裂" in w or "upstream Apple Music catalog metadata" in w)
        for w in res.warnings
    )


def test_merge_hard_blocks_unavailable_catalog_even_with_force() -> None:
    client = FakeMergeClient(catalog_available=False)
    res = merge_split_group(
        client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        dry_run=False,
        force=True,
    )
    assert res.stage == "skipped"
    assert "404" in (res.error or "")
    assert client.deleted_albums == []


def test_restore_from_backup_is_idempotent() -> None:
    client = FakeMergeClient()
    backup = _make_backup()

    # Dry-run sees 2 missing tracks
    dry = restore_from_backup(client, backup, dry_run=True)  # type: ignore[arg-type]
    assert dry["total_missing_tracks"] == 2
    assert dry["total_restored_tracks"] == 0

    # Execute adds the 2 missing tracks and 1 rating
    exe = restore_from_backup(client, backup, dry_run=False)  # type: ignore[arg-type]
    assert exe["total_restored_tracks"] == 2
    assert exe["restored_ratings"] == 1

    # Second run finds 0 missing tracks (idempotent!)
    again = restore_from_backup(client, backup, dry_run=False)  # type: ignore[arg-type]
    assert again["total_missing_tracks"] == 0
    assert again["total_restored_tracks"] == 0


def test_merge_report_separates_succeeded_and_manual_required(tmp_path: Path) -> None:
    ok_client = FakeMergeClient()
    ok_res = merge_split_group(
        ok_client,  # type: ignore[arg-type]
        "cn",
        _make_group(),
        dry_run=False,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )

    class StillSplitClient(FakeMergeClient):
        def check_catalog_album_in_library(self, storefront: str, catalog_album_id: str) -> list[str]:
            return ["l.part1", "l.part2"]

    split_group = _make_group()
    split_group.group_id = "G002"
    split_res = merge_split_group(
        StillSplitClient(),  # type: ignore[arg-type]
        "cn",
        split_group,
        dry_run=False,
        poll_interval=0.0,
        settle_delay=0.0,
        max_poll_attempts=2,
    )

    blocked_group = _make_group()
    blocked_group.group_id = "G006"
    blocked_group.has_mixed_editions = True
    blocked_group.safe_to_merge = False
    skipped_res = make_skipped_result(blocked_group, dry_run=False, reason="安全拦截")

    report = build_merge_report(
        [ok_res, split_res, skipped_res],
        storefront="cn",
        dry_run=False,
        source_scan_report=Path("reports/scan_test.json"),
    )
    assert [r.group_id for r in report.succeeded] == ["G004"]
    assert [r.group_id for r in report.manual_required] == ["G002", "G006"]
    assert "显示简介" in (split_res.manual_action_hint or "") or "Get Info" in (
        split_res.manual_action_hint or ""
    )
    assert "不同录音/版本" in (skipped_res.manual_action_hint or "") or "recordings/editions" in (
        skipped_res.manual_action_hint or ""
    )

    out = tmp_path / "merge_report.json"
    save_merge_report(report, out)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert len(saved["succeeded"]) == 1
    assert len(saved["manual_required"]) == 2
