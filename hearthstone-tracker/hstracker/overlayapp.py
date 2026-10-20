"""Keep the Windows copy of the Electron overlay in step with the repo (#28).

The Electron app runs from a Windows-local copy, by default
`C:\\Users\\<you>\\hearthstone-overlay`, because npm is unreliable on WSL UNC
paths. Every overlay change therefore needed a manual `cp`, and the copy
once sat three months stale. This module knows which files make up the app,
reports which ones differ, and copies them over:

- `hst overlay-sync --check` lists stale files (exit 1 when any are).
- `hst overlay-sync` copies them.
- `hst selftest` WARNs when the copy is stale, so it can't go unnoticed.

Never touched: `config.json` (the user's panel positions and hotkeys; only
seeded from `config.example.json` when missing), `node_modules`,
`art-cache`, logs.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_APP_DIR = Path(__file__).resolve().parents[2] / "hearthstone-overlay"

# The app's files, relative to the overlay folder. Globs cover renderer
# assets so a new renderer file is picked up without editing this list.
APP_FILES = ("main.js", "preload.js", "package.json", "config.example.json",
             "start-overlay.cmd", "stop-overlay.cmd")
APP_GLOBS = ("renderer/*.*", "renderer/fonts/*")


def windows_app_dir() -> Path:
    """HS_OVERLAY_APP_DIR, else the Windows user folder that holds the shared
    overlay folder (the same user hst live mirrors to)."""
    raw = os.environ.get("HS_OVERLAY_APP_DIR")
    if raw:
        return Path(raw).expanduser()
    from .overlay import resolve_overlay_dir

    return resolve_overlay_dir(None).parent / "hearthstone-overlay"


def app_files(repo_dir: Path = REPO_APP_DIR) -> list[str]:
    files = [f for f in APP_FILES if (repo_dir / f).is_file()]
    for pattern in APP_GLOBS:
        files += sorted(str(p.relative_to(repo_dir)).replace(os.sep, "/")
                        for p in repo_dir.glob(pattern) if p.is_file())
    return files


def stale_files(app_dir: Path, repo_dir: Path = REPO_APP_DIR) -> list[str]:
    """App files missing from, or different in, the Windows copy."""
    # Byte comparison, not filecmp: its cache keys on (size, mtime), and
    # copy2 hands the copy the source's mtime, so a same-size change that
    # was just synced could read back as stale (or a stale one as current).
    return [f for f in app_files(repo_dir)
            if not (app_dir / f).is_file()
            or (repo_dir / f).read_bytes() != (app_dir / f).read_bytes()]


def sync(app_dir: Path, repo_dir: Path = REPO_APP_DIR) -> dict[str, list[str]]:
    """Copy stale app files over; seed config.json only if it's missing."""
    copied = []
    for rel in stale_files(app_dir, repo_dir):
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_dir / rel, dest)
        copied.append(rel)
    seeded = []
    if not (app_dir / "config.json").exists() and (repo_dir / "config.example.json").is_file():
        shutil.copy2(repo_dir / "config.example.json", app_dir / "config.json")
        seeded.append("config.json")
    return {"copied": copied, "seeded": seeded}
