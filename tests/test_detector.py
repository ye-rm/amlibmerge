"""Unit tests for split album detection, JSON parsing, and safety guardrails."""

from __future__ import annotations

from amlibmerge.detector import (
    build_included_index,
    detect_split_groups,
    evaluate_group_safety,
    parse_library_album,
    parse_library_track,
)
from amlibmerge.models import LibraryAlbum, LibraryTrack, SplitTier


def test_parse_library_album_from_embedded_and_included_and_playparams() -> None:
    page = {
        "data": [
            {
                "id": "l.alb1",
                "type": "library-albums",
                "attributes": {
                    "name": "Abbey Road",
                    "artistName": "The Beatles",
                    "trackCount": 5,
                },
                "relationships": {
                    "catalog": {
                        "data": [
                            {
                                "id": "1441164426",
                                "type": "albums",
                                "attributes": {
                                    "name": "Abbey Road (Remastered)",
                                    "artistName": "The Beatles",
                                    "trackCount": 17,
                                },
                            }
                        ]
                    }
                },
            },
            {
                "id": "l.alb2",
                "type": "library-albums",
                "attributes": {
                    "name": "Abbey Road",
                    "artistName": "The Beatles",
                    "trackCount": 12,
                    "playParams": {"id": "l.alb2", "catalogId": "1441164426"},
                },
            },
            {
                "id": "l.alb3",
                "type": "library-albums",
                "attributes": {
                    "name": "Included Ref Album",
                    "artistName": "Tester",
                    "trackCount": 10,
                },
                "relationships": {
                    "catalog": {"data": [{"id": "999999", "type": "albums"}]}
                },
            },
        ],
        "included": [
            {
                "id": "999999",
                "type": "albums",
                "attributes": {
                    "name": "Included Ref Album",
                    "artistName": "Tester",
                    "trackCount": 10,
                },
            }
        ],
    }

    idx = build_included_index(page)
    parsed = [parse_library_album(item, idx) for item in page["data"]]

    assert parsed[0].catalog_id == "1441164426"
    assert parsed[0].catalog_track_count == 17
    assert parsed[1].catalog_id == "1441164426"
    assert parsed[2].catalog_id == "999999"
    assert parsed[2].catalog_track_count == 10


def test_parse_library_track_extracts_catalog_id() -> None:
    raw = {
        "id": "i.song1",
        "type": "library-songs",
        "attributes": {
            "name": "Come Together",
            "artistName": "The Beatles",
            "albumName": "Abbey Road",
            "trackNumber": 1,
            "discNumber": 1,
            "playParams": {"id": "i.song1", "catalogId": "1441164430"},
        },
    }
    track = parse_library_track(raw)
    assert track.id == "i.song1"
    assert track.catalog_id == "1441164430"
    assert track.has_catalog is True


def test_tier1_same_catalog_id_split() -> None:
    albums = [
        LibraryAlbum(
            id="l.a1",
            name="Midnights",
            artist_name="Taylor Swift",
            track_count=3,
            catalog_id="1649434004",
            catalog_track_count=13,
        ),
        LibraryAlbum(
            id="l.a2",
            name="Midnights",
            artist_name="Taylor Swift",
            track_count=10,
            catalog_id="1649434004",
            catalog_track_count=13,
        ),
        LibraryAlbum(
            id="l.normal",
            name="1989",
            artist_name="Taylor Swift",
            track_count=13,
            catalog_id="1440935467",
            catalog_track_count=13,
        ),
    ]

    groups = detect_split_groups(albums)
    assert len(groups) == 1
    g = groups[0]
    assert g.tier == SplitTier.HIGH_SAME_CATALOG_ID
    assert g.target_catalog_album_id == "1649434004"
    assert g.split_count == 2
    assert g.safe_to_merge is True
    assert g.has_local_uploads is False


