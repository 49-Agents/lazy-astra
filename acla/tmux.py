from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time

SOCKET = os.environ.get("ACLA_TMUX_SOCKET", "acla")
TMUX = shutil.which("tmux")


def _run(*args: str, input_text: str | None = None) -> str:
    if not TMUX:
        raise RuntimeError("tmux is not installed")
    result = subprocess.run([TMUX, "-L", SOCKET, *args], input=input_text, text=True,
                            capture_output=True, check=False, timeout=20)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "tmux command failed").strip())
    return result.stdout


def safe_session(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    if not cleaned:
        raise ValueError("session name must contain letters or numbers")
    return cleaned[:180]


def alive(session: str) -> bool:
    if not TMUX:
        return False
    return subprocess.run([TMUX, "-L", SOCKET, "has-session", "-t", f"={session}"],
                          capture_output=True, check=False).returncode == 0


def launch(session: str, workspace: str, command: str | None = None) -> bool:
    session = safe_session(session)
    if alive(session):
        return False
    if not os.path.isdir(workspace):
        raise ValueError(f"workspace does not exist: {workspace}")
    command = command or os.environ.get("ACLA_LUNA_COMMAND", "codex")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("Luna command is empty")
    _run("new-session", "-d", "-s", session, "-c", workspace, "--", *argv)
    return True


def deliver(session: str, body: str) -> None:
    """Paste a complete message into an existing Luna prompt and submit it."""
    if not alive(session):
        raise RuntimeError(f"Luna tmux session is not running: {session}")
    buffer = "acla-" + str(os.getpid()) + "-" + str(time.time_ns())
    try:
        _run("load-buffer", "-b", buffer, "-", input_text=body)
        _run("paste-buffer", "-d", "-r", "-p", "-b", buffer, "-t", f"={session}:")
        time.sleep(0.2)
        _run("send-keys", "-t", f"={session}:", "Enter")
    finally:
        subprocess.run([TMUX, "-L", SOCKET, "delete-buffer", "-b", buffer], capture_output=True, check=False)
