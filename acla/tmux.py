from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

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


def _bind_created_session(session: str, agent_id: str, run_id: str, role: str,
                          socket: str | None = None) -> None:
    expected = {"ACLA_AGENT_ID": agent_id, "ACLA_RUN_ID": run_id, "ACLA_ROLE": role}
    for key, value in expected.items():
        _run("set-environment", "-t", f"={session}", key, value, socket=socket)
    check = metadata(session, socket)
    if any(check.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"could not verify ownership metadata for tmux session {session}")


def verify_destination(session: str, agent_id: str, run_id: str, role: str,
                       socket: str | None = None, command: str | None = None,
                       require_existing: bool = False) -> None:
    expected = {"ACLA_AGENT_ID": agent_id, "ACLA_RUN_ID": run_id, "ACLA_ROLE": role}
    saved = metadata(session, socket)
    if any(saved.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"tmux session {session} has no matching ownership metadata")
    pane = _run("list-panes", "-t", f"={session}:", "-F", "#{pane_id}\t#{pane_pid}\t#{pane_current_command}", socket=socket).strip().splitlines()
    if len(pane) != 1:
        raise RuntimeError(f"tmux session {session} must have exactly one pane")
    pane_id, pane_pid, current = pane[0].split("\t", 2)
    if saved.get("ACLA_PANE_ID") != pane_id or saved.get("ACLA_PANE_PID") != pane_pid:
        raise RuntimeError(f"tmux pane for session {session} no longer matches its saved runtime")
    current = current.lower()
    if current in SHELL_COMMANDS or current.endswith("/bash") or current.endswith("/zsh"):
        raise RuntimeError(f"tmux destination {session} is an idle shell, not the {role} runtime")


def _environment_keys(socket: str | None = None) -> set[str]:
    """Find ACLA variables inherited by the launcher or stored in tmux global env."""
    keys = {key for key in os.environ if key.startswith("ACLA_")}
    try:
        output = _run("show-environment", "-g", socket=socket)
    except RuntimeError:
        return keys
    for line in output.splitlines():
        if line.startswith("-") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key.startswith("ACLA_"):
            keys.add(key)
    return keys


def launch(session: str, workspace: str, command: str | list[str], *, agent_id: str, run_id: str,
           role: str, socket: str | None = None, env: dict[str, str] | None = None) -> bool:
    session = safe_session(session)
    if alive(session, socket):
        verify_destination(session, agent_id, run_id, role, socket, command, require_existing=True)
        return False
    if not os.path.isdir(workspace):
        raise ValueError(f"workspace does not exist: {workspace}")
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("runtime command is empty")
    # A child must not inherit the Astra/Codex thread binding from its launcher.
    # Apply requested environment only after clearing those inherited identities.
    clean = ["env", "-u", "CODEX_THREAD_ID"]
    for key in sorted(_environment_keys(socket)):
        clean.extend(["-u", key])
    values = {**(env or {}), "ACLA_AGENT_ID": agent_id, "ACLA_RUN_ID": run_id, "ACLA_ROLE": role}
    clean.extend(f"{key}={value}" for key, value in values.items())
    clean.extend(argv)
    args = ["new-session", "-d", "-s", session, "-c", workspace]
    _run(*args, "--", shlex.join(clean), socket=socket)
    _bind_created_session(session, agent_id, run_id, role, socket)
    pane = _run("list-panes", "-t", f"={session}:", "-F", "#{pane_id}\t#{pane_pid}", socket=socket).strip().splitlines()
    if len(pane) != 1:
        raise RuntimeError(f"tmux session {session} must have exactly one pane")
    pane_id, pane_pid = pane[0].split("\t", 1)
    _run("set-environment", "-t", f"={session}", "ACLA_PANE_ID", pane_id, socket=socket)
    _run("set-environment", "-t", f"={session}", "ACLA_PANE_PID", pane_pid, socket=socket)
    return True


def stop_owned(session: str, *, agent_id: str, run_id: str, role: str,
               socket: str | None = None) -> None:
    verify_destination(session, agent_id, run_id, role, socket, require_existing=True)
    _run("kill-session", "-t", f"={session}", socket=socket)
