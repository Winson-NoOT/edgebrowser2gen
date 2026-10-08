"""Current sync records, deletion handling, ordering, and profile precedence."""

from __future__ import annotations

import json
import struct

import pytest

from extractors._protobuf import fields, varint
from extractors.edge_sync import decode_sync_workspaces, read_sync_records
from extractors.edge_workspaces import scan_workspaces

SPACE = "11111111-1111-4111-a111-111111111111"
GROUP = "22222222-2222-4222-a222-222222222222"
TAB = "33333333-3333-4333-a333-333333333333"
DIRECT = "44444444-4444-4444-a444-444444444444"


def vi(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def proto(*items):
    out = b""
    for number, value in items:
        if isinstance(value, str):
            value = value.encode("utf-8")
        if isinstance(value, bytes):
            out += vi(number * 8 + 2) + vi(len(value)) + value
        else:
            out += vi(number * 8) + vi(value)
    return out


def entity(identifier, kind, value):
    return proto((1, 1), (2, proto((1, identifier), (kind, value))))


def fixture_records():
    return {
        f"edge_workspace-dt-{SPACE}".encode(): entity(SPACE, 4, proto((1, "Research"), (2, 0xFF336699))),
        f"edge_workspace-dt-{DIRECT}".encode(): entity(DIRECT, 5, proto(
            (1, SPACE), (2, proto((4, b"a-position"))),
            (3, "https://example.com/direct"), (4, "Direct tab"))),
        f"saved_tab_group-dt-{GROUP}".encode(): entity(GROUP, 4, proto(
            (2, "Group"), (1002, SPACE), (1003, proto((4, b"b-position"))))),
        f"saved_tab_group-dt-{TAB}".encode(): entity(TAB, 5, proto(
            (1, GROUP), (2, 0), (3, "https://example.com/grouped"), (4, "Grouped tab"))),
    }


def test_sync_workspace_and_group_relationships():
    results = decode_sync_workspaces(fixture_records(), "Default")
    assert len(results) == 1
    scan = results[0]
    assert scan.storage_kind == "sync_v2"
    assert scan.reported_tabs == 2
    assert [tab.url for tab in scan.space.open_tabs] == [
        "https://example.com/direct", "https://example.com/grouped"]
    assert scan.space.open_tabs[1].folder_id == GROUP
    assert scan.space.folders[0].title == "Group"
    assert scan.space.color == {"r": 0x33 / 255, "g": 0x66 / 255, "b": 0x99 / 255}


def test_positions_interleave_groups_and_direct_tabs():
    records = fixture_records()
    records[f"edge_workspace-dt-{DIRECT}".encode()] = entity(DIRECT, 5, proto(
        (1, SPACE), (2, proto((4, b"c-position"))),
        (3, "https://example.com/direct"), (4, "Direct tab")))
    scan = decode_sync_workspaces(records, "Default")[0]
    assert [tab.url for tab in scan.space.open_tabs] == [
        "https://example.com/grouped", "https://example.com/direct"]


def test_pending_sync_deletion_is_not_exported():
    records = fixture_records()
    records[f"edge_workspace-md-{DIRECT}".encode()] = proto((3, 1))
    scan = decode_sync_workspaces(records, "Default")[0]
    assert scan.reported_tabs == 1
    assert scan.space.open_tabs[0].url == "https://example.com/grouped"


@pytest.mark.parametrize("version", [None, 0, 1])
def test_supported_envelope_versions(version):
    records = fixture_records()
    for key, value in records.items():
        specifics = fields(value)[2][0]
        records[key] = proto((2, specifics)) if version is None else proto((1, version), (2, specifics))
    assert len(decode_sync_workspaces(records, "Default")[0].space.open_tabs) == 2


def test_unknown_envelope_version_is_rejected():
    records = fixture_records()
    key = next(iter(records))
    records[key] = proto((1, 2), (2, fields(records[key])[2][0]))
    with pytest.raises(ValueError, match="record version"):
        decode_sync_workspaces(records, "Default")


def test_sync_orphan_is_rejected():
    records = fixture_records()
    del records[f"edge_workspace-dt-{SPACE}".encode()]
    with pytest.raises(ValueError, match="missing workspace"):
        decode_sync_workspaces(records, "Default")


@pytest.mark.parametrize("data", [b"\x00", b"\x0a\x05xx", b"\x0e", b"\x08\x80"])
def test_invalid_protobuf_is_rejected(data):
    with pytest.raises(ValueError):
        fields(data)


def test_protobuf_overflow_is_rejected():
    with pytest.raises(ValueError, match="64 bits"):
        varint(b"\xff" * 10, 0)


def log_record(payload):
    # CRC is populated to mirror real LevelDB WAL records.
    from vendor.snappy import crc32c

    block = b"\x01" + payload
    checksum = crc32c(block)
    masked = (((checksum >> 15) | (checksum << 17)) + 0xA282EAD8) & 0xFFFFFFFF
    return struct.pack("<IHB", masked, len(payload), 1) + payload


def write_store(path, batches):
    path.mkdir(parents=True)
    (path / "CURRENT").write_text("MANIFEST-000001\n", encoding="ascii")
    # VersionEdit: log_number = 2, next_file_number = 3, last_sequence = 10.
    (path / "MANIFEST-000001").write_bytes(log_record(vi(2) + vi(2) + vi(3) + vi(3) + vi(4) + vi(10)))
    output = b""
    for sequence, changes in batches:
        batch = struct.pack("<QI", sequence, len(changes))
        for key, value in changes:
            batch += bytes([0 if value is None else 1]) + vi(len(key)) + key
            if value is not None:
                batch += vi(len(value)) + value
        output += log_record(batch)
    (path / "000002.log").write_bytes(output)


def test_log_replay_keeps_latest_and_honors_tombstones(tmp_path):
    key = b"edge_workspace-dt-test"
    directory = tmp_path / "db"
    write_store(directory, [(1, [(key, b"old")]), (2, [(key, b"new")]), (3, [(key, None)])])
    assert read_sync_records(directory) == {}


def test_obsolete_log_is_not_resurrected(tmp_path):
    directory = tmp_path / "db"
    write_store(directory, [(5, [(b"edge_workspace-dt-current", b"current")])])
    batch = struct.pack("<QI", 999, 1) + b"\x01" + vi(23) + b"edge_workspace-dt-stale" + vi(5) + b"stale"
    (directory / "000001.log").write_bytes(log_record(batch))
    assert read_sync_records(directory) == {b"edge_workspace-dt-current": b"current"}


def test_snapshot_is_read_only_and_scanner_prefers_v2(tmp_path):
    profile = tmp_path / "Default"
    directory = profile / "Sync Data/LevelDB"
    records = fixture_records()
    write_store(directory, [(1, list(records.items()))])
    legacy = profile / "Workspaces"
    legacy.mkdir()
    (legacy / "WorkspacesCache").write_text(json.dumps({
        "edgeWorkspaceCacheVersion": 1, "workspaces": [{"id": SPACE, "name": "Old name", "count": 10}]}))
    before = {path: path.read_bytes() for path in directory.iterdir()}
    scans = scan_workspaces(tmp_path, profile_name="Default")
    assert scans[0].name == "Research"
    assert scans[0].storage_kind == "sync_v2"
    assert before == {path: path.read_bytes() for path in directory.iterdir()}
    assert scan_workspaces(tmp_path, profile_name="Other") == []


def test_empty_authoritative_v2_does_not_restore_legacy_cache(tmp_path):
    profile = tmp_path / "Default"
    write_store(profile / "Sync Data/LevelDB", [(1, [(b"edge_workspace-GlobalMetadata", b"empty")])])
    legacy = profile / "Workspaces"
    legacy.mkdir()
    (legacy / "WorkspacesCache").write_text(json.dumps({
        "edgeWorkspaceCacheVersion": 1, "workspaces": [{"id": SPACE, "name": "Deleted", "count": 10}]}))
    assert scan_workspaces(tmp_path) == []
