"""Read current Edge v2 Workspaces and saved groups from a LevelDB snapshot."""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from vendor.ccl_leveldb import KeyState, LdbFile, LogFile, ManifestFile

from ._protobuf import fields, nested, one, text
from .base import FolderRecord, SpaceRecord, TabRecord
from .edge_workspaces import WorkspaceScan

_DATA_PREFIX = b"edge_workspace-dt-"
_GROUP_PREFIX = b"saved_tab_group-dt-"
_DATA_FILE = re.compile(r"[0-9]+\.(ldb|sst|log)\Z")
_MANIFEST_FILE = re.compile(r"MANIFEST-[0-9]+\Z")


def _active_files(directory: Path) -> list[Path]:
    current = (directory / "CURRENT").read_text(encoding="ascii").strip()
    if not _MANIFEST_FILE.fullmatch(current):
        raise ValueError("Invalid LevelDB CURRENT manifest")
    manifest = ManifestFile(directory / current)
    tables: set[int] = set()
    log_number: int | None = None
    previous_log = 0
    try:
        for edit in manifest:
            tables.difference_update(item.file_no for item in edit.deleted_files)
            tables.update(item.file_no for item in edit.new_files)
            if edit.log_number is not None:
                log_number = edit.log_number
            if edit.prev_log_number is not None:
                previous_log = edit.prev_log_number
    finally:
        manifest.close()
    if log_number is None:
        raise ValueError("LevelDB manifest has no active log number")
    result: list[Path] = []
    available_tables: set[int] = set()
    for path in sorted(directory.iterdir()):
        if not _DATA_FILE.fullmatch(path.name):
            continue
        number = int(path.stem)
        if path.suffix in {".ldb", ".sst"} and number in tables:
            result.append(path)
            available_tables.add(number)
        elif path.suffix == ".log" and (number >= log_number or number == previous_log):
            result.append(path)
    if tables != available_tables:
        raise ValueError("The sync snapshot is missing an active LevelDB table")
    return result


def _logical_records(directory: Path) -> dict[bytes, bytes]:
    latest = {}
    for path in _active_files(directory):
        reader = LogFile(path) if path.suffix == ".log" else LdbFile(path)
        try:
            for record in reader:
                key = record.user_key
                if not key.startswith((b"edge_workspace-", b"saved_tab_group-")):
                    continue
                if key not in latest or record.seq > latest[key].seq:
                    latest[key] = record
                elif record.seq == latest[key].seq:
                    previous = latest[key]
                    if previous.state != record.state or previous.value != record.value:
                        raise ValueError("Conflicting sync records have the same sequence number")
        finally:
            reader.close()
    return {key: record.value for key, record in latest.items() if record.state == KeyState.Live}


def _source_signature(source: Path) -> dict[str, tuple[int, int]]:
    paths = [path for path in source.iterdir() if path.is_file() and (
        _DATA_FILE.fullmatch(path.name) or _MANIFEST_FILE.fullmatch(path.name) or path.name == "CURRENT")]
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in paths}


def read_sync_records(source: Path) -> dict[bytes, bytes]:
    """Use consistent temporary copies, honoring active files and deletions."""
    for _attempt in range(3):
        before = _source_signature(source)
        if sum(size for size, _mtime in before.values()) > 512 * 1024 * 1024:
            raise ValueError("The Edge sync store exceeds the supported snapshot size")
        try:
            with tempfile.TemporaryDirectory(prefix="edgebrowser2gen-sync-") as temporary:
                target = Path(temporary)
                for name in before:
                    shutil.copy2(source / name, target / name)
                if before != _source_signature(source):
                    continue
                return _logical_records(target)
        except FileNotFoundError:
            continue
    raise ValueError("Edge's sync store changed during the snapshot. Close Edge and scan again.")


def _specifics(records: dict[bytes, bytes], prefix: bytes) -> dict[str, dict]:
    result = {}
    metadata_prefix = prefix.replace(b"-dt-", b"-md-")
    for key, value in records.items():
        if not key.startswith(prefix):
            continue
        metadata = records.get(metadata_prefix + key[len(prefix):])
        if metadata is not None and one(fields(metadata), 3, 0):
            continue  # Sync-level pending deletion, even if a data record remains.
        envelope = fields(value)
        if one(envelope, 1, 0) not in {0, 1}:
            raise ValueError("Unsupported Edge sync record version")
        specifics = nested(envelope, 2)
        identifier = str(uuid.UUID(text(specifics, 1)))
        if (4 in specifics) == (5 in specifics):
            raise ValueError("Invalid Edge sync entity variant")
        result[identifier] = specifics
    return result


