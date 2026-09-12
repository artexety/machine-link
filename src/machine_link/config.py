"""The two configs: who you are (~/.config/mlink/config.toml) and what to deploy (mlink.toml)."""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .models import Machine, valid_name
from .ui import Fail, warn

PROJECT_FILE = "mlink.toml"
#: Kept here rather than imported from machine_link.agent, which reads the config itself.
FORWARD_MODES = ("constrained", "always", "never")


@dataclass(slots=True)
class Ssh:
    identity_file: str = "~/.ssh/id_ed25519"
    default_user: str = "ubuntu"
    connect_timeout: int = 5
    reachability_timeout: int = 900
    #: How much of your agent a rented machine gets to see. See machine_link.agent.
    forward_agent: str = "constrained"

    @property
    def key(self) -> Path:
        return Path(self.identity_file).expanduser()

    @property
    def pubkey(self) -> Path:
        return self.key.with_name(self.key.name + ".pub")


@dataclass(slots=True)
class Git:
    name: str = ""
    email: str = ""


@dataclass(slots=True)
class Repo:
    url: str
    dest: str
    branch: str = ""
    post_clone: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.dest.rstrip("/").rsplit("/", 1)[-1]


@dataclass(slots=True)
class Sync:
    remote: str
    local: str


@dataclass(slots=True)
class Provision:
    commands: list[str] = field(default_factory=list)
    script: str = ""


@dataclass(slots=True)
class Settings:
    """The machine config: the same for every project."""

    ssh: Ssh = field(default_factory=Ssh)
    git: Git = field(default_factory=Git)
    providers: dict[str, dict] = field(default_factory=dict)
    machines: list[Machine] = field(default_factory=list)
    path: Path = field(default_factory=Path)


@dataclass(slots=True)
class Project:
    """The project config, found by walking up from the working directory like git finds .git."""

    path: Path | None = None
    repos: list[Repo] = field(default_factory=list)
    sync: list[Sync] = field(default_factory=list)
    provision: Provision = field(default_factory=Provision)

    @property
    def dir(self) -> Path | None:
        return self.path.parent if self.path else None


