# Observed Edge workspace storage

The adapter uses read-only snapshots of Edge's local profile storage. It supports
two observed formats and does not claim complete coverage of every Edge version.

## Modern sync records

The current reader snapshots `<profile>/Sync Data/LevelDB`, checks that source
file sizes and modification times did not change while copying, and parses the
temporary copy. `CURRENT` selects the manifest. Version edits identify active
tables and logs; obsolete files are excluded. Sequence numbers select the newest
record for each key, honoring LevelDB tombstones and sync metadata pending deletions.

Only the `edge_workspace` and `saved_tab_group` namespaces are used. Their data
records use an envelope with entity specifics in protobuf field 2. Observed
envelopes have an omitted/zero version or version one in field 1; both are accepted.
The shared saved-group envelope is documented in Chromium's
[SavedTabGroupData schema](https://chromium.googlesource.com/chromium/src/+/HEAD/components/saved_tab_groups/proto/saved_tab_group_data.proto).
The specifics have a UUID in field 1 and a workspace/group variant in field 4 or
a tab variant in field 5.

| Entity | Fields used |
| --- | --- |
| Workspace | 1: name, 2: ARGB color |
| Direct workspace tab | 1: parent workspace UUID, 2: UniquePosition, 3: URL, 4: title |
| Saved tab group | 2: title, 1002: parent workspace UUID, 1003: UniquePosition |
| Saved group tab | 1: parent group UUID, 2: integer position, 3: URL, 4: title |

Direct tabs and groups are interleaved using the lexicographically ordered
`custom_compressed_v1` UniquePosition bytes (field 4). Group members use their
integer position. Independent saved groups without a parent workspace are not
assigned to an invented workspace. Missing parent references and unsupported
encodings stop the scan. The observed colors are retained as RGB theme colors.

Modern workspace storage takes precedence for each profile, including an
authoritative empty store. The reader never resurrects deleted workspaces by
combining modern records with legacy caches. A local sync database cannot prove
that every cloud workspace has finished downloading or that its state is current.

The pure Python LevelDB and Snappy readers are vendored under MIT licenses; see
[provenance](../src/vendor/ORIGIN.md). These forensic readers can expose historical
data, so the current-record replay above is required before workspace decoding.

## Legacy cache records

The reader is based on local, read-only inspection of Edge caches on Windows.
It supports `edgeWorkspaceCacheVersion: 1` in the workspace list and `Version: 6`
in each workspace JSON cache. A matching cache can be absent, old, or have a
different tab count. This path is used only when modern workspace storage is absent.

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
Recovered tabs are imported as ordinary open tabs. Both readers give each source
workspace a stable destination UUID. Workspaces with the same name keep separate
identities; importing the same source workspace again keeps its existing Zen copy.

Tests construct synthetic snapshots. Real browser data is never committed to
the repository; local validation output belongs in ignored `local/` storage.