def _position(message: dict, number: int) -> bytes:
    position = nested(message, number)
    compressed = one(position, 4)
    # Chromium UniquePosition custom_compressed_v1 preserves lexical order.
    if not isinstance(compressed, bytes) or not compressed:
        raise ValueError("Unsupported Edge UniquePosition encoding")
    return compressed


def decode_sync_workspaces(records: dict[bytes, bytes], profile_name: str) -> list[WorkspaceScan]:
    entities = _specifics(records, _DATA_PREFIX)
    groups = _specifics(records, _GROUP_PREFIX)
    workspaces = {identifier: nested(value, 4) for identifier, value in entities.items() if 4 in value}
    direct_tabs = {identifier: nested(value, 5) for identifier, value in entities.items() if 5 in value}
    saved_groups = {identifier: nested(value, 4) for identifier, value in groups.items() if 4 in value}
    grouped_tabs = {identifier: nested(value, 5) for identifier, value in groups.items() if 5 in value}
    for tab in direct_tabs.values():
        if text(tab, 1) not in workspaces:
            raise ValueError("An Edge tab refers to a missing workspace")
    for tab in grouped_tabs.values():
        if text(tab, 1) not in saved_groups:
            raise ValueError("An Edge saved tab refers to a missing group")
    results = []
    for identifier, workspace in workspaces.items():
        name = text(workspace, 1)
        space_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"edge-sync:{profile_name}:{identifier}"))
        children: list[tuple[bytes, str, dict]] = []
        for tab_id, tab in direct_tabs.items():
            if text(tab, 1) == identifier:
                children.append((_position(tab, 2), tab_id, {"tab": tab}))
        for group_id, group in saved_groups.items():
            if text(group, 1002, "") == identifier:
                children.append((_position(group, 1003), group_id, {"group": group}))
        children.sort(key=lambda child: (child[0], child[1]))
        tabs: list[TabRecord] = []
        folders: list[FolderRecord] = []
        reported_count = 0

        def append_tab(tab: dict, group_id: str | None = None, group_title: str = "",
                       output: list[TabRecord] = tabs) -> None:
            nonlocal reported_count
            reported_count += 1
            url = text(tab, 3)
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https", "ftp"} or not parsed.netloc:
                return
            output.append(TabRecord(url=url, title=text(tab, 4, ""), folder_id=group_id,
                                    folder_path=[group_title] if group_id else []))

        for _position_bytes, child_id, child in children:
            if "tab" in child:
                append_tab(child["tab"])
            else:
                group_title = text(child["group"], 2, "Untitled group")
                folders.append(FolderRecord(folder_id=child_id, title=group_title,
                                             space_id=space_id, index=len(folders)))
                members = [(one(tab, 2, 0), tab_id, tab) for tab_id, tab in grouped_tabs.items()
                           if text(tab, 1) == child_id]
                for _index, _tab_id, tab in sorted(members):
                    append_tab(tab, child_id, group_title)
        argb = one(workspace, 2)
        color = None
        if isinstance(argb, int) and 0 <= argb <= 0xFFFFFFFF:
            color = {"r": ((argb >> 16) & 255) / 255,
                     "g": ((argb >> 8) & 255) / 255, "b": (argb & 255) / 255}
        space = SpaceRecord(space_id=space_id, space_name=name, open_tabs=tabs, folders=folders,
                            color=color, bookmarks=list(tabs), bookmark_folders=list(folders))
        scan = WorkspaceScan(identifier, name, profile_name, reported_count, space=space,
                             storage_kind="sync_v2")
        if len(tabs) != reported_count:
            scan.issues.append("Browser-internal or unsupported URLs were skipped.")
        results.append(scan)
    return sorted(results, key=lambda scan: (scan.name.casefold(), scan.workspace_id))


def scan_sync_profile(profile: Path) -> list[WorkspaceScan] | None:
    directory = profile / "Sync Data" / "LevelDB"
    if not (directory / "CURRENT").is_file():
        return None
    records = read_sync_records(directory)
    if not any(key.startswith(b"edge_workspace-") for key in records):
        return None
    # An empty authoritative v2 store must not resurrect old legacy caches.
    return decode_sync_workspaces(records, profile.name)
