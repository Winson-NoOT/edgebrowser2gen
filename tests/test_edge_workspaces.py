"""Synthetic Mojo snapshots test recovery, corruption, and isolated staging."""

from __future__ import annotations

import base64
import json
import struct
import uuid

import pytest

from edge_to_zen import main
from extractors.edge_workspaces import decode_workspace, export_workspaces, scan_profile
from zen_sessions_importer import read_mozlz4

WORKSPACE_ID = "11111111-1111-4111-a111-111111111111"
GROUP_ID = "22222222-2222-4222-a222-222222222222"


class Builder:
    def __init__(self):
        self.data = bytearray()

    def allocate(self, size, count=0):
        self.data.extend(b"\0" * (-len(self.data) % 8))
        offset = len(self.data)
        self.data.extend(b"\0" * size)
        struct.pack_into("<II", self.data, offset, size, count)
        return offset

    def link(self, field, target):
        struct.pack_into("<Q", self.data, field, target - field)

    def string(self, field, value):
        raw = value.encode("utf-8")
        target = self.allocate(len(raw) + 8, len(raw))
        self.data[target + 8:target + 8 + len(raw)] = raw
        self.link(field, target)


def cache_fixture(*, grouped=True, url="https://example.com/current", selected=1):
    b = Builder()
    workspace = b.allocate(80)
    container = b.allocate(24)
    b.link(workspace + 24, container)
    array = b.allocate(16, 1)
    b.link(container + 8, array)
    groups = b.allocate(16 if grouped else 8, 1 if grouped else 0)
    b.link(container + 16, groups)
    if grouped:
        group = b.allocate(32)
        b.link(groups + 8, group)
        b.string(group + 8, GROUP_ID)
        b.string(group + 16, "Research <test>")
    tab = b.allocate(88)
    b.link(array + 8, tab)
    struct.pack_into("<i", b.data, tab + 28, selected)
    if grouped:
        b.string(tab + 32, GROUP_ID)
    navigations = b.allocate(24, 2)
    b.link(tab + 40, navigations)
    for index, (navigation_url, title) in enumerate([
        ("https://example.com/private-history", "Historical page"),
        (url, "Current page \u2603"),
    ]):
        nav = b.allocate(96)
        b.link(navigations + 8 + index * 8, nav)
        wrapper = b.allocate(16)
        b.link(nav + 8, wrapper)
        b.string(wrapper + 8, navigation_url)
        b.string(nav + 16, title)
    return {"Version": 6, "Fluid_Data": base64.b64encode(b.data).decode("ascii")}


def profile_fixture(tmp_path, *, include_cache=True, reported_count=1):
    root = tmp_path / "Edge"
    profile = root / "Profile 1"
    directory = profile / "Workspaces"
    directory.mkdir(parents=True)
    manifest = {"edgeWorkspaceCacheVersion": 1, "workspaces": [
        {"id": WORKSPACE_ID, "name": "Research", "count": reported_count},
    ]}
    (directory / "WorkspacesCache").write_text(json.dumps(manifest), encoding="utf-8")
    if include_cache:
        (directory / f"workspace_cache_{WORKSPACE_ID}").write_text(
            json.dumps(cache_fixture()), encoding="utf-8")
    return root, profile


def test_only_selected_navigation_is_exported():
    space = decode_workspace(cache_fixture(), "space", "Research")
    assert [tab.url for tab in space.open_tabs] == ["https://example.com/current"]
    assert space.open_tabs[0].title == "Current page \u2603"
    assert space.open_tabs[0].folder_id == GROUP_ID
    assert space.open_tabs[0].folder_path == ["Research <test>"]
    assert len(space.folders) == 1


def test_ungrouped_tab_is_supported():
    space = decode_workspace(cache_fixture(grouped=False), "space", "Research")
    assert space.open_tabs[0].folder_id is None
    assert space.folders == []


def test_internal_browser_page_is_skipped():
    space = decode_workspace(cache_fixture(url="edge://settings/"), "space", "Research")
    assert space.open_tabs == []


