"""Pydantic data models for Apple Music library scanning, detection, and backups."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Credentials(BaseModel):
    media_user_token: str
    developer_token: str | None = None
    developer_token_exp: int | None = None
    storefront: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)


class LibraryTrack(BaseModel):
    id: str
    name: str
    artist_name: str = ""
    album_name: str = ""
    track_number: int | None = None
    disc_number: int | None = None
    duration_ms: int | None = None
    catalog_id: str | None = None

    @property
    def has_catalog(self) -> bool:
        return bool(self.catalog_id)


class LibraryAlbum(BaseModel):
    id: str
    name: str
    artist_name: str = ""
    track_count: int = 0
    date_added: str | None = None
    catalog_id: str | None = None
    catalog_name: str | None = None
    catalog_artist_name: str | None = None
    catalog_track_count: int | None = None
    tracks: list[LibraryTrack] = Field(default_factory=list)
    tracks_loaded: bool = False

    @property
    def has_catalog(self) -> bool:
        return bool(self.catalog_id)


class SplitTier(str, Enum):
    HIGH_SAME_CATALOG_ID = "high_same_catalog_id"
    MEDIUM_SAME_NAME_ARTIST = "medium_same_name_artist"


class SplitGroup(BaseModel):
    group_id: str
    tier: SplitTier
    canonical_name: str
    canonical_artist: str
    target_catalog_album_id: str | None = None
    target_catalog_name: str | None = None
    target_catalog_track_count: int | None = None
    target_catalog_available: bool | None = None
    albums: list[LibraryAlbum] = Field(default_factory=list)
    has_local_uploads: bool = False
    has_mixed_editions: bool = False
    warnings: list[str] = Field(default_factory=list)
    safe_to_merge: bool = False

    @property
    def split_count(self) -> int:
        return len(self.albums)

    @property
    def total_library_tracks(self) -> int:
        return sum(a.track_count for a in self.albums)


class ScanStats(BaseModel):
    total_library_albums: int = 0
    catalog_linked_albums: int = 0
    local_only_albums: int = 0
    high_confidence_groups: int = 0
    medium_confidence_groups: int = 0
    safe_to_merge_groups: int = 0
    blocked_groups: int = 0


class ScanReport(BaseModel):
    version: str = "1"
    scanned_at: str = Field(default_factory=utc_now_iso)
    storefront: str
    stats: ScanStats
    split_groups: list[SplitGroup] = Field(default_factory=list)


class PlaylistTrackEntry(BaseModel):
    position: int
    library_song_id: str | None = None
    catalog_song_id: str | None = None
    name: str = ""
    artist_name: str = ""
    album_name: str = ""
    resource_type: str = "library-songs"


class PlaylistSnapshot(BaseModel):
    id: str
    name: str
    can_edit: bool = False
    is_public: bool = False
    description: str = ""
    date_added: str | None = None
    tracks: list[PlaylistTrackEntry] = Field(default_factory=list)


class RatingEntry(BaseModel):
    resource_type: str  # "library-songs" | "library-albums"
    resource_id: str
    catalog_id: str | None = None
    value: int  # 1 = love/favorite, -1 = dislike


class LibraryBackup(BaseModel):
    version: str = "1"
    created_at: str = Field(default_factory=utc_now_iso)
    storefront: str
    playlists: list[PlaylistSnapshot] = Field(default_factory=list)
    ratings: list[RatingEntry] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MergeJournalEntry(BaseModel):
    timestamp: str = Field(default_factory=utc_now_iso)
    group_id: str
    canonical_name: str
    target_catalog_album_id: str
    library_album_ids: list[str] = Field(default_factory=list)
    affected_playlists: dict[str, list[str]] = Field(default_factory=dict)
    affected_ratings: list[RatingEntry] = Field(default_factory=list)
    stage: str  # planned | deleted | readded | restored | failed
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MergeResult(BaseModel):
    group_id: str
    canonical_name: str
    artist_name: str = ""
    target_catalog_album_id: str
    dry_run: bool
    stage: str  # dry_run | restored | still_split | skipped | failed
    deleted_album_statuses: dict[str, int] = Field(default_factory=dict)
    deleted_song_statuses: dict[str, int] = Field(default_factory=dict)
    readd_status: int | None = None
    new_library_album_ids: list[str] = Field(default_factory=list)
    affected_playlists: dict[str, list[str]] = Field(default_factory=dict)
    restored_playlist_tracks: dict[str, int] = Field(default_factory=dict)
    restored_ratings: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    manual_action_hint: str | None = None


class MergeReport(BaseModel):
    version: str = "1"
    generated_at: str = Field(default_factory=utc_now_iso)
    storefront: str
    dry_run: bool
    source_scan_report: str
    journal_path: str | None = None
    succeeded: list[MergeResult] = Field(default_factory=list)
    manual_required: list[MergeResult] = Field(default_factory=list)
    dry_run_ready: list[MergeResult] = Field(default_factory=list)

