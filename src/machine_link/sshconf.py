"""The managed block in ~/.ssh/config, and mlink's own known_hosts.

Every machine gets a Host stanza, and every ssh call mlink makes goes through it, so agent
forwarding, connection multiplexing, the identity file and host-key handling are configured in
exactly one place: the same place `ssh <name>`, rsync, git and VS Code Remote-SSH read.
Everything outside the markers belongs to the user and is preserved byte for byte.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Machine

BEGIN = "# >>> machine-link managed block >>>"
END = "# <<< machine-link managed block <<<"
#: Rented machines recycle addresses; keeping their host keys out of the user's own
#: known_hosts (and dropping them on `down`) is what prevents "REMOTE HOST IDENTIFICATION
#: HAS CHANGED" when a later machine gets the same IP.
KNOWN_HOSTS = "~/.config/mlink/known_hosts"


def config_file() -> Path:
    return Path.home() / ".ssh" / "config"


def known_hosts() -> Path:
    return Path(KNOWN_HOSTS).expanduser()


def stanza(machine: Machine, identity_file: str, forward: str, *, bare: bool) -> str:
    """One Host stanza. The bare name is included unless the user already uses it themselves."""
    patterns = f"{machine.name} {machine.alias}" if bare else machine.alias
    lines = [f"Host {patterns}", f"    HostName {machine.host}", f"    User {machine.user}"]
    if machine.port != 22:
        lines.append(f"    Port {machine.port}")
    lines += [
        f"    IdentityFile {identity_file}",
        f"    ForwardAgent {forward}",
        "    ControlMaster auto",
        "    ControlPath ~/.ssh/mlink-%C",
        "    ControlPersist 10m",
        f"    UserKnownHostsFile {KNOWN_HOSTS}",
        "    StrictHostKeyChecking accept-new",
    ]
    return "\n".join(lines)


def render(machines: list[Machine], identity_file: str, forward: str, claimed: set[str]) -> str:
    parts = [BEGIN, "# Managed by machine-link. Edits inside this block are overwritten."]
    for machine in sorted(machines, key=lambda m: m.name):
        parts += ["", stanza(machine, identity_file, forward, bare=machine.name not in claimed)]
    return "\n".join([*parts, END])


def _split(text: str) -> tuple[str, str | None, str]:
    """(before, block, after); the block is None when the file has no managed block yet."""
    start, stop = text.find(BEGIN), text.find(END)
    if start == -1 or stop < start:
        return text, None, ""
    stop += len(END)
    return text[:start], text[start:stop], text[stop:]


def host_patterns(text: str) -> set[str]:
    """Host patterns the user declares outside our block, so a bare alias is never stolen."""
    before, _, after = _split(text)
    patterns: set[str] = set()
    for line in (before + after).splitlines():
        words = line.split()
        if len(words) > 1 and words[0].lower() == "host":
            patterns.update(words[1:])
    return patterns


def apply(text: str, machines: list[Machine], identity_file: str, forward: str) -> str:
    block = render(machines, identity_file, forward, host_patterns(text))
    before, existing, after = _split(text)
    if existing is not None:
        return before + block + after
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}\n{block}\n" if text else f"{block}\n"


def write(
    machines: list[Machine], identity_file: str, forward: str, path: Path | None = None
) -> bool:
    """Rewrite the managed block, keeping one backup of the pre-mlink file. True if it changed."""
    path = path or config_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    hosts = known_hosts()
    hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    hosts.touch(mode=0o600)
    text = path.read_text() if path.exists() else ""
    updated = apply(text, machines, identity_file, forward)
    if updated == text:
        return False
    backup = path.with_name("config.mlink.bak")
    if text and not backup.exists():
        backup.write_text(text)
        backup.chmod(0o600)
    path.write_text(updated)
    path.chmod(0o600)
    return True


def alias_for(machine: Machine, path: Path | None = None) -> str:
    """The name to hand the user: the bare one when it is ours, else the prefixed one."""
    path = path or config_file()
    text = path.read_text() if path.exists() else ""
    return machine.alias if machine.name in host_patterns(text) else machine.name


def forget_host(machine: Machine) -> None:
    """Close the control connection and drop the host key: that address is about to be recycled."""
    if not machine.host:
        return  # never had a stanza or a host key
    subprocess.run(["ssh", "-O", "exit", machine.alias], capture_output=True)
    for host in (machine.host, f"[{machine.host}]:{machine.port}"):
        subprocess.run(["ssh-keygen", "-R", host, "-f", str(known_hosts())], capture_output=True)
    known_hosts().with_name(known_hosts().name + ".old").unlink(missing_ok=True)
