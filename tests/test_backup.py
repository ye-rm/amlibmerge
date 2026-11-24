"""Unit tests for playlist and rating backup utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from amlibmerge.backup import create_library_backup, load_backup, save_backup


class FakeClient:
    def iter_library_playlist_pages(self, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        yield {
            "data": [
                {
                    "id": "p.editable1",
                    "type": "library-playlists",
                    "attributes": {
                        "name": "My Favorites",
                        "canEdit": True,
                        "isPublic": False,
                        "description": {"standard": "Personal list"},
                    },
                },
                {
                    "id": "p.subscribed1",
                    "type": "library-playlists",
                    "attributes": {
                        "name": "Apple Curated",
                        "canEdit": False,
                        "isPublic": True,
                    },
                },
            ]
        }

    def iter_playlist_tracks_pages(
        self, playlist_id: str, *, limit: int = 100, include: str = "catalog"
    ) -> Iterator[dict[str, Any]]:
        if playlist_id == "p.editable1":
            yield {
                "data": [
                    {
                        "id": "i.songA",
                        "type": "library-songs",
                        "attributes": {
                            "name": "Track A",
                            "artistName": "Artist 1",
                            "albumName": "Album 1",
                            "playParams": {"id": "i.songA", "catalogId": "9001"},
                        },
                    }
                ]
            }
        else:
            return
            yield  # pragma: no cover

    def get_ratings(self, resource_type: str, resource_ids: list[str]) -> list[dict[str, Any]]:
        if resource_type == "library-songs" and "i.songA" in resource_ids:
            return [{"id": "i.songA", "attributes": {"value": 1}}]
        return []


def test_backup_creation_and_roundtrip(tmp_path: Path) -> None:
    client = FakeClient()
    backup = create_library_backup(client, "cn")  # type: ignore[arg-type]

    assert backup.storefront == "cn"
    assert len(backup.playlists) == 1
    assert backup.playlists[0].id == "p.editable1"
    assert len(backup.playlists[0].tracks) == 1
    assert backup.playlists[0].tracks[0].catalog_song_id == "9001"
    assert len(backup.ratings) == 1
    assert backup.ratings[0].value == 1
    assert backup.ratings[0].catalog_id == "9001"

    out_file = tmp_path / "backup.json"
    save_backup(backup, out_file)
    reloaded = load_backup(out_file)
    assert reloaded == backup
