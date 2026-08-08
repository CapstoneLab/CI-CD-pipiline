from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def append_log(log_file: Path, message: str, echo: bool = True) -> None:
    timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")

    if echo:
        print(line)
