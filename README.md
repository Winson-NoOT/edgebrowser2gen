# edgebrowser2gen

A development fork of [browser2zen](https://github.com/tarikbc/browser2zen), hosted at [Winson-NoOT/edgebrowser2gen](https://github.com/Winson-NoOT/edgebrowser2gen), focused on moving **Microsoft Edge Workspaces and their tabs into Zen Browser**. The destination is Zen, despite the project's requested `gen` spelling.

We reuse browser2zen's data model, compression, Zen writers, and existing app instead of building another browser or rewriting the migration engine. The original MIT license and attribution are retained. See [upstream documentation](docs/UPSTREAM_README.md).

## Current development status

The new workspace recovery path can:

- Read modern Edge workspace and saved-group records from consistent LevelDB snapshots.
- Recover older workspaces from version-six caches when modern workspace storage is absent.
- Preserve names, available colors, web tabs, tab order, and group membership.
- Honor current LevelDB records, tombstones and pending sync deletions.
- Report missing local data, mismatched counts, and unverified sync freshness.
- Choose Edge profiles and individual workspaces in the desktop preview.
- Review locally installed Edge add-ons by profile and open Firefox Add-ons searches in Zen.
- Merge workspaces into Zen with backups, stable source identities and repeated-import protection.
- Export a JSON recovery file or generate a Zen session file in a new staging directory.

This migrates locally recoverable browser data, rather than an entire browser installation. Workspace tabs become ordinary open tabs; workspace tab pin state is not recovered. Groups become Zen folders. Collaboration, Microsoft account sync, extension settings, saved passwords, autofill, browser preferences, and full tab navigation histories are not migrated. Internal Edge pages are skipped. Local snapshots can be stale, and a cloud-only workspace with no supported local tab records cannot be recovered.

Run the scanner to see coverage for your own installation. Reported workspace counts and recoverable cached-tab counts can differ. Install and launch Zen once to create a regular browsing profile before planning a live migration.

The desktop Edge import combines this reader with browser2zen's normal Edge tabs, bookmarks and optional history/cookies. Cookies remain subject to the inherited Windows encryption limitations; importing tabs does not guarantee restored logins. Development validation uses isolated profiles and never copies imports into the user's live Zen profile.

## Setup on Windows

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

To use the desktop app, install its GUI dependencies and launch it:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m app
```

`Launch edgebrowser2gen.ps1` is a convenience launcher. Choose Microsoft Edge, close both browsers, select an Edge profile and a Zen profile, then review the workspace list and coverage warnings. Start migration only when the preview matches what you want. The app backs up destination files before writing and refuses to start while either browser is running. Importing the same source workspace again keeps its existing Zen copy; this is a one-time migration, not ongoing synchronization.

Cookies are optional. Edge background processes can keep the cookie database locked after its window closes. The app checks cookie file access before writing imports; close Edge completely or disable **Cookies / login state** if that check fails. If a later step fails, the error screen lists completed imports and offers **Open Zen** when sessions were imported. Those imports remain saved. Avoid repeating a completed history import because it can add duplicate visits.

## Edge extensions

Click **Review Edge extensions** on the welcome screen or the workspace preview. This shows user add-ons found in local Edge profiles, excluding browser components, apps, themes and explicitly removed external extensions. Localized names are resolved from their manifests; enabled state is labeled unknown when Edge does not store it in a supported form.

Each **Find Firefox version** button opens a Mozilla Add-ons search in Zen. Searches are not verified compatibility matches, and the tool does not install add-ons automatically. Confirm the publisher, supported features and permissions, then install the Firefox version yourself. Some Edge extensions have no equivalent. Extension settings, private storage, sign-ins and licenses are not copied. Close Zen again before starting workspace migration.

Zen's [extension documentation](https://docs.zen-browser.app/user-manual/extensions) specifies Firefox add-ons as its extension source. The **Backup or restore Zen** feature can copy extensions between Zen profiles; that is a separate operation from migrating Edge extensions.

See the [migration review](docs/MIGRATION_REVIEW.md) for the complete support matrix and remaining gaps.

## Inspect workspace coverage

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py scan
```

For counts without names, page titles, or URLs:

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py scan --summary-only
```

Add `--profile Default` or `--profile "Profile 1"` to inspect a specific source profile. With modern storage, the reader uses current logical sync records and does not combine them with older caches for that same profile.

`Scan Workspaces.ps1` is a convenience launcher. Reading uses temporary copies and never writes into Edge or Zen storage. Missing legacy caches are listed as unavailable. An unsupported or inconsistent modern store stops the scan rather than falling back to potentially deleted legacy workspaces.

## Export recoverable local workspaces

After reviewing coverage, select local-snapshot recovery:

```powershell
New-Item -ItemType Directory -Path local -Force
.\.venv\Scripts\python.exe edge_to_zen.py export --profile Default --output local/workspace-export.json --allow-snapshot
```

The output contains URLs and titles and should stay private. `local/` is ignored by Git. Existing output files are never overwritten. Unavailable workspaces are recorded in the coverage section but cannot be exported as complete workspaces.

## Prepare an isolated test profile

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py stage --profile Default --output local/zen-test --allow-snapshot --summary-only
```

This creates a **new directory** with the JSON recovery export and `zen-sessions.jsonlz4`. It rejects existing directories and browser data paths. This directory is a staging artifact, not a complete Zen profile. It is not copied into your live Zen profile. `--allow-cached-snapshot` remains an alias for compatibility.

`--edge-user-data <path>` overrides source discovery for fixtures or another installation.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check edge_to_zen.py src/extractors/edge.py src/extractors/edge_workspaces.py src/extractors/edge_sync.py tests/test_edge_migration.py tests/test_edge_sync.py tools/validate_zen_restore.py
```

Tests use synthetic binary fixtures and cover both storage readers, deleted records, ordering, groups, malformed data, source immutability, desktop preview integration, output isolation, repeated imports, and backup failures.

Restoration was also verified in installed Zen 1.23.1b using a fresh, headless profile and synthetic example.com tabs. The check verifies two workspaces, four open tabs, their URLs/order, group membership, and a second import without duplication. To repeat it with your installed Zen:

```powershell
.\.venv\Scripts\python.exe -m pip install marionette_driver
.\.venv\Scripts\python.exe tools/validate_zen_restore.py --zen-executable "C:/Program Files/Zen Browser/zen.exe"
```

The validation script creates an unregistered test profile under ignored `local/`, blocks page loading through a loopback proxy, and closes its own headless process afterward. Desktop preview interactions were checked with synthetic data; this does not imply a live migration was performed.

See [observed cache format](docs/WORKSPACE_FORMAT.md) for the parser schema and remaining limitations.

## Next development work

Additional Edge schema versions and recovery of workspaces absent from local storage remain outside current support. Synchronize those workspaces in Edge first, then rescan. There is no cloud or collaboration API in this tool.
