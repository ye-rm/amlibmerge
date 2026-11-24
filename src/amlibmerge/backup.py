"""Playlist and rating snapshot backup utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from amlibmerge.client import AppleMusicClient
from amlibmerge.detector import build_included_index, parse_library_track
from amlibmerge.models import (
    LibraryBackup,
    PlaylistSnapshot,
    PlaylistTrackEntry,
    RatingEntry,
)


def create_library_backup(
    client: AppleMusicClient,
    storefront: str,
    *,
    include_non_editable_playlists: bool = False,
    include_ratings: bool = True,
    on_playlist: Callable[[int, int, str], None] | None = None,
) -> LibraryBackup:
    """Snapshot user playlists (with catalog IDs) and optionally library song/album ratings."""
    raw_playlists: list[dict] = []
    for page in client.iter_library_playlist_pages(limit=100):
        for item in page.get("data") or []:
            if isinstance(item, dict):
                raw_playlists.append(item)

    filtered_playlists = [
        p
        for p in raw_playlists
        if include_non_editable_playlists or bool((p.get("attributes") or {}).get("canEdit"))
    ]

    snapshots: list[PlaylistSnapshot] = []
    all_library_song_ids: dict[str, str | None] = {}

    for idx, raw_pl in enumerate(filtered_playlists, start=1):
        pl_id = str(raw_pl.get("id", ""))
        attrs = raw_pl.get("attributes") or {}
        pl_name = str(attrs.get("name") or "")
        desc_obj = attrs.get("description")
        description = (
            str(desc_obj.get("standard") or "")
            if isinstance(desc_obj, dict)
            else str(desc_obj or "")
        )

        tracks: list[PlaylistTrackEntry] = []
        position = 0
        for t_page in client.iter_playlist_tracks_pages(pl_id, limit=100, include="catalog"):
            t_included = build_included_index(t_page)
            for raw_track in t_page.get("data") or []:
                if not isinstance(raw_track, dict):
                    continue
                parsed = parse_library_track(raw_track, t_included)
                res_type = str(raw_track.get("type") or "library-songs")
                lib_song_id = parsed.id if res_type.startswith("library-") else None
                tracks.append(
                    PlaylistTrackEntry(
                        position=position,
                        library_song_id=lib_song_id,
                        catalog_song_id=parsed.catalog_id,
                        name=parsed.name,
                        artist_name=parsed.artist_name,
                        album_name=parsed.album_name,
                        resource_type=res_type,
                    )
                )
                if lib_song_id:
                    all_library_song_ids[lib_song_id] = parsed.catalog_id
                position += 1

        snapshots.append(
            PlaylistSnapshot(
                id=pl_id,
                name=pl_name,
                can_edit=bool(attrs.get("canEdit")),
                is_public=bool(attrs.get("isPublic")),
                description=description,
                date_added=attrs.get("dateAdded"),
                tracks=tracks,
            )
        )
        if on_playlist:
            on_playlist(idx, len(filtered_playlists), pl_name)

    ratings: list[RatingEntry] = []
    if include_ratings and all_library_song_ids:
        raw_ratings = client.get_ratings("library-songs", list(all_library_song_ids.keys()))
        for r in raw_ratings:
            rid = str(r.get("id", ""))
            val = (r.get("attributes") or {}).get("value")
            if rid and isinstance(val, int):
                ratings.append(
                    RatingEntry(
                        resource_type="library-songs",
                        resource_id=rid,
                        catalog_id=all_library_song_ids.get(rid),
                        value=val,
                    )
                )

    return LibraryBackup(
        storefront=storefront,
        playlists=snapshots,
        ratings=ratings,
        metadata={
            "total_playlists_seen": len(raw_playlists),
            "playlists_backed_up": len(snapshots),
            "total_playlist_tracks": sum(len(p.tracks) for p in snapshots),
            "ratings_backed_up": len(ratings),
        },
    )


def save_backup(backup: LibraryBackup, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(backup.model_dump_json(indent=2), encoding="utf-8")
    tmp_path.replace(output_path)
    return output_path


def load_backup(input_path: Path) -> LibraryBackup:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    return LibraryBackup.model_validate(raw)
