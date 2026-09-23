"""Reliable handoff to an existing Codex app thread through its local queue."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path


class DeliveryUnavailable(RuntimeError):
    """The queue command could not be started, so no handoff was attempted."""


class DeliveryUncertain(RuntimeError):
    """The queue command ran but its delivery outcome could not be confirmed."""


def queue_message(thread_id: str, body: str, *, codex_home: str, workspace: str) -> None:
    try:
        parsed = uuid.UUID(thread_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("thread_id must be a UUID") from exc
    if str(parsed) != thread_id:
        raise ValueError("thread_id must be a canonical UUID")
    if not Path(workspace).is_dir():
        raise DeliveryUnavailable("workspace does not exist or is not a directory")

    env = os.environ.copy()
    env["CODEX_HOME"] = codex_home
    executable = shutil.which("codex", path=env.get("PATH"))
    if not executable:
        raise DeliveryUnavailable("codex queue command is unavailable")
    argv = [executable, "queue", "--thread", thread_id, "--message", body]
    try:
        result = subprocess.run(argv, cwd=workspace, env=env, text=True,
                                capture_output=True, check=False, timeout=30)
    except FileNotFoundError as exc:
        # It disappeared after lookup but before exec: no child was dispatched.
        raise DeliveryUnavailable("codex queue command could not be started") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryUncertain("codex queue delivery outcome is uncertain") from exc
    if result.returncode:
        # CLI output may contain the full private message; never include it.
        raise DeliveryUncertain(f"codex queue exited with status {result.returncode}; delivery outcome is uncertain")
