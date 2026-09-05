from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

KST = timezone(timedelta(hours=9))

LogListener = Callable[[Path, str], None]

_listeners: list[LogListener] = []
_listeners_lock = threading.Lock()


@contextmanager
def listen_to_logs(listener: LogListener) -> Iterator[None]:
    """Observe newly appended log lines without changing local log persistence.

    Listeners are process-local and best-effort: delivery errors must never make
    a pipeline step fail.  The log file remains the authoritative copy.
    """

    with _listeners_lock:
        _listeners.append(listener)
    try:
        yield
    finally:
        with _listeners_lock:
            if listener in _listeners:
                _listeners.remove(listener)


def append_log(log_file: Path, message: str, echo: bool = True) -> None:
    timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")

    with _listeners_lock:
        listeners = tuple(_listeners)
    for listener in listeners:
        try:
            listener(log_file, line)
        except Exception:  # noqa: BLE001
            # Streaming is an optional transport. The on-disk log above must
            # remain available even when a listener has a transient failure.
            continue

    if echo:
        print(line)