def settings_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if env := os.environ.get("MLINK_CONFIG"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "mlink" / "config.toml"


def load_settings(explicit: str | None = None) -> Settings:
    path = settings_path(explicit)
    if not path.is_file():
        raise Fail(2, f"no config at {path}", "run: mlink init")
    load_env(path.parent / ".env")
    data = _toml(path)
    ssh = _build(Ssh, data.get("ssh") or {}, "ssh")
    if ssh.forward_agent not in FORWARD_MODES:
        raise Fail(
            2,
            f"ssh.forward_agent = {ssh.forward_agent!r} is not a mode",
            f"use one of: {', '.join(FORWARD_MODES)}",
        )
    return Settings(
        ssh=ssh,
        git=_build(Git, data.get("git") or {}, "git"),
        providers={str(k): dict(v) for k, v in (data.get("providers") or {}).items()},
        machines=[_static(entry, ssh.default_user) for entry in data.get("machines") or []],
        path=path,
    )


def _static(entry: dict, default_user: str) -> Machine:
    """A [[machines]] entry: a box with a fixed address and no API behind it."""
    if not entry.get("name") or not entry.get("host"):
        raise Fail(2, "a [[machines]] entry is missing name or host", "see examples/config.toml")
    return Machine(
        name=valid_name(str(entry["name"])),
        host=str(entry["host"]),
        user=str(entry.get("user") or default_user),
        port=int(entry.get("port") or 22),
    )


def find_project(start: Path | None = None) -> Path | None:
    directory = (start or Path.cwd()).resolve()
    for candidate in (directory, *directory.parents):
        if (path := candidate / PROJECT_FILE).is_file():
            return path
    return None


def load_project(start: Path | None = None) -> Project:
    """The nearest mlink.toml, if any. An empty one deploys the enclosing repository."""
    path = find_project(start)
    if path is None:
        return Project()
    data = _toml(path)
    project = Project(
        path=path,
        repos=[_build(Repo, entry, "repos") for entry in data.get("repos") or []],
        sync=[_build(Sync, entry, "sync") for entry in data.get("sync") or []],
        provision=_build(Provision, data.get("provision") or {}, "provision"),
    )
    if not project.repos:
        origin = subprocess.run(
            ["git", "-C", str(path.parent), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if origin:
            project.repos = [Repo(url=origin, dest="~/" + path.parent.name)]
        else:
            warn(f"{path} lists no repos and {path.parent} has no git origin remote")
    return project


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise Fail(2, f"invalid TOML in {path}: {exc}", "fix the syntax and retry") from None


def _build(cls, values: object, where: str):
    """One dataclass from one table. Unknown keys are reported; missing required ones fail."""
    if not isinstance(values, dict):
        raise Fail(2, f"[{where}] must be a table", "see the examples/ directory")
    known = {f.name for f in fields(cls)}
    for key in values:
        if key not in known:
            warn(f"unknown config key {where}.{key} (ignored)")
    try:
        return cls(**{k: v for k, v in values.items() if k in known})
    except TypeError as exc:
        raise Fail(2, f"[{where}] is incomplete: {exc}", "see the examples/ directory") from None


def load_env(path: Path) -> None:
    """Fill unset credentials from ~/.config/mlink/.env or $MLINK_ENV. A real export always wins."""
    path = Path(os.environ.get("MLINK_ENV") or path).expanduser()
    if not path.is_file():
        return
    if path.stat().st_mode & 0o077:
        warn(f"{path} is readable by others; chmod 600 it")
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key and not key.startswith("#") and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip("'\"")


TEMPLATE = """\
[ssh]
identity_file = "{identity_file}"   # its .pub is registered at every provider by 'mlink init'
default_user = "{default_user}"     # remote user when a target names none
connect_timeout = 5                 # seconds per connection attempt
reachability_timeout = 900          # total seconds to wait for a fresh machine to accept ssh
forward_agent = "{forward_agent}"
# what a rented machine sees of your agent:
#   constrained  only your key, only for github.com, only from that machine (OpenSSH 8.9+)
#   always       the whole agent, as 'ssh -A' does it
#   never        nothing; private repos will not clone

[git]
name = "{git_name}"
email = "{git_email}"

# A provider is on when its section exists; 'mlink init' turns on the ones whose credentials
# it finds. Credentials are read from the environment or from ~/.config/mlink/.env, never
# from this file:
#   PRIME_API_KEY                          app.primeintellect.ai > settings > API keys
#   VAST_API_KEY                           cloud.vast.ai > Account > Keys
#   VERDA_CLIENT_ID, VERDA_CLIENT_SECRET   Verda console > Credentials > Cloud API

{providers}
# Machines with a fixed address and no API behind them: a workstation, a lab server.
# [[machines]]
# name = "workstation"
# host = "192.168.1.50"
# user = "{default_user}"
"""

PROVIDER_SECTIONS = {
    "prime": """\
[providers.prime]
# image = "ubuntu_22_cuda_12"       # default: the first image the offer lists
# disk_gb = 256
""",
    "vast": """\
[providers.vast]
# image = "vastai/base-image:@vastai-automatic-tag"   # any docker image; Vast adds sshd
# disk_gb = 50
""",
    "verda": """\
[providers.verda]
image = "ubuntu-24.04-cuda-12.6"    # the plain ubuntu-24.04 image ships without a driver
# location = "FIN-01"               # used when an offer names no location
# disk_gb = 100
""",
}


def _section(name: str, on: bool) -> str:
    """A provider's section, commented out line by line when it is off."""
    text = PROVIDER_SECTIONS[name]
    if on:
        return text
    return "".join(
        ("" if line.startswith("#") else "# ") + line + "\n" for line in text.splitlines()
    )


def write_settings(settings: Settings) -> None:
    settings.path.parent.mkdir(parents=True, exist_ok=True)
    settings.path.write_text(
        TEMPLATE.format(
            identity_file=settings.ssh.identity_file,
            default_user=settings.ssh.default_user,
            forward_agent=settings.ssh.forward_agent,
            git_name=settings.git.name,
            git_email=settings.git.email,
            providers="\n".join(_section(n, n in settings.providers) for n in PROVIDER_SECTIONS),
        )
    )
