"""Split album detection engine and safety evaluation."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from amlibmerge.models import LibraryAlbum, LibraryTrack, SplitGroup, SplitTier

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Normalize album/artist names for Tier 2 matching without stripping Single/EP suffixes."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return text


def build_included_index(page_payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index top-level JSON:API `included` objects by (type, id)."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in page_payload.get("included") or []:
        if isinstance(item, dict) and item.get("type") and item.get("id"):
            index[(str(item["type"]), str(item["id"]))] = item
    return index


def parse_library_album(
    raw: dict[str, Any],
    included_index: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> LibraryAlbum:
    """Parse a raw Apple Music library-albums resource into a LibraryAlbum model."""
    attrs = raw.get("attributes") or {}
    play_params = attrs.get("playParams") or {}
    rel_catalog_data = ((raw.get("relationships") or {}).get("catalog") or {}).get("data") or []

    catalog_obj: dict[str, Any] | None = None
    catalog_id: str | None = None
    if rel_catalog_data and isinstance(rel_catalog_data[0], dict):
        catalog_obj = rel_catalog_data[0]
        raw_cid = catalog_obj.get("id")
        if raw_cid:
            catalog_id = str(raw_cid)

    if not catalog_id and play_params.get("catalogId"):
        catalog_id = str(play_params["catalogId"])

    catalog_attrs: dict[str, Any] = {}
    if catalog_obj and isinstance(catalog_obj.get("attributes"), dict):
        catalog_attrs = catalog_obj["attributes"]
    elif catalog_id and included_index:
        inc = included_index.get(("albums", catalog_id))
        if inc and isinstance(inc.get("attributes"), dict):
            catalog_attrs = inc["attributes"]

    return LibraryAlbum(
        id=str(raw.get("id", "")),
        name=str(attrs.get("name") or ""),
        artist_name=str(attrs.get("artistName") or ""),
        track_count=int(attrs.get("trackCount") or 0),
        date_added=attrs.get("dateAdded"),
        catalog_id=catalog_id,
        catalog_name=catalog_attrs.get("name"),
        catalog_artist_name=catalog_attrs.get("artistName"),
        catalog_track_count=(
            int(catalog_attrs["trackCount"])
            if catalog_attrs.get("trackCount") is not None
            else None
        ),
    )


def parse_library_track(
    raw: dict[str, Any],
    included_index: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> LibraryTrack:
    """Parse a raw library-songs/tracks resource into a LibraryTrack model."""
    attrs = raw.get("attributes") or {}
    play_params = attrs.get("playParams") or {}
    rel_catalog_data = ((raw.get("relationships") or {}).get("catalog") or {}).get("data") or []

    catalog_id: str | None = None
    if rel_catalog_data and isinstance(rel_catalog_data[0], dict) and rel_catalog_data[0].get("id"):
        catalog_id = str(rel_catalog_data[0]["id"])
    elif play_params.get("catalogId"):
        catalog_id = str(play_params["catalogId"])
    elif raw.get("type") == "songs" and raw.get("id"):
        catalog_id = str(raw["id"])

    return LibraryTrack(
        id=str(raw.get("id", "")),
        name=str(attrs.get("name") or ""),
        artist_name=str(attrs.get("artistName") or ""),
        album_name=str(attrs.get("albumName") or ""),
        track_number=(
            int(attrs["trackNumber"]) if attrs.get("trackNumber") is not None else None
        ),
        disc_number=(
            int(attrs["discNumber"]) if attrs.get("discNumber") is not None else None
        ),
        duration_ms=(
            int(attrs["durationInMillis"])
            if attrs.get("durationInMillis") is not None
            else None
        ),
        catalog_id=catalog_id,
    )


def _pick_preferred_catalog_album(albums: list[LibraryAlbum]) -> LibraryAlbum | None:
    """Choose the best catalog-linked album candidate in a split group."""
    candidates = [a for a in albums if a.catalog_id]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda a: (
            a.catalog_track_count or 0,
            a.track_count,
            a.date_added or "",
        ),
    )


def evaluate_group_safety(group: SplitGroup) -> SplitGroup:
    """Recompute warnings, local upload / mixed-edition flags, and safe_to_merge on a SplitGroup."""
    warnings: list[str] = []
    has_local_uploads = False
    has_mixed_editions = False

    if any(not a.has_catalog for a in group.albums):
        has_local_uploads = True
        warnings.append("包含无 Catalog 映射的专辑（可能是本地自传或本区已下架），默认禁止自动删除")

    distinct_track_cids: set[str] = set()
    for album in group.albums:
        if album.tracks_loaded:
            local_tracks = [t for t in album.tracks if not t.has_catalog]
            if local_tracks:
                has_local_uploads = True
                warnings.append(
                    f"专辑 {album.id} 包含 {len(local_tracks)} 首无 Catalog 映射的曲目（本地自传或本区已下架），默认禁止自动删除"
                )
            for track in album.tracks:
                if track.catalog_id:
                    distinct_track_cids.add(track.catalog_id)

    if (
        group.target_catalog_track_count
        and len(distinct_track_cids) > group.target_catalog_track_count
    ):
        has_mixed_editions = True
        warnings.append(
            f"组内包含 {len(distinct_track_cids)} 首不同曲库曲目，超过目标整专曲目数 ({group.target_catalog_track_count})，"
            "疑似混入了多个不同录音/版本；合并到单一版本可能丢失其余录音，默认拦截保护"
        )

    if not group.target_catalog_album_id:
        warnings.append("缺少可用的目标 Catalog Album ID")
    elif group.target_catalog_available is False:
        warnings.append(
            f"目标 Catalog 专辑 {group.target_catalog_album_id} 在当前区服已下架或不可用，禁止删除"
        )

    distinct_album_cids = {a.catalog_id for a in group.albums if a.catalog_id}
    if group.tier == SplitTier.MEDIUM_SAME_NAME_ARTIST:
        if len(distinct_album_cids) > 1:
            has_mixed_editions = True
            warnings.append(
                f"跨版本分裂（发现 {len(distinct_album_cids)} 个不同 Catalog ID：{', '.join(sorted(distinct_album_cids))}），"
                "请人工核对目标版本，默认拦截保护"
            )
        if group.target_catalog_track_count and all(
            a.track_count >= group.target_catalog_track_count for a in group.albums
        ):
            warnings.append("各条目曲目数均已达到完整专辑长度，可能是刻意保留的不同版本（如不同年份录音/豪华版）")

    group.has_local_uploads = has_local_uploads
    group.has_mixed_editions = has_mixed_editions
    group.warnings = warnings
    group.safe_to_merge = (
        not has_local_uploads
        and not has_mixed_editions
        and bool(group.target_catalog_album_id)
        and group.target_catalog_available is not False
    )
    return group


def detect_split_groups(
    albums: list[LibraryAlbum],
    *,
    include_weak: bool = True,
) -> list[SplitGroup]:
    """Detect Tier 1 (same Catalog ID) and Tier 2 (same normalized name+artist) split albums."""
    groups: list[SplitGroup] = []
    grouped_album_ids: set[str] = set()
    seq = 1

    # --- Tier 1: Same Catalog Album ID ---
    by_catalog_id: dict[str, list[LibraryAlbum]] = defaultdict(list)
    for album in albums:
        if album.catalog_id:
            by_catalog_id[album.catalog_id].append(album)

    for catalog_id, members in sorted(
        by_catalog_id.items(),
        key=lambda kv: (normalize_text(kv[1][0].artist_name), normalize_text(kv[1][0].name)),
    ):
        if len(members) < 2:
            continue
        sorted_members = sorted(members, key=lambda a: (a.track_count, a.id), reverse=True)
        preferred = _pick_preferred_catalog_album(sorted_members) or sorted_members[0]
        group = SplitGroup(
            group_id=f"G{seq:03d}",
            tier=SplitTier.HIGH_SAME_CATALOG_ID,
            canonical_name=preferred.catalog_name or preferred.name,
            canonical_artist=preferred.catalog_artist_name or preferred.artist_name,
            target_catalog_album_id=catalog_id,
            target_catalog_name=preferred.catalog_name or preferred.name,
            target_catalog_track_count=preferred.catalog_track_count,
            albums=sorted_members,
        )
        groups.append(evaluate_group_safety(group))
        grouped_album_ids.update(a.id for a in sorted_members)
        seq += 1

    # --- Tier 2: Same normalized (album name, artist name) across remaining albums ---
    if include_weak:
        by_name_artist: dict[tuple[str, str], list[LibraryAlbum]] = defaultdict(list)
        for album in albums:
            if album.id in grouped_album_ids:
                continue
            norm_name = normalize_text(album.name)
            norm_artist = normalize_text(album.artist_name)
            if not norm_name:
                continue
            by_name_artist[(norm_name, norm_artist)].append(album)

        for key, members in sorted(by_name_artist.items()):
            if len(members) < 2:
                continue
            # Ignore groups where EVERY album is purely local (no Apple Music catalog link at all)
            if not any(a.has_catalog for a in members):
                continue

            sorted_members = sorted(members, key=lambda a: (a.track_count, a.id), reverse=True)
            preferred = _pick_preferred_catalog_album(sorted_members)
            group = SplitGroup(
                group_id=f"G{seq:03d}",
                tier=SplitTier.MEDIUM_SAME_NAME_ARTIST,
                canonical_name=(preferred.catalog_name or preferred.name) if preferred else sorted_members[0].name,
                canonical_artist=(preferred.catalog_artist_name or preferred.artist_name)
                if preferred
                else sorted_members[0].artist_name,
                target_catalog_album_id=preferred.catalog_id if preferred else None,
                target_catalog_name=(preferred.catalog_name or preferred.name) if preferred else None,
                target_catalog_track_count=preferred.catalog_track_count if preferred else None,
                albums=sorted_members,
            )
            groups.append(evaluate_group_safety(group))
            seq += 1

    return groups
