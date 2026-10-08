"""Synthetic Edge extension inventory and manual reinstall flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge import Bridge
from extractors.edge import EdgeExtractor
from extractors.edge_extensions import scan_extensions

ID = "a" * 32


def fixture(profile, identifier=ID, *, name="Example Add-on", version="1.0", **metadata):
    folder = profile / "Extensions" / identifier / (version + "_0")
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "manifest_version": 3}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    path = profile / "Secure Preferences"
    preferences = json.loads(path.read_text()) if path.exists() else {"extensions": {"settings": {}}}
    preferences["extensions"]["settings"][identifier] = {"manifest": manifest, "location": 1, **metadata}
    path.write_text(json.dumps(preferences), encoding="utf-8")
    return folder


def test_inventory_reads_localized_active_version_without_source_writes(tmp_path):
    fixture(tmp_path, name="Old name", version="9.0")
    folder = fixture(tmp_path, name="__MSG_AddonName__", version="2.0", state=1)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["default_locale"] = "en"
    (folder / "manifest.json").write_text(json.dumps(manifest))
    locale = folder / "_locales/en"
    locale.mkdir(parents=True)
    (locale / "messages.json").write_text(json.dumps({"addonname": {"message": "Example & Add-on"}}))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    row = scan_extensions([tmp_path])["extensions"][0]
    assert row["name"] == "Example & Add-on"
    assert row["version"] == "2.0"
    assert row["enabled"] is True
    assert row["searchUrl"] == "https://addons.mozilla.org/en-US/firefox/search/?q=Example+%26+Add-on"
    assert row["status"] == "needs_firefox_version"
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("metadata,enabled", [({"state": 0}, False), ({"state": 1}, True),
                                               ({}, None), ({"state": 1, "disable_reasons": 8}, False)])
def test_enabled_state_is_not_guessed(tmp_path, metadata, enabled):
    fixture(tmp_path, **metadata)
    row = scan_extensions([tmp_path])["extensions"][0]
    assert row["enabled"] is enabled


@pytest.mark.parametrize("metadata", [{"location": 5}, {"location": 10}, {"state": 2}])
def test_components_and_removed_addons_are_excluded(tmp_path, metadata):
    fixture(tmp_path, **metadata)
    assert scan_extensions([tmp_path])["extensions"] == []


def test_broken_extension_does_not_hide_other_extensions(tmp_path):
    folder = fixture(tmp_path)
    fixture(tmp_path, identifier="b" * 32)
    (folder / "manifest.json").write_text("not JSON")
    result = scan_extensions([tmp_path])
    assert len(result["extensions"]) == 1
    assert result["warnings"]
    assert result["automatic_install_supported"] is False
    assert result["settings_transfer_supported"] is False


def test_locale_path_cannot_escape_extension_directory(tmp_path):
    folder = fixture(tmp_path, name="__MSG_Name__")
    value = json.loads((folder / "manifest.json").read_text())
    value["default_locale"] = "../../outside"
    (folder / "manifest.json").write_text(json.dumps(value))
    assert scan_extensions([tmp_path])["extensions"][0]["name"] == ID


def test_profiles_stay_separate_and_search_only_opens_after_click(tmp_path, monkeypatch):
    root = tmp_path / "Edge"
    for name in ("Default", "Profile 1"):
        profile = root / name
        fixture(profile)
        (profile / "History").touch()
    monkeypatch.setattr(EdgeExtractor, "_user_data_dir", lambda self: root)
    from app import bridge as bridge_module

    opened = []
    monkeypatch.setattr(bridge_module, "launch_zen", lambda url: opened.append(url) or True)
    bridge = Bridge()
    assert len(bridge.edge_extension_inventory()["extensions"]) == 2
    assert len(bridge.edge_extension_inventory("Default")["extensions"]) == 1
    assert bridge.edge_extension_inventory("../outside").get("error")
    assert opened == []
    assert bridge.open_edge_extension_search("Default", ID)["ok"] is True
    assert opened == ["https://addons.mozilla.org/en-US/firefox/search/?q=Example+Add-on"]
    assert bridge.open_edge_extension_search("Default", "x" * 32)["ok"] is False
    assert len(opened) == 1


def test_failed_container_save_stops_session_import(tmp_path, monkeypatch):
    from app.orchestrator import MigrationOptions, MigrationOrchestrator
    from tests.test_edge_migration import payload
    from zen_sessions_importer import ZenSessionsImporter
    from zen_space_importer import ZenProfile, ZenSpaceImporter

    monkeypatch.setattr(ZenSpaceImporter, "save_containers", lambda *args: False)
    assert ZenSpaceImporter(ZenProfile("Test", tmp_path)).import_spaces_as_containers(payload()) == {}
    source = EdgeExtractor()
    from extractors.edge_workspaces import export_workspaces
    from tests.test_edge_sync import decode_sync_workspaces, fixture_records

    monkeypatch.setattr(source, "extract", lambda: export_workspaces(
        decode_sync_workspaces(fixture_records(), "Default")))
    called = []
    monkeypatch.setattr(ZenSessionsImporter, "import_data", lambda *args, **kwargs: called.append(True) or True)
    events = list(MigrationOrchestrator(source).migrate(MigrationOptions(zen_profile_path=tmp_path)))
    assert any(event["kind"] == "step_error" and event["step"] == "containers" for event in events)
    assert called == []
