"""Read-only Edge add-on inventory for a Firefox-version reinstall checklist.

No extension code, permissions, private storage or browser credentials are copied.
Store searches occur only when the user opens a checklist link.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

from .edge_workspaces import _snapshot_json

_ID = re.compile(r"[a-p]{32}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:_[0-9]+)?\Z")
_LOCALE = re.compile(r"[A-Za-z0-9_-]+\Z")
_MESSAGE = re.compile(r"__MSG_([A-Za-z0-9_@]+)__\Z")
_COMPONENT_LOCATIONS = {5, 10}


def _version_key(path: Path):
    return tuple(int(number) for number in re.split(r"[._]", path.name))


def _manifest(profile: Path, identifier: str, metadata: dict) -> tuple[dict, Path | None]:
    root = (profile / "Extensions" / identifier).resolve()
    if profile.resolve() not in root.parents:
        raise ValueError("Extension folder leaves the selected profile")
    versions = sorted((p for p in root.iterdir() if p.is_dir() and _VERSION.fullmatch(p.name)),
                      key=_version_key, reverse=True) if root.is_dir() else []
    cached = metadata.get("manifest", {})
    expected = cached.get("version") if isinstance(cached, dict) else None
    candidates = []
    for version in versions:
        # Do not follow linked paths outside this extension's directory.
        manifest = version / "manifest.json"
        if root not in manifest.resolve().parents:
            continue
        if manifest.is_file():
            value = _snapshot_json(manifest)
            candidates.append((value, version))
    for value, version in candidates:
        if expected and value.get("version") == expected:
            return value, version
    if candidates:
        return candidates[0]
    # Unpacked developer extensions can live outside the profile. Use only the
    # cached metadata; never follow their external path or execute their code.
    if metadata.get("location") == 4 and isinstance(cached, dict):
        return cached, None
    return {}, None


def _name(manifest: dict, directory: Path | None, identifier: str) -> str:
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        return identifier
    message = _MESSAGE.fullmatch(name)
    if not message:
        return name
    locale = manifest.get("default_locale")
    if directory and isinstance(locale, str) and _LOCALE.fullmatch(locale):
        source = directory / "_locales" / locale / "messages.json"
        if directory.resolve() in source.resolve().parents and source.is_file():
            messages = _snapshot_json(source)
            for key, entry in messages.items():
                if key.casefold() == message[1].casefold() and isinstance(entry, dict):
                    translated = entry.get("message")
                    if isinstance(translated, str) and translated.strip():
                        return translated
    return identifier


def scan_extensions(profiles: list[Path]) -> dict:
    rows = []
    warnings = []
    for profile in profiles:
        settings = {}
        for filename in ("Preferences", "Secure Preferences"):
            source = profile / filename
            if not source.is_file():
                continue
            try:
                preferences = _snapshot_json(source)
                entries = preferences.get("extensions", {}).get("settings", {})
                if not isinstance(entries, dict):
                    raise ValueError("Invalid extension preferences")
                settings.update(entries)
            except (ValueError, OSError, AttributeError):
                warnings.append(f"{profile.name}: extension preferences could not be fully read.")
        directory = profile / "Extensions"
        identifiers = set(settings)
        if directory.is_dir():
            identifiers.update(p.name for p in directory.iterdir() if p.is_dir())
        for identifier in sorted(identifiers):
            if not _ID.fullmatch(identifier):
                continue
            metadata = settings.get(identifier, {})
            if not isinstance(metadata, dict):
                continue
            if metadata.get("location") in _COMPONENT_LOCATIONS or metadata.get("state") == 2:
                continue  # Browser components or an explicitly uninstalled external extension.
            try:
                manifest, version_directory = _manifest(profile, identifier, metadata)
                if not manifest or "app" in manifest or "theme" in manifest:
                    continue
                name = _name(manifest, version_directory, identifier)
                state = metadata.get("state")
                enabled = True if state == 1 else False if state == 0 else None
                if metadata.get("disable_reasons"):
                    enabled = False
                rows.append({
                    "id": identifier, "profile": profile.name, "name": name,
                    "version": str(manifest.get("version", "")), "enabled": enabled,
                    "status": "needs_firefox_version",
                    "searchUrl": "https://addons.mozilla.org/en-US/firefox/search/?" + urlencode({"q": name}),
                })
            except (ValueError, OSError):
                warnings.append(f"{profile.name}: one extension manifest could not be read.")
    return {"extensions": sorted(rows, key=lambda row: (row["profile"], row["name"].casefold(), row["id"])),
            "warnings": warnings, "automatic_install_supported": False,
            "settings_transfer_supported": False}