@pytest.mark.parametrize("version", [0, 1, 7])
def test_unknown_cache_version_is_rejected(version):
    cache = cache_fixture()
    cache["Version"] = version
    with pytest.raises(ValueError, match="Unsupported"):
        decode_workspace(cache, "space", "Research")


def test_out_of_bounds_pointer_is_rejected():
    cache = cache_fixture()
    raw = bytearray(base64.b64decode(cache["Fluid_Data"]))
    struct.pack_into("<Q", raw, 24, len(raw) * 5)
    cache["Fluid_Data"] = base64.b64encode(raw).decode("ascii")
    with pytest.raises(ValueError, match="pointer"):
        decode_workspace(cache, "space", "Research")


def test_invalid_selected_navigation_is_rejected():
    with pytest.raises(ValueError, match="selected navigation"):
        decode_workspace(cache_fixture(selected=9), "space", "Research")


def test_missing_cache_is_reported_and_not_exported(tmp_path):
    _, profile = profile_fixture(tmp_path, include_cache=False)
    results = scan_profile(profile)
    assert results[0].summary()["status"] == "unavailable"
    assert results[0].issues
    assert export_workspaces(results).spaces == []


def test_stale_counts_are_reported_and_source_is_unchanged(tmp_path):
    _, profile = profile_fixture(tmp_path, reported_count=8)
    files = {p: p.read_bytes() for p in profile.rglob("*") if p.is_file()}
    results = scan_profile(profile)
    assert "differs" in results[0].issues[0]
    assert files == {p: p.read_bytes() for p in files}
    legacy = export_workspaces(results).to_legacy_dict()
    assert legacy["spaces"][0]["open_tabs"][0]["parent_id"] == GROUP_ID


def test_path_traversal_workspace_id_is_rejected(tmp_path):
    _, profile = profile_fixture(tmp_path)
    path = profile / "Workspaces/WorkspacesCache"
    value = json.loads(path.read_text())
    value["workspaces"][0]["id"] = "../../outside"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        scan_profile(profile)


def test_stage_requires_snapshot_choice(tmp_path):
    root, _ = profile_fixture(tmp_path)
    target = tmp_path / "staged"
    assert main(["stage", "--edge-user-data", str(root), "--output", str(target)]) == 1
    assert not target.exists()


def test_stage_preserves_workspace_and_group_links(tmp_path):
    root, _ = profile_fixture(tmp_path)
    target = tmp_path / "staged"
    assert main(["stage", "--edge-user-data", str(root), "--output", str(target),
                 "--allow-cached-snapshot", "--summary-only"]) == 0
    session = read_mozlz4(target / "zen-sessions.jsonlz4")
    assert session["spaces"][0]["name"] == "Research"
    tab = next(tab for tab in session["tabs"] if not tab.get("pinned"))
    assert tab["entries"][0]["url"] == "https://example.com/current"
    assert tab["zenWorkspace"] == session["spaces"][0]["uuid"]
    assert tab["groupId"] == session["folders"][0]["id"]
    assert str(uuid.UUID(tab["zenWorkspace"].strip("{}")))


def test_stage_refuses_existing_output_and_browser_data(tmp_path):
    root, profile = profile_fixture(tmp_path)
    for target in [profile, root / "new-profile"]:
        assert main(["stage", "--edge-user-data", str(root), "--output", str(target),
                     "--allow-cached-snapshot"]) == 1
    assert not (root / "new-profile").exists()


def test_export_is_exclusive_and_has_coverage(tmp_path):
    root, _ = profile_fixture(tmp_path)
    target = tmp_path / "export.json"
    args = ["export", "--edge-user-data", str(root), "--output", str(target),
            "--allow-cached-snapshot", "--summary-only"]
    assert main(args) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["coverage"]["recoverable_workspaces"] == 1
    assert payload["total_spaces"] == 1
    assert "private-history" not in target.read_text(encoding="utf-8")
    original = target.read_bytes()
    assert main(args) == 1
    assert target.read_bytes() == original
