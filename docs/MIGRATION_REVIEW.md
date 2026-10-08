# Edge to Zen migration review

The original implementation focused on workspaces and tabs. It did not inventory
or migrate installed Edge extensions. This review adds a visible extension
reinstall checklist and documents the remaining gaps instead of presenting the
tool as a complete browser-profile migration.

| Data | Current support | What still needs attention |
| --- | --- | --- |
| Local Edge workspaces and web tabs | Modern sync snapshots and supported legacy caches | Cloud-only workspaces and unsupported schemas; sync freshness is not verified |
| Workspace names and colors | Names; observed modern RGB colors | Older cache colors are not recovered |
| Tab groups | Converted to Zen folders | Zen folders differ from Edge groups |
| Pinned tabs | Normal Chromium session pins are retained | Workspace pin state is not decoded |
| Bookmarks | Import channel supported | Verify the resulting bookmark layout |
| History | Optional import | No complete per-tab back/forward history |
| Cookies and login state | Optional, subject to decryption support | New Windows app-bound encryption can prevent import; sites may require sign-in |
| Installed extensions | Local inventory and Firefox Add-ons search links | Manual compatible-version installation; searches do not establish compatibility |
| Extension settings, storage, sign-ins | No cross-browser transfer | Use each extension's own export/import or sync feature where available |
| Saved passwords | No Edge-to-Zen importer | Separate password migration is needed |
| Autofill and payment data | No importer | Separate setup is needed |
| Browser preferences, search providers and themes | No Edge-to-Zen importer | Configure Zen separately |
| Workspace collaboration and Microsoft account sync | Not migrated | Workspaces become local Zen workspaces |

Zen installs [Firefox-compatible add-ons](https://docs.zen-browser.app/user-manual/extensions).
An Edge extension package is not a ready-to-install Zen add-on. Mozilla's
[porting guide](https://extensionworkshop.com/documentation/develop/porting-a-google-chrome-extension/)
requires checking browser API differences, testing, packaging and signing.
The checklist avoids asserting compatibility from an extension name alone.

The inherited Zen backup/restore feature includes categories called passwords,
preferences and extensions. Those copy **Zen data into Zen**, not Edge data into
Zen. Their presence must not be interpreted as support for those Edge imports.

## Reliability issue fixed during this review

The container importer ignored failures from saving containers and workspace
preferences. It could return mappings after a failed write, allowing the session
step to proceed. Failures now propagate, and the migration stops before session
import when container setup fails. Synthetic regression tests cover this path.

## Verification boundaries

Automated tests cover the extension inventory, locale handling, active-version
selection, filtering, unknown enabled states, source immutability and store-link
launching. The inventory does not contact an extension store until the user
clicks a search button. Neither tests nor the checklist prove that every Edge
extension has a matching Firefox version or transferable settings.

Existing Zen restoration tests use synthetic workspaces in an isolated native
profile. A staged export of real source data verifies relationships without
performing a live migration. Installed add-ons were inventoried read-only; no
add-ons were installed or removed during this review.
