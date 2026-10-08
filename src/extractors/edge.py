"""Microsoft Edge extractor."""

from __future__ import annotations

from .base import BrowserExtractorError, ExportData
from .chromium import ChromiumExtractor
from .edge_workspaces import export_workspaces, scan_workspaces


class EdgeExtractor(ChromiumExtractor):
    name = "edge"
    display_name = "Microsoft Edge"

    user_data_dirs_macos = (
        "Library/Application Support/Microsoft Edge",
    )
    user_data_dirs_windows = (
        "AppData/Local/Microsoft/Edge/User Data",
    )
    user_data_dirs_linux = (
        ".config/microsoft-edge",
        # Flatpak (com.microsoft.Edge).
        ".var/app/com.microsoft.Edge/config/microsoft-edge",
    )

    keychain_service = "Microsoft Edge Safe Storage"
    keychain_account = "Microsoft Edge"

    macos_app_name = "Microsoft Edge"
    macos_process_paths = ("Microsoft Edge.app/Contents/MacOS/Microsoft Edge",)
    windows_process_names = ("msedge.exe",)

    def __init__(self, profile_name: str | None = None):
        self.profile_name = profile_name

    def available_profile_names(self) -> list[str]:
        return [path.name for path in super().profile_paths()]

    def profile_paths(self):
        return [path for path in super().profile_paths()
                if self.profile_name is None or path.name == self.profile_name]

    def extract(self) -> ExportData:
        try:
            data = super().extract()
        except BrowserExtractorError as exc:
            if exc.code != "no_edge_data":
                raise
            data = ExportData(source=self.name)
        root = self._user_data_dir()
        if root is not None:
            try:
                workspaces = export_workspaces(scan_workspaces(root, self.profile_name))
            except (ValueError, OSError) as exc:
                raise BrowserExtractorError(
                    "edge_workspace_read_failed",
                    "Edge workspace data could not be read safely. Close Edge and recheck.") from exc
            data.spaces.extend(workspaces.spaces)
            data.warnings.extend(workspaces.warnings)
        for space in data.spaces:
            space.zen_uuid = "{" + space.space_id + "}"
        if not data.spaces:
            raise BrowserExtractorError(
                "no_edge_data", "No readable Edge tabs, bookmarks, or workspace snapshots were found.")
        return data
