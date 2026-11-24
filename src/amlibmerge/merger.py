"""Phase 2 split-album merge state machine, WAL journaling, and idempotent playlist restore."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from amlibmerge.client import AppleMusicAPIError, AppleMusicClient
from amlibmerge.detector import build_included_index, parse_library_track
from amlibmerge.models import (
    LibraryBackup,
    MergeJournalEntry,
    MergeReport,
    MergeResult,
    RatingEntry,
    ScanReport,
    SplitGroup,
)


def build_manual_action_hint(
    group: SplitGroup,
    stage: str,
    error: str | None = None,
) -> str:
    """Return a concise, actionable manual-merge suggestion based on why automation did not complete."""
    if stage == "still_split":
        return (
            "Apple Music 官方曲库当前元数据自身存在分裂，删后重导无法合并；"
            "请在桌面端 Music 应用选中该专辑全部曲目 →「显示简介」，统一「专辑」与「专辑艺人」（可先临时加后缀再改回触发重新归组）。"
        )
    if group.target_catalog_available is False or (error and "404" in error):
        return (
            "目标专辑在当前区服已下架或不可用，切勿从资料库删除；"
            "仅可在桌面端 Music 应用手动编辑元数据合并。"
        )
    if group.has_local_uploads:
        return (
            "包含无 Catalog 映射的曲目（本地自传或本区已下架），删除会导致这些曲目丢失；"
            "建议在桌面端 Music 应用手动统一专辑元数据。"
        )
    if group.has_mixed_editions:
        return (
            "组内混入了多个不同录音/版本，直接合并到单一版本会丢失其余录音；"
            "请在客户端人工核对要保留的版本，或确认后用 `--group <ID> --catalog-id <目标ID> --force --execute` 指定版本合并。"
        )
    if stage == "failed":
        return (
            "执行期间发生网络或接口异常；请检查 WAL 操作日志，按需运行 `amlibmerge restore --execute` 恢复歌单后重试。"
        )
    return "请人工核对该专辑信息并在桌面端 Music 应用手动合并。"


def make_skipped_result(
    group: SplitGroup,
    *,
    dry_run: bool,
    reason: str,
    target_cid: str | None = None,
) -> MergeResult:
    cid = (target_cid if target_cid is not None else group.target_catalog_album_id) or ""
    return MergeResult(
        group_id=group.group_id,
        canonical_name=group.canonical_name,
        artist_name=group.canonical_artist,
        target_catalog_album_id=cid,
        dry_run=dry_run,
        stage="skipped",
        warnings=list(group.warnings),
        error=reason,
        manual_action_hint=build_manual_action_hint(group, "skipped", reason),
    )


def build_merge_report(
    results: list[MergeResult],
    *,
    storefront: str,
    dry_run: bool,
    source_scan_report: Path,
    journal_path: Path | None = None,
) -> MergeReport:
    succeeded = [r for r in results if r.stage == "restored"]
    dry_run_ready = [r for r in results if r.stage == "dry_run"]
    manual_required = [r for r in results if r.stage in ("still_split", "skipped", "failed")]
    return MergeReport(
        storefront=storefront,
        dry_run=dry_run,
        source_scan_report=str(source_scan_report),
        journal_path=str(journal_path) if journal_path else None,
        succeeded=succeeded,
        manual_required=manual_required,
        dry_run_ready=dry_run_ready,
    )


def save_merge_report(report: MergeReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    tmp_path.replace(output_path)
    return output_path


def load_scan_report(path: Path) -> ScanReport:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ScanReport.model_validate(raw)


def find_latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def append_journal(journal_path: Path, entry: MergeJournalEntry) -> None:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(entry.model_dump_json() + "\n")


def compute_affected_items(
    group: SplitGroup,
    backup: LibraryBackup | None,
) -> tuple[dict[str, list[str]], list[RatingEntry]]:
    """Find playlists and ratings referencing any track in the split group."""
    if backup is None:
        return {}, []

    group_lib_song_ids: set[str] = set()
    group_cat_song_ids: set[str] = set()
    for album in group.albums:
        for track in album.tracks:
            if track.id:
                group_lib_song_ids.add(track.id)
            if track.catalog_id:
                group_cat_song_ids.add(track.catalog_id)

    affected_playlists: dict[str, list[str]] = {}
    for pl in backup.playlists:
        if not pl.can_edit:
            continue
        matched_cids: list[str] = []
        seen: set[str] = set()
        for entry in pl.tracks:
            is_match = (
                (entry.library_song_id and entry.library_song_id in group_lib_song_ids)
                or (entry.catalog_song_id and entry.catalog_song_id in group_cat_song_ids)
            )
            if is_match and entry.catalog_song_id and entry.catalog_song_id not in seen:
                matched_cids.append(entry.catalog_song_id)
                seen.add(entry.catalog_song_id)
        if matched_cids:
            affected_playlists[pl.id] = matched_cids

    affected_ratings: list[RatingEntry] = []
    seen_rating_cids: set[str] = set()
    for rating in backup.ratings:
        is_match = (
            rating.resource_id in group_lib_song_ids
            or (rating.catalog_id and rating.catalog_id in group_cat_song_ids)
        )
        if is_match and rating.catalog_id and rating.catalog_id not in seen_rating_cids:
            affected_ratings.append(rating)
            seen_rating_cids.add(rating.catalog_id)

    return affected_playlists, affected_ratings


def _get_live_playlist_catalog_song_ids(client: AppleMusicClient, playlist_id: str) -> set[str]:
    existing: set[str] = set()
    for page in client.iter_playlist_tracks_pages(playlist_id, limit=100, include="catalog"):
        included = build_included_index(page)
        for raw_track in page.get("data") or []:
            if isinstance(raw_track, dict):
                parsed = parse_library_track(raw_track, included)
                if parsed.catalog_id:
                    existing.add(parsed.catalog_id)
    return existing


def merge_split_group(
    client: AppleMusicClient,
    storefront: str,
    group: SplitGroup,
    *,
    backup: LibraryBackup | None = None,
    dry_run: bool = True,
    force: bool = False,
    override_catalog_id: str | None = None,
    journal_path: Path | None = None,
    poll_interval: float = 2.0,
    settle_delay: float = 3.0,
    max_poll_attempts: int = 6,
    on_step: Callable[[str], None] | None = None,
) -> MergeResult:
    """Execute (or dry-run) the delete -> poll -> full-album re-add -> playlist/rating restore pipeline."""

    def log(msg: str) -> None:
        if on_step:
            on_step(msg)

    target_cid = (override_catalog_id or group.target_catalog_album_id or "").strip()
    if not target_cid:
        return make_skipped_result(
            group,
            dry_run=dry_run,
            reason="缺少目标 Catalog Album ID",
            target_cid="",
        )

    if not group.safe_to_merge and not force:
        return make_skipped_result(
            group,
            dry_run=dry_run,
            reason="该分裂组触发安全保护拦截（未自动删除）",
            target_cid=target_cid,
        )

    # Hard guardrail (never bypassable): verify target catalog album is available in storefront.
    log(f"校验目标专辑 {target_cid} 在区服 {storefront} 的在架状态...")
    cat_album = client.get_catalog_album(storefront, target_cid)
    if cat_album is None:
        return make_skipped_result(
            group,
            dry_run=dry_run,
            reason=f"目标 Catalog 专辑 {target_cid} 在区服 {storefront} 不可用（404），拒绝删除！",
            target_cid=target_cid,
        )

    affected_playlists, affected_ratings = compute_affected_items(group, backup)

    if dry_run:
        return MergeResult(
            group_id=group.group_id,
            canonical_name=group.canonical_name,
            artist_name=group.canonical_artist,
            target_catalog_album_id=target_cid,
            dry_run=True,
            stage="dry_run",
            affected_playlists=affected_playlists,
            warnings=list(group.warnings),
        )

    album_ids = [a.id for a in group.albums]
    if journal_path:
        append_journal(
            journal_path,
            MergeJournalEntry(
                group_id=group.group_id,
                canonical_name=group.canonical_name,
                target_catalog_album_id=target_cid,
                library_album_ids=album_ids,
                affected_playlists=affected_playlists,
                affected_ratings=affected_ratings,
                stage="planned",
            ),
        )

    deleted_album_statuses: dict[str, int] = {}
    deleted_song_statuses: dict[str, int] = {}

    try:
        # 1. Delete each split library album (with song-level fallback if album delete fails)
        for album in group.albums:
            log(f"删除旧资料库专辑 {album.id}（{album.track_count} 首）...")
            try:
                status = client.delete_library_album(album.id)
                deleted_album_statuses[album.id] = status
            except AppleMusicAPIError as exc:
                if not album.tracks:
                    raise
                log(f"专辑级删除 {album.id} 返回异常（{exc}），降级为逐首曲目删除...")
                deleted_album_statuses[album.id] = exc.status_code
                for track in album.tracks:
                    deleted_song_statuses[track.id] = client.delete_library_song(track.id)

        # 2. Poll until old library album IDs disappear (cloud propagation)
        still_present: list[str] = []
        for attempt in range(max_poll_attempts):
            still_present = [
                aid for aid in album_ids if client.get_library_album(aid) is not None
            ]
            if not still_present:
                break
            log(
                f"等待云端同步删除（{attempt + 1}/{max_poll_attempts}），"
                f"剩余: {', '.join(still_present)}..."
            )
            time.sleep(poll_interval)

        if still_present:
            # Some split shells have empty `/tracks` while their tracks were listed under a sibling album ID.
            # Collect every song ID across the entire group (plus live tracks on remaining shells) and delete them.
            fallback_song_ids: list[str] = []
            seen_song_ids: set[str] = set()
            for album in group.albums:
                for track in album.tracks:
                    if track.id and track.id not in seen_song_ids:
                        fallback_song_ids.append(track.id)
                        seen_song_ids.add(track.id)
            for rem_id in still_present:
                if hasattr(client, "get_library_album_tracks_pages"):
                    for t_page in client.get_library_album_tracks_pages(rem_id, include="catalog"):
                        t_inc = build_included_index(t_page)
                        for raw_t in t_page.get("data") or []:
                            if isinstance(raw_t, dict):
                                parsed_t = parse_library_track(raw_t, t_inc)
                                if parsed_t.id and parsed_t.id not in seen_song_ids:
                                    fallback_song_ids.append(parsed_t.id)
                                    seen_song_ids.add(parsed_t.id)

            if fallback_song_ids:
                log(
                    f"专辑 {', '.join(still_present)} 仍在云端缓存中，"
                    f"降级逐首清理整组共 {len(fallback_song_ids)} 首曲目..."
                )
                for sid in fallback_song_ids:
                    if sid not in deleted_song_statuses:
                        deleted_song_statuses[sid] = client.delete_library_song(sid)

                for attempt in range(max_poll_attempts):
                    time.sleep(poll_interval)
                    still_present = [
                        aid for aid in album_ids if client.get_library_album(aid) is not None
                    ]
                    if not still_present:
                        break
                    log(
                        f"逐首删歌后等待云端同步（{attempt + 1}/{max_poll_attempts}），"
                        f"剩余: {', '.join(still_present)}..."
                    )

        if journal_path:
            append_journal(
                journal_path,
                MergeJournalEntry(
                    group_id=group.group_id,
                    canonical_name=group.canonical_name,
                    target_catalog_album_id=target_cid,
                    library_album_ids=album_ids,
                    affected_playlists=affected_playlists,
                    affected_ratings=affected_ratings,
                    stage="deleted",
                    details={
                        "deleted_album_statuses": deleted_album_statuses,
                        "deleted_song_statuses": deleted_song_statuses,
                        "still_present_before_readd": still_present,
                    },
                ),
            )

        if still_present:
            log(f"警告：旧专辑 {', '.join(still_present)} 在重导前仍未完全消失，继续尝试重导...")
        elif settle_delay > 0:
            log(f"旧专辑已从云端清除，等待 {settle_delay:.0f}s 让云端分片同步稳定后再重导...")
            time.sleep(settle_delay)

        # 3. Re-add full catalog album to library
        log(f"正在重新导入完整官方专辑 {target_cid} 至资料库...")
        readd_status = client.add_catalog_album_to_library(target_cid)

        # 4. Poll for new library album entry (do NOT exit early on stale multi-ID cache)
        new_lib_ids: list[str] = []
        old_id_set = set(album_ids)
        for attempt in range(max_poll_attempts):
            time.sleep(poll_interval)
            found_ids = client.check_catalog_album_in_library(storefront, target_cid)
            new_lib_ids = found_ids
            if len(found_ids) == 1:
                break
            fresh_ids = [i for i in found_ids if i not in old_id_set]
            if fresh_ids and len(fresh_ids) == 1:
                new_lib_ids = fresh_ids
                break
            log(
                f"等待新专辑入库同步（{attempt + 1}/{max_poll_attempts}），"
                f"当前关联 ID: {found_ids or '无'}..."
            )

        still_split = len(new_lib_ids) > 1
        final_warnings = list(group.warnings)
        if still_split:
            final_warnings.append(
                f"整专重导后云端仍返回 {len(new_lib_ids)} 个资料库专辑 ID（{', '.join(new_lib_ids)}），"
                "说明该专辑当前 Apple Music 官方曲库自身元数据即存在分裂（删后重导无法根治，需在桌面端 Music 应用手动统一专辑信息）"
            )

        if journal_path:
            append_journal(
                journal_path,
                MergeJournalEntry(
                    group_id=group.group_id,
                    canonical_name=group.canonical_name,
                    target_catalog_album_id=target_cid,
                    library_album_ids=album_ids,
                    affected_playlists=affected_playlists,
                    affected_ratings=affected_ratings,
                    stage="readded",
                    details={
                        "readd_status": readd_status,
                        "new_library_album_ids": new_lib_ids,
                        "still_split": still_split,
                    },
                ),
            )

        # 5. Idempotently restore affected playlists
        restored_playlist_tracks: dict[str, int] = {}
        for pl_id, song_cids in affected_playlists.items():
            live_cids = _get_live_playlist_catalog_song_ids(client, pl_id)
            missing_cids = [cid for cid in song_cids if cid not in live_cids]
            if missing_cids:
                log(f"恢复歌单 {pl_id} 中缺失的 {len(missing_cids)} 首曲目...")
                client.add_tracks_to_playlist(pl_id, missing_cids)
            restored_playlist_tracks[pl_id] = len(missing_cids)

        # 6. Restore ratings/favorites
        restored_ratings = 0
        for rating in affected_ratings:
            if rating.catalog_id:
                client.set_song_rating(rating.catalog_id, rating.value)
                restored_ratings += 1

        final_stage = "still_split" if still_split else "restored"

        if journal_path:
            append_journal(
                journal_path,
                MergeJournalEntry(
                    group_id=group.group_id,
                    canonical_name=group.canonical_name,
                    target_catalog_album_id=target_cid,
                    library_album_ids=album_ids,
                    affected_playlists=affected_playlists,
                    affected_ratings=affected_ratings,
                    stage=final_stage,
                    details={
                        "new_library_album_ids": new_lib_ids,
                        "restored_playlist_tracks": restored_playlist_tracks,
                        "restored_ratings": restored_ratings,
                    },
                ),
            )

        return MergeResult(
            group_id=group.group_id,
            canonical_name=group.canonical_name,
            artist_name=group.canonical_artist,
            target_catalog_album_id=target_cid,
            dry_run=False,
            stage=final_stage,
            deleted_album_statuses=deleted_album_statuses,
            deleted_song_statuses=deleted_song_statuses,
            readd_status=readd_status,
            new_library_album_ids=new_lib_ids,
            affected_playlists=affected_playlists,
            restored_playlist_tracks=restored_playlist_tracks,
            restored_ratings=restored_ratings,
            warnings=final_warnings,
            manual_action_hint=(
                build_manual_action_hint(group, final_stage) if still_split else None
            ),
        )
    except Exception as exc:
        if journal_path:
            append_journal(
                journal_path,
                MergeJournalEntry(
                    group_id=group.group_id,
                    canonical_name=group.canonical_name,
                    target_catalog_album_id=target_cid,
                    library_album_ids=album_ids,
                    affected_playlists=affected_playlists,
                    affected_ratings=affected_ratings,
                    stage="failed",
                    error=str(exc),
                ),
            )
        return MergeResult(
            group_id=group.group_id,
            canonical_name=group.canonical_name,
            artist_name=group.canonical_artist,
            target_catalog_album_id=target_cid,
            dry_run=False,
            stage="failed",
            deleted_album_statuses=deleted_album_statuses,
            deleted_song_statuses=deleted_song_statuses,
            affected_playlists=affected_playlists,
            warnings=list(group.warnings),
            error=str(exc),
            manual_action_hint=build_manual_action_hint(group, "failed", str(exc)),
        )


def restore_from_backup(
    client: AppleMusicClient,
    backup: LibraryBackup,
    *,
    dry_run: bool = True,
    playlist_id: str | None = None,
    restore_ratings: bool = True,
    on_step: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Idempotently restore missing playlist tracks and song ratings from a backup snapshot."""

    def log(msg: str) -> None:
        if on_step:
            on_step(msg)

    playlist_summary: list[dict[str, object]] = []
    total_missing_tracks = 0
    total_restored_tracks = 0

    for pl in backup.playlists:
        if not pl.can_edit:
            continue
        if playlist_id and pl.id != playlist_id:
            continue

        log(f"比对歌单：{pl.name} ({pl.id})...")
        live_cids = _get_live_playlist_catalog_song_ids(client, pl.id)
        desired_cids: list[str] = []
        seen: set[str] = set()
        for entry in pl.tracks:
            if entry.catalog_song_id and entry.catalog_song_id not in seen:
                desired_cids.append(entry.catalog_song_id)
                seen.add(entry.catalog_song_id)

        missing = [cid for cid in desired_cids if cid not in live_cids]
        total_missing_tracks += len(missing)

        restored_count = 0
        if missing and not dry_run:
            log(f"向歌单 {pl.name} 补回 {len(missing)} 首缺失曲目...")
            client.add_tracks_to_playlist(pl.id, missing)
            restored_count = len(missing)
            total_restored_tracks += restored_count

        playlist_summary.append(
            {
                "playlist_id": pl.id,
                "name": pl.name,
                "backup_tracks": len(desired_cids),
                "missing_tracks": len(missing),
                "restored_tracks": restored_count,
            }
        )

    restored_ratings = 0
    if restore_ratings and not dry_run:
        for rating in backup.ratings:
            if rating.catalog_id:
                client.set_song_rating(rating.catalog_id, rating.value)
                restored_ratings += 1

    return {
        "dry_run": dry_run,
        "playlists": playlist_summary,
        "total_missing_tracks": total_missing_tracks,
        "total_restored_tracks": total_restored_tracks,
        "ratings_in_backup": len([r for r in backup.ratings if r.catalog_id]),
        "restored_ratings": restored_ratings,
    }
