"""Read Edge's locally cached Workspaces without changing browser data.

The legacy workspace cache wraps a base64-encoded Mojo struct in JSON.
Only the current navigation of each tab is exported; historical URLs,
collaborators, and account information are never exported. Newer Edge
workspace formats are not assumed to be compatible with this cache.
"""

from __future__ import annotations

import base64
import json
import shutil
import struct
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .base import ExportData, FolderRecord, SpaceRecord, TabRecord

MAX_CACHE_BYTES = 64 * 1024 * 1024


def _snapshot_json(path: Path) -> dict:
    if path.stat().st_size > MAX_CACHE_BYTES:
        raise ValueError("Workspace cache exceeds the supported size")
    with tempfile.TemporaryDirectory(prefix="edgebrowser2gen-") as directory:
        snapshot = Path(directory) / "cache.json"
        shutil.copy2(path, snapshot)
        if snapshot.stat().st_size > MAX_CACHE_BYTES:
            raise ValueError("Workspace cache exceeds the supported size")
        value = json.loads(snapshot.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Workspace cache must be a JSON object")
    return value


class _MojoReader:
    """Bounds-checked reader for the observed version-zero workspace schema."""

    def __init__(self, data: bytes):
        self.data = data

    def _check(self, offset: int, length: int) -> None:
        if offset < 0 or length < 0 or offset + length > len(self.data):
            raise ValueError("Truncated or invalid workspace pointer")

    def uint(self, offset: int) -> int:
        self._check(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def integer(self, offset: int) -> int:
        self._check(offset, 4)
        return struct.unpack_from("<i", self.data, offset)[0]

    def pointer(self, field_offset: int) -> int:
        self._check(field_offset, 8)
        relative = struct.unpack_from("<Q", self.data, field_offset)[0]
        if relative == 0:
            raise ValueError("Required workspace pointer is null")
        target = field_offset + relative
        self._check(target, 8)
        return target

    def record(self, offset: int, expected_size: int) -> int:
        size, version = self.uint(offset), self.uint(offset + 4)
        if size != expected_size or version != 0:
            raise ValueError("Unsupported workspace record version or layout")
        self._check(offset, size)
        return offset

    def string(self, field_offset: int) -> str:
        offset = self.pointer(field_offset)
        size, length = self.uint(offset), self.uint(offset + 4)
        if size != length + 8:
            raise ValueError("Invalid workspace string")
        self._check(offset, size)
        return self.data[offset + 8:offset + size].decode("utf-8")

    def optional_string(self, field_offset: int) -> str:
        self._check(field_offset, 8)
        if struct.unpack_from("<Q", self.data, field_offset)[0] == 0:
            return ""
        return self.string(field_offset)

    def array(self, field_offset: int) -> list[int]:
        offset = self.pointer(field_offset)
        size, count = self.uint(offset), self.uint(offset + 4)
        if size != 8 + count * 8:
            raise ValueError("Invalid workspace pointer array")
        self._check(offset, size)
        return [self.pointer(offset + 8 + index * 8) for index in range(count)]


def decode_workspace(cache: dict, space_id: str, name: str) -> SpaceRecord:
    encoded = cache.get("Fluid_Data")
    if cache.get("Version") != 6 or not isinstance(encoded, str):
        raise ValueError("Unsupported Edge workspace cache format")
    raw = base64.b64decode(encoded, validate=True)
    reader = _MojoReader(raw)
    reader.record(0, 80)
    container = reader.record(reader.pointer(24), 24)
    groups: list[FolderRecord] = []
    for index, group in enumerate(reader.array(container + 16)):
        reader.record(group, 32)
        groups.append(FolderRecord(
            folder_id=reader.string(group + 8),
            title=reader.string(group + 16),
            space_id=space_id,
            index=index,
        ))
    group_names = {group.folder_id: group.title for group in groups}
    tabs: list[TabRecord] = []
    for tab in reader.array(container + 8):
        reader.record(tab, 88)
        navigations = reader.array(tab + 40)
        selected = reader.integer(tab + 28)
        if not 0 <= selected < len(navigations):
            raise ValueError("Workspace tab has an invalid selected navigation")
        navigation = reader.record(navigations[selected], 96)
        url_wrapper = reader.record(reader.pointer(navigation + 8), 16)
        url = reader.string(url_wrapper + 8)
        parsed = urlsplit(url)
        # Browser-internal pages cannot be reopened in a different engine.
        if parsed.scheme not in {"https", "http", "ftp"} or not parsed.netloc:
            continue
        group_id = reader.optional_string(tab + 32)
        folder_name = group_names.get(group_id)
        tabs.append(TabRecord(
            url=url,
            title=reader.string(navigation + 16),
            folder_id=group_id if folder_name is not None else None,
            folder_path=[folder_name] if folder_name is not None else [],
        ))
    return SpaceRecord(
        space_id=space_id, space_name=name, open_tabs=tabs,
        folders=groups, bookmarks=list(tabs), bookmark_folders=list(groups),
    )


@dataclass
class WorkspaceScan:
    workspace_id: str
    name: str
    profile: str
    reported_tabs: int | None
    space: SpaceRecord | None = None
    issues: list[str] = field(default_factory=list)
    cache_modified: float | None = None
    storage_kind: str = "legacy_cache"

    def summary(self, *, include_names: bool = True) -> dict:
        return {
            **({"name": self.name, "profile": self.profile} if include_names else {}),
            "reported_tabs": self.reported_tabs,
            "recoverable_tabs": len(self.space.open_tabs) if self.space else 0,
            "groups": len(self.space.folders) if self.space else 0,
            "cache_modified": self.cache_modified,
            "status": ("local_sync_snapshot" if self.storage_kind == "sync_v2" else "cached_snapshot")
            if self.space else "unavailable",
            "storage_kind": self.storage_kind,
            "issues": list(self.issues),
        }


def scan_profile(profile: Path) -> list[WorkspaceScan]:
    directory = profile / "Workspaces"
    manifest = directory / "WorkspacesCache"
    if not manifest.is_file():
        return []
    value = _snapshot_json(manifest)
    if value.get("edgeWorkspaceCacheVersion") != 1 or not isinstance(value.get("workspaces"), list):
        raise ValueError("Unsupported Edge workspace manifest format")
    results: list[WorkspaceScan] = []
    seen: set[str] = set()
    for metadata in value["workspaces"]:
        if not isinstance(metadata, dict):
            raise ValueError("Invalid workspace metadata entry")
        # Validate before constructing a filename from untrusted metadata.
        workspace_id = str(uuid.UUID(str(metadata.get("id", ""))))
        if workspace_id in seen:
            continue
        seen.add(workspace_id)
        name = metadata.get("name")
        if not isinstance(name, str):
            raise ValueError("Workspace name must be a string")
        reported = metadata.get("count")
        reported = reported if type(reported) is int and reported >= 0 else None
        result = WorkspaceScan(workspace_id, name, profile.name, reported)
        cache = directory / f"workspace_cache_{workspace_id}"
        if not cache.is_file():
            result.issues.append("No supported local tab cache. This workspace cannot be imported yet.")
        else:
            try:
                result.cache_modified = cache.stat().st_mtime
                space_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"edge:{profile.name}:{workspace_id}"))
                result.space = decode_workspace(_snapshot_json(cache), space_id, name)
                recovered = len(result.space.open_tabs)
                if reported is not None and recovered != reported:
                    result.issues.append("Recovered tab count differs from the workspace list; cache may be stale.")
                result.issues.append("Cached snapshot only. Current Edge tab state has not been verified.")
            except (ValueError, OSError, UnicodeError, struct.error) as exc:
                result.issues.append(f"Unable to read the workspace cache: {type(exc).__name__}.")
        results.append(result)
    return results


