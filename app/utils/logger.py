from __future__ import annotations

from datetime import datetime
from pathlib import Path


def append_log(log_file: Path, message: str, echo: bool = True) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")

    if echo:
        print(line)
