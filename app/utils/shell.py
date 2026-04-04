from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.utils.logger import append_log


@dataclass
class CommandResult:
    exit_code: int
    output: str


def _decode_chunk(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def run_command(
    command: list[str],
    cwd: Path,
    log_file: Path,
    env: dict[str, str] | None = None,
) -> CommandResult:
    append_log(log_file, f"$ {' '.join(command)}")

    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            env=process_env,
        )
    except FileNotFoundError as exc:
        message = f"Command not found: {command[0]} ({exc})"
        append_log(log_file, message)
        append_log(log_file, "[exit_code] 127")
        return CommandResult(exit_code=127, output=message)

    chunks: list[str] = []

    assert process.stdout is not None
    while True:
        raw = process.stdout.readline()
        if not raw and process.poll() is not None:
            break
        if not raw:
            continue

        decoded = _decode_chunk(raw).rstrip("\r\n")
        chunks.append(decoded)
        append_log(log_file, decoded)

    exit_code = process.wait()
    append_log(log_file, f"[exit_code] {exit_code}")

    return CommandResult(exit_code=exit_code, output="\n".join(chunks))