def scan_workspaces(user_data: Path, profile_name: str | None = None) -> list[WorkspaceScan]:
    from .edge_sync import scan_sync_profile

    results: list[WorkspaceScan] = []
    for profile in sorted(user_data.iterdir()):
        if not profile.is_dir() or (profile_name is not None and profile.name != profile_name):
            continue
        modern = scan_sync_profile(profile)
        if modern is not None:
            results.extend(modern)
        elif (profile / "Workspaces" / "WorkspacesCache").is_file():
            results.extend(scan_profile(profile))
    return results


def export_workspaces(results: list[WorkspaceScan]) -> ExportData:
    spaces = [result.space for result in results if result.space is not None]
    for space in spaces:
        space.zen_uuid = "{" + str(uuid.UUID(space.space_id)) + "}"
    unavailable = sum(result.space is None for result in results)
    legacy = sum(result.storage_kind == "legacy_cache" for result in results)
    warnings = []
    if results:
        warnings.append("Workspace tabs come from local Edge snapshots. Online sync freshness is not verified.")
    if unavailable:
        warnings.append(f"{unavailable} listed workspace(s) have no readable local tab data and will be omitted.")
    if legacy:
        warnings.append(f"{legacy} workspace(s) use legacy caches that may be stale or incomplete.")
    if any(result.space and result.reported_tabs != len(result.space.open_tabs) for result in results):
        warnings.append("Some tab counts differ from Edge's records. Internal pages are skipped; "
                        "legacy caches may be incomplete.")
    return ExportData(source="edge", spaces=spaces, warnings=warnings)
