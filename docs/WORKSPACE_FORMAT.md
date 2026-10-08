# Observed Edge workspace cache format

The reader is based on local, read-only inspection of Edge caches on Windows.
It supports `edgeWorkspaceCacheVersion: 1` in the workspace list and `Version: 6`
in each workspace JSON cache. Newer Edge workspace storage needs a separate
adapter. A matching cache can be absent, old, or have a different tab count.

The workspace list is `<profile>/Workspaces/WorkspacesCache`; each tab cache is
`workspace_cache_<workspace UUID>` in that same directory. `Fluid_Data` is
base64-encoded binary data, using Mojo-style relative pointers and aligned
structures. No collaborator or account data is needed for extraction.

| Structure | Size | Fields used |
| --- | --- | --- |
| Workspace | 80 bytes | Tab/group container pointer at offset 24 |
| Tab/group container | 24 bytes | Tab array pointer at 8, group array pointer at 16 |
| Tab | 88 bytes | Selected navigation index at 28, optional group ID at 32, navigation array at 40 |
| Navigation | 96 bytes | URL wrapper pointer at 8, title string pointer at 16 |
| URL wrapper | 16 bytes | URL string pointer at 8 |
| Group | 32 bytes | Group ID string at 8, title string at 16 |

These structures have version zero. Pointer fields are unsigned 64-bit offsets
relative to the field itself. Strings have an 8-byte array header followed by
UTF-8 bytes. Pointer arrays have an 8-byte header followed by 64-bit offsets.
Every read checks the buffer bounds and expected structure size/version.

Tab array order is retained. Each tab exports only its selected navigation.
Duplicates are retained because two tabs can intentionally show the same URL.
HTTP, HTTPS, and FTP URLs are allowed; browser-internal URLs are skipped.
Group IDs are mapped to Zen folder IDs during staging. Pinned state, color,
sharing, cloud synchronization, and current-cache freshness are not inferred.
Recovered tabs are imported as ordinary open tabs.

Tests construct synthetic snapshots. Real browser data is never committed to
the repository; local validation output belongs in ignored `local/` storage.
