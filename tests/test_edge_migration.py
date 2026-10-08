"""Edge reader to desktop preview and Zen writer integration, with synthetic data."""

from __future__ import annotations

import json

from app.bridge import Bridge
from app.orchestrator import MigrationOptions, MigrationOrchestrator, preview_to_dict
from extractors.edge import EdgeExtractor
from extractors.edge_workspaces import export_workspaces
from tests.test_edge_sync import decode_sync_workspaces, fixture_records, write_store
from zen_sessions_importer import ZenSessionsImporter, read_mozlz4, write_mozlz4


def payload():
    scans = decode_sync_workspaces(fixture_records(), "Default")
    return export_workspaces(scans).to_legacy_dict()


def test_workspace_reader_is_used_by_desktop_preview(tmp_path, monkeypatch):
    root = tmp_path / "Edge"
    for name in ("Default", "Profile 1"):
        profile = root / name
        profile.mkdir(parents=True)
        (profile / "History").touch()
        write_store(profile / "Sync Data/LevelDB", [(1, list(fixture_records().items()))])
    monkeypatch.setattr(EdgeExtractor, "_user_data_dir", lambda self: root)
    source = EdgeExtractor(profile_name="Default")
    assert source.available_profile_names() == ["Default", "Profile 1"]
    data = source.extract()
    assert len(data.spaces) == 1
    assert len(data.spaces[0].open_tabs) == 2
    report = MigrationOrchestrator(source).preview(MigrationOptions(zen_profile_path=tmp_path))
    preview = preview_to_dict(report)
    assert preview["openTotal"] == 2
    assert preview["warnings"]
    assert preview["spaces"][0]["spaceId"] == data.spaces[0].space_id
    bridge = Bridge()
    assert bridge.set_source_profile("../outside")["ok"] is False
    assert bridge.set_source_profile("Default")["ok"] is True
    assert bridge.orchestrator.source.profile_name == "Default"


def test_repeated_import_does_not_add_tabs_or_folder_placeholders(tmp_path):
    importer = ZenSessionsImporter(tmp_path)
    assert importer.import_data(payload(), {})
    before = read_mozlz4(importer.sessions_file)
    assert importer.import_data(payload(), {})
    after = read_mozlz4(importer.sessions_file)
    for key in ("spaces", "tabs", "folders", "groups"):
        assert before[key] == after[key]
    assert "_migrationStableIdentity" not in after["spaces"][0]


def test_same_name_workspaces_keep_distinct_source_identities(tmp_path):
    data = payload()
    second = dict(data["spaces"][0], zen_uuid="{99999999-9999-4999-a999-999999999999}")
    data["spaces"].append(second)
    importer = ZenSessionsImporter(tmp_path)
    assert importer.import_data(data, {})
    sessions = read_mozlz4(importer.sessions_file)
    assert len(sessions["spaces"]) == 2
    assert len({space["uuid"] for space in sessions["spaces"]}) == 2


def test_failed_backup_stops_before_session_write(tmp_path, monkeypatch):
    importer = ZenSessionsImporter(tmp_path)
    write_mozlz4(importer.sessions_file, {"spaces": [], "tabs": [], "folders": []})
    before = importer.sessions_file.read_bytes()
    monkeypatch.setattr(importer, "_backup_sessions", lambda: False)
    assert importer.import_data(payload(), {}) is False
    assert importer.sessions_file.read_bytes() == before


def test_corrupt_existing_session_is_not_replaced(tmp_path):
    importer = ZenSessionsImporter(tmp_path)
    importer.sessions_file.write_bytes(b"corrupt existing data")
    assert importer.import_data(payload(), {}) is False
    assert importer.sessions_file.read_bytes() == b"corrupt existing data"
    assert next(tmp_path.glob("zen-sessions.jsonlz4.backup.*")).read_bytes() == b"corrupt existing data"


