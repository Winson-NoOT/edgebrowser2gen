"""Validate synthetic workspace restoration in an installed Zen, using a fresh profile.

Requires marionette_driver. Never opens or registers a user's browsing profile.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from marionette_driver.marionette import Marionette

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zen_sessions_importer import ZenSessionsImporter  # noqa: E402

PAYLOAD = {"spaces": [
    {"space_id": "native-a", "zen_uuid": "{11111111-1111-4111-a111-111111111111}",
     "space_name": "Migration Test A", "pinned_tabs": [], "open_tabs": [
         {"url": "https://example.com/one", "title": "Synthetic one", "parent_id": "group-one"},
         {"url": "https://example.com/two", "title": "Synthetic two", "parent_id": "group-one"},
         {"url": "https://example.com/three", "title": "Synthetic three"}],
     "folders": [{"folder_id": "group-one", "title": "Synthetic group", "index": 0}]},
    {"space_id": "native-b", "zen_uuid": "{22222222-2222-4222-a222-222222222222}",
     "space_name": "Migration Test B", "pinned_tabs": [], "open_tabs": [
         {"url": "https://example.com/four", "title": "Synthetic four"}], "folders": []},
]}


def inspect(client):
    return client.execute_script("""
        const w = Services.wm.getMostRecentWindow('navigator:browser');
        return {
          spaces: w?.gZenWorkspaces?.getWorkspaces?.().map(s => ({name:s.name, uuid:s.uuid})) ?? [],
          tabs: Array.from(w?.gBrowser?.tabContainer?.querySelectorAll('tab') ?? []).map(t => ({
            url:t.linkedBrowser.currentURI.spec, label:t.label,
            workspace:t.getAttribute('zen-workspace-id'), group:t.group?.id ?? null, pinned:t.pinned})),
          groups: w?.gBrowser?.tabGroups?.map(g => ({id:g.id, label:g.label})) ?? []
        };
    """)


def launch_and_inspect(executable, profile, port, *, restore):
    with (profile / "process.log").open("ab") as log:
        process = subprocess.Popen(
            [str(executable), "-headless", "-no-remote", "-profile", str(profile),
             "--marionette", "--remote-allow-system-access"], stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        client = Marionette(host="127.0.0.1", port=port, socket_timeout=20)
        connected = False
        try:
            client.raise_for_port(timeout=40)
            client.start_session()
            connected = True
            client.set_context("chrome")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = inspect(client)
                synthetic = [tab for tab in state["tabs"] if tab["label"].startswith("Synthetic")]
                if state["spaces"] and (not restore or len(synthetic) == 4):
                    return state
                time.sleep(0.2)
            raise AssertionError("Zen did not restore the expected synthetic tabs within 30 seconds")
        finally:
            if connected:
                try:
                    client._send_message("Marionette:Quit", {"flags": ["eForceQuit"]})
                    process.wait(timeout=15)
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def validate(state):
    spaces = [space for space in state["spaces"] if space["name"].startswith("Migration Test")]
    assert len(spaces) == 2, "Workspace count differs"
    tabs = [tab for tab in state["tabs"] if tab["label"].startswith("Synthetic")]
    assert len(tabs) == 4, "Tab count differs"
    assert [tab["url"] for tab in tabs] == ["https://example.com/one", "https://example.com/two",
                                          "https://example.com/three", "https://example.com/four"]
    assert [tab["workspace"] for tab in tabs] == [PAYLOAD["spaces"][0]["zen_uuid"]] * 3 + [
        PAYLOAD["spaces"][1]["zen_uuid"]]
    groups = [group for group in state["groups"] if group["label"] == "Synthetic group"]
    assert len(groups) == 1, "Group count differs"
    assert tabs[0]["group"] == tabs[1]["group"] == groups[0]["id"]
    assert tabs[2]["group"] is None and tabs[3]["group"] is None
    assert not any(tab["pinned"] for tab in tabs), "Open tabs changed pinned state"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zen-executable", type=Path, required=True)
    args = parser.parse_args()
    if not args.zen_executable.is_file():
        parser.error("Zen executable was not found")
    profile = ROOT / "local" / ("zen-native-" + uuid.uuid4().hex)
    profile.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    prefs = {
        "marionette.port": port, "marionette.enabled": True,
        "browser.aboutwelcome.enabled": False, "browser.startup.homepage_override.mstone": "ignore",
        "browser.startup.page": 3, "browser.sessionstore.resume_session_once": True,
        "browser.sessionstore.restore_on_demand": True, "browser.shell.checkDefaultBrowser": False,
        "network.proxy.type": 1, "network.proxy.http": "127.0.0.1", "network.proxy.http_port": 9,
        "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": 9, "network.proxy.no_proxies_on": "",
        "datareporting.healthreport.uploadEnabled": False, "datareporting.policy.dataSubmissionEnabled": False,
        "toolkit.telemetry.enabled": False,
    }
    (profile / "user.js").write_text("\n".join(
        f"user_pref({json.dumps(key)}, {json.dumps(value)});" for key, value in prefs.items()), encoding="utf-8")
    launch_and_inspect(args.zen_executable, profile, port, restore=False)
    for _ in range(2):
        assert ZenSessionsImporter(profile).import_data(PAYLOAD, {})
        state = launch_and_inspect(args.zen_executable, profile, port, restore=True)
        validate(state)
    report = {"passed": True, "workspaces": 2, "web_tabs": 4, "groups": 1,
              "repeat_import_verified": True, "profile": str(profile)}
    (profile / "native-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
