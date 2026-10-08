"""edgebrowser2gen: inspect Edge Workspaces and prepare an isolated Zen test profile."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from extractors.edge import EdgeExtractor
from extractors.edge_workspaces import export_workspaces, scan_workspaces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["scan", "export", "stage"])
    parser.add_argument("--edge-user-data", type=Path, help="Override Edge's User Data directory")
    parser.add_argument("--profile", help="Choose an Edge profile directory, such as Default or Profile 1")
    parser.add_argument("--output", type=Path, help="New export file or new staging directory")
    parser.add_argument("--summary-only", action="store_true", help="Print counts without names, titles, or URLs")
    parser.add_argument("--allow-snapshot", "--allow-cached-snapshot",
                        dest="allow_cached_snapshot", action="store_true",
                        help="Prepare recoverable local workspace snapshots, which may be incomplete or stale")
    args = parser.parse_args(argv)
    try:
        root = args.edge_user_data or EdgeExtractor()._user_data_dir()
        if root is None or not root.is_dir():
            raise ValueError("Edge User Data directory was not found")
        if args.profile and (Path(args.profile).name != args.profile or not (root / args.profile).is_dir()):
            raise ValueError("The selected Edge profile was not found")
        results = scan_workspaces(root, profile_name=args.profile)
        summary = {
            "project": "edgebrowser2gen",
            "destination": "Zen Browser",
            "listed_workspaces": len(results),
            "recoverable_workspaces": sum(result.space is not None for result in results),
            "recoverable_tabs": sum(len(result.space.open_tabs) for result in results if result.space),
            "reported_tabs": sum(result.reported_tabs or 0 for result in results),
            "live_browser_data_changed": False,
            "sync_workspaces": sum(result.storage_kind == "sync_v2" for result in results),
            "legacy_workspaces": sum(result.storage_kind == "legacy_cache" for result in results),
            "note": "Local sync snapshots and supported legacy caches; online sync freshness is not verified.",
        }
        if not args.summary_only:
            summary["workspaces"] = [result.summary() for result in results]
        if args.command != "scan":
            if args.output is None:
                raise ValueError("--output is required")
            if not args.allow_cached_snapshot:
                raise ValueError("Snapshots may be stale or incomplete. Review scan, then use --allow-snapshot.")
            if args.output.exists():
                raise ValueError("Output already exists; choose a new path")
            export = export_workspaces(results)
            if not export.spaces:
                raise ValueError("No workspace tab caches can be recovered")
            payload = export.to_legacy_dict()
            payload["coverage"] = {**summary, "workspaces": [result.summary() for result in results]}
            if args.command == "export":
                with args.output.open("x", encoding="utf-8") as file:
                    json.dump(payload, file, ensure_ascii=False, indent=2)
            else:
                from zen_sessions_importer import ZenSessionsImporter

                target = args.output.resolve()
                # Staging must never create files anywhere in live browser storage.
                protected = [root.resolve()]
                for variable in ("APPDATA", "LOCALAPPDATA"):
                    if os.environ.get(variable):
                        protected.append((Path(os.environ[variable]) / "zen").resolve())
                protected.extend([(Path.home() / ".zen").resolve(),
                                  (Path.home() / "Library/Application Support/zen").resolve()])
                if any(target == path or path in target.parents for path in protected):
                    raise ValueError("Choose a staging directory outside browser data")
                # mkdir(exist_ok=False) makes the new-directory requirement race-safe.
                target.mkdir(parents=True, exist_ok=False)
                (target / "edge-workspaces.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                importer = ZenSessionsImporter(target)
                logger = logging.getLogger("zen_sessions_importer")
                previously_disabled = logger.disabled
                try:
                    if args.summary_only:
                        logger.disabled = True
                    if not importer.import_data(payload, container_mappings={}):
                        raise RuntimeError("Unable to prepare the Zen workspace file")
                finally:
                    logger.disabled = previously_disabled
                summary["staging_directory"] = str(target)
                summary["staged_workspaces"] = len(export.spaces)
            summary["output"] = str(args.output)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(f"edgebrowser2gen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
