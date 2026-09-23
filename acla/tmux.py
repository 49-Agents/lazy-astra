from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time

DEFAULT_SOCKET = os.environ.get("ACLA_TMUX_SOCKET", "acla")
TMUX = shutil.which("tmux")
SHELL_COMMANDS = {"bash", "sh", "zsh", "fish", "dash", "login", "-bash", "-zsh"}


def _prefix(socket: str | None = None) -> list[str]:
    value = socket or DEFAULT_SOCKET
    return [TMUX, "-S", value] if "/" in value else [TMUX, "-L", value]


def _run(*args: str, socket: str | None = None, input_text: str | None = None) -> str:
    if not TMUX:
        raise RuntimeError("tmux is not installed")
    result = subprocess.run([*_prefix(socket), *args], input=input_text, text=True,
                            capture_output=True, check=False, timeout=20)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "tmux command failed").strip())
    return result.stdout


def safe_session(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    if not cleaned:
        raise ValueError("session name must contain letters or numbers")
    return cleaned[:180]


def alive(session: str, socket: str | None = None) -> bool:
    if not TMUX:
        return False
    return subprocess.run([*_prefix(socket), "has-session", "-t", f"={session}"],
                          capture_output=True, check=False).returncode == 0


def current_session() -> tuple[str | None, str | None]:
    """Find the current tmux session and socket when Astra is running inside tmux."""
    explicit = os.environ.get("ACLA_ASTRA_SESSION")
    explicit_socket = os.environ.get("ACLA_ASTRA_TMUX_SOCKET") or DEFAULT_SOCKET
    if explicit:
        return explicit, explicit_socket
    if not os.environ.get("TMUX"):
        return None, None
    socket_path = os.environ["TMUX"].split(",", 1)[0]
    try:
        session = _run("display-message", "-p", "#S", socket=socket_path).strip()
    except RuntimeError:
        return None, None
    return (session or None), socket_path


def pane_path(session: str, socket: str | None = None) -> str | None:
    try:
        value = _run("display-message", "-t", f"={session}:", "-p", "#{pane_current_path}", socket=socket).strip()
    except RuntimeError:
        return None
    return value or None


def metadata(session: str, socket: str | None = None) -> dict[str, str]:
    if not alive(session, socket):
        raise RuntimeError(f"tmux session is not running: {session}")
    output = _run("show-environment", "-t", f"={session}", socket=socket)
    values: dict[str, str] = {}
    for line in output.splitlines():
        if line.startswith("-") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def bind(session: str, agent_id: str, run_id: str, role: str, socket: str | None = None,
         require_existing: bool = False) -> None:
    existing = metadata(session, socket)
    expected = {"ACLA_AGENT_ID": agent_id, "ACLA_RUN_ID": run_id, "ACLA_ROLE": role}
    if require_existing and (existing.get("ACLA_AGENT_ID") != agent_id or existing.get("ACLA_ROLE") != role):
        raise RuntimeError(f"tmux session {session} has no matching ownership metadata")
    for key, value in expected.items():
        if key in {"ACLA_AGENT_ID", "ACLA_ROLE"} and existing.get(key) and existing[key] != value:
            raise RuntimeError(f"tmux session {session} belongs to another {role} identity")
        _run("set-environment", "-t", f"={session}", key, value, socket=socket)
    check = metadata(session, socket)
    if any(check.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"could not verify ownership metadata for tmux session {session}")


def pane_command(session: str, socket: str | None = None) -> str:
    value = _run("list-panes", "-t", f"={session}:", "-F", "#{pane_current_command}", socket=socket).strip()
    lines = [line for line in value.splitlines() if line]
    if len(lines) != 1:
        raise RuntimeError(f"tmux session {session} must have exactly one pane")
    return lines[0]


def verify_destination(session: str, agent_id: str, run_id: str, role: str,
                       socket: str | None = None, command: str | None = None,
                       require_existing: bool = False) -> None:
    bind(session, agent_id, run_id, role, socket, require_existing=require_existing)
    current = pane_command(session, socket).lower()
    if current in SHELL_COMMANDS or current.endswith("/bash") or current.endswith("/zsh"):
        raise RuntimeError(f"tmux destination {session} is an idle shell, not the {role} runtime")
    if command:
        executable = os.path.basename(shlex.split(command)[0]).lower()
        allowed = {executable, "node", "codex"}
        if role == "watcher":
            allowed.update({"python", "python3"})
        if current not in allowed and not any(token in current for token in allowed):
            raise RuntimeError(f"tmux destination {session} is running {current}, expected {executable}")


def wait_destination(session: str, agent_id: str, run_id: str, role: str,
                     socket: str | None = None, command: str | None = None, timeout: float = 30,
                     require_existing: bool = True) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            verify_destination(session, agent_id, run_id, role, socket, command, require_existing=require_existing)
            return
        except RuntimeError as exc:
            last = exc
            time.sleep(0.25)
    raise RuntimeError(str(last or "tmux destination did not become ready"))


def launch(session: str, workspace: str, command: str, *, agent_id: str, run_id: str,
           role: str, socket: str | None = None, env: dict[str, str] | None = None) -> bool:
    session = safe_session(session)
    if alive(session, socket):
        verify_destination(session, agent_id, run_id, role, socket, command, require_existing=True)
        return False
    if not os.path.isdir(workspace):
        raise ValueError(f"workspace does not exist: {workspace}")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("runtime command is empty")
    args = ["new-session", "-d", "-s", session, "-c", workspace]
    for key, value in {**(env or {}), "ACLA_AGENT_ID": agent_id, "ACLA_RUN_ID": run_id, "ACLA_ROLE": role}.items():
        args.extend(["-e", f"{key}={value}"])
    _run(*args, "--", *argv, socket=socket)
    bind(session, agent_id, run_id, role, socket)
    return True


def deliver(session: str, body: str, *, agent_id: str, run_id: str, role: str,
            socket: str | None = None, command: str | None = None) -> None:
    """Verify the owned runtime, then paste and submit one complete message."""
    verify_destination(session, agent_id, run_id, role, socket, command, require_existing=True)
    buffer = "acla-" + str(os.getpid()) + "-" + str(time.time_ns())
    try:
        _run("load-buffer", "-b", buffer, "-", input_text=body, socket=socket)
        _run("paste-buffer", "-d", "-r", "-p", "-b", buffer, "-t", f"={session}:", socket=socket)
        time.sleep(0.2)
        verify_destination(session, agent_id, run_id, role, socket, command, require_existing=True)
        _run("send-keys", "-t", f"={session}:", "Enter", socket=socket)
    finally:
        subprocess.run([*_prefix(socket), "delete-buffer", "-b", buffer], capture_output=True, check=False)