def test_tier2_same_name_artist_cross_catalog_and_single_separation() -> None:
    albums = [
        LibraryAlbum(
            id="l.v1",
            name="  Fantasy  ",
            artist_name="Jay Chou",
            track_count=4,
            catalog_id="1001",
            catalog_track_count=10,
        ),
        LibraryAlbum(
            id="l.v2",
            name="fantasy",
            artist_name="jay  chou",
            track_count=6,
            catalog_id="1002",
            catalog_track_count=10,
        ),
        LibraryAlbum(
            id="l.single",
            name="Fantasy - Single",
            artist_name="Jay Chou",
            track_count=1,
            catalog_id="1003",
            catalog_track_count=1,
        ),
    ]

    groups = detect_split_groups(albums, include_weak=True)
    assert len(groups) == 1
    g = groups[0]
    assert g.tier == SplitTier.MEDIUM_SAME_NAME_ARTIST
    assert {a.id for a in g.albums} == {"l.v1", "l.v2"}
    assert g.has_mixed_editions is True
    assert g.safe_to_merge is False
    assert any(("跨版本分裂" in w or "Cross-version split" in w) for w in g.warnings)

    # Disabling weak matching omits Tier 2 groups
    assert detect_split_groups(albums, include_weak=False) == []


def test_mixed_editions_excess_tracks_blocks_merge() -> None:
    # Mimics real-world classical album where tracks from 2 different editions got scrambled
    # under the same album catalog ID (e.g. 5 + 5 = 10 distinct tracks > 4 target tracks).
    a1 = LibraryAlbum(
        id="l.e1",
        name="Beethoven 9",
        artist_name="Furtwangler",
        track_count=5,
        catalog_id="1552919202",
        catalog_track_count=4,
        tracks_loaded=True,
        tracks=[
            LibraryTrack(id=f"i.1.{n}", name=f"T{n}", catalog_id=f"c1_{n}")
            for n in range(1, 6)
        ],
    )
    a2 = LibraryAlbum(
        id="l.e2",
        name="Beethoven 9",
        artist_name="Furtwangler",
        track_count=5,
        catalog_id="1552919202",
        catalog_track_count=4,
        tracks_loaded=True,
        tracks=[
            LibraryTrack(id=f"i.2.{n}", name=f"T{n}", catalog_id=f"c2_{n}")
            for n in range(1, 6)
        ],
    )
    groups = detect_split_groups([a1, a2])
    assert len(groups) == 1
    assert groups[0].has_mixed_editions is True
    assert groups[0].safe_to_merge is False
    assert any(("混入了多个不同录音/版本" in w or "multiple recordings/editions" in w) for w in groups[0].warnings)


def test_local_upload_albums_are_ignored_or_blocked() -> None:
    # Purely local duplicate albums -> ignored completely
    pure_local = [
        LibraryAlbum(id="l.loc1", name="My CD Rip", artist_name="Indie", track_count=4),
        LibraryAlbum(id="l.loc2", name="My CD Rip", artist_name="Indie", track_count=5),
    ]
    assert detect_split_groups(pure_local) == []

    # Mixed (one catalog + one local upload with same name/artist) -> blocked!
    mixed = [
        LibraryAlbum(
            id="l.cat",
            name="Hybrid Album",
            artist_name="Band",
            track_count=5,
            catalog_id="5555",
            catalog_track_count=10,
        ),
        LibraryAlbum(
            id="l.loc",
            name="Hybrid Album",
            artist_name="Band",
            track_count=2,
            catalog_id=None,
        ),
    ]
    groups = detect_split_groups(mixed)
    assert len(groups) == 1
    assert groups[0].has_local_uploads is True
    assert groups[0].safe_to_merge is False


def test_track_level_local_upload_and_unavailable_catalog_block_merge() -> None:
    a1 = LibraryAlbum(
        id="l.1",
        name="Split Test",
        artist_name="Artist",
        track_count=2,
        catalog_id="777",
        tracks_loaded=True,
        tracks=[
            LibraryTrack(id="i.1", name="Track 1", catalog_id="7771"),
            LibraryTrack(id="i.2", name="Bonus Rip", catalog_id=None),
        ],
    )
    a2 = LibraryAlbum(
        id="l.2",
        name="Split Test",
        artist_name="Artist",
        track_count=1,
        catalog_id="777",
        tracks_loaded=True,
        tracks=[LibraryTrack(id="i.3", name="Track 3", catalog_id="7773")],
    )

    groups = detect_split_groups([a1, a2])
    assert len(groups) == 1
    assert groups[0].has_local_uploads is True
    assert groups[0].safe_to_merge is False

    # Also verify storefront unavailability blocks merge even without local tracks
    a1.tracks[1].catalog_id = "7772"
    groups[0].target_catalog_available = False
    evaluate_group_safety(groups[0])
    assert groups[0].has_local_uploads is False
    assert groups[0].safe_to_merge is False
    assert any(("已下架" in w or "delisted" in w) for w in groups[0].warnings)
