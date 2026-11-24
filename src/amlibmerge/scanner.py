"""Library scanner that fetches library albums, enriches split candidates, and validates catalog availability."""

from __future__ import annotations

from typing import Any, Callable

from amlibmerge.client import AppleMusicClient
from amlibmerge.detector import (
    build_included_index,
    detect_split_groups,
    evaluate_group_safety,
    parse_library_album,
    parse_library_track,
)
from amlibmerge.models import LibraryAlbum, ScanReport, ScanStats, SplitGroup, SplitTier


def scan_library(
    client: AppleMusicClient,
    storefront: str,
    *,
    include_weak: bool = True,
    inspect_tracks: bool = True,
    on_album_page: Callable[[int, int], None] | None = None,
    on_enrich_group: Callable[[int, int, SplitGroup], None] | None = None,
    raw_sample_sink: list[dict[str, Any]] | None = None,
) -> ScanReport:
    """Scan all library albums, detect split groups, and enrich candidates with tracks & catalog checks."""
    albums: list[LibraryAlbum] = []
    page_num = 0

    for page in client.iter_library_album_pages(limit=100, include="catalog"):
        page_num += 1
        included_index = build_included_index(page)
        data = page.get("data") or []
        if raw_sample_sink is not None and len(raw_sample_sink) < 3:
            for raw_item in data:
                if len(raw_sample_sink) >= 3:
                    break
                if isinstance(raw_item, dict):
                    raw_sample_sink.append(raw_item)
        for raw_item in data:
            if isinstance(raw_item, dict):
                albums.append(parse_library_album(raw_item, included_index))
        if on_album_page:
            on_album_page(page_num, len(albums))

    groups = detect_split_groups(albums, include_weak=include_weak)

    # Enrich each split candidate with track-level catalog checks & storefront availability check
    catalog_cache: dict[str, dict[str, Any] | None] = {}
    for idx, group in enumerate(groups, start=1):
        if inspect_tracks:
            for album in group.albums:
                tracks = []
                for t_page in client.get_library_album_tracks_pages(album.id, include="catalog"):
                    t_included = build_included_index(t_page)
                    for raw_track in t_page.get("data") or []:
                        if isinstance(raw_track, dict):
                            tracks.append(parse_library_track(raw_track, t_included))
                album.tracks = tracks
                album.tracks_loaded = True

        # Check all unique candidate catalog album IDs in storefront and prefer an available one
        candidate_cids: list[str] = []
        if group.target_catalog_album_id:
            candidate_cids.append(group.target_catalog_album_id)
        for album in group.albums:
            if album.catalog_id and album.catalog_id not in candidate_cids:
                candidate_cids.append(album.catalog_id)

        best_available: tuple[str, dict[str, Any]] | None = None
        for cid in candidate_cids:
            if cid not in catalog_cache:
                catalog_cache[cid] = client.get_catalog_album(storefront, cid)
            cat_obj = catalog_cache[cid]
            if cat_obj is not None:
                cat_attrs = cat_obj.get("attributes") or {}
                cat_tc = int(cat_attrs.get("trackCount") or 0)
                if best_available is None or cat_tc > int(
                    (best_available[1].get("attributes") or {}).get("trackCount") or 0
                ):
                    best_available = (cid, cat_obj)

        if best_available is not None:
            chosen_cid, cat_obj = best_available
            cat_attrs = cat_obj.get("attributes") or {}
            group.target_catalog_album_id = chosen_cid
            group.target_catalog_name = cat_attrs.get("name") or group.target_catalog_name
            if cat_attrs.get("trackCount") is not None:
                group.target_catalog_track_count = int(cat_attrs["trackCount"])
            group.target_catalog_available = True
        elif group.target_catalog_album_id:
            group.target_catalog_available = False

        evaluate_group_safety(group)
        if on_enrich_group:
            on_enrich_group(idx, len(groups), group)

    catalog_linked = sum(1 for a in albums if a.has_catalog)
    stats = ScanStats(
        total_library_albums=len(albums),
        catalog_linked_albums=catalog_linked,
        local_only_albums=len(albums) - catalog_linked,
        high_confidence_groups=sum(
            1 for g in groups if g.tier == SplitTier.HIGH_SAME_CATALOG_ID
        ),
        medium_confidence_groups=sum(
            1 for g in groups if g.tier == SplitTier.MEDIUM_SAME_NAME_ARTIST
        ),
        safe_to_merge_groups=sum(1 for g in groups if g.safe_to_merge),
        blocked_groups=sum(1 for g in groups if not g.safe_to_merge),
    )

    return ScanReport(
        storefront=storefront,
        stats=stats,
        split_groups=groups,
    )
