from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_executable(command: str) -> str | None:
    direct = shutil.which(command)
    if direct:
        return direct

    if os.name != "nt":
        return None

    for candidate in (f"{command}.exe", f"{command}.cmd", f"{command}.bat", f"{command}.ps1"):
        via_ext = shutil.which(candidate)
        if via_ext:
            return via_ext

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None

    links_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Links"
    if links_dir.exists():
        link_candidate = links_dir / f"{command}.exe"
        if link_candidate.exists():
            return str(link_candidate)

    winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if winget_packages.exists():
        for match in winget_packages.rglob(f"{command}.exe"):
            return str(match)

    return None
