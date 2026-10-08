# edgebrowser2gen

A development fork of [browser2zen](https://github.com/tarikbc/browser2zen), hosted at [Winson-NoOT/edgebrowser2gen](https://github.com/Winson-NoOT/edgebrowser2gen), focused on moving **Microsoft Edge Workspaces and their tabs into Zen Browser**. The destination is Zen, despite the project's requested `gen` spelling.

We reuse browser2zen's data model, compression, Zen writers, and existing app instead of building another browser or rewriting the migration engine. The original MIT license and attribution are retained. See [upstream documentation](docs/UPSTREAM_README.md).

## Current development status

The new workspace recovery path can:

- List legacy Edge Workspaces across local profiles.
- Recover each tab's selected URL and title from version-six workspace caches.
- Preserve workspace names, tab array order, and group membership in exported and staged data.
- Report missing tab caches, mismatched counts, and unverified cache freshness.
- Export a JSON recovery file or generate a Zen session file in a new staging directory.

It does **not** yet migrate a complete live browser profile. Only the observed legacy cache format is supported. Newer Edge workspace storage, uncached workspaces, colors, pinned state, collaboration, and full session histories still require development. Cached tabs may be outdated. Reading caches is not proof that all current workspaces are present.

Run the scanner to see coverage for your own installation. Reported workspace counts and recoverable cached-tab counts can differ. Install and launch Zen once to create a regular browsing profile before planning a live migration.

The new recovery commands are separate from the inherited GUI. The inherited GUI's standard Edge import still handles generic profile data, and does not use this workspace-cache reader yet. No live browser profile has been modified during development.

## Setup on Windows

Python 3.11 or newer is required. A virtual environment has already been created on this machine.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

## Inspect workspace coverage

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py scan
```

For counts without names, page titles, or URLs:

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py scan --summary-only
```

`Scan Workspaces.ps1` is a convenience launcher. Reading uses temporary copies and never writes into Edge or Zen storage. An unsupported or missing workspace cache is listed as unavailable, rather than silently omitted from the coverage report.

## Export recoverable cached workspaces

After reviewing coverage, deliberately select cached-snapshot recovery:

```powershell
New-Item -ItemType Directory -Path local -Force
.\.venv\Scripts\python.exe edge_to_zen.py export --output local/workspace-export.json --allow-cached-snapshot
```

The output contains URLs and titles and should stay private. `local/` is ignored by Git. Existing output files are never overwritten. Unavailable workspaces are recorded in the coverage section but cannot be exported as complete workspaces.

## Prepare an isolated test profile

```powershell
.\.venv\Scripts\python.exe edge_to_zen.py stage --output local/zen-test --allow-cached-snapshot --summary-only
```

This creates a **new directory** with the JSON recovery export and `zen-sessions.jsonlz4`. It rejects existing directories and browser data paths. This directory is a staging artifact, not a complete Zen profile. It has been checked by reading back the compressed session and verifying workspace/tab/group links; browser GUI restoration has not yet been tested. It is not copied into your live Zen profile.

`--edge-user-data <path>` overrides source discovery for fixtures or another installation.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check edge_to_zen.py src/extractors/edge_workspaces.py tests/test_edge_workspaces.py
```

New tests use synthetic binary fixtures and cover selected-navigation recovery, groups, null group IDs, invalid versions and pointers, internal URLs, stale counts, source immutability, exclusive exports, and isolated staging. The inherited Windows test path-discovery issues were corrected so the upstream checks can run here.

See [observed cache format](docs/WORKSPACE_FORMAT.md) for the parser schema and remaining limitations.

## Next development work

1. Add a source for current tabs in workspaces without supported local caches, including newer Edge storage.
2. Add coverage and workspace selection to the desktop preview.
3. Test restoration in a dedicated Zen profile and verify ordering, groups, and repeated imports.
4. Integrate the new reader with the existing backup and migration flow after complete source coverage is understood.