def test_sessionstore_backup_and_repeat_sync_preserve_existing_tab(tmp_path):
    store = tmp_path / "sessionstore.jsonlz4"
    write_mozlz4(store, {"windows": [{"tabs": [{"entries": [{"url": "https://example.com/existing"}],
                                               "zenSyncId": "existing", "pinned": False}]}]})
    original = store.read_bytes()
    importer = ZenSessionsImporter(tmp_path)
    assert importer.import_data(payload(), {})
    assert importer.import_data(payload(), {})
    assert next(tmp_path.glob("sessionstore.jsonlz4.backup.*")).read_bytes() == original
    urls = [tab["entries"][0]["url"] for tab in read_mozlz4(store)["windows"][0]["tabs"]]
    assert urls.count("https://example.com/existing") == 1
    assert urls.count("https://example.com/direct") == 1
    assert urls.count("https://example.com/grouped") == 1


def test_progress_remains_available_to_desktop_poller(tmp_path, monkeypatch):
    source = EdgeExtractor()
    monkeypatch.setattr(source, "extract", lambda: export_workspaces(
        decode_sync_workspaces(fixture_records(), "Default")))
    orchestrator = MigrationOrchestrator(source)
    opts = MigrationOptions(zen_profile_path=tmp_path, include_workspaces=False,
                            include_bookmarks=False, include_favicons=False)
    events = list(orchestrator.migrate(opts, preserve_progress=True))
    queued = orchestrator.bus.drain()
    assert any(event["kind"] == "step_done" and event["step"] == "sessions" for event in events)
    assert any(event["kind"] == "step_done" and event["step"] == "sessions" for event in queued)
    assert (tmp_path / ".browser2zen-migrated").exists()


def test_all_workspaces_excluded_does_not_write(tmp_path, monkeypatch):
    source = EdgeExtractor()
    data = export_workspaces(decode_sync_workspaces(fixture_records(), "Default"))
    monkeypatch.setattr(source, "extract", lambda: data)
    opts = MigrationOptions(zen_profile_path=tmp_path, excluded_space_ids=[data.spaces[0].space_id])
    events = list(MigrationOrchestrator(source).migrate(opts))
    assert any(event["kind"] == "step_error" for event in events)
    assert list(tmp_path.iterdir()) == []


def test_desktop_rejects_running_browser_before_writing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    bridge = Bridge()
    monkeypatch.setattr(bridge.orchestrator, "check_environment", lambda: SimpleNamespace(
        source_running=True, zen_running=False))
    result = bridge.start_migration(json.dumps({"zenProfilePath": str(tmp_path)}))
    assert result["ok"] is False
    assert list(tmp_path.iterdir()) == []


def test_desktop_reports_session_failure_in_final_state(tmp_path, monkeypatch):
    from types import SimpleNamespace

    bridge = Bridge()
    monkeypatch.setattr(bridge.orchestrator.source, "extract", lambda: export_workspaces(
        decode_sync_workspaces(fixture_records(), "Default")))
    monkeypatch.setattr(bridge.orchestrator, "check_environment", lambda: SimpleNamespace(
        source_running=False, zen_running=False, zen_profiles=[SimpleNamespace(path=tmp_path)]))
    monkeypatch.setattr(ZenSessionsImporter, "import_data", lambda *args, **kwargs: False)
    result = bridge.start_migration(json.dumps({"zenProfilePath": str(tmp_path),
                                               "includeWorkspaces": False, "includeBookmarks": False,
                                               "includeFavicons": False}))
    assert result["ok"] is True
    bridge._worker.join(timeout=5)
    state = bridge.drain_progress()
    assert state["state"]["status"] == "error"
    assert any(event["kind"] == "step_error" for event in state["events"])
    assert not (tmp_path / ".browser2zen-migrated").exists()


def test_workspace_and_open_tabs_use_the_selected_container(tmp_path):
    importer = ZenSessionsImporter(tmp_path)
    assert importer.import_data(payload(), {"Research": 4})
    sessions = read_mozlz4(importer.sessions_file)
    assert sessions["spaces"][0]["containerTabId"] == 4
    assert all(tab["userContextId"] == 4 for tab in sessions["tabs"] if not tab.get("zenIsEmpty"))
